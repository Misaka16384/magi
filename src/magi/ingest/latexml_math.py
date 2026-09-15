"""What LaTeXML and pandoc do to mathematics on the way from arXiv, undone.

arXiv's HTML carries each formula's original TeX (see `arxiv_html`), and that
part is faithful. The layout around it is not. LaTeXML sets every numbered
equation, every `align` and every equation group as an HTML table, and pandoc
writes those tables back out as Markdown tables: a formula becomes a cell, its
number another cell, and a formula that runs over several lines becomes a cell
that breaks the table it sits in — leaving column borders inside the TeX.

Measured on seventeen papers ingested on one day, all by this route: 22
four-column and 64 five-column equation tables, 58 display blocks with the
equation number swept inside them, about 130 places where figure code landed
inside a formula, 14 `\\hline\\cr`, 9 `$` nested inside `\\text{}`, 7 row breaks
that lost a backslash, and one `\\\\` + `\\vskip` that sent pdflatex into an
endless loop. Review showed none of it; all of it surfaced after commit. The
382 before/after pairs a person wrote while repairing those papers by hand are
the corpus these repairs were built against.

Every repair here is mechanical and checked, and **skips rather than guesses**:
a table whose cells do not sit under its borders, a cell whose `$` do not pair,
a number that is not a clean LaTeXML tag — each is left exactly as it was and
reported, so review can say what is still wrong instead of pretending.
"""

from __future__ import annotations

import argparse
import bisect
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple


class Repair(NamedTuple):
    kind: str
    line: int
    before: str
    after: str


class Skipped(NamedTuple):
    kind: str
    line: int
    why: str


# --------------------------------------------------------------------------
# shared pieces
# --------------------------------------------------------------------------

#: What a figure leaves behind inside a formula. One shape, so that a reader —
#: and the compile and ask skills — can tell "a picture was removed here" from
#: text the author wrote. Before this existed a repair session invented three
#: (`[tikz figure: …]`, `[figure: file]`, `[pgf figure omitted]`) and nothing
#: downstream could tell any of them from prose.
PLACEHOLDER_PREFIX = "[omitted: "
PLACEHOLDER_RE = re.compile(r"\\text\{\[omitted: [^\]]*\]\}")


def placeholder(what: str) -> str:
    return r"\text{" + PLACEHOLDER_PREFIX + _textify(what) + "]}"


def _textify(s: str) -> str:
    """Make *s* safe inside `\\text{…}`."""
    s = s.replace("\\", "").replace("{", "").replace("}", "")
    s = s.replace("[", "(").replace("]", ")")
    for ch in "_#%&$^~":
        s = s.replace(ch, "\\" + ch)
    return " ".join(s.split())


#: A TeX length, the kind `\\[<length>]` and `\vskip` take.
_LENGTH = r"-?\s*(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|pc|in|bp|cm|mm|dd|cc|sp|em|ex|mu)"

#: One LaTeXML equation number as pandoc writes it: `[(12)]{.ltx_tag …}`.
_TAG = r"\[\([^\[\]()\n]+\)\]\{\.ltx_tag[^}]*\}"
_TAG_RE = re.compile(r"[ \t]*" + _TAG)
_TAGS_ONLY = re.compile(r"(?:" + _TAG + r"\s*)+")

#: `\\` whose next non-blank line starts with `[` — TeX reads that bracket as
#: the row break's spacing argument. `\\[0pt]` is the same break, said safely.
_GUARD_RE = re.compile(r"\\\\([ \t]*\n[ \t]*)(?=\[)")

#: A right-hand side: the second column of an alignment starts with its relation.
_RELATION_START = re.compile(
    r"^\s*(?:\\displaystyle\s*)?(?:=|<|>|:=|\\(?:leq?|geq?|le|ge|neq?|equiv|approx|"
    r"sim|simeq|cong|to|rightarrow|Rightarrow|longrightarrow|Longrightarrow|mapsto|"
    r"propto|in|subset|subseteq|supset|supseteq|coloneqq|iff|implies)(?![A-Za-z]))")

_FENCE_RE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)


