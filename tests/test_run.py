"""`magi run` — an unattended exploration whose whole state is in the CLI.

docs/design-auto.md, A1. Two requirements from the author on 2026-09-17 are
what almost every test here is about:

*The state lives in the CLI, not in an agent.* When one vendor's quota runs
out, another opens the same folder and carries on. So the budget, the number
of things in flight and "what was the last session in the middle of" are all
counts over posts on the run note, enforced where every host must pass — in
`magi run step` — and readable cold with `magi run status`.

*Discussing and running are separate states.* Each verb belongs to one phase
and is refused in the other; signing freezes the contract, and the only way to
change a signed one is a person's `magi run amend`. The drift this guards
against is specific: success quietly redefined as whatever was found.
"""

from __future__ import annotations

import datetime as dt
import json
import threading

import pytest

from magi import hook_cmd, run_cmd, state
from magi.core import vocab
from magi.kb import runs, thread_cmd, threads

RUN = "run-gap"


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "threads").mkdir()
    (tmp_path / "output").mkdir()
    return tmp_path


def run(ws, *argv) -> int:
    return run_cmd.main([argv[0], "--project-dir", str(ws), *argv[1:]])


def note(ws):
    return threads.read_note(ws / "threads" / f"{RUN}.md")


def started(ws):
    assert run(ws, "start", "--title", "Does the gap survive", "--slug", RUN) == 0
    return ws


def signed(ws, steps=5, parallel=2, *extra):
    started(ws)
    assert run(ws, "sign", RUN, "--steps", str(steps), "--max-parallel", str(parallel),
               "--anyway", *extra) == 0
    return ws


def step(ws, do="compute the gap at L=8", *extra) -> int:
    return run(ws, "step", RUN, "--do", do, "--if-true", "go to L=16",
               "--if-false", "drop the direction", *extra)


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------

def test_both_phase_changes_are_a_persons_and_handing_in_is_not():
    assert vocab.is_human_only(vocab.RUN, "discussing", "running"), "signing"
    assert vocab.is_human_only(vocab.RUN, "running", "discussing"), "amending"
    assert vocab.is_human_only(vocab.RUN, "discussing", "dropped")
    assert not vocab.is_human_only(vocab.RUN, "running", "reported")
    assert not vocab.is_legal_transition(vocab.RUN, "discussing", "reported"), \
        "a run nobody signed has nothing to report"
    assert vocab.allowed_targets(vocab.RUN, "reported") == (vocab.CONFLICT,)


# --------------------------------------------------------------------------
# discussing
# --------------------------------------------------------------------------

def test_a_run_starts_in_discussion_with_an_empty_contract(ws):
    started(ws)
    fresh = note(ws)
    assert fresh.kind == vocab.RUN and fresh.status == runs.DISCUSSING
    assert [name for name, _ in runs.SECTIONS] == runs.unfilled(fresh)
    assert runs.phase_line(fresh).startswith("DISCUSSING")
    assert not threads.validate(fresh), "its own fields are known to the schema"


def test_nothing_registers_before_a_person_has_signed(ws, capsys):
    started(ws)
    assert step(ws) == 1
    assert "still being discussed" in capsys.readouterr().err
    assert not runs.ledger(note(ws)).steps


def test_the_generic_doors_into_a_run_are_shut(ws, capsys):
    """`run` in `vocab.KINDS` made `thread new --kind run` legal by itself, and
    `thread status <run> running` a run nobody signed, already running."""
    with pytest.raises(SystemExit):
        thread_cmd.main(["new", "sneaky", "--project-dir", str(ws), "--kind", "run",
                         "--title", "t", "--purpose", "p"])
    started(ws)
    assert thread_cmd.main(["status", RUN, "running", "--project-dir", str(ws),
                            "--text", "sneak"]) == 1
    assert "its status is its phase" in capsys.readouterr().err
    assert note(ws).status == runs.DISCUSSING


def test_signing_needs_the_one_number_only_the_person_can_say(ws, capsys):
    started(ws)
    assert run(ws, "sign", RUN, "--anyway") == 1
    assert "--steps N" in capsys.readouterr().err


def test_an_unwritten_contract_is_not_signed_by_accident(ws, capsys):
    started(ws)
    assert run(ws, "sign", RUN, "--steps", "5") == 1
    assert "empty sections" in capsys.readouterr().err
    assert note(ws).status == runs.DISCUSSING


