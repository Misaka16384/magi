"""A reviewer is a program that starts programs, and none of them may outlive it.

2026-09-17, `magi review --host antigravity`: the reviewer wrote an endless
search and ran it. It reached 18.9 GB of committed memory and took the desktop
down; the host's own kill killed nothing; the review ended normally, the
ledger was written, and the process was still there. Three separate holes —
no ceiling, a timeout that ended only the host, and a normal return that ended
nothing — and each has a test here that was seen to fail with the hole open.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
import time

import pytest

from magi import review
from magi.core import proc
from magi.kb import threads

#: A stand-in reviewer CLI. It starts a middle process, the middle starts a
#: sleeper and exits — so the sleeper has no living parent, which is exactly
#: what `taskkill /T` and a parent-walk cannot find — then answers or hangs.
HOST = textwrap.dedent('''
    import os, subprocess, sys
    if sys.argv[1] == "middle":
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
        with open(sys.argv[2], "w") as fh:
            fh.write(str(sleeper.pid))
        sys.exit(0)
    subprocess.Popen([sys.executable, __file__, "middle", sys.argv[2]]).wait()
    if sys.argv[1] == "hang":
        import time
        time.sleep(90)
    print("VERDICT: stands")
    print("REASON: it holds.")
''')


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def ws_with_claim(tmp_path, monkeypatch):
    from magi.core import vocab

    (tmp_path / "threads").mkdir()
    path = threads.create(tmp_path / "threads" / "p-gap.md", vocab.PROPOSITION,
                          "The gap survives", "Decide before a month of numerics.")
    threads.set_status(path, "testing", "started", host="claude")
    threads.set_status(path, "supported", "converged", host="claude")
    monkeypatch.setattr(review, "installed_hosts", lambda *_a, **_k: ["claude", "codex"])
    return tmp_path, "p-gap"


@pytest.fixture
def host(tmp_path):
    script = tmp_path / "fake_host.py"
    script.write_text(HOST, encoding="utf-8")
    pidfile = tmp_path / "sleeper.pid"
    yield script, pidfile
    if pidfile.exists():      # never leave a sleeper behind a failed assertion
        pid = int(pidfile.read_text())
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_what_the_reviewer_left_running_is_reported_and_ended(host):
    script, pidfile = host
    started = time.monotonic()
    done = proc.run_contained([sys.executable, str(script), "answer", str(pidfile)],
                              timeout=60, memory_mb=4096)
    assert done.returncode == 0 and "VERDICT: stands" in done.stdout
    orphan = int(pidfile.read_text())
    assert orphan in done.leftover, "the straggler is named, not only ended"
    assert _gone(orphan), "and nothing the host started outlives the call"
    # The sleeper inherited the pipe. Waiting for end-of-file would have sat
    # here for the sleeper's ninety seconds with the answer already printed.
    assert time.monotonic() - started < 30


def test_a_timeout_ends_the_whole_tree_not_only_the_host(host):
    script, pidfile = host
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run_contained([sys.executable, str(script), "hang", str(pidfile)],
                           timeout=6, memory_mb=4096)
    assert _gone(int(pidfile.read_text()))


@pytest.mark.skipif(os.name != "nt", reason="the ceiling is a Windows Job Object; "
                    "elsewhere `capped` is False and says so")
def test_the_tree_cannot_commit_more_than_its_ceiling():
    hog = "b = bytearray(1200 * 1024 * 1024); print('allocated')"
    capped = proc.run_contained([sys.executable, "-c", hog], timeout=60, memory_mb=300)
    assert capped.capped
    assert "allocated" not in capped.stdout and "MemoryError" in capped.stderr
    free = proc.run_contained([sys.executable, "-c", "print('ok')"], timeout=60, memory_mb=0)
    assert not free.capped and "ok" in free.stdout


def test_ask_runs_the_host_contained_and_keeps_what_it_left(monkeypatch, tmp_path):
    """Through `ask`: the helper being right proves nothing about `ask` using it."""
    seen = {}

    def fake(argv, cwd, timeout, memory_mb):
        seen.update(argv=argv, memory_mb=memory_mb)
        return proc.Finished(0, "VERDICT: stands\nREASON: ok", "", leftover=[4242],
                             capped=True)

    monkeypatch.setattr(review.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(review, "_run_host", fake)
    reply = review.ask("claude", "Q", cwd=tmp_path, model="opus")
    assert isinstance(reply, str) and reply.startswith("VERDICT: stands")
    assert reply.leftover == [4242] and reply.capped
    assert seen["memory_mb"] == review.MEMORY_MB


def test_the_ceiling_comes_from_the_config_and_a_typo_is_not_zero(tmp_path, monkeypatch):
    seen = {}

    def fake(argv, cwd, timeout, memory_mb):
        seen["memory_mb"] = memory_mb
        return proc.Finished(0, "VERDICT: stands\nREASON: ok", "")

    monkeypatch.setattr(review.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(review, "_run_host", fake)
    review.ask("claude", "Q", cwd=tmp_path, settings=review.Settings(memory_mb=1024))
    assert seen["memory_mb"] == 1024
    assert review._memory_mb("lots") == review.MEMORY_MB
    assert review._memory_mb("") == review.MEMORY_MB
    assert review._memory_mb(0) == 0, "zero is how a person turns it off"


def test_the_post_says_the_reviewer_answered_with_work_still_running():
    """The secondary point of the same report: the verdict read "exact search
    confirms" and the search had not finished. The post is the record."""
    result = review.Verdict(slug="p", verdict=review.VERDICT_STANDS, reason="holds",
                            host="antigravity", leftover=[1, 2])
    note = review.leftover_note(result)
    assert "2 processes" in note and "may not have finished" in note


