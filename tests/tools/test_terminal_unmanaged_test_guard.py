"""Agent test runs on the local host go through scripts/safe-test; `&` detaches regardless of spacing.

Regression for two `pytest -n 5 ... &` suites that outlived their agent session and exhausted a
16 GB Mac: direct test-runner invocations are refused with the managed command to use instead,
and `a& b&` / `(cmd &)` no longer slip past the background-operator refusal.
"""
import json
from unittest.mock import patch

import pytest

from tools.terminal_tool_guards import _foreground_background_guidance
from tools.terminal_tool_test_guard import SAFE_TEST, unmanaged_test_run_guidance


@pytest.mark.parametrize("command", [
    "pytest -n 5 tests/agent",
    "python -m pytest -q tests/x.py",
    ".venv/bin/python3.11 -I -m pytest tests/x.py",
    "cd repo && uv run pytest",
    "VAR=1 timeout 600 pytest tests",
    "scripts/run_tests.sh tests/gateway/",
    "bash scripts/run_tests.sh",
    "python scripts/run_tests_parallel.py -j 8",
    "bash -c 'cd repo; pytest -q'",
    "for f in a b; do pytest $f; done",
    "pytest -n 8 tests -v",
    "scripts/safe-test --status; pytest -n 5 tests",
    "bash -lc 'pytest -q'",
    "pytest --version && pytest -n 4 tests",
])
def test_direct_test_runs_are_refused_with_the_managed_command(command):
    guidance = unmanaged_test_run_guidance(command, "local")
    assert guidance and str(SAFE_TEST) in guidance


@pytest.mark.parametrize("command", [
    "scripts/safe-test tests/x.py -q",
    f"{SAFE_TEST} --mode exec -- .venv/bin/python -m pytest tests",
    "pip install pytest",
    "git commit -m 'make pytest pass'",
    "grep -rn pytest .",
    "cat pytest.ini",
    "pytest --version",
    "pytest -h",
    "cd repo && scripts/safe-test tests/x.py -q -v",
    "scripts/safe-test --mode exec -- bash -c 'cd x; pytest -q'",
    "grep -c 'pytest' tests/conftest.py",
    "docker exec app pytest -n 4",
])
def test_non_runs_and_managed_runs_pass(command):
    assert unmanaged_test_run_guidance(command, "local") is None


def test_remote_backends_are_not_this_hosts_memory():
    assert unmanaged_test_run_guidance("pytest -n 5 tests", "docker") is None


@pytest.mark.parametrize("command", ["pytest a& pytest b&", "(sleep 60 &)", "a&b"])
def test_background_amp_is_refused_without_surrounding_spaces(command):
    assert "'&' backgrounding" in _foreground_background_guidance(command)


@pytest.mark.parametrize("command", ["make 2>&1 | tee log", "a && b || c", "cmd &> out", "cmd |& tee x", "echo 'a & b'",
                                     "echo a\\&b"])
def test_non_background_amp_spellings_still_run(command):
    assert _foreground_background_guidance(command) is None


@pytest.mark.parametrize("background", [False, True])
def test_terminal_tool_refuses_before_spawning(tmp_path, monkeypatch, background):
    from tools.terminal_tool import terminal_tool

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    marker = tmp_path / "ran"
    config = {"env_type": "local", "timeout": 60, "cwd": str(tmp_path), "host_cwd": None, "modal_mode": "auto",
              "docker_image": "", "singularity_image": "", "modal_image": "", "daytona_image": ""}
    with patch("tools.terminal_tool._get_env_config", return_value=config), \
         patch("tools.terminal_tool._start_cleanup_thread"):
        result = json.loads(terminal_tool(command=f"touch {marker} && pytest -n 5 tests", background=background))

    assert "managed runner" in result["error"]
    assert not marker.exists()
