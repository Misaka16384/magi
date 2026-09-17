"""What a run rests on, and the one thing it hands to a person (design-auto A2).

Two halves. The first is `premises:` — which claims rest on which — because
with nobody watching, the expensive mistake is not a wrong claim but four more
built on top of it. The second is the report: a fixed shape, produced from
files so any agent writes the same skeleton, and *checked at the moment it is
handed in*, because a rule about what reaches a person's eyes is worth more as
a gate than as advice.
"""

from __future__ import annotations

import json
import re

import pytest

from magi import decide_cmd, review, run_cmd, state
from magi.core import familiar, vocab
from magi.kb import chain, report, runs, thread_cmd, threads

RUN = "run-gap"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for sub in ("threads", "output", "wiki/concepts", "drafts"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    monkeypatch.setenv("MAGI_CONFIG_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(review, "installed_hosts", lambda *_a, **_k: ["claude", "codex"])
    return tmp_path


def run(ws, *argv) -> int:
    return run_cmd.main([argv[0], "--project-dir", str(ws), *argv[1:]])


def thread(ws, *argv) -> int:
    return thread_cmd.main([argv[0], *argv[1:], "--project-dir", str(ws)])


def prop(ws, slug, claim, *extra):
    assert thread(ws, "new", slug, "--kind", "proposition", "--title", slug.replace("-", " "),
                  "--claim", claim, "--purpose", "test", *extra) == 0
    return ws / "threads" / f"{slug}.md"


def support(ws, slug, host="claude"):
    threads.set_status(ws / "threads" / f"{slug}.md", "supported", "derived", host=host)


def verdict(ws, slug, word="stands", tier="strong"):
    threads.append_post(ws / "threads" / f"{slug}.md",
                        f"VERDICT: {word}\n\nfine.\n\n(reviewed headless by codex · model x · "
                        f"{tier} tier)", host=vocab.REVIEWER)


def notes(ws):
    return [threads.read_note(p) for p in threads.note_paths(ws)]


def signed(ws, steps=6):
    assert run(ws, "start", "--title", "Does the gap survive", "--slug", RUN) == 0
    path = ws / "threads" / f"{RUN}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Motivation\n", "## Motivation\n\nDecide before a month of numerics.\n"),
        encoding="utf-8")
    assert run(ws, "sign", RUN, "--steps", str(steps), "--anyway") == 0


def step(ws, do, result):
    assert run(ws, "step", RUN, "--do", do, "--if-true", "go on", "--if-false", "drop") == 0
    number = len(runs.ledger(threads.read_note(ws / "threads" / f"{RUN}.md")).steps)
    assert run(ws, "result", RUN, str(number), "--text", result) == 0


# --------------------------------------------------------------------------
# premises
# --------------------------------------------------------------------------

def test_a_premise_is_a_proposition_that_is_already_written_down(ws, capsys):
    prop(ws, "p-base", "The spectrum is bounded below.")
    assert thread(ws, "new", "p-top", "--kind", "proposition", "--title", "top",
                  "--purpose", "t", "--premise", "p-nowhere") == 1
    assert "already written down" in capsys.readouterr().err
    prop(ws, "p-top", "The gap survives.", "--premise", "p-base")
    assert chain.premises(threads.read_note(ws / "threads" / "p-top.md")) == ["p-base"]
    assert thread(ws, "post", "p-top", "--premise", "p-top") == 1, "not its own premise"


def test_a_premise_found_later_is_recorded_as_an_event(ws):
    prop(ws, "p-a", "A.")
    prop(ws, "p-b", "B.")
    prop(ws, "p-c", "C.", "--premise", "p-a")
    assert thread(ws, "post", "p-c", "--premise", "p-b", "--text", "it also needs B") == 0
    note = threads.read_note(ws / "threads" / "p-c.md")
    assert chain.premises(note) == ["p-a", "p-b"]
    assert note.posts[-1].field == "premises"
    assert not threads.validate(note)


def test_supported_is_the_authors_word_and_stands_is_earned(ws):
    prop(ws, "p-base", "Base.")
    prop(ws, "p-top", "Top.", "--premise", "p-base")
    support(ws, "p-base"), support(ws, "p-top")
    by = chain.index(notes(ws))
    assert not chain.stands("p-top", by), "nobody has read either"

    verdict(ws, "p-top")
    by = chain.index(notes(ws))
    assert not chain.stands("p-top", by), "it was read, what it rests on was not"
    assert chain.label("p-top", by) == chain.UNREVIEWED

    verdict(ws, "p-base", tier="cheap")
    assert not chain.stands("p-top", chain.index(notes(ws))), \
        "the cheap tier passed four real errors out of four; it does not count"
    verdict(ws, "p-base", tier="strong")
    by = chain.index(notes(ws))
    assert chain.stands("p-top", by) and chain.label("p-top", by) == chain.STANDS


