#!/usr/bin/env python3
"""Resource-safe managed test runner for agent-initiated test runs (``scripts/safe-test``).

One supervised test run at a time per user, one pytest worker by default, a wall-clock
timeout, an aggregate memory budget over the whole process tree, and cleanup of every
process the run created — on normal exit, timeout, memory breach, cancellation
(SIGTERM/SIGINT/SIGHUP), loss of the launching agent, or SIGKILL of the supervisor itself.

Why this exists: an agent once started two pytest suites with shell ``&`` and ``-n 5``.
The shell returned at once, nothing owned the suites, and they outlived the session while
growing to tens of GB on a 16 GB Mac (memory pressure, gateway crash, reboot).

How it holds on macOS, where there is no cgroup:

* **Ownership.** The test command runs in its own session. Every process of the run is
  found three ways: the live tree under the command, members of its process group, and an
  environment tag (``HERMES_SAFE_TEST_RUN=<uuid>``) inherited by descendants — the tag
  finds ``setsid``/``start_new_session`` escapees after they reparent to launchd. Every
  kill is identity-checked (pid + create time); nothing untagged and unowned is touched.
* **Supervisor death.** A watchdog process in its own session holds a pipe from the
  supervisor. EOF without a "done" message (supervisor SIGKILLed, e.g. by the terminal
  tool's process-group kill) makes the watchdog run the same cleanup.
* **Memory.** macOS ignores RLIMIT_RSS and rejects RLIMIT_AS/RLIMIT_DATA below the ~33 GiB
  of address space every process reserves at start, so there is no hard per-tree memory
  cap. The supervisor samples the aggregate physical footprint of the tree (RSS plus
  compressed/swapped pages — what Activity Monitor shows; RSS elsewhere) and kills it over budget, and
  also on low available memory or critical kernel memory pressure. Each process
  additionally gets RLIMIT_AS = baseline + headroom, which the kernel does enforce on
  allocation: a single runaway process fails with ENOMEM/MemoryError instead of eating
  the machine. Sampling is not a hard limit: an allocation burst faster than the poll
  interval can overshoot before the kill lands.
* **Concurrency.** An ``flock`` on a per-user lock file, held by the supervisor, the test
  command and the watchdog; the kernel drops it when they are all gone, so it never goes
  stale.

Exit codes: the test command's own code, or 2 policy/usage, 75 another run holds the lock,
124 timeout, 125 memory budget/pressure, 129 launching agent gone, 130 cancelled.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import fcntl
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psutil

TAG_ENV = "HERMES_SAFE_TEST_RUN"
LOCK_DIR_ENV = "HERMES_SAFE_TEST_LOCK_DIR"

EXIT_POLICY = 2
EXIT_LOCK_BUSY = 75
EXIT_TIMEOUT = 124
EXIT_MEMORY = 125
EXIT_ORPHANED = 129
EXIT_CANCELLED = 130

MAX_WORKERS = 2
_GIB = 1024 ** 3
_MIB = 1024 ** 2
_POLL_S = 0.25
_TAG_SCAN_EVERY_S = 3.0
_HOST_CHECK_EVERY_S = 1.0
_DARWIN_PRESSURE_CRITICAL = 4

REPO_ROOT = Path(__file__).resolve().parent.parent


def _say(msg: str) -> None:
    """Status line on stderr; a closed pipe (agent gone) must not abort cleanup."""
    with contextlib.suppress(OSError, ValueError):
        print(f"[safe-test] {msg}", file=sys.stderr, flush=True)


# --- Limits -----------------------------------------------------------------------------

@dataclass
class Limits:
    timeout_s: float
    max_memory: int          # aggregate footprint of the run's processes, bytes
    min_available: int       # kill when host available memory drops below, bytes
    vm_headroom: int | None  # per-process RLIMIT_AS above the spawn baseline, bytes
    cpu_s: int               # per-process RLIMIT_CPU backstop for anything that escapes
    grace_s: float


def default_limits() -> Limits:
    """Sized for a 16 GB laptop: a quarter of RAM for the run (≤ 4 GiB), keep ≥ 2 GiB free."""
    total = psutil.virtual_memory().total
    timeout = 1800.0
    return Limits(
        timeout_s=timeout,
        max_memory=max(_GIB, min(4 * _GIB, total // 4)),
        min_available=min(2 * _GIB, total // 8),
        vm_headroom=8 * _GIB,
        cpu_s=int(timeout) + 60,
        grace_s=3.0,
    )


# --- Single-run lock --------------------------------------------------------------------

class LockBusy(Exception):
    def __init__(self, holder: dict):
        super().__init__("another managed test run is active")
        self.holder = holder


def lock_dir() -> Path:
    """Per-user dir shared by every agent and sandbox on the host (``/tmp``, not ``$TMPDIR``,
    which differs per sandbox). Refuses a dir another user could have planted."""
    path = Path(os.environ.get(LOCK_DIR_ENV) or f"/tmp/hermes-safe-test-{os.getuid()}")
    with contextlib.suppress(FileExistsError):
        path.mkdir(mode=0o700, parents=False)
    st = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise RuntimeError(f"unsafe lock dir {path}: must be a real dir owned by uid {os.getuid()}, not group/world-writable")
    return path


def acquire_lock(directory: Path, wait_s: float) -> int:
    """Return an fd holding the exclusive run lock, or raise LockBusy after *wait_s*."""
    fd = os.open(directory / "run.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                raise
        if time.monotonic() >= deadline:
            os.close(fd)
            raise LockBusy(read_holder(directory))
        time.sleep(0.5)


def read_holder(directory: Path) -> dict:
    try:
        return json.loads((directory / "holder.json").read_text())
    except (OSError, ValueError):
        return {}


def write_holder(directory: Path, info: dict) -> None:
    tmp = directory / f"holder.json.{os.getpid()}"
    tmp.write_text(json.dumps(info))
    os.replace(tmp, directory / "holder.json")


# --- Command policy ---------------------------------------------------------------------

_XDIST_LONG = ("--numprocesses", "--dist", "--maxprocesses", "--max-worker-restart", "--tx")
_PYTEST_VALUE_OPTS = frozenset({"-k", "-m", "-o", "-c", "--rootdir", "--confcutdir", "--basetemp", "-W"})


def xdist_violation(args: list[str]) -> str | None:
    """The first token that would start pytest-xdist workers, else None."""
    skip = False
    for i, tok in enumerate(args):
        if skip:
            skip = False
            continue
        if tok == "--":
            continue
        if tok in _PYTEST_VALUE_OPTS:
            skip = True
            continue
        if tok == "-n" or (tok.startswith("-n") and tok[2:].lstrip("=") and not tok.startswith("--")):
            return tok
        if any(tok == opt or tok.startswith(opt + "=") for opt in _XDIST_LONG):
            return tok
        plugin = args[i + 1] if tok == "-p" and i + 1 < len(args) else (tok[2:] if tok.startswith("-p") and not tok.startswith("--") else "")
        if plugin.split(".")[0] == "xdist":
            return f"-p {plugin}"
    return None


def _jobs_violation(args: list[str]) -> str | None:
    for tok in args:
        if tok in ("-j", "--jobs") or tok.startswith("--jobs=") or (tok.startswith("-j") and tok[2:].isdigit()):
            return tok
    return None


def _pytest_index(argv: list[str]) -> int | None:
    """Index of the token that starts pytest (``pytest``, ``py.test`` or ``-m pytest``)."""
    return next((i for i, tok in enumerate(argv) if os.path.basename(tok) in ("pytest", "py.test")), None)


def build_command(mode: str, args: list[str], workers: int, python: str) -> list[str]:
    """argv for *mode*; raises ValueError with an agent-readable reason on a policy breach."""
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"--workers must be 1..{MAX_WORKERS} (got {workers}); one worker is the default for a reason")
    if mode == "exec":
        if not args:
            raise ValueError("exec mode needs a command after --")
        at = _pytest_index(args)
        bad = xdist_violation(args[at + 1:]) if at is not None else None
    else:
        bad = xdist_violation(args)
    if bad:
        raise ValueError(f"{bad!r}: pytest-xdist workers are not allowed; use --workers (max {MAX_WORKERS}) instead")
    if (bad := _jobs_violation(args)) and mode == "run_tests":
        raise ValueError(f"{bad!r}: pass parallelism as --workers, not to run_tests.sh")
    if mode == "run_tests":
        return [str(REPO_ROOT / "scripts" / "run_tests.sh"), "-j", str(workers), *args]
    if mode == "pytest":
        if workers != 1:
            raise ValueError("pytest mode runs one interpreter; use run_tests mode for --workers > 1")
        return [python, "-m", "pytest", "-p", "no:xdist", *args]
    # `-p no:xdist` makes an `-n` from the project's addopts fail loudly instead of fanning out.
    return args if at is None else [*args[:at + 1], "-p", "no:xdist", *args[at + 1:]]


def child_env(run_id: str, workers: int) -> dict:
    env = dict(os.environ)
    if xdist_violation(shlex.split(env.get("PYTEST_ADDOPTS", ""))):
        raise ValueError("PYTEST_ADDOPTS enables pytest-xdist workers; unset it")
    env[TAG_ENV] = run_id
    env["HERMES_TEST_WORKERS"] = str(workers)
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = "1"
    return env


# --- Process ownership ------------------------------------------------------------------

def _identity(proc: psutil.Process) -> tuple[int, float] | None:
    try:
        return proc.pid, proc.create_time()
    except psutil.Error:
        return None


def _same_process(pid: int, ctime: float) -> psutil.Process | None:
    """The live process *pid* only if it is still the one first seen at *ctime*."""
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - ctime) < 0.01 and proc.status() != psutil.STATUS_ZOMBIE:
            return proc
    except psutil.Error:
        pass
    return None


@dataclass
class Tracker:
    """Everything this run spawned: pid -> create time, collected while the run is alive."""
    run_id: str
    root_pid: int
    root_ctime: float
    exclude: frozenset = frozenset()
    known: dict = field(default_factory=dict)

    def note(self, procs) -> list[tuple[int, float]]:
        fresh = []
        for proc in procs:
            ident = _identity(proc)
            if ident and ident[0] not in self.exclude and self.known.get(ident[0]) != ident[1]:
                self.known[ident[0]] = ident[1]
                fresh.append(ident)
        return fresh

    def scan_tree(self) -> list[tuple[int, float]]:
        procs = []
        for proc in self.alive():
            with contextlib.suppress(psutil.Error):
                procs.extend(proc.children(recursive=True))
        return self.note(procs)

    def _group_pinned(self) -> bool:
        """True while a process we already own is in the run's process group. A pgid cannot
        be reused while any member lives, so only then does membership prove ownership."""
        for proc in self.alive():
            with contextlib.suppress(OSError):
                if os.getpgid(proc.pid) == self.root_pid:
                    return True
        return False

    def scan_host(self) -> list[tuple[int, float]]:
        """Tagged processes and members of the run's process group anywhere on the host —
        catches escapees that reparented to launchd before the tree scan saw them."""
        uid = os.getuid()
        group_ok = self._group_pinned()
        hits = []
        for proc in psutil.process_iter(["uids", "create_time"]):
            try:
                if proc.info["uids"] is None or proc.info["uids"].real != uid or proc.pid in self.exclude:
                    continue
                in_group = group_ok and os.getpgid(proc.pid) == self.root_pid and proc.info["create_time"] >= self.root_ctime
                if in_group or proc.environ().get(TAG_ENV) == self.run_id:
                    hits.append(proc)
            except (psutil.Error, OSError):
                continue
        return self.note(hits)

    def alive(self) -> list[psutil.Process]:
        """Live owned processes; dead entries are dropped (a recycled pid fails the ctime check)."""
        live = []
        for pid, ctime in list(self.known.items()):
            if (proc := _same_process(pid, ctime)) is None:
                del self.known[pid]
            else:
                live.append(proc)
        return live

    def footprint(self) -> int:
        return sum(memory_footprint(proc) for proc in self.alive())


def _libproc():
    if sys.platform != "darwin":
        return None
    try:
        return ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        return None


_LIBPROC = _libproc()


def memory_footprint(proc: psutil.Process) -> int:
    """macOS ``phys_footprint`` (resident + compressed + swapped), else RSS. Compressed
    pages leave RSS, so RSS alone under-reports exactly when the host is under pressure."""
    if _LIBPROC is not None:
        buf = (ctypes.c_uint64 * 32)()  # rusage_info_v0: uuid (2 words), then 7 counters, phys_footprint
        if _LIBPROC.proc_pid_rusage(proc.pid, 0, buf) == 0:
            return int(buf[9])
    try:
        return proc.memory_info().rss
    except psutil.Error:
        return 0


def terminate(procs: list[psutil.Process], grace_s: float) -> int:
    """TERM, wait *grace_s*, KILL survivors. Returns how many were signalled."""
    procs = [p for p in procs if p.is_running()]
    for proc in procs:
        with contextlib.suppress(psutil.Error):
            proc.terminate()
    _, survivors = psutil.wait_procs(procs, timeout=grace_s)
    for proc in survivors:
        with contextlib.suppress(psutil.Error):
            proc.kill()
    psutil.wait_procs(survivors, timeout=2.0)
    return len(procs)


def reap_run(tracker: Tracker, grace_s: float) -> int:
    """Kill every live process of the run, rescanning until nothing new appears (a dying
    parent can fork once more between the scan and the signal)."""
    killed = 0
    for _ in range(4):
        tracker.scan_tree()
        tracker.scan_host()
        targets = tracker.alive()
        if not targets:
            break
        killed += terminate(targets, grace_s)
    return killed


# --- Host health ------------------------------------------------------------------------

def darwin_pressure_level() -> int | None:
    """kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical."""
    if sys.platform != "darwin":
        return None
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    libc = ctypes.CDLL(None)
    if libc.sysctlbyname(b"kern.memorystatus_vm_pressure_level", ctypes.byref(value), ctypes.byref(size), None, 0):
        return None
    return value.value


def ancestors() -> list[tuple[int, float]]:
    """The launching chain (excluding launchd/init). If any of it dies, nobody is left to
    collect this run's result, so the run is abandoned and must be cleaned up."""
    out = []
    for proc in psutil.Process().parents():
        if proc.pid > 1 and (ident := _identity(proc)):
            out.append(ident)
    return out


