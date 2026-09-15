"""What LaTeXML and pandoc do to formulas on the arXiv HTML route, repaired.

Every input here is a trimmed real one from the 382 before/after pairs a
person wrote by hand while tidying seventeen papers on 2026-09-15 — the
corpus `latexml_math` was built against. The shapes are kept exactly; the
formulas are shortened.
"""

from __future__ import annotations

import re

import pytest

from magi.ingest import latexml_math as L

TAG4 = "[(4)]{.ltx_tag .ltx_tag_equation .ltx_align_right}"


def grid(widths, rows):
    """A pandoc grid table. A row is a list of cells, or "partial" for a border
    whose last column spans on, or "full" for a border."""
    full = "+" + "+".join("-" * w for w in widths) + "+"
    out = [full]
    for row in rows:
        if row == "full":
            out.append(full)
        elif row == "partial":
            out.append("+" + "+".join("-" * w for w in widths[:-1]) + "+" + " " * widths[-1] + "|")
        else:
            out.append("|" + "|".join((" " + c).ljust(w) for c, w in zip(row, widths)) + "|")
    out.append(full)
    return "\n".join(out)


def run(md):
    new, repairs, left = L.normalize(md)
    return new, L.tally(repairs), left


def blocks(text):
    return re.findall(r"\$\$(.*?)\$\$", text, re.S)


# --------------------------------------------------------------------------
# equation tables
# --------------------------------------------------------------------------

def test_a_four_column_table_a_multiline_cell_broke_becomes_one_display():
    md = "\n".join([
        "+---+" + "-" * 60 + "+---+" + "-" * 20 + "+",
        "|   | $\\displaystyle",
        "\\begin{split}D_{x}=&\\;\\sum_{j}j_{x},\\\\    |   | [(4)]{.ltx_tag     |",
        "|   | D_{y}=&\\;\\sum_{j}j_{y},\\end{split}",
        "$                     |   | .ltx_tag_equation  |",
        "|   |" + " " * 60 + "|   | .ltx_align_right}  |",
        "+---+" + "-" * 60 + "+---+" + "-" * 20 + "+",
    ])
    new, kinds, left = run(md)
    assert new == ("$$\n\\displaystyle\n\\begin{split}D_{x}=&\\;\\sum_{j}j_{x},\\\\\n"
                   "D_{y}=&\\;\\sum_{j}j_{y},\\end{split}\n$$      " + TAG4)
    assert kinds == {"equation grid table (4 columns)": 1} and not left


def test_a_five_column_equation_group_becomes_gathered_with_its_number_after_it():
    md = grid([3, 3, 44, 3, 28], [
        ["", "", "$\\displaystyle\\Delta_{\\tau}p=0\\mod N~,$", "", "[(4.5)]{.ltx_tag"],
        ["", "", "", "", ".ltx_tag_equationgroup"],
        ["", "", "", "", ".ltx_align_right}"],
        "partial",
        ["", "", "$\\displaystyle\\Delta_{x}^{2}p=0\\mod N~.$", "", ""],
    ])
    new, kinds, _ = run(md)
    assert new == ("$$\n\\begin{gathered}\n\\displaystyle\\Delta_{\\tau}p=0\\mod N~, \\\\\n"
                   "\\displaystyle\\Delta_{x}^{2}p=0\\mod N~.\n\\end{gathered}\n$$      "
                   "[(4.5)]{.ltx_tag .ltx_tag_equationgroup .ltx_align_right}")
    assert kinds == {"equation grid table (5 columns)": 1}


def test_left_and_right_hand_sides_align_on_the_relation():
    md = grid([3, 25, 38, 3, 40], [
        ["", "$\\displaystyle E_{ij}$", "$\\displaystyle=\\partial_{i}A_{j},$", "",
         "[(2)]{.ltx_tag .ltx_tag_equationgroup}"],
        "partial",
        ["", "$\\displaystyle$", "$\\displaystyle=0.$", "", ""],
    ])
    body = blocks(run(md)[0])[0]
    assert "\\begin{aligned}" in body
    assert "\\displaystyle E_{ij} &\\displaystyle=\\partial_{i}A_{j}, \\\\" in body
    assert "&\\displaystyle=0." in body


