"""Between an empty folder and the first paper in `raw/`, fewer things to know.

2026-09-17, the author: starting MAGI in a new project "is still slightly
tedious". Traced step by step, the tedium was not `magi init` — that became
one command in v2.7.1 — but what comes after it. A PDF dropped into `inbox/`
was invisible to `magi next`, the one command that is supposed to say what
comes next; `magi sync` told a project with five PDFs waiting to "drop sources
in inbox/"; and a link took four commands to become a file.
"""

from __future__ import annotations

import types

import pytest

from magi import state
from magi.ingest import batch, enqueue, ledger
from magi.ingest.convert_result import ConversionResult


@pytest.fixture
def project(tmp_path, monkeypatch):
    for sub in ("raw/papers", "wiki", "inbox", "output", "threads"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.md").write_text("# scope\n", encoding="utf-8")

    def fake(route, entry, staging, topic=None, **kw):
        if entry.value.endswith("bad"):
            return ConversionResult.failed("no rendering")
        staging.mkdir(parents=True, exist_ok=True)
        name = entry.value.rsplit("/", 1)[-1]
        md = staging / f"2026-09-17-{name}.md"
        md.write_text(f"---\ntitle: {name}\n---\n\n" + "word " * 300, encoding="utf-8")
        return ConversionResult(success=True, markdown_path=str(md))

    monkeypatch.setattr(batch, "_run_route", fake)
    monkeypatch.setattr(batch, "_resolve_topic", lambda explicit: tmp_path)
    monkeypatch.setattr(batch.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    return tmp_path


def _actions(root):
    return state.candidates(state.load(root))


def test_next_sees_a_file_dropped_in_the_inbox(project):
    assert not [a for a in _actions(project) if a.key == "ingest"]
    (project / "inbox" / "haah-2011.pdf").write_bytes(b"%PDF-1.4")
    found = [a for a in _actions(project) if a.key == "ingest"]
    assert len(found) == 1
    assert "haah-2011.pdf" in found[0].why and found[0].run == "magi ingest auto"
    assert found[0].cost == "certain", "running a converter needs no model"


def test_the_notes_file_is_not_a_source_waiting(project):
    (project / "inbox" / "notes.md").write_text("a thought\n", encoding="utf-8")
    assert not [a for a in _actions(project) if a.key == "ingest"]


def test_next_sees_a_link_queued_and_never_fetched(project):
    ledger.enqueue(project, source_type="arxiv", value="2609.14858")
    found = [a for a in _actions(project) if a.key == "ingest"]
    assert [a.run for a in found] == ["magi ingest batch-run"]


def test_sync_stops_telling_a_full_inbox_to_drop_sources_in_it(project, monkeypatch):
    from magi import sync

    (project / "inbox" / "a.pdf").write_bytes(b"%PDF-1.4")
    (project / "inbox" / "b.pdf").write_bytes(b"%PDF-1.4")
    report = sync.build_report(project)
    hint = next(h for h in report["hints_structured"] if h["code"] == "ingest-start")
    assert "2 file(s) waiting" in hint["text"] and "magi ingest auto" in hint["text"]


def test_go_lands_a_clean_link_in_one_command(project):
    assert enqueue.main(["https://example.org/good", "--project-dir", str(project), "--go"]) == 0
    assert (project / "raw" / "papers" / "2026-09-17-good.md").is_file()
    assert not ledger.pending(project)


def test_go_leaves_the_whole_batch_when_anything_in_it_needs_a_look(project, capsys):
    """The listing exists so a person sees what a conversion flagged before it
    lands. `--go` skips the listing only when it would have been empty."""
    rc = enqueue.main(["https://example.org/good", "https://example.org/bad",
                       "--project-dir", str(project), "--go"])
    assert rc == 0
    assert not list((project / "raw" / "papers").glob("*.md")), "nothing lands, not even the clean one"
    out = capsys.readouterr().out
    assert "1 item(s) need a look" in out and "magi ingest review" in out
    items = ledger.load_batch(project, ledger.list_batches(project)[0])
    assert all(item.decision is None for item in items), "and nothing was decided for them"


def test_without_go_nothing_is_fetched(project, capsys):
    assert enqueue.main(["https://example.org/good", "--project-dir", str(project)]) == 0
    assert len(ledger.pending(project)) == 1 and not ledger.list_batches(project)
    assert "magi ingest batch-run" in capsys.readouterr().out