def test_a_verdict_with_stragglers_lands_in_the_note(ws_with_claim, monkeypatch):
    root, slug = ws_with_claim
    reply = review.Reply("VERDICT: stands\nREASON: it holds.")
    reply.leftover = [777]
    monkeypatch.setattr(review, "ask", lambda *a, **k: reply)
    verdict = review.review(root, slug, author="claude", host="codex")
    assert verdict.leftover == [777]
    review.apply_verdict(root, verdict)
    text = (root / "threads" / f"{slug}.md").read_text(encoding="utf-8")
    assert "Still running when it answered: 1 process " in text


def test_the_host_is_not_left_waiting_on_an_open_stdin(tmp_path):
    """`codex exec` reads stdin whenever it is not a terminal. Inherited from an
    agent's shell that is a pipe nobody closes, and the review hung to its
    timeout (2026-09-17, cutting v2.8.0).

    The situation is built rather than hoped for: whatever stdin pytest itself
    was given, the caller below runs with one that is open and never written
    to, and calls `run_contained` on a stand-in that reads stdin to the end."""
    reader = tmp_path / "reads_stdin.py"
    reader.write_text("import sys\nextra = sys.stdin.read()\nprint('VERDICT: stands', len(extra))\n",
                      encoding="utf-8")
    caller = tmp_path / "caller.py"
    caller.write_text(textwrap.dedent(f'''
        import subprocess, sys
        sys.path.insert(0, {str(Path(proc.__file__).parents[2])!r})
        from magi.core import proc
        try:
            done = proc.run_contained([sys.executable, {str(reader)!r}], timeout=8, memory_mb=0)
            print(done.stdout.strip())
        except subprocess.TimeoutExpired:
            print("HUNG")
    '''), encoding="utf-8")
    # The caller's stdin is a pipe this test holds open and never writes to —
    # the shape of an agent's shell — until the caller has answered.
    held = subprocess.Popen([sys.executable, str(caller)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        out = held.stdout.read()
    finally:
        held.stdin.close()
        held.wait(timeout=30)
    assert "VERDICT: stands 0" in out, out