def test_two_claims_resting_on_each_other_stand_on_nothing(ws):
    prop(ws, "p-a", "A.")
    prop(ws, "p-b", "B.", "--premise", "p-a")
    assert thread(ws, "post", "p-a", "--premise", "p-b") == 0
    for slug in ("p-a", "p-b"):
        support(ws, slug), verdict(ws, slug)
    by = chain.index(notes(ws))
    assert not chain.stands("p-a", by) and not chain.stands("p-b", by)
    assert set(chain.closure(["p-a"], by)) == {"p-a", "p-b"}, "and walking it terminates"


def test_the_chain_is_in_the_order_it_has_to_be_read(ws):
    prop(ws, "p-1", "One.")
    prop(ws, "p-2", "Two.", "--premise", "p-1")
    prop(ws, "p-3", "Three.", "--premise", "p-2", "--premise", "p-1")
    prop(ws, "p-side", "Unrelated.")
    assert chain.closure(["p-3"], chain.index(notes(ws))) == ["p-1", "p-2", "p-3"]


def test_a_refuted_premise_is_owed_a_second_look_not_a_status(ws):
    prop(ws, "p-base", "Base.")
    prop(ws, "p-top", "Top.", "--premise", "p-base")
    support(ws, "p-top")
    threads.set_status(ws / "threads" / "p-base.md", "refuted", "counterexample at L=3",
                       host="codex", at="2099-01-01T00:00:00Z")
    owed = [item for item in state.load(ws).debt if item.slug == "p-top"]
    assert len(owed) == 1 and "p-base was refuted" in owed[0].why
    assert threads.read_note(ws / "threads" / "p-top.md").status == "supported", \
        "whether it survives is a judgement"
    threads.append_post(ws / "threads" / "p-top.md", "still holds: only used the bound for L>4",
                        host="claude", at="2099-01-02T00:00:00Z")
    assert not [item for item in state.load(ws).debt if item.slug == "p-top"]


def test_conceding_to_the_reviewer_is_nobodys_adjudication():
    assert not vocab.is_human_only(vocab.PROPOSITION, "disputed", "refuted")
    assert vocab.is_human_only(vocab.PROPOSITION, "disputed", "supported"), \
        "overruling the reviewer still is"


def test_unattended_the_premise_is_read_first_and_trivia_is_not_offered(ws):
    (ws / "threads" / "q-gap.md").write_text("", encoding="utf-8")
    (ws / "threads" / "q-gap.md").unlink()
    assert thread(ws, "new", "q-gap", "--kind", "question", "--title", "Does it survive",
                  "--purpose", "t") == 0
    prop(ws, "a-base", "Base.", "--line", "gap")
    prop(ws, "z-top", "Top.", "--premise", "a-base", "--line", "gap")
    prop(ws, "b-trivia", "A normalisation constant.", "--line", "gap")
    for slug in ("z-top", "a-base"):
        support(ws, slug)
    signed(ws)
    actions = state.candidates(state.load(ws))
    reviews = [a.slug for a in actions if a.key == "review"]
    assert reviews == ["a-base", "z-top"], "what is built on is read before what is built"
    assert "b-trivia" not in [a.slug for a in actions if a.key == "work"]


