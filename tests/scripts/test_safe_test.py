"""scripts/safe-test: managed agent test runs cannot outlive their owner, run concurrently,
fan out workers, or grow without bound.

Regression for the incident where two `pytest -n 5 ... &` suites outlived their agent
session and pushed a 16 GB Mac into a reboot. Every test drives the real supervisor in a
subprocess with a private lock dir and tiny workloads (sleeps, a few hundred MB at most).
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from scripts import safe_test
from tests.waiting import wait_until

pytestmark = pytest.mark.platforms("linux", "macos")

SCRIPT = Path(safe_test.__file__)


def _env(tmp_path: Path) -> dict:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in (safe_test.TAG_ENV, "PYTEST_ADDOPTS")}
    env[safe_test.LOCK_DIR_ENV] = str(lock_dir)
    return env


def _start(tmp_path: Path, *args: str, **kw) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(SCRIPT), *args], env=_env(tmp_path), cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


def _run(tmp_path: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=_env(tmp_path), cwd=tmp_path,
                          capture_output=True, text=True, timeout=timeout, check=False)


def _pid_from(path: Path) -> int:
    return int(wait_until(lambda: path.exists() and path.read_text().strip(), timeout=15, what=f"pid in {path}"))


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _bg_script(tmp_path: Path) -> str:
    """A shell that leaves a `&` job AND a new-session escapee behind, then blocks."""
    return textwrap.dedent(f"""
        sleep 300 & echo $! > {tmp_path}/bg.pid
        {sys.executable} -c 'import subprocess; print(subprocess.Popen(["sleep", "300"], start_new_session=True).pid)' > {tmp_path}/escapee.pid
        echo ready > {tmp_path}/ready
        exec sleep 300
    """)


@pytest.fixture
def unrelated():
    """A same-user process the runner must never touch."""
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    yield proc
    proc.kill()
    proc.wait()


def test_background_jobs_and_escapees_are_reaped_when_the_command_exits(tmp_path, unrelated):
    script = f"sleep 300 & echo $! > {tmp_path}/bg.pid\n" + \
        f"{sys.executable} -c 'import subprocess; print(subprocess.Popen([\"sleep\", \"300\"], start_new_session=True).pid)' > {tmp_path}/escapee.pid\nexit 3"
    result = _run(tmp_path, "--mode", "exec", "--timeout", "60", "--", "bash", "-c", script)

    assert result.returncode == 3, result.stderr
    assert "reaped 2 leftover" in result.stderr
    assert _gone(_pid_from(tmp_path / "bg.pid"))
    assert _gone(_pid_from(tmp_path / "escapee.pid"))
    assert unrelated.poll() is None


def test_timeout_kills_the_whole_tree(tmp_path):
    started = time.monotonic()
    result = _run(tmp_path, "--mode", "exec", "--timeout", "1", "--", "bash", "-c", _bg_script(tmp_path))

    assert result.returncode == safe_test.EXIT_TIMEOUT, result.stderr
    assert "timeout" in result.stderr
    assert time.monotonic() - started < 30
    assert _gone(_pid_from(tmp_path / "bg.pid"))
    assert _gone(_pid_from(tmp_path / "escapee.pid"))


def test_cancellation_signal_cleans_up_and_reports_130(tmp_path, unrelated):
    proc = _start(tmp_path, "--mode", "exec", "--", "bash", "-c", _bg_script(tmp_path))
    wait_until(lambda: (tmp_path / "ready").exists(), timeout=15, what="run ready")

    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=30)

    assert proc.returncode == safe_test.EXIT_CANCELLED, out
    assert _gone(_pid_from(tmp_path / "bg.pid"))
    assert _gone(_pid_from(tmp_path / "escapee.pid"))
    assert unrelated.poll() is None


def test_watchdog_reaps_the_run_when_the_supervisor_is_sigkilled(tmp_path, unrelated):
    """The terminal tool's last resort is SIGKILL on the caller's process group: the
    supervisor gets no chance to clean up, so the out-of-session watchdog must."""
    proc = _start(tmp_path, "--mode", "exec", "--", "bash", "-c", _bg_script(tmp_path))
    wait_until(lambda: (tmp_path / "ready").exists(), timeout=15, what="run ready")
    pids = [_pid_from(tmp_path / "bg.pid"), _pid_from(tmp_path / "escapee.pid")]

    proc.kill()
    proc.wait(timeout=10)

    wait_until(lambda: all(_gone(p) for p in pids), timeout=30, what="watchdog reaped the run")
    assert unrelated.poll() is None


def test_run_is_abandoned_and_reaped_when_its_launcher_dies(tmp_path):
    """Agent disconnect: the process that launched the run disappears without signalling it."""
    launcher = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, {str(SCRIPT)!r}, "--mode", "exec", "--", "bash", "-c", {_bg_script(tmp_path)!r}])
        time.sleep(300)
    """)], env=_env(tmp_path), start_new_session=True)
    try:
        wait_until(lambda: (tmp_path / "ready").exists(), timeout=15, what="run ready")
        pids = [_pid_from(tmp_path / "bg.pid"), _pid_from(tmp_path / "escapee.pid")]
        launcher.kill()
        launcher.wait()
        wait_until(lambda: all(_gone(p) for p in pids), timeout=30, what="orphaned run reaped")
    finally:
        launcher.kill()
        launcher.wait()