def _without_code(text: str) -> str:
    """*text* with fenced code blanked out, every offset unchanged."""
    return _FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _match_brace(text: str, i: int) -> int:
    """Index of the `}` closing the group that opens at `text[i] == '{'`, or -1."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _dollars(s: str) -> int:
    return len(re.findall(r"(?<!\\)\$", s))


#: A formula in Markdown: a display block, or inline maths that does not run
#: across a paragraph break. Inline maths stops at any `$`, as a renderer does.
_SPAN_RE = re.compile(
    r"\$\$(?:(?!\n[ \t]*\n).)+?\$\$"
    r"|(?<![\\$])\$(?!\$)(?:(?!\n[ \t]*\n)[^$])+?(?<!\\)\$(?!\$)", re.S)


def math_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _SPAN_RE.finditer(_without_code(text))]


def _inside(spans, start, end) -> bool:
    k = bisect.bisect_right([s for s, _ in spans], start) - 1
    return k >= 0 and spans[k][0] <= start and end <= spans[k][1]


def _replace_all(text, edits):
    """Apply non-overlapping (start, end, new) edits."""
    out, pos = [], 0
    for start, end, new in sorted(edits):
        out.append(text[pos:start])
        out.append(new)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _display(body: str, tags: str = "", indent: str = "") -> list[str]:
    body = _GUARD_RE.sub(lambda m: "\\\\[0pt]" + m.group(1), body)
    return ([indent + "$$"] + [indent + b for b in body.split("\n")]
            + [indent + "$$" + ("      " + tags if tags else "")])


# --------------------------------------------------------------------------
# `$` nested inside \text{} — valid TeX, but a Markdown renderer ends the
# inline formula at the inner `$`, and so does every checker that reads it.
# --------------------------------------------------------------------------

_TEXT_CMD_RE = re.compile(r"\\(text|textrm|textit|textbf|textsf|texttt|textnormal|mbox)\s*\{")


def unnest_text_dollars(tex: str) -> tuple[str, int]:
    """`\\text{along $x$,}` → `\\text{along }x\\text{,}`. (new tex, count)."""
    out, pos, count = [], 0, 0
    for m in _TEXT_CMD_RE.finditer(tex):
        if m.start() < pos:
            continue
        open_at = m.end() - 1
        close_at = _match_brace(tex, open_at)
        if close_at < 0:
            continue
        inner = tex[open_at + 1:close_at]
        n = _dollars(inner)
        if not n or n % 2:
            continue
        parts = re.split(r"(?<!\\)\$", inner)
        pieces = []
        for k, part in enumerate(parts):
            if k % 2 == 0:
                if part:
                    pieces.append("\\" + m.group(1) + "{" + part + "}")
            else:
                pieces.append(part)
        out.append(tex[pos:m.start()])
        out.append("".join(pieces))
        pos = close_at + 1
        count += 1
    out.append(tex[pos:])
    return "".join(out), count


def _nested_dollars(text, repairs, skipped):
    body = _without_code(text)
    edits = []
    for m in _TEXT_CMD_RE.finditer(body):
        open_at = m.end() - 1
        close_at = _match_brace(body, open_at)
        if close_at < 0 or "$" not in body[open_at:close_at]:
            continue
        if edits and m.start() < edits[-1][1]:
            continue
        old = text[m.start():close_at + 1]
        new, n = unnest_text_dollars(old)
        if n and new != old:
            edits.append((m.start(), close_at + 1, new))
            repairs.append(Repair("nested-$-in-text", _line_of(text, m.start()), old, new))
    return _replace_all(text, edits)


def _formula_inner(s: str) -> str:
    """The TeX of one `$…$` or `$$…$$` item; ValueError when it is not one formula.

    Empty for a cell that holds only `\\displaystyle` — LaTeXML's empty
    left-hand side.
    """
    s = s.strip()
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        inner = s[2:-2]
    elif s.startswith("$") and s.endswith("$") and len(s) > 1:
        inner = s[1:-1]
    else:
        raise ValueError("a cell that is not one formula")
    if _dollars(inner):
        inner, _ = unnest_text_dollars(inner)
        if _dollars(inner):
            raise ValueError("a formula whose `$` do not pair")
    if not s.startswith("$$") and not inner.lstrip().startswith("\\displaystyle"):
        raise ValueError("a cell LaTeXML did not set as display maths")
    inner = inner.strip("\n").rstrip()
    lines = inner.split("\n")
    cut = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    inner = "\n".join(l[cut:].rstrip() for l in lines).strip()
    return "" if inner == "\\displaystyle" else inner


def _join_row(cells):
    """One row of formulas that share a line, in column order."""
    cells = [c for c in cells]
    if len(cells) == 1:
        return cells[0]
    if len(cells) == 2:
        if not cells[1]:
            return cells[0]
        if not cells[0]:
            return "&" + cells[1] if _RELATION_START.match(cells[1]) else cells[1]
        if _RELATION_START.match(cells[1]):
            return f"{cells[0]} &{cells[1]}"
        return f"{cells[0]} \\qquad {cells[1]}"
    if len(cells) == 3:
        # lhs, relation, rhs: aligned's columns alternate right/left, so the
        # relation goes with the right-hand side.
        return f"{cells[0]} &{cells[1]} {cells[2]}"
    return " & ".join(f"{cells[k]} &{cells[k + 1]}" for k in range(0, len(cells), 2))


def _assemble(rows) -> str:
    """A display body from rows of formula cells (all rows the same width)."""
    rows = [r for r in rows if any(r)]
    if not rows:
        raise ValueError("no formula in the table")
    width = len(rows[0])
    if width > 3 and width % 2:
        raise ValueError(f"{width} formula columns do not pair into an alignment")
    if len(rows) == 1:
        # One row has nothing to align with: an `&` here is outside any
        # alignment, which pdflatex reports as a misplaced tab.
        return _row_text(rows[0])
    joined = [_join_row(r) for r in rows]
    if any("&" in j for j in joined):
        return "\\begin{aligned}\n" + " \\\\\n".join(joined) + "\n\\end{aligned}"
    return "\\begin{gathered}\n" + " \\\\\n".join(joined) + "\n\\end{gathered}"


def _clean_tags(parts) -> str:
    tag = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if tag and not _TAGS_ONLY.fullmatch(tag + " "):
        raise ValueError(f"the number cell is not a clean tag ({tag[:50]!r})")
    return tag


# --------------------------------------------------------------------------
# grid tables: numbered equations, aligned rows, equation groups
# --------------------------------------------------------------------------

_GRID_BORDER = re.compile(r"^([ \t]*)\+(?:-+\+)+[ \t]*$")


def _grid_end(lines, i, ind):
    """Index of the border that closes the grid table opening at line *i*."""
    j = i + 1
    limit = min(len(lines), i + 600)
    while j < limit:
        if _GRID_BORDER.match(lines[j]) and lines[j].startswith(ind):
            nxt = lines[j + 1] if j + 1 < len(lines) else ""
            if nxt.startswith(ind + "|") or nxt.startswith(ind + "+"):
                j += 1
                continue
            return j
        j += 1
    return None


def _positional_cells(block, ind):
    """(row, column, text) pieces by the border's `+` positions; ValueError if off.

    A cell that spans columns — the last row of an equation group often does —
    has no `|` under the inner borders it covers, and its text belongs to the
    first column it starts in.
    """
    border = block[0][len(ind):].rstrip()
    bounds = [k for k, ch in enumerate(border) if ch == "+"]
    ncols = len(bounds) - 1
    row, pieces = 0, []
    for raw in block[1:-1]:
        if not raw.startswith(ind):
            raise ValueError("a line outside the table's indentation")
        line = raw[len(ind):]
        if line.startswith("+"):
            row += 1
            for k in range(ncols):
                cell = line[bounds[k] + 1:bounds[k + 1]] if len(line) > bounds[k] + 1 else ""
                if cell.strip() and not set(cell.strip()) <= {"-", "+"}:
                    pieces.append((row, k, cell.strip()))
            continue
        if (not line.startswith("|") or len(line.rstrip()) < bounds[-1]
                or line[bounds[-1]] != "|"):
            raise ValueError("cell borders not under the table's border")
        walls = [b for b in bounds if line[b] == "|"]
        for a, b in zip(walls, walls[1:]):
            cell = line[a + 1:b]
            if cell.strip():
                pieces.append((row, bounds.index(a), cell.rstrip()))
    return pieces, ncols


def _broken_cells(block):
    """The four-column shape a multi-line cell broke: `|   | math |   | tag |`.

    Pandoc wrote the formula's own newlines into the table, so nothing lines
    up; the spacer columns are what is still recognisable.
    """
    pieces = []
    for raw in block[1:-1]:
        s = re.sub(r"^\s*\|\s{3}\|\s?", "", raw, count=1)
        t = re.search(r"\s*\|\s{3}\|\s*([^|]*?)\s*\|\s*$", s)
        if t:
            if t.group(1):
                pieces.append((0, 3, t.group(1)))
            s = s[:t.start()]
        if s.strip():
            pieces.append((0, 1, s.rstrip()))
    return pieces


def _grid_display(pieces, ncols):
    """(body, tags) for a grid table's cell pieces, or ValueError."""
    tag_cols = {k for k in range(ncols)
                if ".ltx_tag" in " ".join(t for _, c, t in pieces if c == k)}
    tags = _clean_tags(t.strip() for _, c, t in pieces if c in tag_cols)
    cells: dict[int, dict[int, list[str]]] = {}
    for r, c, text in pieces:
        if c not in tag_cols:
            cells.setdefault(r, {}).setdefault(c, []).append(text)
    rows = {r: {c: _formula_inner("\n".join(v)) for c, v in row.items()}
            for r, row in cells.items()}
    columns = sorted({c for row in rows.values() for c, tex in row.items() if tex})
    body = _assemble([[rows[r].get(c, "") for c in columns] for r in sorted(rows)])
    return body, tags


