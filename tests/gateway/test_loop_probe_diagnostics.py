"""Missed probes retain evidence distinguishing latency from a fixed blocker."""
import asyncio
import threading
from unittest.mock import MagicMock, patch
from gateway.shutdown_watchdog import start_loop_liveness_watchdog


def test_each_miss_records_timing_and_stack_without_waiting_for_exit():
    evidence, captured = [], threading.Event()
    release = threading.Event()
    def capture(*args, **kwargs):
        evidence.append(kwargs)
        captured.set()
        assert release.wait(5)
    with patch('gateway.shutdown_watchdog._write_watchdog_dump', side_effect=capture), \
            patch('gateway.shutdown_watchdog._hard_exit') as hard_exit:
        handle = start_loop_liveness_watchdog(
            MagicMock(spec=asyncio.AbstractEventLoop),
            probe_interval=0.01, probe_timeout=0.01, max_strikes=3)
        try:
            assert captured.wait(5)
        finally:
            handle.stop()
            release.set()
            handle.join(5)
    hard_exit.assert_not_called()
    record = evidence[0]
    assert record['event'] == 'loop_liveness_probe_missed'
    snapshot = record['snapshot']
    assert snapshot['strikes'] == 1
    assert snapshot['probe_timeout_s'] == 0.01
    assert snapshot['probe_elapsed_s'] >= 0.01
    assert snapshot['watchdog_elapsed_s'] >= snapshot['probe_elapsed_s']
    assert snapshot['process_cpu_elapsed_s'] >= 0
    assert snapshot['thread_count'] >= 1


def test_final_dump_reports_actual_elapsed_time_not_timeout_sum():
    evidence, exited = [], threading.Event()
    with patch('gateway.shutdown_watchdog._write_watchdog_dump',
               side_effect=lambda *a, **kw: evidence.append(kw)), \
            patch('gateway.shutdown_watchdog._mark_exited_quietly'), \
            patch('gateway.shutdown_watchdog._hard_exit', side_effect=lambda _: exited.set()):
        handle = start_loop_liveness_watchdog(
            MagicMock(spec=asyncio.AbstractEventLoop),
            probe_interval=0.01, probe_timeout=0.01, max_strikes=2)
        try:
            assert exited.wait(5)
        finally:
            handle.stop()
            handle.join(5)
    final = evidence[-1]
    assert final['event'] == 'loop_liveness_watchdog_fired'
    assert final['delay_s'] >= 0.04
    assert final['snapshot']['probe_timeout_s'] == 0.01
