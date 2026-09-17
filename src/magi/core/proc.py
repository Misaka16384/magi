"""Run a program with a deadline that reaches everything it started.

`subprocess.run(timeout=...)` kills the process it launched and nothing below
it. For a program that is itself a launcher — a TeX distribution's `pdflatex`
is a stub that starts the engine on some installs, MiKTeX's always is — the
engine carries on after the timeout "fired", holding the CPU and the temporary
directory the caller is about to delete. So the child is started in its own
process group and the whole group is ended.

Measured on the case that made this necessary: `magi ingest review --commit`
sat for twenty minutes on a pdflatex that had entered an endless loop, with no
error raised inside it for `-interaction=nonstopmode` to stop on.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field


def kill_tree(proc: subprocess.Popen) -> None:
    """End *proc* and every process it started. Never raises."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — it may already be gone
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=15)
    except Exception:  # noqa: BLE001
        pass


def run_with_deadline(cmd: list[str], *, cwd=None, timeout: float) -> int | None:
    """Run *cmd* with its output discarded. The exit code, or None on timeout.

    stdin is closed rather than inherited: a program that stops to ask a
    question in the middle of a batch is a hang with extra steps.
    """
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
             if os.name == "nt" else {"start_new_session": True})
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **group)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        return None


# ------------------------------------------------------------- containment
#
# A reviewer CLI is a program that starts programs: asked to check a claim, it
# writes a script and runs it. Two things went wrong on the day this was added
# (2026-09-17, `magi review --host antigravity`). The script was an endless
# search that grew to 18.9 GB of committed memory and took the desktop down with
# it; and it outlived the review — the host's own kill answered "context
# canceled" and killed nothing, the review ended normally, the ledger was
# written, and the process was still there. `kill_tree` above would not have
# found it either: `taskkill /T` walks parent links, and a grandchild whose
# parent has already exited has none left to walk.
#
# So the host runs inside something that does not depend on parent links. On
# Windows that is a Job Object: every process the host starts is born into it,
# the job's committed memory has a ceiling, and closing the job ends whatever
# is still in it. Elsewhere it is a session (process group), which gives the
# sweep but not the ceiling — a per-process `RLIMIT_AS` is not a tree-wide
# limit, and the runtimes these CLIs are built on reserve more address space
# than any sane value of it, so setting one breaks the host instead of the
# runaway. That gap is stated in `Finished.capped`, not hidden.

@dataclass
class Finished:
    returncode: int
    stdout: str
    stderr: str
    #: Processes still alive under the command when it returned. All of them
    #: have been ended by the time the caller reads this.
    leftover: list = field(default_factory=list)
    #: Whether the memory ceiling was really in force.
    capped: bool = False


def run_contained(argv: list, *, cwd=None, timeout: float | None = None,
                  memory_mb: int = 0) -> Finished:
    """Run *argv* to the end with its output captured, inside a container.

    Nothing the command started survives this call, however it returns: on a
    normal exit the stragglers are counted and then ended, on a timeout the
    whole container is ended and `subprocess.TimeoutExpired` is raised as
    `subprocess.run` would have.

    stdin is closed. Every host takes its prompt as an argument, and at least
    one reads stdin *as well* whenever it is not a terminal: `codex exec` says
    "Reading additional input from stdin..." and waits. Inherited from an
    agent's shell, stdin is a pipe that never closes, so the review sat there
    until its timeout — measured 2026-09-17 while cutting v2.8.0, and true of
    the plain `subprocess.run` this replaced as well.
    """
    if os.name == "nt":
        return _run_in_job(argv, cwd, timeout, memory_mb)
    return _run_in_session(argv, cwd, timeout)


_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


class _Output:
    """Both pipes, read on threads, so that waiting is for the *process*.

    `communicate()` waits for end-of-file, and a straggler that inherited the
    pipe keeps it open: the command has exited, its answer is sitting in the
    buffer, and the caller waits out the whole timeout for a process it is about
    to end anyway. Wait for the exit, end the stragglers, and the pipes close.
    """

    def __init__(self, proc):
        self._parts = {"out": [], "err": []}
        self._threads = [
            threading.Thread(target=self._read, args=(proc.stdout, "out"), daemon=True),
            threading.Thread(target=self._read, args=(proc.stderr, "err"), daemon=True)]
        for thread in self._threads:
            thread.start()

    def _read(self, pipe, key) -> None:
        try:
            self._parts[key].append(pipe.read())
        except Exception:  # noqa: BLE001 — a closed pipe is the end of the output
            pass
        finally:
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass

    def finish(self, wait: float = 10.0) -> tuple:
        for thread in self._threads:
            thread.join(wait)
        return "".join(self._parts["out"]), "".join(self._parts["err"])


def _run_in_session(argv, cwd, timeout) -> Finished:
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, **_TEXT)
    output = _Output(proc)
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = None
    try:
        proc.wait(timeout=timeout)
    except BaseException:
        _end_group(pgid)
        kill_tree(proc)
        output.finish(2.0)
        raise
    leftover = [pid for pid in _group_members(pgid) if pid != proc.pid]
    _end_group(pgid)
    out, err = output.finish()
    return Finished(proc.returncode, out, err, leftover, capped=False)


