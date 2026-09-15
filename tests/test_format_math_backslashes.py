"""`magi math format` must not change what a formula means, and must say what it will do.

From 2026-09-15: pandoc wrote `\\\\[14.22636pt]` as `\\[14.22636pt]`, and
format's stray-delimiter rule — `replace(r'\\[', '[')` — took the remaining
backslash too, leaving `[14.22636pt]`: two rows of an array silently merged,
seven times in one paper. The same `replace` also turned every correct
`\\\\[2pt]` into `\\[2pt]`. It had no dry-run, and its orphan warning fired on
nearly every paper for blocks that were fine.
"""

from __future__ import annotations

from magi.kb import format_math, tidy_log
from magi.kb.format_math import _orphan_dollar_lines, fix_ocr_math_artifacts as fix


def test_a_row_break_with_its_spacing_keeps_both_backslashes():
    assert fix(r"a\\[2pt] b") == r"a\\[2pt] b"


def test_a_row_break_that_lost_a_backslash_gets_it_back():
    assert fix(r"a\[14.22636pt] b") == r"a\\[14.22636pt] b"


def test_a_stray_display_delimiter_still_goes():
    assert fix(r"\(x\) + \[y\]") == "(x) + [y]"


def test_a_multiline_block_in_a_table_is_not_an_orphan():
    text = ("  -- ------ -- ----\n     $$\n     x = 1.$$      [(69)]{.ltx_tag}\n"
            "  -- ------ -- ----\n")
    assert _orphan_dollar_lines(text) == []


def test_a_real_orphan_is_named_where_the_closer_went_missing():
    text = "$$\nx\n\nThe paragraph it swallowed.\n\n$$\ny\n$$\n"
    assert _orphan_dollar_lines(text) == [(1, "$$")]


def _project(tmp_path):
    from magi import init_workspace

    init_workspace.main(["--topic-dir", str(tmp_path), "--name", "T"])
    return tmp_path


def test_dry_run_writes_nothing_and_shows_the_change(tmp_path, capsys):
    root = _project(tmp_path)
    paper = root / "raw" / "papers" / "p.md"
    original = "# P\n\n$$x = 1$$\n\n$\\begin{array}{l}a\\[3pt]b\\end{array}$\n"
    paper.write_text(original, encoding="utf-8")
    format_math.main([str(paper), "--dry-run"])
    out = capsys.readouterr().out
    assert paper.read_text(encoding="utf-8") == original
    assert "-$$x = 1$$" in out and "\\\\[3pt]" in out and "would change" in out
    assert not (root / "output" / "tidy").exists()


def test_a_run_keeps_a_record_and_undo_puts_it_back(tmp_path):
    root = _project(tmp_path)
    paper = root / "raw" / "papers" / "p.md"
    original = "# P\n\n$$x = 1$$\n\nText.\n"
    paper.write_text(original, encoding="utf-8")
    format_math.main([str(paper)])
    assert paper.read_text(encoding="utf-8") != original
    records = sorted((root / "output" / "tidy").glob("*.json"))
    assert len(records) == 1

    restored, skipped, problems = tidy_log.undo(records[0])
    assert (restored, skipped, problems) == (1, 0, [])
    assert paper.read_text(encoding="utf-8") == original


def test_undo_leaves_alone_what_was_edited_after_the_run(tmp_path):
    root = _project(tmp_path)
    paper = root / "raw" / "papers" / "p.md"
    paper.write_text("# P\n\n$$x = 1$$\n", encoding="utf-8")
    format_math.main([str(paper)])
    record = sorted((root / "output" / "tidy").glob("*.json"))[0]
    paper.write_text(paper.read_text(encoding="utf-8").replace("x = 1", "x = 2"), encoding="utf-8")
    restored, skipped, problems = tidy_log.undo(record)
    assert restored == 0 and skipped == 1 and "edited since" in problems[0]
    assert "x = 2" in paper.read_text(encoding="utf-8")


def test_undo_keeps_a_crlf_file_crlf(tmp_path):
    root = tmp_path
    (root / "raw").mkdir()
    paper = root / "raw" / "p.md"
    paper.write_bytes(b"a\r\nnew line\r\nc\r\n")
    changes = tidy_log.changes_between("raw/p.md", "a\nold line\nc\n", "a\nnew line\nc\n", "test")
    log = tidy_log.write(root, "test", changes, results={"raw/p.md": "a\nnew line\nc\n"})
    assert tidy_log.undo(log)[0] == 1
    assert paper.read_bytes() == b"a\r\nold line\r\nc\r\n"
