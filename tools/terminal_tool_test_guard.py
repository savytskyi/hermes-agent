"""Route agent-initiated test runs on the local host through ``scripts/safe-test``.

Two pytest suites started with ``&`` and ``-n 5`` once outlived their agent session and
took a 16 GB Mac down. The managed runner bounds a run (one at a time, one worker,
timeout, memory budget, whole-tree cleanup); this guard refuses a local terminal command
that starts a test runner directly. It is a command-shape heuristic for a cooperative
agent, not a sandbox: an interpreter that spawns pytest itself is out of its sight.
"""

import re
from pathlib import Path

from tools.terminal_tool_guards import _strip_quotes

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
_COMMAND_END_RE = re.compile(r"[;&|)\n]")
# Only these make a runner print and exit; `-v` is verbose, not version, for pytest.
_INFO_ONLY_RE = re.compile(r"(?:^|\s)(?:-h|--help|--version|-V)(?=$|\s)")
_MANAGED_RE = re.compile(r"(?:^|[\s;&|(/])safe[-_]test(?:\.py)?(?=$|[\s;&|)])")
# A command whose program is the managed runner: its arguments are the run it supervises.
_MANAGED_COMMAND_RE = re.compile(
    r"^\s*" + _WRAPPERS + r"(?:\S*python[\d.]*\s+(?:-\S+\s+)*)?(?:\S*/)?safe[-_]test(?:\.py)?(?=$|\s)")
# `bash -c '…'`, `sh -lc "…"`: the quote masking hides the payload, so it is checked on its own.
# Anchored on a shell so `grep -c 'pytest' f` or `git -c k=v` are not mistaken for one.
_SHELL_C_PAYLOAD_RE = re.compile(
    r"""(?:^|[\s;&|(/])(?:ba|z|da|k)?sh(?:\s+-[-\w]+)*?\s+-\w*c\w*\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""")


def _top_level_commands(command: str) -> list[str] | None:
    """*command* split at unquoted ``; & | ( )`` and newlines, or None when its quoting does not
    balance (a heredoc with a stray apostrophe) and the split cannot be trusted."""
    parts, start, quote, i = [], 0, None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "\\":
            i += 1
        elif ch in "'\"`":
            quote = ch
        elif ch in ";&|()\n":
            parts.append(command[start:i])
            start = i + 1
        i += 1
    if quote:
        return None
    parts.append(command[start:])
    return parts


def _without_managed_runs(command: str) -> str:
    """*command* minus the commands that invoke the managed runner, so `safe-test --status;
    pytest -n 5` is still caught. Unsplittable quoting keeps a whole-command exemption."""
    if not _MANAGED_RE.search(command):
        return command
    parts = _top_level_commands(command)
    if parts is None:
        return ""
    return "\n".join(part for part in parts if not _MANAGED_COMMAND_RE.match(part))


def _runs_tests(text: str) -> bool:
    """A runner starts in *text* with arguments other than help/version alone."""
    for match in _DIRECT_TEST_RUN_RE.finditer(text):
        end = _COMMAND_END_RE.search(text, match.end())
        if not _INFO_ONLY_RE.search(text[match.end():end.start() if end else len(text)]):
            return True
    return False


def _starts_test_runner(command: str) -> bool:
    if _runs_tests(_strip_quotes(command)):
        return True
    return any(_runs_tests(a or b) for a, b in _SHELL_C_PAYLOAD_RE.findall(command))


def unmanaged_test_run_guidance(command: str, env_type: str) -> str | None:
    """Refusal text when *command* starts a test runner on this host outside the managed
    runner, else None. Remote backends (docker, ssh, modal, ...) are not this host's memory."""
    if env_type != "local" or not SAFE_TEST.exists() or not _starts_test_runner(_without_managed_runs(command)):
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