def _grid_tables(text, repairs, skipped):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = _GRID_BORDER.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind = m.group(1)
        end = _grid_end(lines, i, ind)
        if end is None:
            out.append(lines[i])
            i += 1
            continue
        block = lines[i:end + 1]
        blob = "\n".join(block)
        if "$" not in blob or "\\displaystyle" not in blob:
            out.extend(block)
            i = end + 1
            continue
        ncols = block[0].count("+") - 1
        kind = f"equation grid table ({ncols} columns)"
        try:
            try:
                pieces, ncols = _positional_cells(block, ind)
                body, tags = _grid_display(pieces, ncols)
            except ValueError:
                if ncols != 4:
                    raise
                body, tags = _grid_display(_broken_cells(block), 4)
        except ValueError as exc:
            skipped.append(Skipped(kind, len(out) + 1, str(exc)))
            out.extend(block)
            i = end + 1
            continue
        new = _display(body, tags, ind)
        repairs.append(Repair(kind, len(out) + 1, blob, "\n".join(new)))
        out.extend(new)
        i = end + 1
    return "\n".join(out)


# --------------------------------------------------------------------------
# simple tables: one numbered formula per row, or a formula over several rows
# --------------------------------------------------------------------------

_SIMPLE_BORDER = re.compile(r"^[ \t]*-{2,}(?:[ \t]+-{2,})+[ \t]*$")


