"""Unit tests for the Radarr/Sonarr HTTP clients and media services."""

from __future__ import annotations

import json

import httpx
import pytest

from tools.media_clients import (
    ArrAPIError,
    RadarrClient,
    RadarrService,
    SonarrClient,
    SonarrService,
    _lookup_term,
    _queue_state,
    _select_quality_profile,
)


RADARR_LOOKUP = [
    {
        "title": "Dune",
        "year": 2021,
        "tmdbId": 438631,
        "titleSlug": "dune-438631",
        "overview": "Paul Atreides travels to Arrakis.",
        "images": [],
        "minimumAvailability": "released",
    },
    {
        "title": "Dune",
        "year": 1984,
        "tmdbId": 841,
        "titleSlug": "dune-841",
        "overview": "The 1984 adaptation.",
        "images": [],
        "minimumAvailability": "released",
    },
]

SONARR_LOOKUP = [
    {
        "title": "Severance",
        "year": 2022,
        "tvdbId": 371980,
        "titleSlug": "severance",
        "overview": "Employees split their work and home memories.",
        "images": [],
        "seasons": [
            {"seasonNumber": 1, "monitored": False},
            {"seasonNumber": 2, "monitored": False},
        ],
        "seriesType": "standard",
    }
]


def _json_response(
    request: httpx.Request, payload, status_code: int = 200
) -> httpx.Response:
    return httpx.Response(status_code, request=request, json=payload)


def test_movie_lookup_marks_existing_movie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "secret"
        if request.url.path.endswith("/movie/lookup"):
            assert request.url.params["term"] == "Dune"
            return _json_response(request, RADARR_LOOKUP)
        if request.url.path.endswith("/movie"):
            return _json_response(
                request,
                [{"id": 12, "tmdbId": 438631, "title": "Dune", "hasFile": False}],
            )
        raise AssertionError(request.url)

    client = RadarrClient(
        "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
    )
    result = RadarrService(client).search_movie("Dune")

    assert result["ambiguous"] is True
    assert result["results"][0]["existing"] is True
    assert result["results"][0]["radarr_id"] == 12
    assert result["results"][1]["existing"] is False


