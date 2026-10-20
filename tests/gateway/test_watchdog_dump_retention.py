"""Watchdog dump file stays bounded and never stalls the process it diagnoses.

Before: every non-final missed probe appended a complete all-thread faulthandler dump to
``logs/gateway-shutdown-watchdog.log`` AND to stderr (``gateway.error.log``), so one freeze wrote
``max_strikes`` full dumps per sink, a loop flapping just under ``max_strikes`` wrote them forever,
and neither file was ever rotated. faulthandler also writes while holding the GIL, so a slow disk or
a stalled stderr reader froze the event loop for the length of the write.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway import shutdown_watchdog
from gateway.shutdown_watchdog import (
    _DumpState,
    _write_watchdog_dump,
    configure_watchdog_dumps,
    start_loop_liveness_watchdog,
)

MISSED, FIRED = "loop_liveness_probe_missed", "loop_liveness_watchdog_fired"


@pytest.fixture(autouse=True)
def fresh_dump_state(monkeypatch):
    monkeypatch.setattr(shutdown_watchdog, "_dump_state", _DumpState())


@pytest.fixture
def parked_thread():
    """A thread whose stack never changes, so dedup can be observed on it."""
    release = threading.Event()
    thread = threading.Thread(target=release.wait, name="parked-witness", daemon=True)
    thread.start()
    yield thread
    release.set()
    thread.join(5)


def _records(path):
    """``(header, body)`` per dump record in ``path``."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith('{"event"'):
            out.append([json.loads(line), ""])
        elif out:
            out[-1][1] += line + "\n"
    return [tuple(r) for r in out]


def _dump(path, event=MISSED, strikes=1):
    _write_watchdog_dump(path, delay_s=1.0, snapshot={"strikes": strikes}, event=event)


def test_one_freeze_writes_full_stacks_once_then_only_changed_threads(tmp_path, parked_thread):
    path = tmp_path / "logs" / "gateway-shutdown-watchdog.log"
    _dump(path, strikes=1)
    _dump(path, strikes=2)
    _dump(path, event=FIRED, strikes=3)

    (first, first_body), (second, second_body), (final, final_body) = _records(path)
    assert first["stacks"] == "full" and "[parked-witness]" in first_body
    assert second["stacks"] == "changed_since_last_dump"
    assert "[parked-witness]" not in second_body  # identical stack: not repeated
    assert second["unchanged_thread_count"] >= 1
    assert final["event"] == FIRED and final["stacks"] == "full"
    assert "[parked-witness]" in final_body and "most recent call first" in final_body


def test_zero_cooldown_restores_a_full_dump_on_every_miss(tmp_path, parked_thread):
    configure_watchdog_dumps(full_stack_cooldown_s=0)
    path = tmp_path / "w.log"
    for strike in (1, 2):
        _dump(path, strikes=strike)
    assert [h["stacks"] for h, _ in _records(path)] == ["full", "full"]