def test_a_merged_last_row_and_side_by_side_equations_are_not_forced_into_alignment():
    widths = [3, 3, 30, 3, 30, 3, 20]
    full = "+" + "+".join("-" * w for w in widths) + "+"
    merged = ("|   |   |" + " $\\displaystyle c=1\\,.$".ljust(30) + "|"
              + " " * (3 + 1 + 30 + 1 + 3) + "|" + " " * 20 + "|")
    md = "\n".join([
        full,
        "|   |   |" + " $\\displaystyle a=1\\,,$".ljust(30) + "|   |"
        + " $\\displaystyle b=2\\,,$".ljust(30) + "|   |" + " [(2.19)]{.ltx_tag".ljust(20) + "|",
        "|   |   |" + " " * 30 + "|   |" + " " * 30 + "|   |" + " .ltx_tag_equation".ljust(20) + "|",
        "|   |   |" + " " * 30 + "|   |" + " " * 30 + "|   |" + " .ltx_align_right}".ljust(20) + "|",
        "+" + "+".join("-" * w for w in widths[:-1]) + "+" + " " * 20 + "|",
        merged,
        full,
    ])
    new, kinds, left = run(md)
    assert kinds == {"equation grid table (7 columns)": 1} and not left
    body = blocks(new)[0]
    assert "\\begin{gathered}" in body and "a=1\\,, \\qquad \\displaystyle b=2" in body
    assert "c=1" in body and "&" not in body


def test_a_data_table_of_formulas_is_not_an_equation_table():
    md = grid([12, 12], [["$x$", "$y$"], ["$1$", "$2$"]])
    assert run(md)[0] == md


# --------------------------------------------------------------------------
# simple tables
# --------------------------------------------------------------------------

def test_a_display_block_in_a_simple_table_whose_closer_runs_into_the_number_column():
    md = ("  -- ------------------------------ -- ------------------\n"
          "     $$\n"
          "     \\min\\big(v_{j}(M_{i}),e_{j}\\big)=e_{j}-k_{i,j}.$$      "
          "[(69)]{.ltx_tag .ltx_tag_equation .ltx_align_right}\n"
          "  -- ------------------------------ -- ------------------")
    assert run(md)[0] == ("$$\n\\min\\big(v_{j}(M_{i}),e_{j}\\big)=e_{j}-k_{i,j}.\n$$      "
                          "[(69)]{.ltx_tag .ltx_tag_equation .ltx_align_right}")


def test_rows_that_each_carry_a_number_become_one_display_each():
    border = "  -- " + "-" * 25 + " " + "-" * 16 + " " + "-" * 20 + " -- " + "-" * 52
    starts = [5, 31, 48]

    def line(a, b, c, tag):
        s = " " * 5 + a.ljust(26) + b.ljust(17) + c.ljust(21) + "   " + tag
        return s

    md = "\n".join([
        border,
        line("$\\displaystyle e^{2}$", "$\\displaystyle=$", "$\\displaystyle 1$",
             "[(24)]{.ltx_tag .ltx_tag_equation .ltx_align_right}"),
        line("$\\displaystyle\\theta_{e}$", "$\\displaystyle=$", "$\\displaystyle 0$",
             "[(25)]{.ltx_tag .ltx_tag_equation .ltx_align_right}"),
        border,
    ])
    new, kinds, _ = run(md)
    assert kinds == {"equation simple table": 1}
    assert len(blocks(new)) == 2
    assert re.search(r"e\^\{2\} \\displaystyle= \\displaystyle 1\n\$\$      \[\(24\)\]", new)
    assert re.search(r"\\theta_\{e\} \\displaystyle= \\displaystyle 0\n\$\$      \[\(25\)\]", new)