class _Damaged(ValueError):
    """A table that is broken, not merely one this pass does not rebuild.

    Most simple tables it declines are correct LaTeXML layout — a number beside
    a one-line formula — and reporting those would bury the ones a person has
    to rebuild from the PDF: a formula whose lines were dealt out across the
    rows of a table.
    """


_DISPLAY_ITEM_RE = re.compile(
    r"[ \t]*\$\$((?:(?!\$\$).)+?)\$\$[ \t]*((?:" + _TAG + r"[ \t]*)*)[ \t]*(?:\n|$)", re.S)
_INLINE_TOKEN_RE = re.compile(r"(?<!\\)\$(?!\$)(?:[^$\\]|\\.)*?(?<!\\)\$")
_TRAILING_TAGS_RE = re.compile(r"((?:" + _TAG + r"[ \t]*)+)[ \t]*$")


def _display_items(joined):
    """A table of `$$` blocks, each with the numbers after its closer.

    Positions are no help here: pandoc writes the formula's own lines from
    column 0 and lets the closing line run into the number column.
    """
    out, pos = [], 0
    while joined[pos:].strip():
        m = _DISPLAY_ITEM_RE.match(joined, pos)
        if not m:
            raise ValueError("text beside the formulas in the table")
        inner = _formula_inner("$$" + m.group(1) + "$$")
        tags = _clean_tags([m.group(2)]) if m.group(2).strip() else ""
        if inner:
            if out:
                out.append("")
            out.extend(_display(inner, tags))
        pos = m.end()
    if not out:
        raise ValueError("no formula in the table")
    return out


def _row_text(cells):
    """Formulas that share a row, as one display without alignment."""
    cells = [c for c in cells if c]
    if len(cells) == 2 and not _RELATION_START.match(cells[1]):
        return f"{cells[0]} \\qquad {cells[1]}"
    return " ".join(cells)


class _Split(ValueError):
    """A formula cut off at the edge of its cell — the table split it."""