def _group_members(pgid) -> list:
    if pgid is None:
        return []
    try:
        listed = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True,
                                text=True, timeout=10)
        return [int(tok) for tok in listed.stdout.split() if tok.isdigit()]
    except Exception:  # noqa: BLE001 — no pgrep: ask the kernel instead
        try:
            os.killpg(pgid, 0)
        except OSError:
            return []
        return [pgid]


def _end_group(pgid) -> None:
    if pgid is None:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def _run_in_job(argv, cwd, timeout, memory_mb) -> Finished:
    job = _Job.create(memory_mb)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    # Born suspended, so that it is inside the job before it can start
    # anything: assigning after the fact leaves a window in which a child is
    # created outside, and the one that escaped is the one this is for.
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            creationflags=flags | (_CREATE_SUSPENDED if job else 0),
                            **_TEXT)
    if job is not None:
        held = job.take(proc)
        if not _resume(proc):
            kill_tree(proc)
            job.close()
            raise OSError("could not resume the process after placing it in a job")
        if not held:
            job.close()
            job = None
    output = _Output(proc)
    try:
        proc.wait(timeout=timeout)
    except BaseException:
        if job is not None:
            job.close()
        kill_tree(proc)
        output.finish(2.0)
        raise
    if job is None:
        kill_tree(proc)
        out, err = output.finish()
        return Finished(proc.returncode, out, err, [], capped=False)
    leftover = [pid for pid in job.members() if pid != proc.pid]
    capped = job.capped
    job.close()
    out, err = output.finish()
    return Finished(proc.returncode, out, err, leftover, capped=capped)


_CREATE_SUSPENDED = 0x00000004
_JOB_LIMIT_JOB_MEMORY = 0x00000200
_JOB_LIMIT_KILL_ON_CLOSE = 0x00002000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_JOB_BASIC_PROCESS_ID_LIST = 3
_MOST_PIDS = 1024


def _resume(proc) -> bool:
    """Let a process created suspended run. False if Windows would not."""
    try:
        import ctypes
        from ctypes import wintypes
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        return resume(int(proc._handle)) == 0
    except Exception:  # noqa: BLE001
        return False


class _Job:
    """A Windows Job Object: a ceiling on the tree, and one handle that ends it."""

    def __init__(self, kernel32, handle, capped: bool):
        self._k = kernel32
        self._handle = handle
        self.capped = capped

    @classmethod
    def create(cls, memory_mb: int):
        """A job, or None where one cannot be made — the caller runs bare."""
        try:
            import ctypes
            from ctypes import wintypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD]
            k.SetInformationJobObject.restype = wintypes.BOOL
            k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.c_void_p]
            k.QueryInformationJobObject.restype = wintypes.BOOL
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.AssignProcessToJobObject.restype = wintypes.BOOL
            k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.TerminateJobObject.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            k.CloseHandle.restype = wintypes.BOOL

            handle = k.CreateJobObjectW(None, None)
            if not handle:
                return None
            limits = _extended_limits(ctypes, wintypes)()
            limits.BasicLimitInformation.LimitFlags = _JOB_LIMIT_KILL_ON_CLOSE
            capped = memory_mb > 0
            if capped:
                limits.BasicLimitInformation.LimitFlags |= _JOB_LIMIT_JOB_MEMORY
                limits.JobMemoryLimit = int(memory_mb) * 1024 * 1024
            if not k.SetInformationJobObject(handle, _JOB_EXTENDED_LIMIT_INFORMATION,
                                             ctypes.byref(limits), ctypes.sizeof(limits)):
                k.CloseHandle(handle)
                return None
            return cls(k, handle, capped)
        except Exception:  # noqa: BLE001 — no job is a weaker run, not a failed one
            return None

    def take(self, proc) -> bool:
        return bool(self._k.AssignProcessToJobObject(self._handle, int(proc._handle)))

    def members(self) -> list:
        """PIDs alive in the job right now."""
        import ctypes
        from ctypes import wintypes

        class _PidList(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
                        ("pids", ctypes.c_size_t * _MOST_PIDS)]

        found = _PidList()
        if not self._k.QueryInformationJobObject(self._handle, _JOB_BASIC_PROCESS_ID_LIST,
                                                 ctypes.byref(found), ctypes.sizeof(found),
                                                 None):
            return []
        return [int(found.pids[i]) for i in range(min(found.listed, _MOST_PIDS))]

    def close(self) -> None:
        """End everything still inside, then let go of the job. Never raises."""
        if self._handle is None:
            return
        try:
            self._k.TerminateJobObject(self._handle, 1)
            self._k.CloseHandle(self._handle)
        except Exception:  # noqa: BLE001
            pass
        self._handle = None


def _extended_limits(ctypes, wintypes):
    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    return _ExtendedLimits