def test_signing_freezes_the_contract_and_is_signed_as_the_person(ws, monkeypatch):
    monkeypatch.setenv("MAGI_HOST", "claude")
    signed(ws, 7, 3)
    now = note(ws)
    assert now.status == runs.RUNNING
    assert now.frontmatter["steps"] == 7 and now.frontmatter["max_parallel"] == 3
    assert now.frontmatter["signed"] == runs.fingerprint(now)
    signature = now.posts[-1]
    assert signature.host == vocab.HUMAN and signature.via == "claude"
    assert signature.dst == runs.RUNNING and signature.text.startswith("SIGNED ")


# --------------------------------------------------------------------------
# the four gates on a step
# --------------------------------------------------------------------------

def test_a_step_needs_both_outcomes_said(ws, capsys):
    signed(ws)
    assert run(ws, "step", RUN, "--do", "tidy the normalisation") == 1
    assert "not worth a step" in capsys.readouterr().err


def test_the_contract_cannot_be_moved_quietly(ws, capsys):
    """The drift named in the design: success redefined as what was found."""
    signed(ws)
    path = ws / "threads" / f"{RUN}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Stop when\n", "## Stop when\n\nwhatever we have by then\n"),
                    encoding="utf-8")
    assert step(ws) == 1
    said = capsys.readouterr().err
    assert "changed after it was signed" in said and "magi run amend" in said
    assert runs.phase_line(note(ws)).count("CONTRACT CHANGED") == 1


def test_the_budget_is_part_of_what_was_signed(ws, capsys):
    """A fingerprint over the prose alone would let the agent that may not
    touch the goals raise its own step count instead."""
    signed(ws, 2)
    path = ws / "threads" / f"{RUN}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("steps: 2", "steps: 200"),
                    encoding="utf-8")
    assert step(ws) == 1
    assert "changed after it was signed" in capsys.readouterr().err


def test_whitespace_and_line_endings_are_not_a_change_of_contract(ws):
    signed(ws)
    path = ws / "threads" / f"{RUN}.md"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(raw.replace(b"## Taste\n", b"## Taste   \n").replace(b"\n", b"\r\n"))
    assert runs.contract_intact(note(ws))
    assert step(ws) == 0


def test_the_steps_run_out(ws, capsys):
    signed(ws, 2)
    assert step(ws, "one") == 0 and run(ws, "result", RUN, "1", "--text", "held") == 0
    assert step(ws, "two") == 0 and run(ws, "result", RUN, "2", "--text", "held") == 0
    assert step(ws, "three") == 1
    assert "all 2 steps are used" in capsys.readouterr().err


def test_only_so_many_things_are_in_flight_at_once(ws, capsys):
    """The author's transcripts, three times: "I hit my usage limit — do not
    dispatch subagents in parallel"."""
    signed(ws, 9, 2)
    assert step(ws, "one") == 0 and step(ws, "two") == 0
    assert step(ws, "three") == 1
    assert "allows 2 at once" in capsys.readouterr().err
    assert run(ws, "result", RUN, "1", "--text", "done") == 0
    assert step(ws, "three") == 0


def test_no_new_step_after_the_moment_the_person_named(ws, capsys):
    signed(ws, 5, 2, "--until", "2020-01-01T08:00")
    assert step(ws) == 1
    assert "it is past 2020-01-01T08:00" in capsys.readouterr().err


def test_two_agents_registering_at_once_do_not_share_a_number_or_a_slot(ws):
    """The count and the write are under one lock. Read-decide-append done
    apart would make both of them step 1, and both "the first of one allowed"."""
    signed(ws, 50, 1)
    outcomes = []

    def go():
        outcomes.append(step(ws, "racing"))

    workers = [threading.Thread(target=go) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    book = runs.ledger(note(ws))
    assert outcomes.count(0) == 1 and len(book.steps) == 1 and book.steps[0].n == 1


# --------------------------------------------------------------------------
# amending, stopping, handing in
# --------------------------------------------------------------------------

def test_an_amendment_goes_back_to_discussion_and_only_a_signature_returns(ws, capsys):
    signed(ws)
    assert step(ws, "in flight") == 0
    assert run(ws, "amend", RUN, "--text", "drop direction B") == 0
    assert note(ws).status == runs.DISCUSSING
    assert note(ws).posts[-1].host == vocab.HUMAN
    assert step(ws, "new work") == 1, "no new step while it is being discussed"
    capsys.readouterr()
    assert run(ws, "result", RUN, "1", "--text", "finished anyway") == 0, \
        "work already authorised may still be closed"

    path = ws / "threads" / f"{RUN}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Directions\n", "## Directions\n\nonly direction A\n"), encoding="utf-8")
    assert run(ws, "sign", RUN, "--anyway") == 0, "the budget carries over unless restated"
    now = note(ws)
    assert now.status == runs.RUNNING and runs.contract_intact(now)
    assert len(runs.ledger(now).marks) == 2
    assert "re-signed after an amendment" in now.posts[-1].text
    assert step(ws, "after") == 0 and runs.ledger(note(ws)).used == 2