def _balanced(tex: str) -> bool:
    """Braces close and every \\begin has its \\end."""
    depth, i = 0, 0
    while i < len(tex):
        c = tex[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0 and len(re.findall(r"\\begin\s*\{", tex)) == len(re.findall(r"\\end\s*\{", tex))


def _inline_rows(content, columns):
    """One-line `$\\displaystyle…$` formulas, placed in columns by where they start."""
    starts = [a for a, _ in columns]
    rows = []
    for line in content:
        m = _TRAILING_TAGS_RE.search(line)
        body, tags = (line[:m.start()], _clean_tags([m.group(1)])) if m else (line, "")
        cells = {}
        for token in _INLINE_TOKEN_RE.finditer(body):
            col = max(k for k, s in enumerate(starts) if s <= token.start()) \
                if token.start() >= starts[0] else 0
            if col in cells:
                raise _Split("two formulas in one cell")
            tex = _formula_inner(token.group(0))
            # `$` can pair up across two formulas that each broke at a line
            # end — `$A\\      $B\\` — and read as one whole formula. Its
            # braces and environments say otherwise.
            if not _balanced(tex):
                raise _Split("a formula cut off at the edge of its cell")
            cells[col] = tex
        if _INLINE_TOKEN_RE.sub("", body).strip():
            raise ValueError("text beside the formulas in the table")
        rows.append((cells, tags))
    columns_used = sorted({c for cells, _ in rows for c, tex in cells.items() if tex})
    if not columns_used:
        raise ValueError("no formula in the table")
    grid = [[cells.get(c, "") for c in columns_used] for cells, _ in rows]
    tags = [t for _, t in rows]
    out: list[str] = []
    if len(rows) > 1 and all(tags):
        # Every row carries its own number: one display per row keeps each
        # number with its formula, which one aligned block cannot.
        for cells, tag in zip(grid, tags):
            if out:
                out.append("")
            out.extend(_display(_row_text(cells), tag))
    else:
        out.extend(_display(_assemble(grid), " ".join(t for t in tags if t)))
    return out


def _spread_rows(content, columns):
    """Formulas whose lines were dealt out across a table's rows.

    Each column's lines are gathered until its `$` pair closes. Anything that
    does not add up is damage a person has to rebuild from the PDF.
    """
    starts = [a for a, _ in columns]

    def cell(line, k):
        a = starts[k]
        b = starts[k + 1] if k + 1 < len(starts) else len(line)
        return line[a:b] if len(line) > a else ""

    ncols = len(columns)
    for line in content:
        if line[:starts[0]].strip():
            raise _Damaged("a formula spread over the rows of a table, not under its columns")
    grid = [[cell(line, k) for k in range(ncols)] for line in content]
    tag_cols = {k for k in range(ncols) if ".ltx_tag" in " ".join(r[k] for r in grid)}
    maths = [k for k in range(ncols) if k not in tag_cols and any(r[k].strip() for r in grid)]
    if not maths or len(maths) > 2:
        raise _Damaged("a formula spread over the rows of a table")
    items = {}
    for k in maths:
        found, current, first = [], [], None
        for idx, row in enumerate(grid):
            piece = row[k].strip()
            if not piece:
                if current:
                    raise _Damaged("a formula spread over rows with an empty cell in it")
                continue
            if not current:
                first = idx
                if not piece.startswith("$"):
                    raise _Damaged("a formula spread over the rows of a table")
            current.append(row[k].rstrip())
            joined = "\n".join(current).strip()
            if _dollars(joined) % 2 == 0 and joined.endswith("$") and len(joined) > 1:
                try:
                    found.append((first, idx, _formula_inner(joined)))
                except ValueError as exc:
                    raise _Damaged(str(exc)) from None
                current = []
        if current:
            raise _Damaged("a formula that never closes inside its table")
        items[k] = found
    tags_by_line = {idx: " ".join(row[k].strip() for k in sorted(tag_cols) if row[k].strip())
                    for idx, row in enumerate(grid)}

    def tags_for(first, last):
        try:
            return _clean_tags(tags_by_line[i] for i in range(first, last + 1) if tags_by_line.get(i))
        except ValueError as exc:
            raise _Damaged(str(exc)) from None

    out: list[str] = []
    if len(maths) == 1:
        for first, last, tex in items[maths[0]]:
            if tex:
                if out:
                    out.append("")
                out.extend(_display(tex, tags_for(first, last)))
    else:
        left, right = items[maths[0]], items[maths[1]]
        by_left = {f: (f, l, t) for f, l, t in left}
        by_right = {f: (f, l, t) for f, l, t in right}
        row_starts = sorted(set(by_left) | set(by_right))
        rows, spans = [], []
        for s in row_starts:
            a, b = by_left.get(s), by_right.get(s)
            rows.append([a[2] if a else "", b[2] if b else ""])
            spans.append((s, max(x[1] for x in (a, b) if x)))
        if all(_RELATION_START.match(r[1]) for r in rows if r[1]):
            out.extend(_display(_assemble(rows), tags_for(spans[0][0], spans[-1][1])))
        else:
            for row, (first, last) in zip(rows, spans):
                if out:
                    out.append("")
                out.extend(_display(_row_text(row), tags_for(first, last)))
    if not out:
        raise _Damaged("no formula in the table")
    return out


def _simple_display(content, columns):
    """Replacement lines for one simple table; ValueError (or _Damaged) if not.

    Three shapes, told apart by evidence rather than guessed: `$$` blocks; one
    formula per cell per line; and — only when a line's `$` do not pair, or a
    cell's formula is cut off at its edge — formulas dealt out across rows.
    """
    joined = "\n".join(content)
    if "$$" in joined:
        return _display_items(joined)
    if not any(_dollars(_TRAILING_TAGS_RE.sub("", line)) % 2 for line in content):
        try:
            return _inline_rows(content, columns)
        except _Split:
            pass
    return _spread_rows(content, columns)


def _fails_to_parse(md: str) -> bool:
    """Would `magi math check`'s structural pass flag anything in *md*?"""
    try:
        from magi.kb.validate_math_latex import validate_math_pylatexenc
    except Exception:  # noqa: BLE001 — without the checker, say it rather than hide it
        return True
    issues, _, _ = validate_math_pylatexenc(md)
    return bool(issues)


def _simple_table_equations(text, repairs, skipped):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if not _SIMPLE_BORDER.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        k = i + 1
        while k < len(lines) and k - i < 400 and lines[k].strip() and not _SIMPLE_BORDER.match(lines[k]):
            k += 1
        if k >= len(lines) or not _SIMPLE_BORDER.match(lines[k]) or k == i + 1:
            out.append(lines[i])
            i += 1
            continue
        content = lines[i + 1:k]
        joined = "\n".join(content)
        if "$" not in joined or not ("\\displaystyle" in joined or "$$" in joined):
            out.extend(lines[i:k + 1])
            i = k + 1
            continue
        columns = [(m.start(), m.end()) for m in re.finditer(r"-+", lines[i])]
        try:
            new = _simple_display(content, columns)
        except _Damaged as exc:
            # Said only when the table as it stands is broken for a reader too.
            # A formula this pass cannot follow across rows, but that parses
            # where it is, is layout left alone — not work for a person.
            if _fails_to_parse(joined):
                skipped.append(Skipped("equation simple table", len(out) + 1, str(exc)))
            out.extend(lines[i:k + 1])
            i = k + 1
            continue
        except ValueError:
            # Correct layout this pass does not rebuild: not damage, so not
            # reported — a count of those would bury the tables that are.
            out.extend(lines[i:k + 1])
            i = k + 1
            continue
        repairs.append(Repair("equation simple table", len(out) + 1,
                              "\n".join(lines[i:k + 1]), "\n".join(new)))
        out.extend(new)
        i = k + 1
    return "\n".join(out)


# --------------------------------------------------------------------------
# inside display blocks
# --------------------------------------------------------------------------

_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.S)


