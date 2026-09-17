"""Lecture notes on request, modes as knobs, and the correction that comes after
(design-auto A3 and A4).

Three small things with one idea behind them: nothing about how a run is
steered lives in an agent. What the person knows is a ledger they fill by
pressing one of two buttons; a mode is a handful of sentences stored in the
run note, so whoever picks the run up reads the same ones; and the steering
that no contract can hold in advance — "this step should have gone the other
way", said on reading the report — is recorded against the step, where the
slow loop can count it.
"""

from __future__ import annotations

import json

import pytest

from magi import familiar_cmd, run_cmd, state
from magi.core import familiar, vocab
from magi.kb import report, runs, thread_cmd, threads
from magi.reflect import signals

RUN = "run-gap"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for sub in ("threads", "output", "wiki/concepts", "drafts/lectures"):
        (tmp_path / sub).mkdir(parents=True)
    monkeypatch.setenv("MAGI_CONFIG_HOME", str(tmp_path / "home"))
    return tmp_path


def run(ws, *argv) -> int:
    return run_cmd.main([argv[0], "--project-dir", str(ws), *argv[1:]])


def note(ws):
    return threads.read_note(ws / "threads" / f"{RUN}.md")


def a_run_that_used_a_method(ws):
    (ws / "wiki" / "concepts" / "transfer-matrix.md").write_text("# Transfer matrix\n",
                                                                 encoding="utf-8")
    assert run(ws, "start", "--title", "Gap", "--slug", RUN, "--mode", "deep") == 0
    assert run(ws, "sign", RUN, "--steps", "6", "--anyway") == 0
    assert thread_cmd.main(["new", "p-top", "--kind", "proposition", "--title", "top",
                            "--claim", "The gap survives.", "--purpose", "t",
                            "--project-dir", str(ws)]) == 0
    threads.set_field(ws / "threads" / "p-top.md", "depends_on", ["[[Transfer matrix]]"],
                      host="claude")
    assert run(ws, "step", RUN, "--do", "bound it", "--if-true", "a", "--if-false", "b") == 0
    assert run(ws, "result", RUN, "1", "--text", "holds: [[p-top]]") == 0


# --------------------------------------------------------------------------
# two buttons
# --------------------------------------------------------------------------

def test_the_methods_of_a_run_are_the_concepts_its_claims_are_stated_in(ws, capsys):
    a_run_that_used_a_method(ws)
    capsys.readouterr()
    assert familiar_cmd.main(["list", "--project-dir", str(ws), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["runs"][0]["methods"]
    assert rows == [{"concept": "Transfer matrix", "key": "transfer-matrix", "state": None,
                     "card": True, "notes_ready": False, "used_in": ["p-top"]}]


def test_asking_for_notes_puts_them_on_the_list_until_they_are_written(ws):
    a_run_that_used_a_method(ws)
    assert not [a for a in state.candidates(state.load(ws)) if a.key == "lecture"]
    assert familiar_cmd.main(["notes", "Transfer matrix", "--project-dir", str(ws)]) == 0
    owed = [a for a in state.candidates(state.load(ws)) if a.key == "lecture"]
    assert len(owed) == 1 and "Transfer matrix" in owed[0].why and "p-top" in owed[0].why
    assert "drafts/lectures/transfer-matrix.md" in owed[0].run and owed[0].cost == "llm"

    (ws / "drafts" / "lectures" / "transfer-matrix.md").write_text("# notes\n", encoding="utf-8")
    assert not [a for a in state.candidates(state.load(ws)) if a.key == "lecture"]


def test_knowing_something_stops_it_counting_as_new(ws):
    a_run_that_used_a_method(ws)
    text = "### Terms\n\n- [[Transfer matrix]] — T.\n"
    assert report.reading_cost(ws, text)["new"] == 1
    assert familiar_cmd.main(["known", "[[Transfer matrix]]"]) == 0, "no project needed"
    assert report.reading_cost(ws, text) == {"terms": 1, "new": 0, "with_notes": 0}
    assert familiar_cmd.main(["forget", "transfer-matrix"]) == 0
    assert report.reading_cost(ws, text)["new"] == 1


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def test_a_mode_is_a_name_for_sentences_stored_in_the_note(ws, capsys):
    a_run_that_used_a_method(ws)
    knobs = note(ws).frontmatter["knobs"]
    assert knobs["mode"] == "deep" and knobs["open_questions"] == "no"
    assert run(ws, "status", RUN) == 0
    said = capsys.readouterr().out
    assert "How to explore (mode: deep):" in said
    assert "Open no new direction" in said and "a chain\n" not in said
    assert "most ambitious one" in said, "the fork rule came out of the replay experiment"


def test_explore_says_what_it_produces_is_conjectures():
    lines = " ".join(runs.instructions(runs.knobs_for("explore")))
    assert "map of conjectures" in lines and "Go wide" in lines
    assert "after 2 steps in a row" in lines


def test_one_knob_can_be_overridden_and_is_part_of_what_is_signed(ws):
    assert run(ws, "start", "--title", "Gap", "--slug", RUN) == 0
    assert run(ws, "sign", RUN, "--steps", "4", "--mode", "explore",
               "--knob", "fork_width=2", "--anyway") == 0
    now = note(ws)
    assert now.frontmatter["knobs"]["fork_width"] == "2" and runs.contract_intact(now)
    path = ws / "threads" / f"{RUN}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("open_questions: 'yes'",
                                                             "open_questions: 'no'")
                    .replace("open_questions: yes", "open_questions: no"), encoding="utf-8")
    assert not runs.contract_intact(note(ws)), "how to explore is part of the contract"


def test_a_knob_nobody_defined_is_shown_as_written():
    assert runs.instructions({"tone": "terse"}) == ["tone: terse"]


# --------------------------------------------------------------------------
# the correction that comes after
# --------------------------------------------------------------------------

def test_an_overturn_is_the_persons_and_lands_on_the_step(ws, monkeypatch):
    monkeypatch.setenv("MAGI_HOST", "claude")
    a_run_that_used_a_method(ws)
    assert run(ws, "overturn", RUN, "1", "--text", "should have tried the radical option") == 0
    now = note(ws)
    assert now.posts[-1].host == vocab.HUMAN and now.posts[-1].via == "claude"
    assert runs.ledger(now).get(1).overturned == "should have tried the radical option"
    assert run(ws, "overturn", RUN, "9", "--text", "x") == 1
    text = report.outline(ws, now, [threads.read_note(p) for p in threads.note_paths(ws)])
    assert "✗ the person, afterwards: should have tried the radical option" in text
    assert runs.contract_intact(note(ws)), "a correction is a post, not an edit"


def test_the_slow_loop_can_count_what_no_contract_held(ws):
    a_run_that_used_a_method(ws)
    assert run(ws, "overturn", RUN, "1", "--text", "the other branch") == 0
    assert run(ws, "amend", RUN, "--text", "I care about the ultraviolet, actually") == 0
    kinds = [s.kind for s in signals.collect(state.load(ws)) if s.slug == RUN]
    assert kinds == [signals.OVERTURN, signals.AMENDMENT]
    assert all(s.is_loss for s in signals.collect(state.load(ws)) if s.slug == RUN)
