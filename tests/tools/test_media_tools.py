"""Tests for the high-level Radarr/Sonarr LLM tool surface."""

from __future__ import annotations

import json

from tools import media_tools
from tools.media_clients import ArrAPIError
from tools.registry import registry


def test_media_tool_availability_is_environment_gated(monkeypatch) -> None:
    for name in ("RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert media_tools._radarr_available() is False
    assert media_tools._sonarr_available() is False
    assert media_tools._media_available() is False

    monkeypatch.setenv("RADARR_URL", "http://radarr.test")
    monkeypatch.setenv("RADARR_API_KEY", "secret")
    assert media_tools._radarr_available() is True
    assert media_tools._media_available() is True


def test_media_tools_are_registered_as_high_level_operations() -> None:
    names = set(registry.get_tool_names_for_toolset("media"))

    assert names == {
        "media_search_movie",
        "media_download_movie",
        "media_search_movie_now",
        "media_movie_status",
        "media_search_series",
        "media_download_series",
        "media_download_episode",
        "media_series_status",
        "media_download_queue",
        "media_health",
    }
    assert all("http" not in name and "delete" not in name for name in names)


def test_movie_handler_passes_safe_high_level_arguments(monkeypatch) -> None:
    captured = {}

    class StubService:
        def download_movie(self, title, **kwargs):
            captured.update({"title": title, **kwargs})
            return {"action": "added"}

    monkeypatch.setattr(media_tools, "_with_radarr", lambda call: call(StubService()))
    payload = json.loads(
        media_tools._handle_download_movie({
            "title": "Dune",
            "year": 2021,
            "quality": "4K",
            "search_now": False,
        })
    )

    assert payload["result"]["action"] == "added"
    assert captured == {
        "title": "Dune",
        "year": 2021,
        "tmdb_id": None,
        "quality": "4K",
        "search": False,
    }


def test_tool_translates_api_error_without_traceback(monkeypatch) -> None:
    def fail(_call):
        raise ArrAPIError("I couldn't reach Radarr at http://r1.local:7878.")

    monkeypatch.setattr(media_tools, "_with_radarr", fail)
    payload = json.loads(media_tools._handle_movie_status({"title": "Dune"}))

    assert payload == {"error": "I couldn't reach Radarr at http://r1.local:7878."}


def test_combined_queue_keeps_other_service_when_one_is_offline(monkeypatch) -> None:
    monkeypatch.setattr(media_tools, "_radarr_available", lambda: True)
    monkeypatch.setattr(media_tools, "_sonarr_available", lambda: True)

    def radarr_fail(_call):
        raise ArrAPIError("I couldn't reach Radarr at http://r1.local:7878.")

    monkeypatch.setattr(media_tools, "_with_radarr", radarr_fail)
    monkeypatch.setattr(
        media_tools,
        "_with_sonarr",
        lambda call: call(
            type("Stub", (), {"queue": lambda self: [{"title": "Severance"}]})()
        ),
    )

    payload = json.loads(media_tools._handle_queue({}))["result"]

    assert payload["count"] == 1
    assert payload["items"] == [{"title": "Severance"}]
    assert payload["errors"] == ["I couldn't reach Radarr at http://r1.local:7878."]
