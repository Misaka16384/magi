"""`magi math check` has to finish, and has to say everything it found.

Both from one real session (2026-09-15, seventeen arXiv papers):

* `magi ingest review --commit` sat for twenty minutes. LaTeXML had written a
  `\\\\[1mm]` as `\\\\` + `\\vskip 2.84526pt`, and inside an array that sends
  pdflatex into a loop that raises no error — so `-interaction=nonstopmode`
  never stops, and `subprocess.run` had no timeout to stop it either.
* The full check returned at most fifteen issues per file, and `count` in
  `--json` was that capped number: 15 reported, 137 real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time

import pytest

import magi.kb.validate_math_latex as V
from magi.core import proc

HAS_TEX = shutil.which("pdflatex") is not None
CAN_PARSE = V.HAS_PYLATEXENC or HAS_TEX


# --------------------------------------------------------------------------
# a deadline that reaches the whole process tree
# --------------------------------------------------------------------------

def test_a_hung_child_is_ended_rather_than_waited_for():
    started = time.monotonic()
    rc = proc.run_with_deadline([sys.executable, "-c", "import time; time.sleep(120)"],
                                timeout=1.0)
    assert rc is None
    assert time.monotonic() - started < 30


def test_a_child_that_finishes_reports_its_exit_code():
    assert proc.run_with_deadline([sys.executable, "-c", "raise SystemExit(3)"],
                                  timeout=60) == 3


# --------------------------------------------------------------------------
# hazards: named, never compiled
# --------------------------------------------------------------------------

@pytest.mark.skipif(not CAN_PARSE, reason="needs pylatexenc or pdflatex")
@pytest.mark.parametrize("tex", [
    "\\begin{array}{l} a=0\\\\\n\\vskip 2.84526pt b=0\\end{array}",
    "x=\\hbox to146pt{\\vbox to3pt{\\pgfpicture\\makeatletter}}",
    "\\begin{tikzpicture}\\fdot{0}\\end{tikzpicture}",
    "U=a\\\\      [(19)]{.ltx_tag .ltx_tag_equation .ltx_align_right}\nb",
    "|   | x=1\n|   | y=2",
])
def test_a_formula_that_loops_or_is_not_maths_never_reaches_pdflatex(tex):
    issues, blocks, inlines = V.validate_math_pylatexenc(f"# t\n\n$$\n{tex}\n$$\n")
    assert not blocks and not inlines
    assert [i["detector"] for i in issues] == ["hazard"]


# --------------------------------------------------------------------------
# a loop the hazard list does not know: found, and the rest still checked
# --------------------------------------------------------------------------

def _fake_compile(calls):
    def compile_(snippets, extra, timeout):
        calls.append((len(snippets), timeout))
        if any("LOOP" in s.tex for s in snippets):
            return False, []
        return True, [{"md_line": s.md_line, "md_end_line": s.md_end_line,
                       "error": "Undefined control sequence.", "context": s.tex,
                       "is_block": True} for s in snippets if "BAD" in s.tex]
    return compile_


def test_a_looping_file_is_bisected_to_the_formula_and_the_rest_is_still_checked(monkeypatch):
    calls = []
    monkeypatch.setattr(V, "_compile", _fake_compile(calls))
    blocks = [(f"x_{{{i}}}", i, i) for i in range(1, 41)]
    blocks[17] = ("LOOP", 18, 18)
    blocks[30] = ("\\BAD", 31, 31)

    issues = V.validate_math_pdflatex(blocks, [])

    looped = [i for i in issues if "never finishes" in i["error"]]
    assert [i["md_line"] for i in looped] == [18]
    assert any(i["md_line"] == 31 and "Undefined" in i["error"] for i in issues)
    # The hunt compiles small pieces against short deadlines, not the whole
    # file against the long one again and again.
    assert min(timeout for _, timeout in calls) < V.deadline_for(len(blocks))


def test_a_loop_no_single_formula_reproduces_is_said_not_blamed(monkeypatch):
    def compile_(snippets, extra, timeout):
        # Loops only when both halves are present together.
        texts = {s.tex for s in snippets}
        return (not {"A", "B"} <= texts), []

    monkeypatch.setattr(V, "_compile", compile_)
    issues = V.validate_math_pdflatex([("A", 1, 1), ("x", 2, 2), ("B", 3, 3)], [])
    assert not any("never finishes on this formula" in i["error"] for i in issues)
    assert any("did not finish" in i["error"] for i in issues)


# --------------------------------------------------------------------------
# no cap, and a count that means what it says
# --------------------------------------------------------------------------

def test_every_error_is_returned_not_the_first_fifteen(monkeypatch):
    def compile_(snippets, extra, timeout):
        return True, [{"md_line": s.md_line, "md_end_line": s.md_line, "error": f"e{s.md_line}",
                       "context": "", "is_block": True} for s in snippets]

    monkeypatch.setattr(V, "_compile", compile_)
    assert len(V.validate_math_pdflatex([(f"x{i}", i, i) for i in range(1, 138)], [])) == 137


@pytest.mark.skipif(not CAN_PARSE, reason="needs pylatexenc or pdflatex")
def test_json_counts_everything_and_limit_only_shortens_the_list(tmp_path):
    paper = tmp_path / "x.md"
    paper.write_text("# t\n\n" + "".join(
        "Here $\\mathcal{A}_{%d}} = 1$ appears.\n\n" % i for i in range(3)), encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "magi", "math", "check", str(paper),
                           "--fast", "--json", "--limit", "1"],
                          capture_output=True, text=True, encoding="utf-8")
    payload = json.loads(done.stdout)
    assert payload["count"] == 1 and payload["total"] == 3 and payload["truncated"] is True


@pytest.mark.skipif(not CAN_PARSE, reason="needs pylatexenc or pdflatex")
def test_a_clean_file_is_reported_as_the_file_not_its_directory(tmp_path):
    paper = tmp_path / "clean.md"
    paper.write_text("# t\n\nInline $a_1$.\n", encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "magi", "math", "check", str(paper), "--fast"],
                          capture_output=True, text=True, encoding="utf-8")
    assert "All formulas in" in done.stdout and "clean.md" in done.stdout


@pytest.mark.skipif(not CAN_PARSE, reason="needs pylatexenc or pdflatex")
def test_a_pdflatex_pass_that_breaks_is_reported_not_swallowed(tmp_path, monkeypatch):
    paper = tmp_path / "a.md"
    paper.write_text("# t\n\n$$\nx=1\n$$\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("tex exploded")

    monkeypatch.setattr(V, "validate_math_pdflatex", boom)
    entries = V.check_file(tmp_path, paper, use_pdflatex=True)
    assert any("could not run" in e["error"] and "tex exploded" in e["error"] for e in entries)


# --------------------------------------------------------------------------
# the preamble: common packages, a project's lines, a paper's definitions
# --------------------------------------------------------------------------

def test_the_preamble_knows_the_packages_valid_papers_were_failed_for():
    text = "\n".join(V.PREAMBLE)
    for package in ("xcolor", "bbm", "yfonts"):
        assert f"{package}.sty" in text


def test_extra_preamble_lines_come_before_the_document():
    tex, _, _ = V._document([V._Snippet("\\mK", True, 1, 1)], ["\\newcommand{\\mK}{K}"])
    assert tex.index("\\newcommand{\\mK}{K}") < tex.index("\\begin{document}")


def test_a_papers_recovered_definitions_are_read_from_beside_it(tmp_path):
    paper = tmp_path / "p.md"
    paper.write_text("x", encoding="utf-8")
    (tmp_path / ("p" + V.MACROS_SUFFIX)).write_text("\\newcommand{\\mK}{K}\n", encoding="utf-8")
    assert V.paper_preamble(paper) == ["\\newcommand{\\mK}{K}"]


def test_a_projects_preamble_comes_from_its_config(tmp_path):
    from magi import init_workspace
    from magi.core.config_edit import set_config_value

    init_workspace.main(["--topic-dir", str(tmp_path), "--name", "T"])
    set_config_value(tmp_path / "config.yaml", "math.preamble", ["\\usepackage{braket}"])
    assert V.project_preamble(tmp_path) == ["\\usepackage{braket}"]


@pytest.mark.skipif(not HAS_TEX or not V.HAS_PYLATEXENC, reason="needs pdflatex and pylatexenc")
def test_with_the_papers_definition_its_macro_is_not_an_error(tmp_path):
    paper = tmp_path / "p.md"
    paper.write_text("# t\n\n$$\n\\mK_{1} = 0\n$$\n", encoding="utf-8")
    without = V.check_file(tmp_path, paper)
    assert any("Undefined control sequence" in e["error"] for e in without)
    (tmp_path / ("p" + V.MACROS_SUFFIX)).write_text("\\newcommand{\\mK}{\\mathcal{K}}\n",
                                                     encoding="utf-8")
    assert V.check_file(tmp_path, paper) == []


def test_filing_a_document_runs_the_structural_check_not_the_pdflatex_pass():
    """The pass that hung a commit for twenty minutes is not a step of filing."""
    import inspect

    from magi.ingest import pipeline

    assert '"check", "--fast"' in inspect.getsource(pipeline.main)