def test_a_stopped_run_takes_only_its_closing_steps_and_not_forever(ws, capsys):
    signed(ws, 9)
    assert run(ws, "step", RUN, "--closing", "--do", "write the report") == 1
    assert "still has steps" in capsys.readouterr().err

    assert run(ws, "stop", RUN, "--text", "enough") == 0
    assert note(ws).status == runs.RUNNING, "it still owes its report"
    assert step(ws) == 1
    assert "a person stopped it" in capsys.readouterr().err
    for _ in range(runs.CLOSING_STEPS):
        assert run(ws, "step", RUN, "--closing", "--do", "write the report") == 0
        assert run(ws, "result", RUN, str(len(runs.ledger(note(ws)).steps)), "--text", "ok") == 0
    assert run(ws, "step", RUN, "--closing", "--do", "one more") == 1
    assert "hand it in" in capsys.readouterr().err
    assert runs.ledger(note(ws)).used == 0, "closing steps are not exploration"


def test_stopping_a_discussion_drops_it(ws):
    started(ws)
    assert run(ws, "stop", RUN) == 0
    assert note(ws).status == runs.DROPPED
    assert not runs.live_runs([note(ws)])


def test_a_report_is_not_handed_in_over_open_work(ws, capsys):
    signed(ws)
    assert step(ws) == 0
    (ws / "drafts" / "runs").mkdir(parents=True)
    (ws / "drafts" / "runs" / "gap.md").write_text("# report\n", encoding="utf-8")
    assert run(ws, "report", RUN, "drafts/runs/gap.md") == 1
    assert "still open" in capsys.readouterr().err
    assert run(ws, "result", RUN, "1", "--abandoned", "--text", "quota ran out") == 0
    assert run(ws, "report", RUN, "drafts/runs/elsewhere.md") == 1
    # What the document must contain is tests/test_run_report.py's business.
    assert run(ws, "report", RUN, "drafts/runs/gap.md", "--anyway") == 0
    done = note(ws)
    assert done.status == runs.REPORTED and done.frontmatter["report"] == "drafts/runs/gap.md"
    assert step(ws) == 1


# --------------------------------------------------------------------------
# taking over cold
# --------------------------------------------------------------------------

def _disputed(ws):
    path = threads.create(ws / "threads" / "p-gap.md", vocab.PROPOSITION,
                          "The gap survives", "Decide before a month of numerics.")
    threads.set_status(path, "supported", "converged", host="claude")
    threads.set_status(path, "disputed", "VERDICT: refuted", host=vocab.REVIEWER)


def test_the_next_agent_learns_everything_from_the_note(ws, capsys):
    """The cold takeover: a session died in the middle of step 2. Whoever comes
    next — another vendor, no memory — is told the phase first, the half-done
    step before anything else, and is not sent to ask the person anything."""
    signed(ws, 5)
    assert step(ws, "first") == 0 and run(ws, "result", RUN, "1", "--text", "it held") == 0
    assert step(ws, "second, interrupted", "--host", "claude") == 0
    _disputed(ws)
    capsys.readouterr()

    assert run(ws, "status", RUN, "--json") == 0
    brief = json.loads(capsys.readouterr().out)["runs"][0]
    assert brief["steps"] == {"used": 2, "allowed": 5, "open": [2], "at_once": 2}
    assert brief["open_steps"][0]["do"] == "second, interrupted"
    assert brief["open_steps"][0]["host"] == "claude"
    assert brief["recent"][0]["result"] == "it held"

    loaded = state.load(ws)
    actions = state.candidates(loaded)
    assert actions[0].key == "step" and "second, interrupted" in actions[0].why
    assert not [a for a in actions if a.cost == "human"], "the person is away"
    assert [a.slug for a in loaded.parked] == ["p-gap"]
    text = state.render(loaded, actions)
    assert text.splitlines()[0].startswith("RUNNING · ")
    assert "Parked for the person until the run reports (1): p-gap" in text


def test_with_no_run_running_the_person_is_asked_as_before(ws):
    started(ws)
    _disputed(ws)
    loaded = state.load(ws)
    actions = state.candidates(loaded)
    assert actions[0].key == "discuss" and not loaded.parked
    assert [a.slug for a in actions if a.cost == "human"] == [RUN, "p-gap"]
    assert state.render(loaded, actions).splitlines()[0].startswith("DISCUSSING · ")