def test_movie_add_uses_existing_root_profile_and_search_option() -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/movie/lookup"):
            return _json_response(request, [RADARR_LOOKUP[0]])
        if request.url.path.endswith("/movie") and request.method == "GET":
            return _json_response(request, [])
        if request.url.path.endswith("/rootfolder"):
            return _json_response(request, [{"id": 1, "path": "/movies"}])
        if request.url.path.endswith("/qualityprofile"):
            return _json_response(request, [{"id": 7, "name": "HD-1080p"}])
        if request.url.path.endswith("/movie") and request.method == "POST":
            return _json_response(request, {**body, "id": 99}, 201)
        raise AssertionError(request.url)

    service = RadarrService(
        RadarrClient(
            "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_movie("Dune", year=2021)

    assert result["action"] == "added"
    payload = next(
        body
        for method, path, body in requests
        if method == "POST" and path.endswith("/movie")
    )
    assert payload["tmdbId"] == 438631
    assert payload["rootFolderPath"] == "/movies"
    assert payload["qualityProfileId"] == 7
    assert payload["monitored"] is True
    assert payload["addOptions"]["searchForMovie"] is True


def test_duplicate_movie_triggers_search_instead_of_adding() -> None:
    posts: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/lookup"):
            return _json_response(request, [RADARR_LOOKUP[0]])
        if request.url.path.endswith("/movie") and request.method == "GET":
            return _json_response(
                request,
                [
                    {
                        "id": 12,
                        "tmdbId": 438631,
                        "title": "Dune",
                        "year": 2021,
                        "hasFile": False,
                    }
                ],
            )
        if request.url.path.endswith("/command"):
            body = json.loads(request.content)
            posts.append((request.url.path, body))
            return _json_response(request, {"id": 55, "status": "queued"}, 201)
        raise AssertionError(request.url)

    service = RadarrService(
        RadarrClient(
            "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_movie("Dune", year=2021)

    assert result["action"] == "search_started"
    assert result["already_existed"] is True
    assert posts == [("/api/v3/command", {"name": "MoviesSearch", "movieIds": [12]})]


def test_existing_downloaded_movie_does_not_search_or_duplicate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/lookup"):
            return _json_response(request, [RADARR_LOOKUP[0]])
        if request.url.path.endswith("/movie"):
            return _json_response(
                request,
                [
                    {
                        "id": 12,
                        "tmdbId": 438631,
                        "title": "Dune",
                        "year": 2021,
                        "hasFile": True,
                    }
                ],
            )
        raise AssertionError(f"Unexpected write: {request.method} {request.url}")

    service = RadarrService(
        RadarrClient(
            "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_movie("Dune", year=2021)

    assert result["action"] == "already_downloaded"


def test_explicit_movie_search_posts_movies_search_command() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/command"):
            captured.update(json.loads(request.content))
            return _json_response(request, {"id": 3}, 201)
        raise AssertionError(request.url)

    client = RadarrClient(
        "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
    )
    result = client.search_movie_now(42)

    assert captured == {"name": "MoviesSearch", "movieIds": [42]}
    assert result["id"] == 3


def test_ambiguous_movie_download_returns_choices_without_writes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/lookup"):
            return _json_response(request, RADARR_LOOKUP)
        if request.url.path.endswith("/movie"):
            return _json_response(request, [])
        raise AssertionError(f"Unexpected write: {request.method} {request.url}")

    service = RadarrService(
        RadarrClient(
            "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_movie("Dune")

    assert result["action"] == "needs_selection"
    assert [(item["title"], item["year"]) for item in result["choices"]] == [
        ("Dune", 2021),
        ("Dune", 1984),
    ]


def test_series_lookup_returns_seasons_and_existing_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series"):
            return _json_response(
                request, [{"id": 21, "tvdbId": 371980, "title": "Severance"}]
            )
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.search_series("Severance")

    assert result["results"][0]["tvdb_id"] == 371980
    assert result["results"][0]["seasons"] == [1, 2]
    assert result["results"][0]["sonarr_id"] == 21


def test_add_all_series_builds_expected_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series") and request.method == "GET":
            return _json_response(request, [])
        if request.url.path.endswith("/rootfolder"):
            return _json_response(request, [{"id": 1, "path": "/tv"}])
        if request.url.path.endswith("/qualityprofile"):
            return _json_response(request, [{"id": 4, "name": "HD-1080p"}])
        if request.url.path.endswith("/series") and request.method == "POST":
            captured.update(json.loads(request.content))
            return _json_response(request, {**captured, "id": 88}, 201)
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_series("Severance")

    assert result["action"] == "added"
    assert captured["rootFolderPath"] == "/tv"
    assert captured["qualityProfileId"] == 4
    assert all(
        season["monitored"]
        for season in captured["seasons"]
        if season["seasonNumber"] > 0
    )
    assert captured["addOptions"]["searchForMissingEpisodes"] is True


def test_season_specific_add_monitors_only_requested_season_and_searches() -> None:
    captured: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series") and request.method == "GET":
            return _json_response(request, [])
        if request.url.path.endswith("/rootfolder"):
            return _json_response(request, [{"id": 1, "path": "/tv"}])
        if request.url.path.endswith("/qualityprofile"):
            return _json_response(request, [{"id": 4, "name": "HD-1080p"}])
        if request.url.path.endswith("/series") and request.method == "POST":
            captured["series"] = json.loads(request.content)
            return _json_response(request, {**captured["series"], "id": 88}, 201)
        if request.url.path.endswith("/command"):
            captured["command"] = json.loads(request.content)
            return _json_response(request, {"id": 9}, 201)
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_series("Severance", seasons=[2])

    monitored = {
        s["seasonNumber"]: s["monitored"] for s in captured["series"]["seasons"]
    }
    assert monitored == {1: False, 2: True}
    assert captured["series"]["addOptions"]["searchForMissingEpisodes"] is False
    assert captured["command"] == {
        "name": "SeasonSearch",
        "seriesId": 88,
        "seasonNumber": 2,
    }
    assert result["searched_seasons"] == [2]


def test_existing_series_season_monitoring_is_updated_before_search() -> None:
    captured: list[tuple[str, str, dict]] = []
    existing = {
        **SONARR_LOOKUP[0],
        "id": 21,
        "seasons": [
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": False},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series") and request.method == "GET":
            return _json_response(request, [existing])
        if request.url.path.endswith("/series/21") and request.method == "PUT":
            body = json.loads(request.content)
            captured.append((request.method, request.url.path, body))
            return _json_response(request, body)
        if request.url.path.endswith("/command"):
            body = json.loads(request.content)
            captured.append((request.method, request.url.path, body))
            return _json_response(request, {"id": 1}, 201)
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_series("Severance", seasons=[2])

    updated = captured[0][2]
    assert {s["seasonNumber"]: s["monitored"] for s in updated["seasons"]} == {
        1: False,
        2: True,
    }
    assert captured[1][2] == {"name": "SeasonSearch", "seriesId": 21, "seasonNumber": 2}
    assert result["already_existed"] is True


def test_episode_download_monitors_episode_and_triggers_episode_search() -> None:
    captured: list[tuple[str, dict]] = []
    existing = {**SONARR_LOOKUP[0], "id": 21}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series") and request.method == "GET":
            return _json_response(request, [existing])
        if request.url.path.endswith("/episode"):
            return _json_response(
                request,
                [
                    {
                        "id": 204,
                        "seriesId": 21,
                        "seasonNumber": 2,
                        "episodeNumber": 4,
                        "monitored": False,
                        "hasFile": False,
                    }
                ],
            )
        if request.url.path.endswith("/episode/204"):
            body = json.loads(request.content)
            captured.append(("episode", body))
            return _json_response(request, body)
        if request.url.path.endswith("/command"):
            body = json.loads(request.content)
            captured.append(("command", body))
            return _json_response(request, {"id": 8}, 201)
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    result = service.download_episode("Severance", season=2, episode=4)

    assert captured[0][1]["monitored"] is True
    assert captured[1][1] == {"name": "EpisodeSearch", "episodeIds": [204]}
    assert result["action"] == "search_started"


def test_series_status_counts_missing_and_downloaded_episodes() -> None:
    existing = {**SONARR_LOOKUP[0], "id": 21}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series"):
            return _json_response(request, [existing])
        if request.url.path.endswith("/episode"):
            return _json_response(
                request,
                [
                    {
                        "id": 1,
                        "seasonNumber": 2,
                        "episodeNumber": 1,
                        "monitored": True,
                        "hasFile": True,
                    },
                    {
                        "id": 2,
                        "seasonNumber": 2,
                        "episodeNumber": 2,
                        "monitored": True,
                        "hasFile": False,
                    },
                    {
                        "id": 3,
                        "seasonNumber": 1,
                        "episodeNumber": 1,
                        "monitored": True,
                        "hasFile": False,
                    },
                ],
            )
        if request.url.path.endswith("/queue"):
            return _json_response(request, {"records": []})
        raise AssertionError(request.url)

    service = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    )
    status = service.series_status("Severance", season=2)

    assert status["state"] == "partially_downloaded"
    assert status["downloaded_episodes"] == 1
    assert status["missing_episodes"] == 1


def test_api_failure_is_human_readable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed", request=request)

    client = RadarrClient(
        "http://r1.local:7878", "secret", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ArrAPIError, match="couldn't reach Radarr"):
        client.system_status()


def test_authentication_failure_is_human_readable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"message": "Unauthorized"}, 401)

    client = SonarrClient(
        "http://r1.local:8989", "bad", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ArrAPIError, match="rejected the configured API key"):
        client.system_status()


def test_malformed_api_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"not-json")

    client = RadarrClient(
        "http://r1.local:7878", "secret", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ArrAPIError, match="unexpected response"):
        client.system_status()


def test_4k_alias_matches_ultra_hd_profile_name() -> None:
    profiles = [
        {"id": 1, "name": "HD-1080p"},
        {"id": 2, "name": "Ultra-HD"},
    ]

    assert _select_quality_profile(profiles, [], "4K")["id"] == 2


def test_lookup_term_removes_conversational_qualifiers_without_breaking_title_words() -> (
    None
):
    assert _lookup_term("the newest Mission Impossible", None) == "Mission Impossible"
    assert _lookup_term("1999 version of The Mummy", None) == "The Mummy 1999"
    assert _lookup_term("That '70s Show", None) == "That '70s Show"


def test_queue_uses_service_specific_flags_and_paginates() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        page = int(request.url.params["page"])
        return _json_response(
            request,
            {
                "page": page,
                "pageSize": 1,
                "totalRecords": 2,
                "records": [{"id": page}],
            },
        )

    client = SonarrClient(
        "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
    )

    assert client.queue(page_size=1) == [{"id": 1}, {"id": 2}]
    assert [params["page"] for params in seen] == ["1", "2"]
    assert all(params["includeEpisode"] == "true" for params in seen)
    assert all(params["includeUnknownSeriesItems"] == "true" for params in seen)


def test_existing_series_without_scope_monitors_all_seasons_before_search() -> None:
    updates: list[dict] = []
    commands: list[dict] = []
    existing = {
        "id": 21,
        "tvdbId": 371980,
        "title": "Severance",
        "year": 2022,
        "monitored": False,
        "seasons": [
            {"seasonNumber": 0, "monitored": True},
            {"seasonNumber": 1, "monitored": False},
            {"seasonNumber": 2, "monitored": False},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/lookup"):
            return _json_response(request, SONARR_LOOKUP)
        if request.url.path.endswith("/series") and request.method == "GET":
            return _json_response(request, [existing])
        if request.url.path.endswith("/series/21") and request.method == "PUT":
            updates.append(json.loads(request.content))
            return _json_response(request, updates[-1])
        if request.url.path.endswith("/episode"):
            return _json_response(
                request,
                [{"id": 11, "monitored": True, "hasFile": False}],
            )
        if request.url.path.endswith("/command"):
            commands.append(json.loads(request.content))
            return _json_response(request, {"id": 99}, 201)
        raise AssertionError(request.url)

    result = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    ).download_series("Severance")

    assert updates[0]["monitored"] is True
    assert {
        item["seasonNumber"]: item["monitored"] for item in updates[0]["seasons"]
    } == {
        0: False,
        1: True,
        2: True,
    }
    assert commands == [{"name": "EpisodeSearch", "episodeIds": [11]}]
    assert result["action"] == "search_started"


def test_series_status_uses_top_level_queue_season_and_excludes_future_episodes() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series"):
            return _json_response(
                request,
                [
                    {
                        "id": 21,
                        "tvdbId": 371980,
                        "title": "Severance",
                        "year": 2022,
                        "monitored": True,
                        "seasons": [{"seasonNumber": 2, "monitored": True}],
                    }
                ],
            )
        if request.url.path.endswith("/episode"):
            return _json_response(
                request,
                [
                    {
                        "id": 1,
                        "seasonNumber": 2,
                        "monitored": True,
                        "hasFile": False,
                        "hasAired": True,
                    },
                    {
                        "id": 2,
                        "seasonNumber": 2,
                        "monitored": True,
                        "hasFile": False,
                        "hasAired": False,
                    },
                ],
            )
        if request.url.path.endswith("/queue"):
            return _json_response(
                request,
                {
                    "totalRecords": 1,
                    "records": [
                        {
                            "id": 9,
                            "seriesId": 21,
                            "seasonNumber": 2,
                            "status": "downloading",
                        }
                    ],
                },
            )
        raise AssertionError(request.url)

    status = SonarrService(
        SonarrClient(
            "http://sonarr.test:8989", "secret", transport=httpx.MockTransport(handler)
        )
    ).series_status("Severance", season=2)

    assert status["state"] == "downloading"
    assert status["missing_episodes"] == 1
    assert status["future_episodes"] == 1


def test_queue_state_uses_tracked_download_state() -> None:
    assert (
        _queue_state({"status": "completed", "trackedDownloadState": "importPending"})
        == "import_pending"
    )
    assert (
        _queue_state({"status": "completed", "trackedDownloadState": "importing"})
        == "importing"
    )
    assert (
        _queue_state({"status": "completed", "trackedDownloadState": "failedPending"})
        == "failed_or_stalled"
    )


def test_existing_unmonitored_movie_is_monitored_before_search() -> None:
    writes: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/lookup"):
            return _json_response(request, [RADARR_LOOKUP[0]])
        if request.url.path.endswith("/movie") and request.method == "GET":
            return _json_response(
                request,
                [
                    {
                        "id": 12,
                        "tmdbId": 438631,
                        "title": "Dune",
                        "year": 2021,
                        "monitored": False,
                        "hasFile": False,
                    }
                ],
            )
        if request.url.path.endswith("/movie/12") and request.method == "PUT":
            payload = json.loads(request.content)
            writes.append(("movie", payload))
            return _json_response(request, payload)
        if request.url.path.endswith("/command"):
            payload = json.loads(request.content)
            writes.append(("command", payload))
            return _json_response(request, {"id": 55}, 201)
        raise AssertionError(request.url)

    result = RadarrService(
        RadarrClient(
            "http://radarr.test:7878", "secret", transport=httpx.MockTransport(handler)
        )
    ).download_movie("Dune", year=2021)

    assert writes[0][0] == "movie"
    assert writes[0][1]["monitored"] is True
    assert writes[1] == ("command", {"name": "MoviesSearch", "movieIds": [12]})
    assert result["action"] == "search_started"
