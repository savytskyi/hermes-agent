"""scripts/safe-test on macOS: process identity must survive a wall-clock step.

psutil shifts create_time() on macOS by any boot-time change since it was imported, so after a
sleep/wake or NTP step an exact identity check fails for every process: the launcher looks dead
(the run is killed as orphaned) and the run's own processes drop out of tracking.
"""
import psutil
import pytest

from scripts import safe_test

pytestmark = pytest.mark.platforms("macos")


def test_process_identity_survives_a_clock_step(monkeypatch):
    import psutil._psosx as psosx

    pid, birth = safe_test._identity(psutil.Process())
    monkeypatch.setattr(psosx, "INIT_BOOT_TIME", psosx.INIT_BOOT_TIME - 30)

    assert abs(psutil.Process().create_time() - birth) >= 29
    assert safe_test._same_process(pid, birth) is not None