def test_flapping_loop_does_not_append_a_full_dump_per_streak(tmp_path, monkeypatch):
    """A loop missing every other probe never reaches max_strikes; it used to append two full
    dumps (file + stderr) per streak, indefinitely."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    probes = iter(range(10**6))
    # Even probes answered at once (healthy), odd ones never (a one-strike streak).
    loop.call_soon_threadsafe.side_effect = lambda cb: cb() if next(probes) % 2 == 0 else None
    real_append, appended, enough = shutdown_watchdog._append_dump, [], threading.Event()

    def counting_append(*args, **kwargs):
        real_append(*args, **kwargs)
        appended.append(1)
        if len(appended) >= 6:
            enough.set()

    with patch.object(shutdown_watchdog, "_append_dump", side_effect=counting_append), \
            patch.object(shutdown_watchdog, "_hard_exit") as hard_exit:
        handle = start_loop_liveness_watchdog(loop, probe_interval=0.01, probe_timeout=0.01,
                                              max_strikes=3)
        try:
            assert enough.wait(10)
        finally:
            handle.stop()
            handle.join(5)
    hard_exit.assert_not_called()
    modes = [h["stacks"] for h, _ in _records(shutdown_watchdog.get_shutdown_watchdog_dump_path())]
    assert modes.count("full") == 1
    assert set(modes[1:]) == {"changed_since_last_dump"}


def test_rotation_preserves_existing_log_and_caps_backups(tmp_path):
    configure_watchdog_dumps(max_mb=0.0625, backups=2, full_stack_cooldown_s=0)  # 64 KiB
    path = tmp_path / "w.log"
    legacy = '{"event": "legacy"}\n' + "x" * (70 * 1024) + "\n"
    path.write_text(legacy, encoding="utf-8")

    _dump(path)
    assert (tmp_path / "w.log.1").read_text(encoding="utf-8") == legacy  # moved intact, not erased
    assert [h["event"] for h, _ in _records(path)] == [MISSED]

    for _ in range(60):  # enough records to rotate several more times
        _dump(path)
    files = sorted(p.name for p in tmp_path.glob("w.log*"))
    assert files == ["w.log", "w.log.1", "w.log.2"]  # retention: at most `backups` rotated files
    record_size = max(len(h) for h in path.read_text(encoding="utf-8").split('{"event"'))
    for name in files:
        assert (tmp_path / name).stat().st_size <= 64 * 1024 + record_size


def test_configure_ignores_values_that_would_erase_or_are_not_numbers():
    configure_watchdog_dumps(max_mb=MagicMock(), backups=0, full_stack_cooldown_s="soon")
    configure_watchdog_dumps(max_mb=0.001, backups=float("nan"), full_stack_cooldown_s=-1)
    st = shutdown_watchdog._dump_state
    assert (st.max_bytes, st.backups, st.full_stack_cooldown_s) == (
        shutdown_watchdog.DEFAULT_WATCHDOG_DUMP_MAX_BYTES,
        shutdown_watchdog.DEFAULT_WATCHDOG_DUMP_BACKUPS,
        shutdown_watchdog.DEFAULT_WATCHDOG_DUMP_FULL_STACK_COOLDOWN_S)


def test_unwritable_dump_file_falls_back_to_stderr_without_raising(tmp_path, capsys,
                                                                   parked_thread):
    path = tmp_path / "w.log"
    path.mkdir()  # open(path, "a") -> IsADirectoryError
    _dump(path)
    err = capsys.readouterr().err
    assert "dump file unavailable" in err
    assert "[parked-witness]" in err  # stderr is now the only copy, so it carries the stacks


def test_missed_probe_on_successful_write_keeps_stderr_to_one_line(tmp_path, capsys):
    _dump(tmp_path / "w.log")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "stacks: full" in err


def test_low_disk_drops_miss_stacks_but_keeps_the_header_and_final_dump(tmp_path):
    path = tmp_path / "w.log"
    with patch.object(shutdown_watchdog, "_free_bytes", return_value=1024):
        _dump(path)
        _dump(path, event=FIRED)
    (miss, miss_body), (final, final_body) = _records(path)
    assert miss["stacks"] == "omitted_low_disk" and miss["snapshot"] == {"strikes": 1}
    assert miss_body == ""
    assert final["stacks"] == "full" and "most recent call first" in final_body


def test_unrotatable_file_stops_growing_with_stacks(tmp_path):
    configure_watchdog_dumps(max_mb=0.0625)
    path = tmp_path / "w.log"
    path.write_text("x" * (70 * 1024) + "\n", encoding="utf-8")
    with patch.object(shutdown_watchdog, "_rotate_dump", side_effect=PermissionError("ro")):
        _dump(path)
        _dump(path, event=FIRED)
    (miss, miss_body), (final, final_body) = _records(path)
    assert miss["stacks"] == "omitted_rotate_failed" and "rotate_error" in miss
    assert miss_body == ""
    assert final["stacks"] == "full" and final_body  # the exit dump is never sacrificed


def test_hung_dump_write_is_abandoned_and_later_dumps_skip_the_file(tmp_path, capsys,
                                                                    monkeypatch):
    monkeypatch.setattr(shutdown_watchdog, "_WATCHDOG_DUMP_IO_TIMEOUT_S", 0.2)
    release = threading.Event()
    with patch.object(shutdown_watchdog, "_append_dump", side_effect=lambda *a: release.wait(10)):
        try:
            started = time.monotonic()
            _dump(tmp_path / "w.log")
            _dump(tmp_path / "w.log", strikes=2)
            assert time.monotonic() - started < 5
        finally:
            release.set()
    err = capsys.readouterr().err
    assert "dump write blocked" in err
    assert "earlier dump write is still blocked" in err


def test_blocked_stderr_keeps_one_writer_thread_not_one_per_miss(tmp_path, monkeypatch):
    """A loop flapping under max_strikes keeps missing probes; with stderr blocked (a wedged log
    volume behind launchd) every miss used to leave another writer thread parked on it forever."""
    from tests.waiting import wait_until

    monkeypatch.setattr(shutdown_watchdog, "_WATCHDOG_DUMP_IO_TIMEOUT_S", 0.2)
    release = threading.Event()
    blocked = SimpleNamespace(write=lambda _text: release.wait(10), flush=lambda: None)
    monkeypatch.setattr(sys, "stderr", blocked)

    def writers():
        return [t for t in threading.enumerate() if t.name == "gateway-watchdog-dump-io"]

    try:
        for strike in range(5):
            _dump(tmp_path / "w.log", strikes=strike)
        wait_until(lambda: len(writers()) <= 1, timeout=5, what="extra stderr writers to give up")
        assert len(writers()) == 1
    finally:
        release.set()
    wait_until(lambda: not writers(), timeout=5, what="the blocked writer to finish")


def test_watchdog_still_restarts_when_the_dump_file_hangs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(shutdown_watchdog, "_WATCHDOG_DUMP_IO_TIMEOUT_S", 0.2)
    release, exited = threading.Event(), threading.Event()
    with patch.object(shutdown_watchdog, "_append_dump", side_effect=lambda *a: release.wait(10)), \
            patch.object(shutdown_watchdog, "_mark_exited_quietly"), \
            patch.object(shutdown_watchdog.logger, "critical"), \
            patch.object(shutdown_watchdog, "_hard_exit", side_effect=lambda _c: exited.set()):
        handle = start_loop_liveness_watchdog(MagicMock(spec=asyncio.AbstractEventLoop),
                                              probe_interval=0.01, probe_timeout=0.01,
                                              max_strikes=2)
        try:
            assert exited.wait(10)
        finally:
            release.set()
            handle.stop()
            handle.join(5)


def _deep(depth, release):
    return _deep(depth - 1, release) if depth else release.wait(30)


@pytest.mark.platforms("posix")
def test_dump_into_a_stalled_sink_does_not_freeze_other_threads(tmp_path):
    """The event loop must keep running while a dump is stuck writing. A reader process waits 3s
    before draining a FIFO; a GIL-holding writer (faulthandler) froze every thread for those 3s."""
    release = threading.Event()
    for _ in range(30):  # ~150 KB of stacks: more than a pipe buffer holds
        threading.Thread(target=_deep, args=(60, release), daemon=True).start()
    fifo = tmp_path / "w.log"
    os.mkfifo(fifo)
    drain = ("import select, sys; f = open(sys.argv[1], 'rb'); select.select([], [], [], 3.0); "
             "[None for _ in iter(lambda: f.read(65536), b'')]")
    reader = subprocess.Popen([sys.executable, "-c", drain, str(fifo)])
    dumper = threading.Thread(target=_dump, args=(fifo,), daemon=True)
    try:
        dumper.start()
        worst, last, tick = 0.0, time.monotonic(), threading.Event()
        while dumper.is_alive() and time.monotonic() - last < 10:
            tick.wait(0.01)
            now = time.monotonic()
            worst, last = max(worst, now - last), now
        dumper.join(10)
    finally:
        release.set()
        reader.kill()
        reader.wait(5)
    assert not dumper.is_alive()
    assert worst < 1.0, f"main thread stalled {worst:.2f}s while a dump was blocked"


def test_retention_config_round_trips_and_clamps():
    from gateway.config import GatewayConfig

    default = GatewayConfig.from_dict({})
    assert (default.watchdog_dump_max_mb, default.watchdog_dump_backups,
            default.watchdog_dump_full_stack_cooldown_s) == (5.0, 3, 600.0)
    nested = GatewayConfig.from_dict({"gateway": {"watchdog_dump_max_mb": 20,
                                                  "watchdog_dump_backups": 5,
                                                  "watchdog_dump_full_stack_cooldown_s": 0}})
    assert (nested.watchdog_dump_max_mb, nested.watchdog_dump_backups,
            nested.watchdog_dump_full_stack_cooldown_s) == (20.0, 5, 0.0)
    assert nested.to_dict()["watchdog_dump_backups"] == 5
    bad = GatewayConfig.from_dict({"watchdog_dump_max_mb": float("inf"),
                                   "watchdog_dump_backups": 0,
                                   "watchdog_dump_full_stack_cooldown_s": -5})
    assert (bad.watchdog_dump_max_mb, bad.watchdog_dump_backups,
            bad.watchdog_dump_full_stack_cooldown_s) == (5.0, 3, 600.0)


def test_load_gateway_config_bridges_retention_keys(tmp_path, monkeypatch):
    from gateway.config import load_gateway_config

    (tmp_path / "config.yaml").write_text(
        "gateway:\n  watchdog_dump_max_mb: 2\n  watchdog_dump_backups: 7\n", encoding="utf-8")
    monkeypatch.setattr("gateway.config.get_hermes_home", lambda: tmp_path)
    cfg = load_gateway_config()
    assert (cfg.watchdog_dump_max_mb, cfg.watchdog_dump_backups) == (2.0, 7)


def test_retention_applies_even_with_the_loop_watchdog_disabled():
    """The shutdown watchdog writes the same file, so its retention cannot hang off the opt-out."""
    from gateway.config import GatewayConfig
    from gateway.run_startup import GatewayStartupMixin

    runner = SimpleNamespace(config=GatewayConfig.from_dict(
        {"loop_watchdog": False, "watchdog_dump_max_mb": 1, "watchdog_dump_backups": 9}))
    GatewayStartupMixin._start_loop_liveness_guards(runner, MagicMock())
    assert shutdown_watchdog._dump_state.max_bytes == 2**20
    assert shutdown_watchdog._dump_state.backups == 9
    assert not hasattr(runner, "_loop_liveness_watchdog")  # opt-out still honoured
