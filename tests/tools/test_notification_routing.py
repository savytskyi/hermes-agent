import pytest

from cron.scheduler import _apply_notification_channel_directive
from cron.scheduler_delivery import _resolve_single_delivery_target
from tools.notification_routing import (
    NotificationRoutingError,
    extract_notification_channel_directive,
    notification_channel_directive,
    resolve_telegram_notification_alias,
    telegram_channel_env_var,
)
from tools.send_message_targets import resolve_send_target


@pytest.fixture(autouse=True)
def _clean_alias_env(monkeypatch):
    for alias in ("MARKETS", "HOME", "HOME_ALL", "DEV", "MEDIA", "CRITICAL", "RESEARCH"):
        monkeypatch.delenv(f"TELEGRAM_{alias}_CHAT_ID", raising=False)


def test_alias_to_environment_variable_name():
    assert telegram_channel_env_var("Home All") == "TELEGRAM_HOME_ALL_CHAT_ID"
    assert telegram_channel_env_var("dev") == "TELEGRAM_DEV_CHAT_ID"


def test_reserved_alias_resolves_from_environment():
    env = {"TELEGRAM_MARKETS_CHAT_ID": "-100123"}
    assert resolve_telegram_notification_alias("markets", environ=env) == (
        "-100123",
        "TELEGRAM_MARKETS_CHAT_ID",
    )


def test_reserved_alias_fails_closed_when_missing():
    with pytest.raises(NotificationRoutingError, match="refusing to fall back"):
        resolve_telegram_notification_alias("home", environ={})


def test_unknown_directory_name_is_not_claimed_by_router():
    assert resolve_telegram_notification_alias("some-team", environ={}) is None
    # Numeric chat ids and topic refs never look like aliases.
    assert resolve_telegram_notification_alias("-100123", environ={}) is None
    assert resolve_telegram_notification_alias("-100123:17", environ={}) is None


def test_new_alias_is_extensible_by_environment_convention():
    env = {"TELEGRAM_RESEARCH_CHAT_ID": "-100456"}
    assert resolve_telegram_notification_alias("research", environ=env) == (
        "-100456",
        "TELEGRAM_RESEARCH_CHAT_ID",
    )


def test_default_lookup_reads_process_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_HOME_ALL_CHAT_ID", "-100999")
    assert resolve_telegram_notification_alias("home_all") == ("-100999", "TELEGRAM_HOME_ALL_CHAT_ID")


# --- shared send-target resolution (cron delivery, `hermes send`, MCP send) ----------------

def test_send_target_resolves_alias(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MARKETS_CHAT_ID", "-100321")
    assert resolve_send_target("telegram", "markets") == ("-100321", None, None)


def test_send_target_alias_fails_closed():
    chat_id, thread_id, error = resolve_send_target("telegram", "home", pass_unresolved_references=True)
    assert chat_id is None and thread_id is None
    assert "TELEGRAM_HOME_CHAT_ID" in error


def test_send_target_numeric_telegram_ids_untouched():
    chat_id, thread_id, error = resolve_send_target("telegram", "-1001234567890:17585")
    assert (chat_id, thread_id, error) == ("-1001234567890", "17585", None)


def test_alias_routing_is_telegram_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MARKETS_CHAT_ID", "-100321")
    chat_id, _thread, _error = resolve_send_target("discord", "markets", pass_unresolved_references=True)
    assert chat_id != "-100321"


def test_scheduler_resolves_notification_alias(monkeypatch):
    monkeypatch.setenv("TELEGRAM_HOME_CHAT_ID", "-100789")
    target = _resolve_single_delivery_target({}, "telegram:home")
    assert target["platform"] == "telegram"
    assert target["chat_id"] == "-100789"
    assert target["thread_id"] is None


def test_scheduler_missing_reserved_alias_does_not_deliver():
    assert _resolve_single_delivery_target({}, "telegram:home") is None


# --- per-result channel directive -------------------------------------------------------------

def test_result_directive_is_stripped():
    cleaned, alias = extract_notification_channel_directive(
        "[[notification_channel:home_all]]\n✅ Nothing relevant"
    )
    assert alias == "home_all"
    assert cleaned == "✅ Nothing relevant"


def test_directive_helper_roundtrip():
    assert notification_channel_directive("Home-All") == "[[notification_channel:home_all]]"


def test_cron_directive_reroutes_copy_and_strips_marker():
    job = {"id": "c825c46a4c66", "deliver": "telegram:home", "no_agent": True}
    final = "[[notification_channel:home_all]]\n✅ No works found."
    output = "# Cron Job\n\n## Response\n\n[[notification_channel:home_all]]\n✅ No works found.\n"
    delivery_job, cleaned, out = _apply_notification_channel_directive(job, final, output)
    assert delivery_job is not job
    assert delivery_job["deliver"] == "telegram:home_all"
    assert job["deliver"] == "telegram:home"  # persisted job record untouched
    assert cleaned == "✅ No works found."
    assert "notification_channel" not in out and "✅ No works found." in out


def test_cron_without_directive_keeps_default_route():
    job = {"id": "x", "deliver": "telegram:home"}
    final = "⚠️ Planned outage on 21.10.2026"
    assert _apply_notification_channel_directive(job, final, final) == (job, final, final)
    assert _apply_notification_channel_directive(job, None, "") == (job, None, "")


def test_cron_directive_mid_text_is_not_a_directive():
    job = {"id": "x", "deliver": "telegram:home"}
    final = "Report\n[[notification_channel:home_all]]"
    delivery_job, cleaned, _ = _apply_notification_channel_directive(job, final, final)
    assert delivery_job is job and cleaned == final


@pytest.mark.parametrize(
    ("final", "expected_deliver", "expected_text"),
    [
        ("[[notification_channel:home_all]]\n✅ No outage today.", "telegram:home_all", "✅ No outage today."),
        ("⚠️ Planned outage 21.10.2026 08:00-12:00", "telegram:home", "⚠️ Planned outage"),
    ],
)
def test_save_compose_deliver_routes_by_directive(
    tmp_path, monkeypatch, final, expected_deliver, expected_text,
):
    """End-to-end through the scheduler's save/compose/deliver phase (no fire claim)."""
    import cron.scheduler as sched

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    saved, delivered = {}, {}
    monkeypatch.setattr(sched, "save_job_output", lambda job_id, out: saved.update(out=out) or str(tmp_path / "o.md"))

    def _fake_deliver(job, content, **kw):
        delivered.update(deliver=job["deliver"], content=content)

    monkeypatch.setattr(sched, "_deliver_result", _fake_deliver)
    job = {"id": "c825c46a4c66", "name": "Elektro", "deliver": "telegram:home", "no_agent": True}
    d = sched._RunDelivery(job=job, success=True, error=None)
    sched._save_compose_deliver(
        d, sched._FireOwnership(job), final, f"## Response\n\n{final}\n",
        adapters=None, loop=None, verbose=False, execution_token=None)

    assert d.delivery_attempted and d.delivery_error is None
    assert delivered["deliver"] == expected_deliver
    assert expected_text in delivered["content"]
    assert "notification_channel" not in delivered["content"]
    assert "notification_channel" not in saved["out"]
    assert job["deliver"] == "telegram:home"