def _tags_inside_display(text, repairs, skipped):
    edits = []
    for m in _DISPLAY_RE.finditer(_without_code(text)):
        inner = text[m.start(1):m.end(1)]
        # A "block" that crosses a paragraph break is two delimiters from two
        # different formulas; moving a tag out of that would move it out of
        # prose.
        if re.search(r"\n[ \t]*\n", inner) or ".ltx_tag" not in inner:
            continue
        tags = [t.group(0).strip() for t in _TAG_RE.finditer(inner)]
        if not tags:
            continue

        def drop(t):
            follows = inner[t.end():t.end() + 1]
            return "" if follows in ("\n", "") else " "

        new_inner = _TAG_RE.sub(drop, inner)
        new_inner = _GUARD_RE.sub(lambda g: "\\\\[0pt]" + g.group(1), new_inner)
        new = "$$" + new_inner + "$$      " + " ".join(tags)
        edits.append((m.start(), m.end(), new))
        repairs.append(Repair("tag-inside-display", _line_of(text, m.start()),
                              text[m.start():m.end()], new))
    return _replace_all(text, edits)


_VSKIP_RE = re.compile(r"\\\\([ \t]*(?:\n[ \t]*)?)\\vskip[ \t]*(" + _LENGTH + r")")


def _vskip_linebreaks(text, repairs, skipped):
    """`\\\\` + `\\vskip 2.84526pt` → `\\\\[2.84526pt]`: LaTeXML's expansion of
    `\\\\[1mm]`, which loops pdflatex forever inside an array."""
    def repl(m):
        new = "\\\\[" + re.sub(r"\s+", "", m.group(2)) + "]" + m.group(1)
        repairs.append(Repair("vskip-linebreak", _line_of(text, m.start()), m.group(0), new))
        return new
    text = _VSKIP_RE.sub(repl, text)
    for m in re.finditer(r"\\vskip", text):
        skipped.append(Skipped("vskip-linebreak", _line_of(text, m.start()),
                               "a \\vskip that does not follow a row break"))
    return text


_LOST_LINEBREAK_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\\[(\s*" + _LENGTH + r"\s*)\]")


def _lost_linebreaks(text, repairs, skipped):
    """`\\[14.22636pt]` → `\\\\[14.22636pt]`: a row break that lost a backslash."""
    def repl(m):
        new = m.group(1) + "\\\\[" + re.sub(r"\s+", "", m.group(2)) + "]"
        repairs.append(Repair("lost-linebreak", _line_of(text, m.start()), m.group(0), new))
        return new
    return _LOST_LINEBREAK_RE.sub(repl, text)


_HLINE_CR_RE = re.compile(r"\\hline\\cr(?![A-Za-z@])")


def _hline_cr(text, repairs, skipped):
    """`\\hline\\cr` → `\\hline`: a plain-TeX row end after a rule, which every
    matrix environment then reports as a misplaced \\cr — 92 of them in one paper."""
    def repl(m):
        repairs.append(Repair("hline-cr", _line_of(text, m.start()), m.group(0), "\\hline"))
        return "\\hline"
    return _HLINE_CR_RE.sub(repl, text)


# --------------------------------------------------------------------------
# figure code inside formulas
# --------------------------------------------------------------------------

_DRAWING_WORDS = {
    "draw", "filldraw", "fill", "node", "path", "coordinate", "clip", "shade",
    "foreach", "begin", "end", "scope", "pgfmathsetmacro", "tikzset", "raisebox",
    "def", "small", "tiny", "footnotesize", "scriptsize", "large", "color",
    "textcolor", "mathbf", "text", "circle", "rectangle", "arc", "to", "cycle",
}


def _tikz_what(body):
    names = [n for n in dict.fromkeys(re.findall(r"\\([A-Za-z]+)", body))
             if n not in _DRAWING_WORDS][:4]
    return "tikz picture" + (": " + ", ".join(names) if names else "")


def _page_of(options):
    m = re.search(r"page=\{?(\d+)", options or "")
    return f" p.{m.group(1)}" if m else ""


_LEAKS = (
    ("figure-leak: vbox/includegraphics",
     re.compile(r"\\vbox\{\\hbox\{\\includegraphics(\[[^\]]*\])?\{([^{}]*)\}\}\}"),
     lambda m: placeholder("figure " + m.group(2).rsplit("/", 1)[-1] + _page_of(m.group(1)))),
    ("figure-leak: includegraphics",
     re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^{}]*)\}"),
     lambda m: placeholder("figure " + m.group(2).rsplit("/", 1)[-1] + _page_of(m.group(1)))),
    ("figure-leak: tikzpicture",
     re.compile(r"\\begin\{tikzpicture\}(.*?)\\end\{tikzpicture\}", re.S),
     lambda m: placeholder(_tikz_what(m.group(1)))),
    ("figure-leak: picture",
     re.compile(r"\\begin\{picture\}(.*?)\\end\{picture\}", re.S),
     lambda m: placeholder("picture")),
)