def test_two_formulas_dealt_out_across_the_rows_of_a_table_are_put_back_together():
    border = "  -- " + "-" * 61 + " -- " + "-" * 44 + " --"
    left = ["$\\displaystyle l_{y}(X)=\\begin{pmatrix}1+y\\\\", "0\\end{pmatrix},$"]
    right = ["$\\displaystyle l_{x}(X)=\\begin{pmatrix}0\\\\", "1+x\\end{pmatrix},$"]
    md = "\n".join([border] + [" " * 5 + a.ljust(65) + b for a, b in zip(left, right)] + [border])
    new, kinds, left_over = run(md)
    assert kinds == {"equation simple table": 1} and not left_over
    body = blocks(new)[0]
    assert "\\end{pmatrix}, \\qquad \\displaystyle l_{x}(X)" in body
    assert "--" not in new


def test_figure_code_in_a_table_cell_does_not_throw_the_columns_off():
    """The placeholder is shorter than the figure code; replacing it first moved
    the next cell out from under its column."""
    border = "  -- " + "-" * 80 + " " + "-" * 40 + " -- " + "-" * 52
    a = "$\\displaystyle A_{v}\\ \\vbox{\\hbox{\\includegraphics[page={6}]{TikzFigures}}}$"
    b = "$\\displaystyle=N_{ij}^{k}\\,,$"
    tag = "[(40)]{.ltx_tag .ltx_tag_equation .ltx_align_right}"
    md = "\n".join([border, " " * 5 + a.ljust(81) + b.ljust(41) + "   " + tag, border])
    new, kinds, left = run(md)
    assert "--" not in new and not left
    assert "\\text{[omitted: figure TikzFigures p.6]}" in new
    assert new.rstrip().endswith("$$      " + tag)


def test_a_table_that_breaks_its_formulas_for_a_reader_too_is_reported():
    from magi.kb.validate_math_latex import validate_math_pylatexenc

    if not validate_math_pylatexenc("$\\begin{pmatrix}$")[0]:
        pytest.skip("no structural checker available here")
    border = "  -- " + "-" * 30 + " " + "-" * 30 + " " + "-" * 30 + " --"
    cells = ["$\\displaystyle a=\\begin{pmatrix}1\\\\", "0\\end{pmatrix},$"]
    md = "\n".join([border] + [" " * 5 + cells[r].ljust(31) * 3 for r in range(2)] + [border])
    new, _, left = run(md)
    assert new == md and [s.kind for s in left] == ["equation simple table"]


def test_a_table_this_pass_does_not_rebuild_is_left_and_not_called_damage():
    md = ("  -- ----------------------- ----------\n"
          "     $\\displaystyle a=b$     some words\n"
          "  -- ----------------------- ----------")
    new, kinds, left = run(md)
    assert new == md and not kinds and not left


# --------------------------------------------------------------------------
# inside formulas
# --------------------------------------------------------------------------

def test_an_equation_number_inside_a_display_goes_after_its_closer():
    md = ("$$\n     U=\\begin{pmatrix}a&b\\\\      [(19)]{.ltx_tag .ltx_tag_equation .ltx_align_right}\n"
          "     c&d\\end{pmatrix},\n     $$")
    new, kinds, _ = run(md)
    assert ".ltx_tag" not in blocks(new)[0]
    assert new.rstrip().endswith("$$      [(19)]{.ltx_tag .ltx_tag_equation .ltx_align_right}")
    assert kinds == {"tag-inside-display": 1}


def test_a_vskip_row_break_in_a_numbered_array_is_rewritten_with_its_spacing():
    border = "  -- " + "-" * 70 + " -- " + "-" * 52
    md = "\n".join([
        border,
        "     $$",
        "     \\begin{array}[]{l}[N_{i},T]=0\\\\          [(22)]{.ltx_tag .ltx_tag_equation .ltx_align_right}",
        "     \\vskip 2.84526pt[N_{i},V]=0\\\\",
        "     \\vskip 2.84526pt[N_{i},S]=0\\end{array}.",
        "     $$",
        border,
    ])
    new, kinds, _ = run(md)
    assert new == ("$$\n\\begin{array}[]{l}[N_{i},T]=0\\\\[2.84526pt]\n[N_{i},V]=0\\\\[2.84526pt]\n"
                   "[N_{i},S]=0\\end{array}.\n$$      [(22)]{.ltx_tag .ltx_tag_equation .ltx_align_right}")
    assert "\\vskip" not in new and kinds["vskip-linebreak"] == 2