def test_a_claim_opened_inside_a_run_is_never_asked_for_a_bet(ws):
    signed(ws)
    prop(ws, "p-inside", "Found on the way.")
    threads.set_status(ws / "threads" / "p-inside.md", "testing", "started", host="claude")
    step(ws, "test it", "opened [[p-inside]]")
    prop(ws, "p-outside", "The person's own conjecture.")
    threads.set_status(ws / "threads" / "p-outside.md", "testing", "started", host="claude")
    asked = [item.slug for item in state.load(ws).queue if item.kind == "bet"]
    assert asked == ["p-outside"]


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def _a_run_with_a_finding(ws):
    (ws / "wiki" / "concepts" / "transfer-matrix.md").write_text(
        "---\ntitle: Transfer matrix\n---\n# Transfer matrix\n", encoding="utf-8")
    signed(ws)
    prop(ws, "p-base", "The spectrum is bounded below for all L.")
    prop(ws, "p-top", "The gap survives weak disorder.", "--premise", "p-base")
    path = ws / "threads" / "p-top.md"
    threads.set_field(path, "depends_on", ["[[Transfer matrix]]", "[[Made up method]]"],
                      host="claude")
    prop(ws, "p-dead", "The gap closes at strong disorder.")
    for slug in ("p-base", "p-top"):
        support(ws, slug)
    verdict(ws, "p-base"), verdict(ws, "p-top")
    threads.set_status(ws / "threads" / "p-dead.md", "supported", "looked right", host="claude")
    threads.set_status(ws / "threads" / "p-dead.md", "disputed", "VERDICT: refuted\n\nno.",
                       host=vocab.REVIEWER)
    threads.set_status(ws / "threads" / "p-dead.md", "refuted", "conceded", host="claude")
    step(ws, "bound the spectrum", "done: [[p-base]]")
    step(ws, "push through disorder", "holds: [[p-top]]; the strong case fails: [[p-dead]]")


def test_the_outline_already_holds_everything_mechanical(ws):
    _a_run_with_a_finding(ws)
    text = report.outline(ws, threads.read_note(ws / "threads" / f"{RUN}.md"), notes(ws))
    assert f"run: {RUN}" in text and "form: draft" in text
    assert "Decide before a month of numerics." in text, "motivation comes from the contract"
    assert "- [[p-top]] — The gap survives weak disorder. — **stands** (supported)" in text
    assert "**contested** (refuted)" in text
    assert text.index("### [[p-base]]") < text.index("### [[p-top]]"), "reading order"
    assert "Step 2 (cli): push through disorder" in text and "If it held: go on" in text
    assert "**The person**" in text and "SIGNED" in text
    assert "candidate: [[p-dead]]" in text
    assert "- [[Transfer matrix]]" in text and "no concept card yet" in text
    for heading in report.HEADINGS[report.DRAFT]:
        assert f"## {heading}" in text


def test_a_run_that_found_nothing_gets_one_page(ws):
    signed(ws)
    step(ws, "try the obvious thing", "it does not work")
    text = report.outline(ws, threads.read_note(ws / "threads" / f"{RUN}.md"), notes(ws),
                          form=report.EMPTY)
    headings = re.findall(r"^## (.+)$", text, flags=re.M)
    assert headings == list(report.HEADINGS[report.EMPTY])


def _written(ws, form="draft"):
    assert run(ws, "outline", RUN, "--write", *(["--empty"] if form == "empty" else [])) == 0
    path = ws / "drafts" / "runs" / f"{RUN}.md"
    text = re.sub(r"<!--(?! candidate).*?-->", "written.", path.read_text(encoding="utf-8"),
                  flags=re.S)
    path.write_text(text, encoding="utf-8")
    return path


def _drop_the_made_up_term(path):
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if "[[Made up method]]" not in line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def test_the_hand_in_gate_names_what_a_reader_would_trip_on(ws, capsys):
    _a_run_with_a_finding(ws)
    path = _written(ws)
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("## 4 Picture and core insight", "## 4 Whatever")
                    .replace("The gap survives weak disorder.", "see the note"),
                    encoding="utf-8")
    assert run(ws, "report", RUN, f"drafts/runs/{RUN}.md") == 1
    said = capsys.readouterr().err
    assert "no `## 4 Picture and core insight` section" in said
    assert "[[p-top]] is cited without its statement" in said
    assert "term [[Made up method]] is not a concept card" in said
    assert f"nobody has read it whole: magi review drafts/runs/{RUN}.md" in said
    assert threads.read_note(ws / "threads" / f"{RUN}.md").status == runs.RUNNING


def test_an_unwritten_section_does_not_pass_as_a_written_one(ws, capsys):
    signed(ws)
    step(ws, "try", "nothing")
    assert run(ws, "outline", RUN, "--write", "--empty") == 0
    assert run(ws, "report", RUN, f"drafts/runs/{RUN}.md") == 1
    assert "template comment(s) are still in it" in capsys.readouterr().err


def test_a_null_report_needs_no_whole_draft_reading(ws):
    signed(ws)
    step(ws, "try", "nothing")
    _written(ws, "empty")
    assert run(ws, "report", RUN, f"drafts/runs/{RUN}.md") == 0
    assert threads.read_note(ws / "threads" / f"{RUN}.md").status == runs.REPORTED