def _figure_leaks(text, repairs, skipped):
    for kind, pattern, make in _LEAKS:
        spans = math_spans(text)
        edits = []
        for m in pattern.finditer(text):
            if edits and m.start() < edits[-1][1]:
                continue
            if not _inside(spans, m.start(), m.end()):
                skipped.append(Skipped(kind, _line_of(text, m.start()),
                                       "figure code outside a formula, or in one whose `$` do not pair"))
                continue
            new = make(m)
            edits.append((m.start(), m.end(), new))
            repairs.append(Repair(kind, _line_of(text, m.start()), m.group(0), new))
        text = _replace_all(text, edits)

    # LaTeXML's pgf/SVG dumps: `\hbox to<dim>{\vbox to<dim>{\pgfpicture…}}`,
    # sometimes 10 KB on one line, ended by the brace that closes the box —
    # and xy-pic's `\lx@xy@svg{…}`, ended by its own brace.
    for kind, anchor, opener_re, what in (
            ("figure-leak: pgf/svg dump", r"\pgfpicture",
             re.compile(r"\\hbox to\s*-?[0-9.]+pt\s*\{"), "pgf drawing"),
            ("figure-leak: xy-pic svg", r"\lx@xy@svg{", None, "xy-pic diagram")):
        pos = 0
        for _ in range(500):
            p = text.find(anchor, pos)
            if p < 0:
                break
            if opener_re is not None:
                openers = list(opener_re.finditer(text, max(0, p - 200), p))
                if not openers:
                    skipped.append(Skipped(kind, _line_of(text, p), "no enclosing \\hbox to find its end"))
                    pos = p + len(anchor)
                    continue
                start = openers[-1].start()
                close = _match_brace(text, openers[-1].end() - 1)
            else:
                start = p
                close = _match_brace(text, p + len(anchor) - 1)
            if close < 0:
                skipped.append(Skipped(kind, _line_of(text, p), "its braces do not close"))
                pos = p + len(anchor)
                continue
            old = text[start:close + 1]
            if _dollars(old) % 2:
                skipped.append(Skipped(kind, _line_of(text, p), "it crosses a `$`"))
                pos = close + 1
                continue
            label = what
            if opener_re is None:
                letters = re.findall(r"\\textstyle\{([A-Za-z])", old)
                if letters:
                    label += ": " + ", ".join(letters[:8])
            new = placeholder(label)
            repairs.append(Repair(kind, _line_of(text, start), old, new))
            text = text[:start] + new + text[close + 1:]
            pos = start + len(new)
    return text


_BRACKET_AFTER_BREAK_RE = re.compile(r"\\\\(\s*)(?=\[(?!\s*" + _LENGTH + r"\s*\]))")


def _bracket_guards(text, repairs, skipped):
    """`\\\\` then `[` inside any formula → `\\\\[0pt]` then `[`.

    TeX reads a `[` after a row break as the break's spacing, however much
    white space sits between them: `[j=1,\\ldots,n]` on the line after `\\\\`
    is "Missing number" and an array that never closes. The guard used to run
    only where a table was rebuilt; inline maths in an HTML table cell met the
    same bracket and was left broken.
    """
    edits = []
    for start, end in math_spans(text):
        tex = text[start:end]
        new = _BRACKET_AFTER_BREAK_RE.sub(lambda m: "\\\\[0pt]" + m.group(1), tex)
        if new != tex:
            edits.append((start, end, new))
            repairs.append(Repair("bracket-after-row-break", _line_of(text, start), tex, new))
    return _replace_all(text, edits)


def _leftovers(text, repairs, skipped):
    for m in re.finditer(r"\$\$\$", _without_code(text)):
        skipped.append(Skipped("broken delimiters", _line_of(text, m.start()),
                               "`$$$` — a delimiter and a brace were lost; rebuild it from the PDF"))
    return text


# --------------------------------------------------------------------------

def looks_like_latexml(md: str) -> bool:
    """Is this pandoc's rendering of a LaTeXML page?"""
    return ".ltx_" in md