# --- Child spawn ------------------------------------------------------------------------

def limited_argv(limits: Limits, argv: list[str]) -> list[str]:
    """*argv* behind the exec trampoline that applies the per-process limits (same pid)."""
    headroom = limits.vm_headroom or 0
    return [sys.executable, os.path.abspath(__file__), "--_exec-limited", str(headroom), str(limits.cpu_s), *argv]


def _lower_limit(name: str, soft: int, hard: int | None = None) -> None:
    """Set a limit without ever raising it: an enclosing run may already have lowered the
    hard limit, and an unprivileged process cannot raise it back."""
    import resource
    res = getattr(resource, name)
    _, cur_hard = resource.getrlimit(res)
    hard = soft if hard is None else hard
    if cur_hard != resource.RLIM_INFINITY:
        hard = min(hard, cur_hard)
    resource.setrlimit(res, (min(soft, hard), hard))


def exec_limited(headroom: int, cpu_s: int, argv: list[str]) -> None:
    """Apply limits inherited by every descendant, then exec *argv*. RLIMIT_AS is this
    process's address-space size plus *headroom*: macOS refuses a limit below the current
    size, and every process starts with ~33 GiB reserved."""
    if headroom:
        try:
            _lower_limit("RLIMIT_AS", psutil.Process().memory_info().vms + headroom)
        except (ValueError, OSError) as exc:
            _say(f"warning: per-process VM cap not applied: {exc}")
    _lower_limit("RLIMIT_CPU", cpu_s, cpu_s + 5)
    _lower_limit("RLIMIT_CORE", 0)
    os.nice(10)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        _say(f"could not start {argv[0]!r}: {exc}")
        os._exit(127)


