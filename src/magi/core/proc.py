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