def test_second_run_is_refused_while_one_is_active_and_lock_never_goes_stale(tmp_path):
    first = _start(tmp_path, "--mode", "exec", "--", "bash", "-c", _bg_script(tmp_path))
    wait_until(lambda: (tmp_path / "ready").exists(), timeout=15, what="first run ready")

    second = _run(tmp_path, "--mode", "exec", "--", "true", timeout=20)
    status = _run(tmp_path, "--status", timeout=20)
    first.kill()  # SIGKILL: the lock must still free once the watchdog has reaped the run
    first.wait(timeout=10)
    third = _run(tmp_path, "--wait-lock", "30", "--mode", "exec", "--", "true")

    assert second.returncode == safe_test.EXIT_LOCK_BUSY
    assert "another managed test run is active" in second.stderr
    assert '"active": true' in status.stdout
    assert third.returncode == 0, third.stderr


def test_memory_budget_kills_a_growing_run(tmp_path):
    grow = "import time\nheld = []\nwhile True:\n    held.append(bytearray(32 << 20)); time.sleep(0.05)"
    result = _run(tmp_path, "--mode", "exec", "--max-memory-mb", "200", "--", sys.executable, "-c", grow)

    assert result.returncode == safe_test.EXIT_MEMORY, result.stderr
    assert "memory budget exceeded" in result.stderr


def test_per_process_address_space_cap_fails_a_runaway_allocation(tmp_path):
    reserve = textwrap.dedent("""
        import mmap
        small = mmap.mmap(-1, 64 << 20)
        try:
            mmap.mmap(-1, 4 << 30)
        except OSError:
            raise SystemExit(42)
    """)
    result = _run(tmp_path, "--mode", "exec", "--vm-headroom-mb", "1024", "--", sys.executable, "-c", reserve)

    assert result.returncode == 42, result.stderr


@pytest.mark.parametrize("args", [
    ["-n", "5"], ["-n5"], ["-nauto"], ["--numprocesses=4"], ["--dist", "loadfile"], ["-p", "xdist"], ["-pxdist.plugin"],
])
def test_xdist_worker_flags_are_refused(args):
    assert safe_test.xdist_violation(["tests/x.py", *args])
    with pytest.raises(ValueError, match="xdist"):
        safe_test.build_command("pytest", ["tests/x.py", *args], 1, sys.executable)


@pytest.mark.parametrize("args", [["-k", "-n"], ["-q", "-x"], ["-p", "no:cacheprovider"], ["--no-header"]])
def test_ordinary_pytest_flags_pass(args):
    assert safe_test.xdist_violation(args) is None


def test_default_command_is_one_worker():
    assert safe_test.build_command("run_tests", ["tests/x.py"], 1, sys.executable)[1:3] == ["-j", "1"]
    assert safe_test.build_command("pytest", ["tests/x.py"], 1, "py")[:5] == ["py", "-m", "pytest", "-p", "no:xdist"]
    with pytest.raises(ValueError, match="--workers"):
        safe_test.build_command("run_tests", ["tests/x.py"], 3, sys.executable)
    with pytest.raises(ValueError, match="--workers"):
        safe_test.build_command("run_tests", ["-j", "8", "tests/x.py"], 1, sys.executable)


@pytest.mark.parametrize("argv, expected", [
    (["uv", "run", "pytest", "-q"], ["uv", "run", "pytest", "-p", "no:xdist", "-q"]),
    ([".venv/bin/python", "-m", "pytest", "x"], [".venv/bin/python", "-m", "pytest", "-p", "no:xdist", "x"]),
    (["npm", "test"], ["npm", "test"]),
])
def test_exec_mode_pins_pytest_to_one_process(argv, expected):
    assert safe_test.build_command("exec", argv, 1, sys.executable) == expected


def test_runner_options_stop_at_the_first_passthrough_token():
    assert safe_test.split_own_args(["--timeout", "5", "-q", "tests/x.py"]) == (["--timeout", "5"], ["-q", "tests/x.py"])
    assert safe_test.split_own_args(["--mode=exec", "--", "npm", "test"]) == (["--mode=exec"], ["npm", "test"])


def test_cli_refuses_xdist_and_nested_runs(tmp_path):
    refused = _run(tmp_path, "--mode", "pytest", "tests/x.py", "-n", "5")
    env = {**_env(tmp_path), safe_test.TAG_ENV: "outer"}
    nested = subprocess.run([sys.executable, str(SCRIPT), "--mode", "exec", "--", "true"], env=env,
                            capture_output=True, text=True, timeout=30, check=False)

    assert refused.returncode == safe_test.EXIT_POLICY and "xdist" in refused.stderr
    assert nested.returncode == safe_test.EXIT_POLICY and "already inside a managed run" in nested.stderr
