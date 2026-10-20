"""A liveness miss records whether the loop later ran the probe, plus host pressure.

Incident 2026-10-19 20:38 / 2026-10-20 06:27 (macOS): the loop watchdog fired while every stack
snapshot showed the main thread dispatching ordinary callbacks; the cause was host-wide memory
exhaustion (jetsam, vm-compressor-space-shortage), visible only in OS logs. These witnesses make
that distinguishable from a wedged loop in the gateway's own dump.
"""
import asyncio
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway import shutdown_watchdog
from gateway.shutdown_watchdog import _host_pressure_snapshot, start_loop_liveness_watchdog


def _run_until_exit(on_miss):
    """Drive a watchdog over a loop that never runs probes on time; ``on_miss(strikes, callbacks)``
    may answer queued probes late. Returns every dump's kwargs."""
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    callbacks, evidence, exited = [], [], threading.Event()
    loop.call_soon_threadsafe.side_effect = callbacks.append

    def capture(*_args, **kwargs):
        evidence.append(kwargs)
        on_miss(kwargs["snapshot"]["strikes"], callbacks)

    with patch.object(shutdown_watchdog, "_write_watchdog_dump", side_effect=capture), \
            patch.object(shutdown_watchdog, "_mark_exited_quietly"), \
            patch.object(shutdown_watchdog, "_hard_exit", side_effect=lambda _code: exited.set()), \
            patch.object(shutdown_watchdog.logger, "critical"):
        handle = start_loop_liveness_watchdog(
            loop, probe_interval=0.01, probe_timeout=0.02, max_strikes=3)
        try:
            assert exited.wait(5)
        finally:
            handle.stop()
            handle.join(5)
    return evidence


def test_late_answer_is_recorded_so_a_slow_loop_is_not_mistaken_for_a_wedged_one():
    def answer_first_probe_late(strikes, callbacks):
        if strikes == 1:
            callbacks[0]()  # the loop finally runs probe #1, after its deadline

    evidence = _run_until_exit(answer_first_probe_late)
    second, final = evidence[1]["snapshot"], evidence[2]["snapshot"]
    assert evidence[2]["event"] == "loop_liveness_watchdog_fired"
    (late,) = second["earlier_missed_probe_answered_after_s"]
    assert late >= 0.02  # answered, but only after the probe timeout
    assert final["earlier_missed_probe_answered_after_s"][0] == late
    assert final["earlier_missed_probe_answered_after_s"][1] is None  # probe #2 never ran


def test_wedged_loop_reports_every_earlier_miss_as_unanswered():
    evidence = _run_until_exit(lambda _strikes, _callbacks: None)
    assert evidence[0]["snapshot"]["earlier_missed_probe_answered_after_s"] == []
    assert evidence[-1]["snapshot"]["earlier_missed_probe_answered_after_s"] == [None, None]
    assert "loadavg" in evidence[-1]["snapshot"]["host"]


def test_host_pressure_snapshot_never_raises_when_os_probes_fail():
    with patch.object(shutdown_watchdog, "_darwin_sysctl", side_effect=OSError("boom")), \
            patch.object(shutdown_watchdog.os, "getloadavg", side_effect=OSError("boom")), \
            patch("gateway.lifecycle_ledger.sample_memory", side_effect=OSError("boom")):
        snap = _host_pressure_snapshot()
    assert snap["cpu_count"] == shutdown_watchdog.os.cpu_count()
    assert "loadavg" not in snap


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sysctl memorystatus keys")
def test_host_pressure_snapshot_reads_macos_memory_pressure():
    snap = _host_pressure_snapshot()
    assert snap["memory_pressure_level"] in (1, 2, 4)
    assert snap["compressor_mib"] >= 0
    assert snap["swap_used_mib"] >= 0
    assert len(snap["loadavg"]) == 3
