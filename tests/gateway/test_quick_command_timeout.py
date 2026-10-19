"""Per-command ``timeout_seconds`` override for ``type: exec`` quick commands."""

import sys

import pytest

from gateway import run_inbound_hooks
from gateway.run_inbound_hooks import GatewayInboundHooksMixin, quick_command_timeout


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"type": "exec", "command": "true"}, 30),
        ({"type": "exec", "command": "true", "timeout_seconds": 120}, 120),
        ({"type": "exec", "command": "true", "timeout_seconds": "90"}, 90),
        ({"type": "exec", "command": "true", "timeout_seconds": 0}, 1),
        ({"type": "exec", "command": "true", "timeout_seconds": 99999}, 600),
        ({"type": "exec", "command": "true", "timeout_seconds": "soon"}, 30),
        ({"type": "exec", "command": "true", "timeout_seconds": None}, 30),
        ({"type": "exec", "command": "true", "timeout_seconds": True}, 30),
        (None, 30),
    ],
)
def test_quick_command_timeout_parsing(entry, expected):
    assert quick_command_timeout(entry) == expected


def test_quick_command_timeout_follows_module_default(monkeypatch):
    monkeypatch.setattr(run_inbound_hooks, "_EXEC_QUICK_COMMAND_TIMEOUT", 7)
    assert quick_command_timeout({"type": "exec", "command": "true"}) == 7


@pytest.mark.platforms("posix")
@pytest.mark.asyncio
async def test_timeout_override_is_reported_in_reply(tmp_path):
    reply = await GatewayInboundHooksMixin()._hm_run_exec_quick_command(
        "probe", "sleep 5", cwd=str(tmp_path), timeout=1)
    assert reply == run_inbound_hooks.t("gateway.quick_command.timed_out", seconds="1")
    assert "(1s)" in reply


@pytest.mark.platforms("posix")
@pytest.mark.asyncio
async def test_longer_override_lets_slow_command_finish(tmp_path, monkeypatch):
    # Default shrunk below the command's runtime; the per-command override must win.
    monkeypatch.setattr(run_inbound_hooks, "_EXEC_QUICK_COMMAND_TIMEOUT", 1)
    reply = await GatewayInboundHooksMixin()._hm_run_exec_quick_command(
        "probe", f"{sys.executable} -c 'import time; time.sleep(1.5); print(\"done\")'",
        cwd=str(tmp_path), timeout=10)
    assert reply == "done"


@pytest.mark.asyncio
async def test_dispatch_passes_configured_timeout(monkeypatch):
    seen = {}

    class _Runner(GatewayInboundHooksMixin):
        _draining = False

        def _hm_quick_commands(self):
            return {"run_elektro": {"type": "exec", "command": "echo hi", "timeout_seconds": 120}}

        def _check_slash_access(self, source, command):
            return None

        async def _hm_run_exec_quick_command(self, command, exec_cmd, cwd=None, timeout=None):
            seen.update(command=command, exec_cmd=exec_cmd, timeout=timeout)
            return "ok"

    handled, result, command = await _Runner()._hm_dispatch_quick_and_plugin_commands(
        event=None, source=None, command="run_elektro")
    assert (handled, result, command) == (True, "ok", "run_elektro")
    assert seen == {"command": "run_elektro", "exec_cmd": "echo hi", "timeout": 120}
