"""Route agent-initiated test runs on the local host through ``scripts/safe-test``.

Two pytest suites started with ``&`` and ``-n 5`` once outlived their agent session and
took a 16 GB Mac down. The managed runner bounds a run (one at a time, one worker,
timeout, memory budget, whole-tree cleanup); this guard refuses a local terminal command
that starts a test runner directly. It is a command-shape heuristic for a cooperative
agent, not a sandbox: an interpreter that spawns pytest itself is out of its sight.
"""

import re
from pathlib import Path

from tools.terminal_tool_guards import _looks_like_help_or_version_command, _strip_quotes

SAFE_TEST = Path(__file__).resolve().parent.parent / "scripts" / "safe-test"

_COMMAND_START = r"(?:^|[;&|(\n]|\$\(|\b(?:then|do|else)\s)\s*"
_WRAPPERS = (
    r"(?:\w+=\S*\s+)*"
    r"(?:(?:sudo|time|nohup|command|exec|nice(?:\s+-n)?(?:\s+-?\d+)?|env(?:\s+-\S+)*|timeout\s+\S+"
    r"|(?:uv|poetry|pdm|hatch|pipenv|rye)\s+run(?:\s+-\S+)*)\s+(?:\w+=\S*\s+)*)*"
)
_RUNNER = (
    r"(?:\S*/)?(?:py\.?test|run_tests\.sh|run_tests_parallel\.py)(?=$|[\s;&|)])"
    r"|(?:\S*/)?python[\d.]*(?:\s+-[A-Za-z]+)*\s+(?:-m\s+pytest\b|(?:\S*/)?run_tests_parallel\.py\b)"
    r"|(?:ba)?sh\s+(?:\S*/)?run_tests\.sh\b"
)
_DIRECT_TEST_RUN_RE = re.compile(_COMMAND_START + _WRAPPERS + r"(?:" + _RUNNER + r")", re.MULTILINE)
_MANAGED_RE = re.compile(r"(?:^|[\s;&|(/])safe[-_]test(?:\.py)?(?=$|[\s;&|)])")
_SHELL_C_PAYLOAD_RE = re.compile(r"""\s-c\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""")


def _starts_test_runner(command: str) -> bool:
    if _DIRECT_TEST_RUN_RE.search(_strip_quotes(command)):
        return True
    # `bash -c 'pytest ...'`: the quote masking above hides the payload, so look inside once.
    return any(_DIRECT_TEST_RUN_RE.search(a or b) for a, b in _SHELL_C_PAYLOAD_RE.findall(command))


def unmanaged_test_run_guidance(command: str, env_type: str) -> str | None:
    """Refusal text when *command* starts a test runner on this host outside the managed
    runner, else None. Remote backends (docker, ssh, modal, ...) are not this host's memory."""
    if env_type != "local" or not SAFE_TEST.exists() or _looks_like_help_or_version_command(command):
        return None
    if _MANAGED_RE.search(command) or not _starts_test_runner(command):
        return None
    return (
        "Test runs on this host must go through the managed runner, which allows one run at a "
        "time with one worker, a timeout and a memory budget, and kills every process the run "
        f"started. Re-send as `{SAFE_TEST} <test paths> -q` (Hermes repo: per-file isolated "
        f"scripts/run_tests.sh), or `{SAFE_TEST} --mode exec -- <your pytest command>` for "
        "other projects. Do not add `&`, `-n`/xdist or `-j`; for runs over the foreground cap use "
        "background=true with notify_on_complete=true. If it reports another run is active, wait "
        f"for it (`{SAFE_TEST} --status`) instead of starting tests another way."
    )