# --- Watchdog ---------------------------------------------------------------------------

class Watchdog:
    """Supervisor side of the watchdog pipe."""

    def __init__(self, lock_fd: int):
        self.proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--_watchdog"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=None,
            start_new_session=True, pass_fds=(lock_fd,), env={k: v for k, v in os.environ.items() if k != TAG_ENV})

    def send(self, **msg) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()

    def finish(self) -> None:
        self.send(done=True)
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=5)


def run_watchdog() -> int:
    """Wait for the supervisor's "done"; on EOF without it, or past the hard deadline, reap
    the run (and a hung supervisor). Ignores terminal hangup/interrupt: it outlives the
    supervisor's session on purpose."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    tracker, supervisor, deadline, grace, buf = None, None, None, 3.0, b""
    fd = sys.stdin.fileno()
    while True:
        wait = 1.0 if deadline is None else max(0.0, min(1.0, deadline - time.time()))
        ready, _, _ = select.select([fd], [], [], wait)
        if deadline is not None and time.time() >= deadline:
            reason = "hard deadline passed"
            break
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            reason = "supervisor died"
            break
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            msg = json.loads(line)
            if msg.get("done"):
                return 0
            if "run_id" in msg:
                supervisor = tuple(msg["supervisor"])
                tracker = Tracker(msg["run_id"], msg["root"][0], msg["root"][1],
                                  exclude=frozenset({os.getpid(), supervisor[0]}))
                deadline, grace = msg["deadline"], msg["grace"]
            for pid, ctime in msg.get("track", ()):
                if tracker is not None:
                    tracker.known[pid] = ctime
    if tracker is None:
        return 0
    if reason == "hard deadline passed" and (sup := _same_process(*supervisor)) is not None:
        terminate([sup], grace)
    killed = reap_run(tracker, grace)
    _say(f"watchdog: {reason}; reaped {killed} process(es) of run {tracker.run_id[:8]}")
    return 0


# --- Supervisor -------------------------------------------------------------------------

def _install_signal_handlers(state: dict) -> None:
    def handler(signum, _frame):
        state["cancelled"] = signal.Signals(signum).name
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handler)


def _breach(tracker: Tracker, limits: Limits, started: float, state: dict, chain, host_due: bool) -> tuple[int, str] | None:
    if state.get("cancelled"):
        return EXIT_CANCELLED, f"cancelled by {state['cancelled']}"
    if time.monotonic() - started > limits.timeout_s:
        return EXIT_TIMEOUT, f"timeout after {limits.timeout_s:.0f}s"
    used = tracker.footprint()
    state["peak"] = max(state.get("peak", 0), used)
    if used > limits.max_memory:
        return EXIT_MEMORY, f"memory budget exceeded: {used / _MIB:.0f} MiB > {limits.max_memory / _MIB:.0f} MiB (whole run)"
    if not host_due:
        return None
    available = psutil.virtual_memory().available
    if available < limits.min_available:
        return EXIT_MEMORY, f"host available memory {available / _MIB:.0f} MiB < floor {limits.min_available / _MIB:.0f} MiB"
    if (level := darwin_pressure_level()) is not None and level >= _DARWIN_PRESSURE_CRITICAL:
        return EXIT_MEMORY, "macOS memory pressure is critical"
    if any(_same_process(pid, ct) is None for pid, ct in chain):
        return EXIT_ORPHANED, "launching process exited (agent disconnected); abandoning run"
    return None


def supervise(argv: list[str], env: dict, limits: Limits, lock_fd: int, run_id: str) -> int:
    state: dict = {}
    _install_signal_handlers(state)
    chain = ancestors()
    watchdog = Watchdog(lock_fd)
    child = subprocess.Popen(limited_argv(limits, argv), env=env, start_new_session=True, pass_fds=(lock_fd,))
    started = time.monotonic()
    root = _identity(psutil.Process(child.pid)) or (child.pid, time.time())
    tracker = Tracker(run_id, root[0], root[1], exclude=frozenset({os.getpid(), watchdog.proc.pid}))
    tracker.known[root[0]] = root[1]
    watchdog.send(run_id=run_id, supervisor=_identity(psutil.Process()), root=root,
                  deadline=time.time() + limits.timeout_s + 4 * limits.grace_s + 30, grace=limits.grace_s)
    vm_note = f", per-process VM headroom {limits.vm_headroom / _GIB:.1f} GiB" if limits.vm_headroom else ""
    _say(f"run {run_id[:8]} pid {child.pid}: timeout {limits.timeout_s:.0f}s, memory budget "
         f"{limits.max_memory / _MIB:.0f} MiB{vm_note}")
    verdict, last_tag_scan, last_host = None, 0.0, 0.0
    while (rc := child.poll()) is None:
        now = time.monotonic()
        fresh = tracker.scan_tree()
        if now - last_tag_scan >= _TAG_SCAN_EVERY_S:
            fresh += tracker.scan_host()
            last_tag_scan = now
        if fresh:
            watchdog.send(track=fresh)
        host_due = now - last_host >= _HOST_CHECK_EVERY_S
        last_host = now if host_due else last_host
        if verdict := _breach(tracker, limits, started, state, chain, host_due):
            break
        time.sleep(_POLL_S)
    killed = reap_run(tracker, limits.grace_s)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=limits.grace_s + 2)
    watchdog.finish()
    peak = f"peak run memory {state.get('peak', 0) / _MIB:.0f} MiB"
    if verdict:
        code, why = verdict
        _say(f"KILLED: {why}; reaped {killed} process(es); {peak}")
        return code
    leftover = f"; reaped {killed} leftover background process(es)" if killed else ""
    _say(f"finished rc={rc} in {time.monotonic() - started:.0f}s; {peak}{leftover}")
    return rc if rc >= 0 else 128 - rc


# --- CLI --------------------------------------------------------------------------------

def _parser(defaults: Limits) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="safe-test", description=__doc__.split("\n\n")[0],
        epilog="Examples:\n  scripts/safe-test tests/tools/test_x.py -q\n"
               "  scripts/safe-test --mode pytest tests/tools/test_x.py -k name\n"
               "  scripts/safe-test --mode exec --timeout 600 -- npm test\n"
               "  scripts/safe-test --status",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=("run_tests", "pytest", "exec"), default="run_tests",
                   help="run_tests: scripts/run_tests.sh -j WORKERS (default); pytest: PYTHON -m pytest; exec: any command")
    p.add_argument("--workers", type=int, default=1, help=f"parallel test workers, 1..{MAX_WORKERS} (default 1)")
    p.add_argument("--timeout", type=float, default=defaults.timeout_s, help="wall-clock seconds (default %(default)s)")
    p.add_argument("--max-memory-mb", type=int, default=defaults.max_memory // _MIB,
                   help="memory budget for the whole run, all processes summed (default %(default)s)")
    p.add_argument("--min-available-mb", type=int, default=defaults.min_available // _MIB,
                   help="kill the run when host available memory falls below (default %(default)s)")
    p.add_argument("--vm-headroom-mb", type=int, default=defaults.vm_headroom // _MIB,
                   help="per-process RLIMIT_AS above the spawn baseline; 0 disables (default %(default)s)")
    p.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS",
                   help="wait this long for another managed run to finish instead of refusing (default 0)")
    p.add_argument("--python", default=os.environ.get("HERMES_PYTHON") or sys.executable,
                   help="interpreter for --mode pytest")
    p.add_argument("--status", action="store_true", help="show the active managed run, if any, and exit")
    return p


_OWN_VALUE_OPTS = frozenset({"--mode", "--workers", "--timeout", "--max-memory-mb", "--min-available-mb",
                             "--vm-headroom-mb", "--wait-lock", "--python"})
_OWN_FLAGS = frozenset({"--status", "-h", "--help"})


def split_own_args(raw: list[str]) -> tuple[list[str], list[str]]:
    """(runner options, passthrough). Runner options come first; the first other token (a
    test path or a pytest flag like ``-q``) or ``--`` starts the passthrough."""
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok == "--":
            return raw[:i], raw[i + 1:]
        if tok in _OWN_FLAGS or tok.split("=", 1)[0] in _OWN_VALUE_OPTS and "=" in tok:
            i += 1
        elif tok in _OWN_VALUE_OPTS:
            i += 2
        else:
            break
    return raw[:i], raw[i:]


def _status(directory: Path) -> int:
    try:
        fd = acquire_lock(directory, 0)
    except LockBusy as busy:
        print(json.dumps({"active": True, **busy.holder}, indent=2))
        return 0
    os.close(fd)
    print(json.dumps({"active": False}))
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw[:1] == ["--_watchdog"]:
        return run_watchdog()
    if raw[:1] == ["--_exec-limited"]:
        exec_limited(int(raw[1]), int(raw[2]), raw[3:])
    defaults = default_limits()
    own, args = split_own_args(raw)
    opts = _parser(defaults).parse_args(own)
    if os.environ.get(TAG_ENV):
        _say("refused: already inside a managed run (nested safe-test would deadlock on the run lock)")
        return EXIT_POLICY
    directory = lock_dir()
    if opts.status:
        return _status(directory)
    run_id = uuid.uuid4().hex
    try:
        command = build_command(opts.mode, args, opts.workers, opts.python)
        env = child_env(run_id, opts.workers)
    except ValueError as exc:
        _say(f"refused: {exc}")
        return EXIT_POLICY
    limits = Limits(
        timeout_s=opts.timeout, max_memory=opts.max_memory_mb * _MIB, min_available=opts.min_available_mb * _MIB,
        vm_headroom=opts.vm_headroom_mb * _MIB or None, cpu_s=int(opts.timeout) + 60, grace_s=defaults.grace_s)
    try:
        lock_fd = acquire_lock(directory, opts.wait_lock)
    except LockBusy as busy:
        h = busy.holder
        _say(f"refused: another managed test run is active (pid {h.get('pid', '?')}, started "
             f"{h.get('started', '?')}, cwd {h.get('cwd', '?')}): {h.get('command', '?')}. Wait for it "
             "(poll `scripts/safe-test --status`) or pass --wait-lock SECONDS. Do not start tests another way.")
        return EXIT_LOCK_BUSY
    write_holder(directory, {"pid": os.getpid(), "run_id": run_id, "cwd": os.getcwd(),
                             "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "command": shlex.join(command)})
    return supervise(command, env, limits, lock_fd, run_id)


if __name__ == "__main__":
    sys.exit(main())