def test_handing_in_anyway_is_allowed_and_says_so(ws):
    signed(ws)
    step(ws, "try", "nothing")
    assert run(ws, "outline", RUN, "--write") == 0
    assert run(ws, "report", RUN, f"drafts/runs/{RUN}.md", "--anyway") == 0
    last = threads.read_note(ws / "threads" / f"{RUN}.md").posts[-1].text
    assert "handed in with" in last and "check(s) unmet" in last


def test_a_full_draft_is_read_whole_by_somebody_else_first(ws, monkeypatch, capsys):
    monkeypatch.setenv("MAGI_HOST", "claude")
    _a_run_with_a_finding(ws)
    path = _written(ws)
    _drop_the_made_up_term(path)
    asked = {}

    def fake(host, prompt, **kw):
        asked.update(host=host, prompt=prompt)
        return "VERDICT: restate\nCHECKED: followed p-base into p-top\nASSUMPTION: the bound\nREASON: fine, rename E_0."

    monkeypatch.setattr(review, "ask", fake)
    relative = f"drafts/runs/{RUN}.md"
    assert review.main(["--project-dir", str(ws), relative]) == 0
    assert asked["host"] == "codex", "claude did the run, so somebody else reads its report"
    assert "Composition." in asked["prompt"] and relative in asked["prompt"]
    note = threads.read_note(ws / "threads" / f"{RUN}.md")
    assert note.posts[-1].host == vocab.REVIEWER and relative in note.posts[-1].text
    assert report.reviewed(note, relative) == "restate"
    assert note.status == runs.RUNNING, "a reading moves nothing"

    assert run(ws, "report", RUN, relative) == 0
    assert "Made up" not in capsys.readouterr().err


def test_a_report_whose_chain_does_not_hold_is_not_handed_in(ws, monkeypatch, capsys):
    _a_run_with_a_finding(ws)
    path = _written(ws)
    _drop_the_made_up_term(path)
    monkeypatch.setattr(review, "ask", lambda *a, **k: "VERDICT: refuted\nREASON: step 2 uses the old bound.")
    relative = f"drafts/runs/{RUN}.md"
    assert review.main(["--project-dir", str(ws), relative]) == 0
    assert run(ws, "report", RUN, relative) == 1
    assert "came back `refuted`" in capsys.readouterr().err


# --------------------------------------------------------------------------
# what reaches the person
# --------------------------------------------------------------------------

def test_the_person_gets_one_item_and_it_says_what_reading_will_cost(ws):
    _a_run_with_a_finding(ws)
    path = _written(ws, "empty")
    path.write_text(path.read_text(encoding="utf-8") + "\n### Terms\n\n- [[Transfer matrix]] — T.\n"
                    "- [[Made up method]] — M.\n", encoding="utf-8")
    (ws / "drafts" / "lectures").mkdir(parents=True)
    (ws / "drafts" / "lectures" / "made-up-method.md").write_text("# notes\n", encoding="utf-8")
    familiar.record("Transfer matrix", familiar.KNOWN)
    assert run(ws, "report", RUN, f"drafts/runs/{RUN}.md") == 0

    items = [item for item in state.load(ws).queue if item.kind == "report"]
    assert len(items) == 1
    assert "1 term(s) new to you, 1 of them with lecture notes" in items[0].why
    assert f"drafts/runs/{RUN}.md" in items[0].why

    # Counted now, not frozen at hand-in: seen in the end-to-end smoke, where
    # the item went on saying "2 new" after the person had marked one known.
    familiar.record("Made up method", familiar.KNOWN)
    again = [item for item in state.load(ws).queue if item.kind == "report"]
    assert "no glossary" not in again[0].why and "0 term(s) new to you" in again[0].why

    decide_cmd.record(ws, "read it; step 2 should have tried the strong case first", about=RUN)
    assert not [item for item in state.load(ws).queue if item.kind == "report"]


def test_what_somebody_knows_is_asked_and_the_last_answer_wins(ws):
    assert not familiar.knows("Transfer matrix")
    familiar.record("[[Transfer matrix]]", familiar.NOTES)
    assert familiar.wants_notes("transfer-matrix")
    familiar.record("wiki/concepts/Transfer matrix.md", familiar.KNOWN)
    assert familiar.knows("Transfer matrix") and not familiar.wants_notes("Transfer matrix")
    familiar.record("Transfer matrix", None)
    assert not familiar.knows("Transfer matrix")
    rows = [json.loads(line) for line in familiar.path().read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3, "append-only: the file is also the history"