def test_a_run_on_no_line_does_not_invent_one(ws):
    """Seen in the takeover probe: the only note in the project was the run,
    and `magi next` listed an "(unlined)" line and parked a question for the
    person about a project that "has nothing open"."""
    signed(ws)
    loaded = state.load(ws)
    assert not loaded.lines
    state.candidates(loaded)
    assert not loaded.parked


def test_a_spent_run_is_told_the_one_thing_left(ws):
    signed(ws, 1)
    assert step(ws) == 0 and run(ws, "result", RUN, "1", "--text", "done") == 0
    keys = [a.key for a in state.candidates(state.load(ws))]
    assert keys[0] == "report" and "mentor" not in keys


def test_a_session_starting_cold_is_told_the_phase_first(ws):
    signed(ws)
    context = hook_cmd.session_start({}, root=ws)["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("RUNNING · run ")


# --------------------------------------------------------------------------
# the stop hook: a convenience, and not a loop
# --------------------------------------------------------------------------

def test_the_stop_hook_holds_a_run_that_is_moving_and_lets_go_of_one_that_is_not(ws):
    signed(ws)
    first = state.hook_payload(state.close(ws, hook=True))
    assert first["decision"] == "block" and "do not stop here" in first["reason"]
    assert state.hook_payload(state.close(ws, hook=True)) == {}, \
        "nothing registered or closed since it last spoke: stuck or done, either way let go"
    assert step(ws) == 0
    again = state.hook_payload(state.close(ws, hook=True), "antigravity")
    assert again["decision"] == "continue"


def test_the_stop_hook_does_not_hold_a_run_nobody_can_move(ws):
    signed(ws)
    path = ws / "threads" / f"{RUN}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Taste\n", "## Taste\n\nedited\n"), encoding="utf-8")
    assert state.hook_payload(state.close(ws, hook=True)) == {}


def test_a_live_run_is_not_unfinished_bookkeeping(ws):
    """`magi sync --close` typed by hand must not fail because a run exists."""
    signed(ws)
    report = state.close(ws)
    assert report.ok and report.keep_going == ""


def test_until_is_read_the_way_the_person_meant_it():
    assert runs.parse_until("2026-09-18") .hour == 23
    assert runs.parse_until("2026-09-18T08:00").tzinfo is not None
    assert runs.parse_until(dt.date(2026, 9, 18)).minute == 59
    assert runs.parse_until("tomorrow-ish") is None


def test_the_ledger_cannot_be_written_from_the_side(ws, capsys):
    """Found while writing A4: `thread post` was a second way to put a STEP on
    a run note — one that went through none of `run step`'s gates — and a post
    reading "SIGNED …" from an agent un-stopped a run a person had stopped."""
    signed(ws, 1)
    assert step(ws) == 0 and run(ws, "result", RUN, "1", "--text", "done") == 0
    for forged in ("STEP 2: keep going", "RESULT 1: rewritten", "SIGNED 0123456789abcdef"):
        assert thread_cmd.main(["post", RUN, "--project-dir", str(ws), "--text", forged]) == 1
    assert "part of run-gap's ledger" in capsys.readouterr().err
    assert thread_cmd.main(["post", RUN, "--project-dir", str(ws),
                            "--text", "The contract's direction B looks ill-posed: …"]) == 0

    assert run(ws, "stop", RUN) == 0
    threads.append_post(ws / "threads" / f"{RUN}.md", "SIGNED 0123456789abcdef",
                        host="claude")
    assert runs.ledger(note(ws)).stopped, "only a person's signature un-stops it"


def test_an_amended_run_tells_the_next_agent_what_changed_and_what_is_open(ws):
    """Seen in the end-to-end smoke: after `run amend` the first line read "not
    signed", as if nothing had happened, and the step left in flight was not
    mentioned at all."""
    signed(ws)
    assert step(ws, "in flight") == 0
    assert run(ws, "amend", RUN, "--text", "direction A is mod 24, not mod 8") == 0
    line = runs.phase_line(note(ws))
    assert "took the signed contract back" in line and "mod 24, not mod 8" in line
    actions = state.candidates(state.load(ws))
    assert [a.key for a in actions[:2]] == ["step", "discuss"]
    assert "Register nothing new" in actions[0].run
    assert run(ws, "sign", RUN, "--anyway") == 0
    assert "frozen" in runs.phase_line(note(ws)) and not runs.ledger(note(ws)).amended