def normalize(md: str, *, macros=None) -> tuple[str, list[Repair], list[Skipped]]:
    """Repair what LaTeXML → pandoc did to the maths in *md*.

    *macros* are the author's own definitions recovered from the paper's
    source (see `tex_macros`); used where a formula calls one.
    """
    md = md.replace("\r\n", "\n")
    repairs: list[Repair] = []
    skipped: list[Skipped] = []
    # Tables before anything that changes the length of a cell. The simple-table
    # pass reads cells by column position, and a figure placeholder shorter than
    # the code it replaced moved every later cell on its line to the left — read
    # as one formula spilling into its neighbour, eight tables in seventeen papers.
    steps = [_grid_tables, _tags_inside_display, _vskip_linebreaks, _lost_linebreaks,
             _hline_cr, _simple_table_equations, _figure_leaks, _nested_dollars]
    # After the `$` nested in `\text{}` are out: until then a renderer — and
    # `math_spans` — ends those formulas early, and a call inside the rest of
    # the formula would not be seen as maths.
    if macros:
        from magi.ingest import tex_macros

        steps.append(lambda t, r, s: tex_macros.expand_in_math(t, macros, r, s))
    steps += [_bracket_guards, _leftovers]
    for step in steps:
        md = step(md, repairs, skipped)
    return md, repairs, skipped


def tally(items) -> Counter:
    """How many of each kind, for a one-line report."""
    return Counter(item.kind for item in items)


def describe(items) -> str:
    return ", ".join(f"{n} {kind}" for kind, n in tally(items).most_common())


# --------------------------------------------------------------------------
# `magi math repair` — the same repairs, for papers already in the library
# --------------------------------------------------------------------------

_ARXIV_ID_RE = re.compile(r"^arxiv_id:\s*['\"]?([^'\"\n]+)", re.M)


def _candidates(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    out = []
    for path in sorted(target.rglob("*.md")):
        rel = path.relative_to(target).parts
        if path.name == "_index.md" or any(p.startswith(".") for p in rel):
            continue
        if rel and rel[0] in ("output", "scratch", "inbox"):
            continue
        out.append(path)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="magi math repair",
        description="Repair what arXiv's HTML route (LaTeXML, then pandoc) did to the "
                    "formulas of papers already in the project: equation tables, numbers "
                    "inside formulas, figure code, lost row breaks. Only files carrying "
                    "LaTeXML's markers are touched, and every run can be undone.")
    parser.add_argument("target", nargs="?", default=None,
                        help="A paper, or a directory (default: raw/ of the project you are in)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Say what would change, per paper; write nothing")
    parser.add_argument("--diff", action="store_true",
                        help="With --dry-run, print the changes as a diff")
    parser.add_argument("--fetch-macros", action="store_true",
                        help="Fetch each paper's arXiv source and expand the author's own "
                             "macros (one request per paper, arXiv asks for 15 s between them)")
    args = parser.parse_args(argv)

    from magi.core.wiki_common import atomic_write
    from magi.core.workspace import find_workspace_root

    if args.target is None:
        root = find_workspace_root()
        if root is None:
            parser.error("no MAGI workspace here — pass a paper or a directory")
        target = root / "raw"
    else:
        target = Path(args.target)
    if not target.exists():
        print(f"Path not found: {target}")
        return 1
    project = find_workspace_root(start=target)
    base = project or (target if target.is_dir() else target.parent)

    from magi.kb import tidy_log
    from magi.kb.validate_math_latex import MACROS_SUFFIX

    record, results = [], {}
    touched = 0
    left_total = 0
    for path in _candidates(target):
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if not looks_like_latexml(text):
            continue
        crlf = b"\r\n" in raw
        text = text.replace("\r\n", "\n")
        macros, packages = {}, []
        if args.fetch_macros:
            m = _ARXIV_ID_RE.search(text)
            if m:
                from magi.ingest import tex_macros

                macros, packages, why = tex_macros.from_eprint(m.group(1).strip())
                if why:
                    print(f"  {path.name}: {why}")
        new, repairs, left = normalize(text, macros=macros)
        try:
            name = path.resolve().relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            name = str(path)
        left_total += len(left)
        if not repairs and not left:
            continue
        print(f"{name}")
        if repairs:
            print(f"  repaired: {describe(repairs)}")
        for item in left[:8]:
            print(f"  left as it is, line {item.line}: {item.kind} — {item.why}")
        if len(left) > 8:
            print(f"  … and {len(left) - 8} more left as they are")
        if new == text:
            continue
        touched += 1
        if args.dry_run:
            if args.diff:
                import difflib

                print("\n".join(difflib.unified_diff(
                    text.split("\n"), new.split("\n"), fromfile=f"a/{name}",
                    tofile=f"b/{name}", lineterm="", n=1)))
            continue
        atomic_write(path, new, encoding="utf-8", newline="\r\n" if crlf else "\n")
        record.extend(tidy_log.changes_between(name, text, new, "math repair"))
        results[name] = new
        if macros or packages:
            from magi.ingest import tex_macros

            leftover = tex_macros.used_in_math(new, macros)
            if leftover or packages:
                side = path.with_name(path.stem + MACROS_SUFFIX)
                atomic_write(side, tex_macros.sidecar_text(
                    leftover, packages, origin="the arXiv e-print"), encoding="utf-8")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{touched} paper(s) {verb}; {left_total} layout(s) left for a person "
          f"(`magi math check --json` lists what still fails).")
    if record and project is not None:
        log = tidy_log.write(project, "math repair", record, results=results)
        print(f"Undo record: {log}  (magi math undo puts it back)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
