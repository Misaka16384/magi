"""One day's feedback from a real session (Mobility_Fusion, 2026-09-14/15): the P1–P3 items.

Each test is a place that session lost time or was told something untrue:
checks that complained about the wrong files and ignored the right links,
output that stayed invisible until a command ended, missing verbs it had to
edit files by hand for, eight steps to start a project, and advice that
contradicted itself.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from magi.ingest import batch, ledger
from magi.ingest.convert_result import ConversionResult


def _project(path: Path, title: str = "T", scope: str | None = None) -> Path:
    from magi import init_workspace

    argv = ["--topic-dir", str(path), "--name", title]
    if scope:
        argv += ["--scope", scope]
    init_workspace.main(argv)
    return path


def _magi(cwd, *args):
    return subprocess.run([sys.executable, "-m", "magi", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# P1 — checks that complained about the wrong things
# --------------------------------------------------------------------------

def test_a_draft_needs_no_card_frontmatter_and_a_source_needs_no_tags(tmp_path):
    ws = _project(tmp_path / "ws")
    (ws / "drafts" / "working.md").write_text("# Working out\n\nNo frontmatter here.\n",
                                              encoding="utf-8")
    (ws / "raw" / "papers" / "p.md").write_text(
        "---\ntitle: P\nsource: https://arxiv.org/abs/2108.10324\ntype: papers\n"
        "ingested: 2026-09-14\ntags: []\n---\n\n" + "word " * 50, encoding="utf-8")
    done = _magi(ws, "lint", ".")
    assert "missing YAML frontmatter" not in done.stdout
    assert "tags must be a non-empty list" not in done.stdout
    assert "Missing required field: tags" not in done.stdout
    assert "0 critical" in done.stdout, done.stdout


def test_verify_refs_reads_relative_markdown_links_in_either_argument_order(tmp_path):
    ws = _project(tmp_path / "ws")
    (ws / "raw" / "papers" / "p.md").write_text("# P\n", encoding="utf-8")
    (ws / "drafts" / "d.md").write_text(
        "# D\n\nSee [the paper](../raw/papers/p.md), [gone](../raw/papers/missing.md), "
        "[web](https://arxiv.org) and `[code](nope.md)`.\n", encoding="utf-8")
    for argv in (("stats", ".", "verify-refs", "drafts/d.md"),
                 ("stats", "drafts/d.md", "verify-refs")):
        done = _magi(ws, *argv)
        report = json.loads(done.stdout)
        assert report["valid_links"] == ["../raw/papers/p.md"], argv
        assert report["dangling_links"] == ["../raw/papers/missing.md"], argv
        assert report["dangling_count"] == 1


def test_a_dry_run_calls_files_that_are_already_current_current(tmp_path):
    from magi.skills_cmd import HOSTS, install_host, load_skills

    skills = load_skills()
    install_host(HOSTS["claude"], skills, "global", force=False, dry_run=False, override_dir=tmp_path)
    counts = install_host(HOSTS["claude"], skills, "global", force=False, dry_run=True,
                          override_dir=tmp_path)["counts"]
    assert counts["created"] == 0 and counts["updated"] == 0 and counts["unchanged"] > 0


def test_output_that_is_not_a_terminal_is_written_a_line_at_a_time(monkeypatch):
    from magi import cli

    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert cli.main(["--version"]) == 0
    assert out.line_buffering and err.line_buffering


def test_an_index_run_says_how_long_is_left_once_it_can():
    from magi.retrieval import _eta

    assert _eta(1, 10, 5.0) == ""
    assert _eta(10, 40, 30.0) == ", about 2 min left"
    assert _eta(30, 40, 60.0) == ", about 20 s left"


# --------------------------------------------------------------------------
# review: formulas said before commit, approve-all, what a route keeps
# --------------------------------------------------------------------------

def test_review_says_how_many_formulas_will_fail_before_anything_is_committed():
    from magi.ingest import gates
    from magi.kb.validate_math_latex import HAS_PYLATEXENC

    if not (HAS_PYLATEXENC or shutil.which("pdflatex")):
        pytest.skip("needs pylatexenc or pdflatex")
    finding = gates.check_math_structure(
        "# t\n\n$$\n\\begin{array}{l}a\\\\\n\\vskip 2pt b\\end{array}\n$$\n")
    assert finding.code == "math-damage" and "\\vskip" in finding.detail
    assert gates.check_math_structure("# t\n\nInline $a_1$.\n") is None


@pytest.fixture
def batch_ws(tmp_path, monkeypatch):
    for sub in ("raw/papers", "wiki", "inbox", "output"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.md").write_text("# scope\n", encoding="utf-8")

    def fake(route, entry, staging, topic=None, **kw):
        if entry.value == "bad":
            return ConversionResult.failed("no rendering")
        staging.mkdir(parents=True, exist_ok=True)
        md = staging / f"2026-09-14-{entry.value}.md"
        md.write_text(f"---\ntitle: {entry.value}\n---\n\n" + "word " * 300, encoding="utf-8")
        (staging / f"2026-09-14-{entry.value}.macros.tex").write_text(
            "\\newcommand{\\mK}{K}\n", encoding="utf-8")
        return ConversionResult(success=True, markdown_path=str(md))

    monkeypatch.setattr(batch, "_run_route", fake)
    monkeypatch.setattr(batch, "_resolve_topic", lambda explicit: tmp_path)
    monkeypatch.setattr(batch.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    return tmp_path


def test_approve_all_approves_what_converted_and_names_what_did_not(batch_ws, capsys):
    ledger.enqueue(batch_ws, source_type="arxiv", value="good")
    ledger.enqueue(batch_ws, source_type="arxiv", value="bad")
    batch.main(["run"])
    assert batch.main(["review", "--approve-all"]) == 0
    items = {i.source_value: i for i in ledger.load_batch(batch_ws, ledger.list_batches(batch_ws)[0])}
    assert items["good"].decision == "approve"
    assert items["bad"].decision is None
    assert "not approved" in capsys.readouterr().out


def test_what_a_route_kept_beside_the_document_is_committed_with_it(batch_ws):
    ledger.enqueue(batch_ws, source_type="arxiv", value="good")
    batch.main(["run"])
    batch.main(["review", "--approve-all", "--commit"])
    assert (batch_ws / "raw" / "papers" / "2026-09-14-good.md").is_file()
    assert (batch_ws / "raw" / "papers" / "2026-09-14-good.macros.tex").is_file()


def test_the_arxiv_html_route_repairs_layout_expands_macros_and_keeps_the_rest(tmp_path, monkeypatch):
    from magi.ingest import arxiv_html as ah
    from magi.ingest import tex_macros

    page = "<html><title>[2108.10324] A Paper</title></html>"
    monkeypatch.setattr(ah, "fetch", lambda ident, timeout=60: ah.FetchResult(
        True, body=page.encode(), url="https://arxiv.org/html/2108.10324v1", endpoint="arxiv"))
    monkeypatch.setattr(ah, "find_pandoc", lambda: "pandoc")
    border = "+---+" + "-" * 30 + "+---+" + "-" * 55 + "+"
    table = "\n".join([
        border,
        "|   |" + " $\\displaystyle x=\\mK$".ljust(30) + "|   |"
        + " [(1)]{.ltx_tag .ltx_tag_equation .ltx_align_right}".ljust(55) + "|",
        border,
    ])
    converted = "::: {.ltx_para}\nWith $\\mK$ inline.\n:::\n\n" + table + "\n"

    def fake_run(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_text(converted, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ah.subprocess, "run", fake_run)
    monkeypatch.setattr(tex_macros, "from_eprint", lambda ident, timeout=120: (
        {"mK": tex_macros.Macro("mK", 0, None, "\\mathcal{K}", "macros.sty:1"),
         "half": tex_macros.Macro("half", 0, None, "$\\frac12$ ", "main.tex:2")},
        ["bbm"], None))

    result = ah.convert("2108.10324", tmp_path, fetch_figures=False)
    md = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "\\mK" not in md and md.count("\\mathcal{K}") == 2
    assert "$$\n\\displaystyle x=\\mathcal{K}\n$$      [(1)]" in md
    assert "math-layout-repaired" in {f.code for f in result.findings}
    side = Path(result.markdown_path).with_name(Path(result.markdown_path).stem + ".macros.tex")
    assert "bbm.sty" in side.read_text(encoding="utf-8")


def test_math_repair_fixes_a_paper_in_place_keeps_crlf_and_can_be_undone(tmp_path):
    from magi.ingest import latexml_math
    from magi.kb import tidy_log

    ws = _project(tmp_path / "ws")
    paper = ws / "raw" / "papers" / "p.md"
    original = ("---\ntitle: P\n---\n\n::: {.ltx_para}\n$$\nU=a\\\\      "
                "[(1)]{.ltx_tag .ltx_tag_equation}\nb\n$$\n:::\n").replace("\n", "\r\n").encode()
    paper.write_bytes(original)

    assert latexml_math.main([str(paper), "--dry-run"]) == 0
    assert paper.read_bytes() == original
    assert latexml_math.main([str(paper)]) == 0
    fixed = paper.read_bytes()
    assert b"\r\n" in fixed and b"$$      [(1)]" in fixed

    record = sorted((ws / "output" / "tidy").glob("*.json"))[-1]
    assert tidy_log.undo(record)[0] >= 1
    assert paper.read_bytes() == original


# --------------------------------------------------------------------------
# P2 — verbs a person had to edit files by hand for
# --------------------------------------------------------------------------

@pytest.fixture
def thread_ws(tmp_path):
    for sub in ("threads", "drafts", "tools", "raw"):
        (tmp_path / sub).mkdir()
    (tmp_path / "tools" / "count.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "drafts" / "d.md").write_text("# D\n", encoding="utf-8")
    (tmp_path / "drafts" / "d2.md").write_text("# D2\n", encoding="utf-8")
    return tmp_path


def _thread(ws, *argv):
    from magi.kb import thread_cmd

    return thread_cmd.main(list(argv) + ["--topic-dir", str(ws)])


def test_a_derivation_changes_through_the_cli_as_a_recorded_change(thread_ws):
    from magi.core import vocab
    from magi.kb import threads

    path = thread_ws / "threads" / "p-idx.md"
    threads.create(path, vocab.PROPOSITION, "P", "Why.", extra={"derivation": ["drafts/d.md"]})
    posts_before = len(threads.read_note(path).posts)

    assert _thread(thread_ws, "derivation", "p-idx", "drafts/d2.md") == 0
    assert threads.read_note(path).frontmatter["derivation"] == ["drafts/d.md", "drafts/d2.md"]
    assert _thread(thread_ws, "derivation", "p-idx", "[[drafts/d2]]", "--replace") == 0
    note = threads.read_note(path)
    assert note.frontmatter["derivation"] == ["[[drafts/d2]]"]
    assert len(note.posts) == posts_before + 2
    assert _thread(thread_ws, "derivation", "p-idx", "drafts/missing.md") == 1


def test_a_question_can_carry_evidence_and_a_line_cannot(thread_ws):
    from magi.core import vocab
    from magi.kb import threads

    threads.create(thread_ws / "threads" / "q-count.md", vocab.QUESTION, "Q", "Why.")
    assert _thread(thread_ws, "post", "q-count", "--evidence", "tools/count.py",
                   "--text", "the enumeration that answers it") == 0
    assert threads.read_note(thread_ws / "threads" / "q-count.md").frontmatter["evidence"] == \
        ["tools/count.py"]
    threads.create(thread_ws / "threads" / "l-main.md", vocab.LINE, "L", "Why.")
    assert _thread(thread_ws, "post", "l-main", "--evidence", "tools/count.py", "--text", "x") == 1


def test_material_outside_the_project_is_copied_and_undo_removes_only_the_copies(tmp_path):
    from magi import adopt

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "notes").mkdir(parents=True)
    (elsewhere / "notes" / "a.md").write_text("# A\n\nSee [b](b.md).\n", encoding="utf-8")
    (elsewhere / "notes" / "b.md").write_text("# B\n", encoding="utf-8")
    ws = _project(tmp_path / "ws")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"moves": [{"from": str(elsewhere / "notes"),
                                           "to": "drafts/notes"}]}), encoding="utf-8")

    assert adopt.main(["apply", str(plan), "--project-dir", str(ws)]) == 1
    assert adopt.main(["apply", str(plan), "--project-dir", str(ws), "--copy"]) == 0
    assert (ws / "drafts" / "notes" / "a.md").read_text(encoding="utf-8").endswith("See [b](b.md).\n")
    assert (elsewhere / "notes" / "a.md").is_file()
    assert not list((ws / "scratch").glob("adopt-copy-*"))

    assert adopt.main(["undo", "--project-dir", str(ws)]) == 0
    assert not (ws / "drafts" / "notes").exists()
    # Removed, not "put back" into a staging folder nobody made.
    assert not list((ws / "scratch").rglob("a.md"))
    assert (elsewhere / "notes" / "b.md").is_file()


def test_prune_drops_temp_workspaces_only_when_asked_and_never_the_current_one(tmp_path, monkeypatch):
    from magi import kb_registry as K

    ws = _project(tmp_path / "ws")
    if not K._under_temp(ws):
        pytest.skip("pytest's temporary directory is not under the system temp directory here")

    def registered():
        return {Path(e["path"]).resolve() for e in K.load_registry()["kbs"].values()}

    monkeypatch.chdir(tmp_path)
    K.cmd_prune(argparse.Namespace(dry_run=False, temp=False, disabled=False))
    assert ws.resolve() in registered()
    monkeypatch.chdir(ws)
    K.cmd_prune(argparse.Namespace(dry_run=False, temp=True, disabled=False))
    assert ws.resolve() in registered()
    monkeypatch.chdir(tmp_path)
    K.cmd_prune(argparse.Namespace(dry_run=False, temp=True, disabled=False))
    assert ws.resolve() not in registered()


def test_title_and_scope_change_in_place_and_the_protocol_block_follows(tmp_path):
    from magi import config_cmd

    ws = _project(tmp_path / "ws", title="My Project", scope="A research project.")
    assert config_cmd.main(["set", "title", "Mobility fusion", "--project-dir", str(ws)]) == 0
    assert config_cmd.main(["set", "scope", "Fusion rules of mobility", "--project-dir", str(ws)]) == 0
    config = (ws / "config.md").read_text(encoding="utf-8")
    assert "title: Mobility fusion" in config and "# Mobility fusion" in config
    assert "## Scope\n\nFusion rules of mobility\n" in config
    agents = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "Mobility fusion" in agents and "Fusion rules of mobility" in agents
    assert (ws / "_index.md").read_text(encoding="utf-8").startswith("# Mobility fusion")

    assert config_cmd.main(["set", "ollama.keep_alive", "30m", "--project-dir", str(ws)]) == 0
    assert yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))["ollama"]["keep_alive"] == "30m"


# --------------------------------------------------------------------------
# P2.15 — one `magi init`
# --------------------------------------------------------------------------

def test_init_merges_an_existing_gitignore_and_names_what_was_already_there(tmp_path, capsys):
    from magi import init_workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".gitignore").write_text("mine/\n", encoding="utf-8")
    (ws / "notes.txt").write_text("x", encoding="utf-8")
    init_workspace.main(["--topic-dir", str(ws)])
    text = (ws / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("mine/\n") and "scratch/" in text and init_workspace.GITIGNORE_MARK in text
    out = capsys.readouterr().out
    assert "already held 1 item(s)" in out and "magi adopt survey ." in out


def test_without_a_terminal_the_title_is_the_folders_name_and_the_missing_scope_is_named(tmp_path, capsys):
    from magi import init_workspace

    ws = tmp_path / "quantum-toys"
    init_workspace.main(["--topic-dir", str(ws)])
    assert 'title: "quantum-toys"' in (ws / "config.md").read_text(encoding="utf-8")
    assert "magi config set scope" in capsys.readouterr().out
    assert not (ws / "raw" / "notes").exists()


def test_init_on_an_existing_project_changes_nothing_and_names_the_command_that_does(tmp_path, capsys):
    from magi import init_workspace

    ws = _project(tmp_path / "ws", title="Old")
    capsys.readouterr()
    init_workspace.main(["--topic-dir", str(ws), "--title", "New"])
    assert 'title: "Old"' in (ws / "config.md").read_text(encoding="utf-8")
    assert "New" not in (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert 'magi config set title "New"' in capsys.readouterr().out


def test_init_installs_into_the_agent_clis_unless_told_not_to(tmp_path, monkeypatch):
    from magi import init_workspace, install_cmd

    calls = []
    monkeypatch.delenv("MAGI_INIT_NO_INSTALL", raising=False)
    monkeypatch.setattr(install_cmd, "main", lambda argv: calls.append(argv) or 0)
    init_workspace.main(["--topic-dir", str(tmp_path / "a"), "--name", "A"])
    init_workspace.main(["--topic-dir", str(tmp_path / "b"), "--name", "B", "--no-install"])
    assert len(calls) == 1
    assert calls[0][:2] == ["--project-dir", str((tmp_path / "a").resolve())]


def test_skills_install_outside_a_project_refuses_instead_of_installing_anyway(tmp_path, monkeypatch, capsys):
    from magi import skills_cmd

    monkeypatch.chdir(tmp_path)
    assert skills_cmd.main(["install", "--host", "claude"]) == 1
    assert "magi init" in capsys.readouterr().out
    assert not (tmp_path / ".claude").exists()


def test_an_empty_project_is_not_told_to_build_an_index(tmp_path):
    from magi.sync import build_report

    ws = _project(tmp_path / "ws")
    assert "index-missing" not in [h["code"] for h in build_report(ws)["hints_structured"]]
    (ws / "raw" / "papers" / "p.md").write_text("---\ntitle: P\n---\n\nwords\n", encoding="utf-8")
    assert "index-missing" in [h["code"] for h in build_report(ws)["hints_structured"]]


# --------------------------------------------------------------------------
# P3 — advice that would have done damage
# --------------------------------------------------------------------------

def test_the_repository_a_project_sits_inside_is_found(tmp_path):
    from magi import pm

    (tmp_path / ".git").mkdir()
    project = tmp_path / "Work" / "proj"
    project.mkdir(parents=True)
    assert pm.enclosing_repo(project) == tmp_path.resolve()
    (project / ".git").mkdir()
    assert pm.enclosing_repo(project) is None


def test_handing_a_folder_inside_a_repository_to_bd_takes_yes(tmp_path, capsys):
    from magi import pm

    (tmp_path / ".git").mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    assert pm._agreed_to_hand_over(project, assumed_yes=False) is False
    assert "Pass --yes" in capsys.readouterr().out
    assert pm._agreed_to_hand_over(project, assumed_yes=True) is True


# --------------------------------------------------------------------------
# addendum — the embedding model is let go when the command ends
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, (None, True)), ("release", (None, True)), (0, (None, True)), ("0", (None, True)),
    (-1, (-1, False)), ("-1", (-1, False)), ("30m", ("30m", False)), (300, (300, False)),
])
def test_keep_alive_settings(value, expected):
    from magi.core import ollama

    assert ollama.keep_alive_policy(value) == expected


def test_the_model_is_released_once_per_server_and_model(monkeypatch):
    from magi.core import ollama

    registered = []
    monkeypatch.delenv("MAGI_NO_OLLAMA_RELEASE", raising=False)
    monkeypatch.setattr(ollama, "_release_armed", set())
    monkeypatch.setattr("atexit.register", lambda fn, *args: registered.append(args))
    ollama.release_at_exit("http://127.0.0.1:11434", "m")
    ollama.release_at_exit("http://127.0.0.1:11434", "m")
    ollama.release_at_exit("http://127.0.0.1:11434", "other")
    assert [args[1] for args in registered] == ["m", "other"]


def test_requests_carry_keep_alive_only_when_configured_and_only_a_local_model_is_released(monkeypatch):
    from magi.retrieval import Embedder

    armed = []
    monkeypatch.setattr("magi.core.ollama.release_at_exit", lambda base, model: armed.append((base, model)))
    embedder = Embedder.__new__(Embedder)
    embedder.provider, embedder.model = "ollama", "m"
    embedder._keep_alive, embedder._release_at_exit = None, True
    assert embedder._ollama_body({"model": "m"}) == {"model": "m"}
    embedder.base_url = "http://127.0.0.1:11434"
    embedder._used_ollama()
    embedder.base_url = "http://gpu-box.lan:11434"
    embedder._used_ollama()
    assert armed == [("http://127.0.0.1:11434", "m")]
    embedder._keep_alive = "30m"
    assert embedder._ollama_body({"model": "m"})["keep_alive"] == "30m"
