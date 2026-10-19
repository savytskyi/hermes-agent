"""High-level LLM tools for safe Radarr and Sonarr media automation.

Only search/add/monitor/status/queue operations are exposed.  There is no raw
HTTP tool and no destructive or download-client/filesystem operation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from tools.media_clients import (
    media_env,
    ArrAPIError,
    RadarrClient,
    RadarrService,
    SonarrClient,
    SonarrService,
)
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


def _radarr_available() -> bool:
    return bool(media_env("RADARR_URL") and media_env("RADARR_API_KEY"))


def _sonarr_available() -> bool:
    return bool(media_env("SONARR_URL") and media_env("SONARR_API_KEY"))


def _media_available() -> bool:
    return _radarr_available() or _sonarr_available()


def _with_radarr(call: Callable[[RadarrService], Any]) -> Any:
    client = RadarrClient.from_env()
    try:
        return call(RadarrService(client))
    finally:
        client.close()


def _with_sonarr(call: Callable[[SonarrService], Any]) -> Any:
    client = SonarrClient.from_env()
    try:
        return call(SonarrService(client))
    finally:
        client.close()


def _result(call: Callable[[], Any], operation: str) -> str:
    try:
        return json.dumps({"result": call()}, default=str)
    except ArrAPIError as exc:
        return tool_error(str(exc))
    except Exception:
        logger.exception("Unexpected media tool failure during %s", operation)
        return tool_error(f"The media request failed unexpectedly while {operation}.")


def _optional_year(args: dict[str, Any]) -> int | None:
    value = args.get("year")
    return int(value) if value is not None else None


def _seasons(args: dict[str, Any]) -> list[int] | None:
    values = args.get("seasons")
    return [int(value) for value in values] if values else None


def _handle_search_movie(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_radarr(
            lambda service: service.search_movie(
                args["title"], year=_optional_year(args)
            )
        ),
        "searching Radarr",
    )


def _handle_download_movie(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_radarr(
            lambda service: service.download_movie(
                args["title"],
                year=_optional_year(args),
                tmdb_id=args.get("tmdb_id"),
                quality=args.get("quality"),
                search=args.get("search_now", True),
            )
        ),
        "adding a movie to Radarr",
    )


def _handle_search_movie_now(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_radarr(
            lambda service: service.search_existing_movie(
                args["title"], year=_optional_year(args)
            )
        ),
        "starting a Radarr movie search",
    )


def _handle_movie_status(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_radarr(
            lambda service: service.movie_status(
                args["title"], year=_optional_year(args)
            )
        ),
        "checking movie status",
    )


def _handle_search_series(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_sonarr(
            lambda service: service.search_series(
                args["title"], year=_optional_year(args)
            )
        ),
        "searching Sonarr",
    )


def _handle_download_series(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_sonarr(
            lambda service: service.download_series(
                args["title"],
                seasons=_seasons(args),
                year=_optional_year(args),
                tvdb_id=args.get("tvdb_id"),
                quality=args.get("quality"),
                search=args.get("search_now", True),
            )
        ),
        "adding a series to Sonarr",
    )


def _handle_download_episode(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_sonarr(
            lambda service: service.download_episode(
                args["title"],
                season=int(args["season"]),
                episode=int(args["episode"]),
                year=_optional_year(args),
                quality=args.get("quality"),
            )
        ),
        "starting a Sonarr episode search",
    )


def _handle_series_status(args: dict[str, Any], **_: Any) -> str:
    return _result(
        lambda: _with_sonarr(
            lambda service: service.series_status(
                args["title"],
                year=_optional_year(args),
                season=(
                    int(args["season"]) if args.get("season") is not None else None
                ),
            )
        ),
        "checking series status",
    )


def _handle_queue(_: dict[str, Any], **__: Any) -> str:
    def load() -> dict[str, Any]:
        result: dict[str, Any] = {"items": [], "errors": []}
        if _radarr_available():
            try:
                result["items"].extend(_with_radarr(lambda service: service.queue()))
            except ArrAPIError as exc:
                result["errors"].append(str(exc))
        if _sonarr_available():
            try:
                result["items"].extend(_with_sonarr(lambda service: service.queue()))
            except ArrAPIError as exc:
                result["errors"].append(str(exc))
        result["count"] = len(result["items"])
        return result

    return _result(load, "checking the download queues")


def _handle_health(_: dict[str, Any], **__: Any) -> str:
    def check() -> dict[str, Any]:
        services: dict[str, Any] = {}
        for name, available, factory in (
            ("radarr", _radarr_available(), RadarrClient.from_env),
            ("sonarr", _sonarr_available(), SonarrClient.from_env),
        ):
            if not available:
                services[name] = {"configured": False}
                continue
            try:
                client = factory()
                try:
                    status = client.system_status()
                finally:
                    client.close()
                services[name] = {
                    "configured": True,
                    "reachable": True,
                    "version": status.get("version"),
                    "app_name": status.get("appName"),
                }
            except ArrAPIError as exc:
                services[name] = {
                    "configured": True,
                    "reachable": False,
                    "error": str(exc),
                }
        return {"services": services}

    return _result(check, "checking media service health")


_TITLE = {
    "type": "string",
    "description": "Natural-language movie or series title. Include qualifiers such as a year or 'newest' when the user supplied them.",
}
_YEAR = {
    "type": "integer",
    "minimum": 1900,
    "maximum": 2200,
    "description": "Release/premiere year when the user specified one; strongly constrains matching.",
}
_QUALITY = {
    "type": "string",
    "description": "Optional existing quality-profile name or clear alias such as '4K' or '1080p'. Never invent an ID.",
}
_SEARCH_NOW = {
    "type": "boolean",
    "default": True,
    "description": "Start searching immediately. Set false only when the user explicitly wants to add/monitor without downloading yet.",
}


registry.register(
    name="media_search_movie",
    toolset="media",
    schema={
        "name": "media_search_movie",
        "description": "Search Radarr's movie metadata and report candidates plus existing-library state. Use before asking the user to choose between remakes/reboots.",
        "parameters": {
            "type": "object",
            "properties": {"title": _TITLE, "year": _YEAR},
            "required": ["title"],
        },
    },
    handler=_handle_search_movie,
    check_fn=_radarr_available,
    requires_env=["RADARR_URL", "RADARR_API_KEY"],
    emoji="🎬",
)

registry.register(
    name="media_download_movie",
    toolset="media",
    schema={
        "name": "media_download_movie",
        "description": "Safely add/download a movie through Radarr. Checks for duplicates, uses Radarr's existing root folder and quality profiles, and returns choices instead of guessing when ambiguous.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": _TITLE,
                "year": _YEAR,
                "tmdb_id": {
                    "type": "integer",
                    "description": "TMDB ID selected from a previous search result.",
                },
                "quality": _QUALITY,
                "search_now": _SEARCH_NOW,
            },
            "required": ["title"],
        },
    },
    handler=_handle_download_movie,
    check_fn=_radarr_available,
    requires_env=["RADARR_URL", "RADARR_API_KEY"],
    emoji="🎬",
)

registry.register(
    name="media_search_movie_now",
    toolset="media",
    schema={
        "name": "media_search_movie_now",
        "description": "Trigger a Radarr search now for a movie that is already in Radarr but missing its file.",
        "parameters": {
            "type": "object",
            "properties": {"title": _TITLE, "year": _YEAR},
            "required": ["title"],
        },
    },
    handler=_handle_search_movie_now,
    check_fn=_radarr_available,
    requires_env=["RADARR_URL", "RADARR_API_KEY"],
    emoji="🔎",
)

registry.register(
    name="media_movie_status",
    toolset="media",
    schema={
        "name": "media_movie_status",
        "description": "Check whether a movie is in Radarr, downloaded, waiting, queued, downloading, imported, failed, or stalled, including progress when available.",
        "parameters": {
            "type": "object",
            "properties": {"title": _TITLE, "year": _YEAR},
            "required": ["title"],
        },
    },
    handler=_handle_movie_status,
    check_fn=_radarr_available,
    requires_env=["RADARR_URL", "RADARR_API_KEY"],
    emoji="🎬",
)

registry.register(
    name="media_search_series",
    toolset="media",
    schema={
        "name": "media_search_series",
        "description": "Search Sonarr's series metadata and report candidate years, TVDB IDs, seasons, and existing-library state.",
        "parameters": {
            "type": "object",
            "properties": {"title": _TITLE, "year": _YEAR},
            "required": ["title"],
        },
    },
    handler=_handle_search_series,
    check_fn=_sonarr_available,
    requires_env=["SONARR_URL", "SONARR_API_KEY"],
    emoji="📺",
)

registry.register(
    name="media_download_series",
    toolset="media",
    schema={
        "name": "media_download_series",
        "description": "Safely add/download a TV series or selected seasons through Sonarr. For an explicit season-only request, pass only those season numbers; other seasons will not be monitored.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": _TITLE,
                "year": _YEAR,
                "tvdb_id": {
                    "type": "integer",
                    "description": "TVDB ID selected from a previous search result.",
                },
                "seasons": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "description": "Exact requested season numbers. Omit for all regular seasons.",
                },
                "quality": _QUALITY,
                "search_now": _SEARCH_NOW,
            },
            "required": ["title"],
        },
    },
    handler=_handle_download_series,
    check_fn=_sonarr_available,
    requires_env=["SONARR_URL", "SONARR_API_KEY"],
    emoji="📺",
)

registry.register(
    name="media_download_episode",
    toolset="media",
    schema={
        "name": "media_download_episode",
        "description": "Monitor and search for one exact Sonarr episode, such as S02E04, adding the series first when necessary.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": _TITLE,
                "year": _YEAR,
                "season": {"type": "integer", "minimum": 0},
                "episode": {"type": "integer", "minimum": 1},
                "quality": _QUALITY,
            },
            "required": ["title", "season", "episode"],
        },
    },
    handler=_handle_download_episode,
    check_fn=_sonarr_available,
    requires_env=["SONARR_URL", "SONARR_API_KEY"],
    emoji="📺",
)

registry.register(
    name="media_series_status",
    toolset="media",
    schema={
        "name": "media_series_status",
        "description": "Check Sonarr series or season status, including downloaded/missing episode counts and active queue progress.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": _TITLE,
                "year": _YEAR,
                "season": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Optional season to inspect.",
                },
            },
            "required": ["title"],
        },
    },
    handler=_handle_series_status,
    check_fn=_sonarr_available,
    requires_env=["SONARR_URL", "SONARR_API_KEY"],
    emoji="📺",
)

registry.register(
    name="media_download_queue",
    toolset="media",
    schema={
        "name": "media_download_queue",
        "description": "List active Radarr and Sonarr queue items with state, progress, time remaining, and failure/stall messages.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    handler=_handle_queue,
    check_fn=_media_available,
    emoji="⬇️",
)

registry.register(
    name="media_health",
    toolset="media",
    schema={
        "name": "media_health",
        "description": "Read-only health check for configured Radarr and Sonarr services using /api/v3/system/status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    handler=_handle_health,
    check_fn=_media_available,
    emoji="🩺",
)