def test_a_row_break_that_lost_a_backslash_gets_it_back():
    new, kinds, _ = run("$$\n\\begin{array}{ll}1&\\mbox{if }L\\[14.22636pt]\nL&m\\end{array}\n$$")
    assert "\\\\[14.22636pt]" in new and kinds["lost-linebreak"] == 1


def test_hline_cr_becomes_hline():
    new, _, _ = run("$$\n\\begin{pmatrix}f&0\\\\\n\\hline\\cr g&h\\end{pmatrix}\n$$")
    assert "\\hline\\cr" not in new and "\\hline g&h" in new


def test_a_bracket_opening_the_line_after_a_row_break_is_guarded():
    new, kinds, _ = run("text $\\begin{array}[]{c}\\gamma_{j}\\\\\n[j=1,\\ldots,n]\\end{array}$ more")
    assert "\\\\[0pt]\n[j=1" in new and kinds["bracket-after-row-break"] == 1


def test_a_dollar_nested_in_text_is_taken_out():
    new, kinds, _ = run("$x\\text{along $\\widehat{y}$,}z$")
    assert new == "$x\\text{along }\\widehat{y}\\text{,}z$"
    assert kinds == {"nested-$-in-text": 1}


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

@pytest.mark.parametrize("leak,marker", [
    ("\\begin{tikzpicture} \\fdot{0} \\end{tikzpicture}", "\\text{[omitted: tikz picture: fdot]}"),
    ("\\vbox{\\hbox{\\includegraphics[page={6}]{TikzFigures}}}", "\\text{[omitted: figure TikzFigures p.6]}"),
    ("\\includegraphics[trim=0.0pt\n142.5pt,clip={true}]{Hexagon_Model.pdf}",
     "\\text{[omitted: figure Hexagon\\_Model.pdf]}"),
    ("\\begin{picture}\\put(0,0){\\line{1}{0}{0.3}}\\end{picture}", "\\text{[omitted: picture]}"),
    ("\\lx@xy@svg{\\hbox{$\\textstyle{X}$}\\hbox{$\\textstyle{Y}$}}", "\\text{[omitted: xy-pic diagram: X, Y]}"),
    ("\\hbox to146.08pt{\\vbox to3.81pt{\\pgfpicture\\makeatletter\\lxSVG@stroke{}}}",
     "\\text{[omitted: pgf drawing]}"),
])
def test_figure_code_inside_a_formula_becomes_the_one_placeholder(leak, marker):
    new, _, left = run(f"Before $a={leak}$ after.")
    assert new == f"Before $a={marker}$ after."
    assert L.PLACEHOLDER_RE.search(new) and not left


def test_figure_code_outside_any_formula_is_reported_not_rewritten():
    md = "A stray \\includegraphics{x.png} in prose."
    new, _, left = run(md)
    assert new == md and [s.kind for s in left] == ["figure-leak: includegraphics"]


# --------------------------------------------------------------------------
# honesty
# --------------------------------------------------------------------------

def test_a_lost_delimiter_is_reported_for_a_person():
    md = "  -- ----\n     $$$\n\\mathrm{Tor}}_{i}\n$$   \n  -- ----"
    _, _, left = run(md)
    assert "broken delimiters" in {s.kind for s in left}


def test_running_it_twice_changes_nothing_the_second_time():
    md = "\n\n".join([
        grid([3, 3, 44, 3, 45], [
            ["", "", "$\\displaystyle\\Delta p=0~,$", "", "[(4.5)]{.ltx_tag"],
            ["", "", "", "", ".ltx_tag_equationgroup .ltx_align_right}"],
        ]),
        "$x\\text{along $y$}\\\\\n[a,b]$",
        "$$\nU=a\\\\      [(1)]{.ltx_tag .ltx_tag_equation}\nb\n$$",
    ])
    once = L.normalize(md)[0]
    assert L.normalize(once) == (once, [], [])


def test_only_pandoc_renderings_of_latexml_are_candidates():
    assert L.looks_like_latexml("::: {.ltx_para}\ntext\n:::")
    assert not L.looks_like_latexml("# A card\n\n$x$\n")
