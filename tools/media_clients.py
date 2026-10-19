"""Typed Radarr/Sonarr API v3 clients and high-level media services.

The clients own HTTP/authentication/error translation.  The services own title
resolution, existing-library awareness, configuration selection, monitoring,
and search orchestration.  No download-client or filesystem APIs are used.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, UTC
from difflib import SequenceMatcher
import logging
import re
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v3"
_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_WORD_RE = re.compile(r"[^a-z0-9]+")


class ArrAPIError(RuntimeError):
    """A safe, user-facing Radarr/Sonarr error."""


class ArrClient:
    """Small synchronous API v3 client shared by Radarr and Sonarr."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        service_name: str,
        *,
        timeout: httpx.Timeout | float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self._client = httpx.Client(
            base_url=f"{self.base_url}{_API_PREFIX}",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected_type: type | tuple[type, ...] = (dict, list),
    ) -> Any:
        try:
            response = self._client.request(method, path, params=params, json=json)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "%s API %s %s returned HTTP %s: %s",
                self.service_name,
                method,
                path,
                exc.response.status_code,
                exc.response.text[:1000],
                exc_info=True,
            )
            if exc.response.status_code in {401, 403}:
                raise ArrAPIError(
                    f"{self.service_name} rejected the configured API key."
                ) from exc
            raise ArrAPIError(
                f"{self.service_name} returned an error (HTTP {exc.response.status_code})."
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "%s API %s %s failed: %s",
                self.service_name,
                method,
                path,
                exc,
                exc_info=True,
            )
            raise ArrAPIError(
                f"I couldn't reach {self.service_name} at {self.base_url}."
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error(
                "%s API %s %s returned malformed JSON: %r",
                self.service_name,
                method,
                path,
                response.text[:1000],
                exc_info=True,
            )
            raise ArrAPIError(
                f"{self.service_name} returned an unexpected response."
            ) from exc
        if not isinstance(payload, expected_type):
            logger.error(
                "%s API %s %s returned %s, expected %s",
                self.service_name,
                method,
                path,
                type(payload).__name__,
                expected_type,
            )
            raise ArrAPIError(f"{self.service_name} returned an unexpected response.")
        return payload

    def system_status(self) -> dict[str, Any]:
        return self.request("GET", "/system/status", expected_type=dict)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def root_folders(self) -> list[dict[str, Any]]:
        return self.request("GET", "/rootfolder", expected_type=list)

    def quality_profiles(self) -> list[dict[str, Any]]:
        return self.request("GET", "/qualityprofile", expected_type=list)

    def queue(self, page_size: int = 100) -> list[dict[str, Any]]:
        page = 1
        records: list[dict[str, Any]] = []
        params: dict[str, Any] = {"pageSize": page_size}
        if self.service_name == "Sonarr":
            params.update({
                "includeUnknownSeriesItems": "true",
                "includeEpisode": "true",
                "includeSeries": "true",
            })
        else:
            params.update({"includeUnknownMovieItems": "true", "includeMovie": "true"})
        while True:
            data = self.request(
                "GET",
                "/queue",
                params={**params, "page": page},
                expected_type=(dict, list),
            )
            if isinstance(data, list):
                return data
            page_records = data.get("records", [])
            if not isinstance(page_records, list):
                raise ArrAPIError(
                    f"{self.service_name} returned an unexpected queue response."
                )
            records.extend(page_records)
            total = data.get("totalRecords")
            if not page_records or not isinstance(total, int) or len(records) >= total:
                return records
            page += 1

    def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/command", json=payload, expected_type=dict)


class RadarrClient(ArrClient):
    def __init__(self, base_url: str, api_key: str, **kwargs: Any) -> None:
        super().__init__(base_url, api_key, "Radarr", **kwargs)

    @classmethod
    def from_env(cls) -> RadarrClient:
        return cls(_required_env("RADARR_URL"), _required_env("RADARR_API_KEY"))

    def lookup_movies(self, term: str) -> list[dict[str, Any]]:
        return self.request(
            "GET", "/movie/lookup", params={"term": term}, expected_type=list
        )

    def movies(self) -> list[dict[str, Any]]:
        return self.request("GET", "/movie", expected_type=list)

    def add_movie(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/movie", json=payload, expected_type=dict)

    def update_movie(self, movie: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PUT", f"/movie/{int(movie['id'])}", json=movie, expected_type=dict
        )

    def search_movie_now(self, movie_id: int) -> dict[str, Any]:
        return self.command({"name": "MoviesSearch", "movieIds": [movie_id]})


class SonarrClient(ArrClient):
    def __init__(self, base_url: str, api_key: str, **kwargs: Any) -> None:
        super().__init__(base_url, api_key, "Sonarr", **kwargs)

    @classmethod
    def from_env(cls) -> SonarrClient:
        return cls(_required_env("SONARR_URL"), _required_env("SONARR_API_KEY"))

    def lookup_series(self, term: str) -> list[dict[str, Any]]:
        return self.request(
            "GET", "/series/lookup", params={"term": term}, expected_type=list
        )

    def series(self) -> list[dict[str, Any]]:
        return self.request("GET", "/series", expected_type=list)

    def add_series(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/series", json=payload, expected_type=dict)

    def update_series(self, series: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PUT", f"/series/{int(series['id'])}", json=series, expected_type=dict
        )

    def episodes(self, series_id: int) -> list[dict[str, Any]]:
        return self.request(
            "GET", "/episode", params={"seriesId": series_id}, expected_type=list
        )

    def update_episode(self, episode: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PUT", f"/episode/{int(episode['id'])}", json=episode, expected_type=dict
        )

    def search_season_now(self, series_id: int, season: int) -> dict[str, Any]:
        return self.command({
            "name": "SeasonSearch",
            "seriesId": series_id,
            "seasonNumber": season,
        })

    def search_episodes_now(self, episode_ids: list[int]) -> dict[str, Any]:
        return self.command({"name": "EpisodeSearch", "episodeIds": episode_ids})


class RadarrService:
    def __init__(self, client: RadarrClient) -> None:
        self.client = client

    def search_movie(self, title: str, year: int | None = None) -> dict[str, Any]:
        candidates = self.client.lookup_movies(_lookup_term(title, year))
        existing = {movie.get("tmdbId"): movie for movie in self.client.movies()}
        results = [
            _movie_summary(item, existing.get(item.get("tmdbId")))
            for item in candidates[:10]
        ]
        resolution = _resolve(candidates, title, "tmdbId", year=year)
        return {
            "query": title,
            "ambiguous": len(resolution) > 1,
            "total_results": len(candidates),
            "results": results,
        }

    def add_movie(
        self,
        tmdb_id: int,
        *,
        quality: str | None = None,
        search: bool = True,
    ) -> dict[str, Any]:
        candidates = self.client.lookup_movies(f"tmdb:{tmdb_id}")
        match = next(
            (item for item in candidates if item.get("tmdbId") == tmdb_id), None
        )
        if match is None:
            return {"action": "not_found", "tmdb_id": tmdb_id}
        return self._add_resolved(match, quality=quality, search=search)

    def download_movie(
        self,
        title: str,
        *,
        year: int | None = None,
        tmdb_id: int | None = None,
        quality: str | None = None,
        search: bool = True,
    ) -> dict[str, Any]:
        if tmdb_id is not None:
            return self.add_movie(tmdb_id, quality=quality, search=search)
        candidates = self.client.lookup_movies(_lookup_term(title, year))
        resolution = _resolve(candidates, title, "tmdbId", year=year)
        if not resolution:
            return {"action": "not_found", "query": title}
        if len(resolution) > 1:
            return {
                "action": "needs_selection",
                "query": title,
                "choices": [_movie_summary(item) for item in resolution],
            }
        return self._add_resolved(resolution[0], quality=quality, search=search)

    def _add_resolved(
        self,
        movie: dict[str, Any],
        *,
        quality: str | None,
        search: bool,
    ) -> dict[str, Any]:
        library = self.client.movies()
        existing = next(
            (item for item in library if item.get("tmdbId") == movie.get("tmdbId")),
            None,
        )
        if existing:
            if existing.get("monitored") is False:
                existing = deepcopy(existing)
                existing["monitored"] = True
                existing = self.client.update_movie(existing)
            summary = _movie_summary(movie, existing)
            if existing.get("hasFile"):
                return {"action": "already_downloaded", "movie": summary}
            if search:
                command = self.client.search_movie_now(int(existing["id"]))
                return {
                    "action": "search_started",
                    "already_existed": True,
                    "movie": summary,
                    "command_id": command.get("id"),
                }
            return {"action": "already_added", "movie": summary}

        root = _select_root_folder(
            self.client.root_folders(), library, media_env("RADARR_ROOT_FOLDER")
        )
        profile = _select_quality_profile(
            self.client.quality_profiles(),
            library,
            quality or media_env("RADARR_QUALITY_PROFILE"),
        )
        payload = {
            key: deepcopy(movie[key])
            for key in (
                "title",
                "titleSlug",
                "tmdbId",
                "year",
                "images",
                "minimumAvailability",
            )
            if key in movie
        }
        payload.update({
            "qualityProfileId": profile["id"],
            "rootFolderPath": root["path"],
            "monitored": True,
            "addOptions": {"searchForMovie": search},
        })
        added = self.client.add_movie(payload)
        return {
            "action": "added",
            "movie": _movie_summary(movie, added),
            "search_started": search,
            "quality_profile": profile.get("name"),
            "root_folder": root.get("path"),
        }

    def search_existing_movie(
        self, title: str, year: int | None = None
    ) -> dict[str, Any]:
        existing = _resolve(self.client.movies(), title, "tmdbId", year=year)
        if not existing:
            return {"action": "not_in_radarr", "query": title}
        if len(existing) > 1:
            return {
                "action": "needs_selection",
                "choices": [_movie_summary(item, item) for item in existing],
            }
        movie = existing[0]
        if movie.get("monitored") is False:
            movie = deepcopy(movie)
            movie["monitored"] = True
            movie = self.client.update_movie(movie)
        if movie.get("hasFile"):
            return {
                "action": "already_downloaded",
                "movie": _movie_summary(movie, movie),
            }
        command = self.client.search_movie_now(int(movie["id"]))
        return {
            "action": "search_started",
            "movie": _movie_summary(movie, movie),
            "command_id": command.get("id"),
        }

    def movie_status(self, title: str, year: int | None = None) -> dict[str, Any]:
        matches = _resolve(self.client.movies(), title, "tmdbId", year=year)
        if not matches:
            return {"state": "not_in_radarr", "query": title}
        if len(matches) > 1:
            return {
                "state": "needs_selection",
                "choices": [_movie_summary(item, item) for item in matches],
            }
        movie = matches[0]
        queue = [
            item
            for item in self.client.queue()
            if item.get("movieId") == movie.get("id")
        ]
        state = (
            _queue_state(queue[0])
            if queue
            else (
                "downloaded"
                if movie.get("hasFile")
                else ("monitored_waiting" if movie.get("monitored") else "unmonitored")
            )
        )
        result = {**_movie_summary(movie, movie), "state": state}
        if queue:
            result["queue"] = _queue_summary(queue[0], "radarr")
        return result

    def queue(self) -> list[dict[str, Any]]:
        return [_queue_summary(item, "radarr") for item in self.client.queue()]


class SonarrService:
    def __init__(self, client: SonarrClient) -> None:
        self.client = client

    def search_series(self, title: str, year: int | None = None) -> dict[str, Any]:
        candidates = self.client.lookup_series(_lookup_term(title, year))
        existing = {series.get("tvdbId"): series for series in self.client.series()}
        results = [
            _series_summary(item, existing.get(item.get("tvdbId")))
            for item in candidates[:10]
        ]
        resolution = _resolve(candidates, title, "tvdbId", year=year)
        return {
            "query": title,
            "ambiguous": len(resolution) > 1,
            "total_results": len(candidates),
            "results": results,
        }

    def add_series(
        self,
        tvdb_id: int,
        *,
        seasons: list[int] | None = None,
        quality: str | None = None,
        search: bool = True,
    ) -> dict[str, Any]:
        candidates = self.client.lookup_series(f"tvdb:{tvdb_id}")
        match = next(
            (item for item in candidates if item.get("tvdbId") == tvdb_id), None
        )
        if match is None:
            return {"action": "not_found", "tvdb_id": tvdb_id}
        return self._add_resolved(
            match, seasons=seasons, quality=quality, search=search
        )

    def download_series(
        self,
        title: str,
        *,
        seasons: list[int] | None = None,
        year: int | None = None,
        tvdb_id: int | None = None,
        quality: str | None = None,
        search: bool = True,
    ) -> dict[str, Any]:
        if tvdb_id is not None:
            return self.add_series(
                tvdb_id, seasons=seasons, quality=quality, search=search
            )
        candidates = self.client.lookup_series(_lookup_term(title, year))
        resolution = _resolve(candidates, title, "tvdbId", year=year)
        if not resolution:
            return {"action": "not_found", "query": title}
        if len(resolution) > 1:
            return {
                "action": "needs_selection",
                "query": title,
                "choices": [_series_summary(item) for item in resolution],
            }
        return self._add_resolved(
            resolution[0], seasons=seasons, quality=quality, search=search
        )

    def _add_resolved(
        self,
        candidate: dict[str, Any],
        *,
        seasons: list[int] | None,
        quality: str | None,
        search: bool,
    ) -> dict[str, Any]:
        requested = sorted(set(seasons or []))
        library = self.client.series()
        existing = next(
            (item for item in library if item.get("tvdbId") == candidate.get("tvdbId")),
            None,
        )
        if existing:
            series = deepcopy(existing)
            if requested:
                _set_season_monitoring(series, requested, exclusive=True)
            else:
                _set_season_monitoring(series, [], exclusive=False)
            series["monitored"] = True
            series = self.client.update_series(series)
            if not search:
                return {
                    "action": "already_added",
                    "already_existed": True,
                    "series": _series_summary(series, series),
                }
            searched = self._search_series_scope(series, requested)
            return {
                "action": "search_started",
                "already_existed": True,
                "series": _series_summary(series, series),
                "searched_seasons": searched,
            }

        root = _select_root_folder(
            self.client.root_folders(), library, media_env("SONARR_ROOT_FOLDER")
        )
        profile = _select_quality_profile(
            self.client.quality_profiles(),
            library,
            quality or media_env("SONARR_QUALITY_PROFILE"),
        )
        series = deepcopy(candidate)
        series["monitored"] = True
        _set_season_monitoring(series, requested, exclusive=bool(requested))
        series.update({
            "qualityProfileId": profile["id"],
            "rootFolderPath": root["path"],
            "seasonFolder": True,
            "addOptions": {
                "searchForMissingEpisodes": search and not requested,
            },
        })
        added = self.client.add_series(series)
        searched = (
            self._search_series_scope(added, requested) if search and requested else []
        )
        return {
            "action": "added",
            "series": _series_summary(candidate, added),
            "search_started": search,
            "searched_seasons": searched,
            "quality_profile": profile.get("name"),
            "root_folder": root.get("path"),
        }

    def _search_series_scope(
        self, series: dict[str, Any], requested: list[int]
    ) -> list[int]:
        series_id = int(series["id"])
        if requested:
            for season in requested:
                self.client.search_season_now(series_id, season)
            return requested
        episode_ids = [
            int(item["id"])
            for item in self.client.episodes(series_id)
            if item.get("monitored")
            and not item.get("hasFile")
            and item.get("id") is not None
        ]
        if episode_ids:
            self.client.search_episodes_now(episode_ids)
        return []

    def download_episode(
        self,
        title: str,
        *,
        season: int,
        episode: int,
        year: int | None = None,
        quality: str | None = None,
    ) -> dict[str, Any]:
        if season < 0 or episode < 1:
            return {"action": "invalid_episode", "season": season, "episode": episode}
        candidates = self.client.lookup_series(_lookup_term(title, year))
        resolution = _resolve(candidates, title, "tvdbId", year=year)
        if len(resolution) != 1:
            if not resolution:
                return {"action": "not_found", "query": title}
            return {
                "action": "needs_selection",
                "choices": [_series_summary(item) for item in resolution],
            }
        candidate = resolution[0]
        library = self.client.series()
        series = next(
            (item for item in library if item.get("tvdbId") == candidate.get("tvdbId")),
            None,
        )
        series_was_added = series is None
        if series is None:
            added = self._add_resolved(
                candidate, seasons=[season], quality=quality, search=False
            )
            series_id = added.get("series", {}).get("sonarr_id")
            if not series_id:
                return added
            series = next(
                (item for item in self.client.series() if item.get("id") == series_id),
                {**candidate, "id": series_id},
            )
        episodes = self.client.episodes(int(series["id"]))
        target = next(
            (
                item
                for item in episodes
                if item.get("seasonNumber") == season
                and item.get("episodeNumber") == episode
            ),
            None,
        )
        if target is None:
            return {
                "action": "episode_not_found",
                "title": series.get("title"),
                "season": season,
                "episode": episode,
                "series_added": series_was_added,
            }
        if target.get("hasFile"):
            return {
                "action": "already_downloaded",
                "title": series.get("title"),
                "season": season,
                "episode": episode,
            }
        target = deepcopy(target)
        target["monitored"] = True
        self.client.update_episode(target)
        command = self.client.search_episodes_now([int(target["id"])])
        return {
            "action": "search_started",
            "title": series.get("title"),
            "season": season,
            "episode": episode,
            "episode_id": target["id"],
            "command_id": command.get("id"),
        }

    def series_status(
        self, title: str, *, year: int | None = None, season: int | None = None
    ) -> dict[str, Any]:
        matches = _resolve(self.client.series(), title, "tvdbId", year=year)
        if not matches:
            return {"state": "not_in_sonarr", "query": title}
        if len(matches) > 1:
            return {
                "state": "needs_selection",
                "choices": [_series_summary(item, item) for item in matches],
            }
        series = matches[0]
        episodes = self.client.episodes(int(series["id"]))
        if season is not None:
            known_seasons = {
                item.get("seasonNumber")
                for item in series.get("seasons", [])
                if isinstance(item, dict)
            }
            if season not in known_seasons and not any(
                item.get("seasonNumber") == season for item in episodes
            ):
                return {
                    **_series_summary(series, series),
                    "state": "season_not_found",
                    "season": season,
                }
            episodes = [item for item in episodes if item.get("seasonNumber") == season]
        monitored = [item for item in episodes if item.get("monitored", False)]
        available = [item for item in monitored if _episode_is_available(item)]
        downloaded = sum(bool(item.get("hasFile")) for item in available)
        missing = len(available) - downloaded
        queued = [
            item
            for item in self.client.queue()
            if item.get("seriesId") == series.get("id")
            and (
                season is None
                or item.get("seasonNumber") == season
                or item.get("episode", {}).get("seasonNumber") == season
            )
        ]
        if queued:
            state = _queue_state(queued[0])
        elif available and missing == 0:
            state = "downloaded"
        elif downloaded:
            state = "partially_downloaded"
        elif monitored:
            state = "monitored_waiting"
        else:
            state = "unmonitored"
        result = {
            **_series_summary(series, series),
            "state": state,
            "season": season,
            "downloaded_episodes": downloaded,
            "missing_episodes": missing,
            "monitored_episodes": len(monitored),
            "future_episodes": len(monitored) - len(available),
        }
        if queued:
            result["queue"] = [_queue_summary(item, "sonarr") for item in queued]
        return result

    def queue(self) -> list[dict[str, Any]]:
        return [_queue_summary(item, "sonarr") for item in self.client.queue()]


def media_env(name: str) -> str | None:
    """Profile-scoped credential/setting lookup (``agent.secret_scope.get_secret``).

    Single-profile deployments read ``os.environ``; the multiplexing gateway reads the
    active profile's ``.env`` and never another profile's value."""
    from agent.secret_scope import UnscopedSecretError, get_secret
    try:
        return get_secret(name)
    except UnscopedSecretError:
        return None


def _required_env(name: str) -> str:
    value = (media_env(name) or "").strip()
    if not value:
        raise ArrAPIError(f"{name} is not configured in the Hermes environment.")
    return value


def _lookup_term(title: str, year: int | None) -> str:
    _, parsed_year, newest = _query_parts(title, year)
    clean_title = _YEAR_RE.sub(" ", title)
    clean_title = re.sub(
        r"\b(newest|latest|most recent|version(?:\s+of)?)\b",
        " ",
        clean_title,
        flags=re.IGNORECASE,
    )
    clean_title = " ".join(clean_title.split())
    if newest and clean_title.lower().startswith("the "):
        clean_title = clean_title[4:]
    if parsed_year:
        return f"{clean_title} {parsed_year}"
    return clean_title


def _normalized(value: str) -> str:
    return " ".join(part for part in _WORD_RE.sub(" ", value.lower()).split() if part)


def _query_parts(query: str, year: int | None) -> tuple[str, int | None, bool]:
    newest = bool(re.search(r"\b(newest|latest|most recent)\b", query, re.IGNORECASE))
    parsed_year = year
    match = _YEAR_RE.search(query)
    if parsed_year is None and match:
        parsed_year = int(match.group(1))
    clean = _YEAR_RE.sub(" ", query)
    clean = re.sub(
        r"\b(newest|latest|most recent|version(?:\s+of)?)\b",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    normalized = _normalized(clean)
    if newest and normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized, parsed_year, newest


def _resolve(
    candidates: Iterable[dict[str, Any]],
    query: str,
    id_key: str,
    *,
    year: int | None = None,
) -> list[dict[str, Any]]:
    items = [
        item
        for item in candidates
        if item.get(id_key) is not None and item.get("title")
    ]
    query_title, query_year, newest = _query_parts(query, year)
    if query_year is not None:
        items = [item for item in items if item.get("year") == query_year]
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        title = _normalized(str(item.get("title", "")))
        score = SequenceMatcher(None, query_title, title).ratio()
        if query_title == title:
            score = 1.0
        elif query_title and (query_title in title or title in query_title):
            score = max(score, 0.9)
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("year") or 0), reverse=True)
    plausible = [pair for pair in scored if pair[0] >= 0.72]
    if not plausible:
        return []
    if newest:
        best_score = plausible[0][0]
        related = [item for score, item in plausible if score >= best_score - 0.12]
        return [max(related, key=lambda item: item.get("year") or 0)]
    exact = [item for score, item in plausible if score == 1.0]
    if len(exact) > 1:
        return exact[:5]
    if len(exact) == 1:
        return exact
    if len(plausible) == 1 or plausible[0][0] - plausible[1][0] >= 0.12:
        return [plausible[0][1]]
    return [item for _, item in plausible[:5]]


def _movie_summary(
    lookup: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = {
        "title": lookup.get("title"),
        "year": lookup.get("year"),
        "tmdb_id": lookup.get("tmdbId"),
        "overview": lookup.get("overview"),
        "existing": existing is not None,
    }
    if existing:
        result.update({
            "radarr_id": existing.get("id"),
            "monitored": existing.get("monitored"),
            "downloaded": bool(existing.get("hasFile")),
        })
    return result


def _series_summary(
    lookup: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = {
        "title": lookup.get("title"),
        "year": lookup.get("year"),
        "tvdb_id": lookup.get("tvdbId"),
        "overview": lookup.get("overview"),
        "seasons": sorted(
            int(season["seasonNumber"])
            for season in lookup.get("seasons", [])
            if isinstance(season, dict) and isinstance(season.get("seasonNumber"), int)
        ),
        "existing": existing is not None,
    }
    if existing:
        result.update({
            "sonarr_id": existing.get("id"),
            "monitored": existing.get("monitored"),
        })
    return result


def _select_root_folder(
    roots: list[dict[str, Any]], library: list[dict[str, Any]], preferred: str | None
) -> dict[str, Any]:
    if not roots:
        raise ArrAPIError("No root folders are configured in the media service.")
    if preferred:
        match = next(
            (
                item
                for item in roots
                if str(item.get("path", "")).rstrip("/").lower()
                == preferred.rstrip("/").lower()
            ),
            None,
        )
        if match is None:
            raise ArrAPIError(f"Configured root folder '{preferred}' was not found.")
        return match
    usage = Counter(
        item.get("rootFolderPath") for item in library if item.get("rootFolderPath")
    )
    if usage:
        most_used, _ = usage.most_common(1)[0]
        match = next((item for item in roots if item.get("path") == most_used), None)
        if match:
            return match
    return roots[0]


def _quality_aliases(name: str) -> set[str]:
    normalized = _normalized(name)
    compact = normalized.replace(" ", "")
    aliases = {normalized}
    if (
        "4k" in normalized
        or "2160" in normalized
        or "uhd" in compact
        or "ultrahd" in compact
    ):
        aliases.update({"4k", "2160p", "uhd"})
    if "1080" in normalized:
        aliases.update({"1080p", "full hd"})
    return aliases


def _select_quality_profile(
    profiles: list[dict[str, Any]], library: list[dict[str, Any]], preferred: str | None
) -> dict[str, Any]:
    if not profiles:
        raise ArrAPIError("No quality profiles are configured in the media service.")
    if preferred:
        wanted = _normalized(preferred)
        matches = [
            item
            for item in profiles
            if wanted in _quality_aliases(str(item.get("name", "")))
            or wanted == _normalized(str(item.get("name", "")))
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            matches = [
                item
                for item in profiles
                if wanted in _normalized(str(item.get("name", "")))
            ]
        if len(matches) != 1:
            available = ", ".join(str(item.get("name")) for item in profiles)
            raise ArrAPIError(
                f"Quality profile '{preferred}' did not match exactly one profile. Available: {available}."
            )
        return matches[0]
    usage = Counter(
        item.get("qualityProfileId") for item in library if item.get("qualityProfileId")
    )
    if usage:
        most_used, _ = usage.most_common(1)[0]
        match = next((item for item in profiles if item.get("id") == most_used), None)
        if match:
            return match
    return profiles[0]


def _set_season_monitoring(
    series: dict[str, Any], requested: list[int], *, exclusive: bool
) -> None:
    seasons = series.get("seasons", [])
    available = {item.get("seasonNumber") for item in seasons if isinstance(item, dict)}
    missing = [season for season in requested if season not in available]
    if missing:
        missing_text = ", ".join(str(season) for season in missing)
        raise ArrAPIError(f"Requested season(s) not found: {missing_text}.")
    for item in seasons:
        number = item.get("seasonNumber")
        if number == 0:
            item["monitored"] = False
        elif exclusive:
            item["monitored"] = number in requested
        else:
            item["monitored"] = True


def _queue_state(item: dict[str, Any]) -> str:
    tracked = str(item.get("trackedDownloadStatus", "")).lower()
    tracked_state = str(item.get("trackedDownloadState", "")).lower()
    status = str(item.get("status", "queued")).lower()
    if tracked in {"warning", "error"} or status in {"failed", "warning"}:
        return "failed_or_stalled"
    if tracked_state == "failedpending":
        return "failed_or_stalled"
    if tracked_state == "importpending":
        return "import_pending"
    if tracked_state == "importing":
        return "importing"
    if tracked_state == "imported":
        return "imported"
    if status in {"downloading", "paused"}:
        return "downloading"
    if status in {"completed", "importpending"}:
        return "import_pending"
    if status in {"delay", "downloadclientunavailable"}:
        return "stalled"
    return "queued"


def _episode_is_available(episode: dict[str, Any]) -> bool:
    """Exclude explicitly future episodes from missing counts."""
    if episode.get("hasAired") is False:
        return False
    air_date = episode.get("airDateUtc")
    if not isinstance(air_date, str) or not air_date:
        return True
    try:
        return datetime.fromisoformat(air_date) <= datetime.now(UTC)  # 3.11+ parses "Z"
    except ValueError:
        return True


def _queue_summary(item: dict[str, Any], source: str) -> dict[str, Any]:
    size = item.get("size") or 0
    size_left = item.get("sizeleft") or item.get("sizeLeft") or 0
    progress = None
    if (
        isinstance(size, (int, float))
        and size > 0
        and isinstance(size_left, (int, float))
    ):
        progress = round(max(0.0, min(100.0, (size - size_left) / size * 100)), 1)
    title = item.get("title")
    if source == "radarr" and isinstance(item.get("movie"), dict):
        title = item["movie"].get("title") or title
    if source == "sonarr" and isinstance(item.get("series"), dict):
        title = item["series"].get("title") or title
    return {
        "source": source,
        "title": title,
        "state": _queue_state(item),
        "progress_percent": progress,
        "time_left": item.get("timeleft") or item.get("timeLeft"),
        "status_messages": item.get("statusMessages", []),
        "movie_id": item.get("movieId"),
        "series_id": item.get("seriesId"),
        "episode_id": item.get("episodeId"),
    }
