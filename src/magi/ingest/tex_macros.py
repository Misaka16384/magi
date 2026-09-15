"""The author's own macros, recovered from a paper's LaTeX source.

LaTeXML's `alttext` is the TeX as the author typed it, which means as the
author *abbreviated* it: `\\mK` for `\\mathcal{K}`, `\\supp` for
`\\operatorname{supp}`, `\\psising` for `\\psi_{\\textrm{sing}}`. The HTML
route kept about 110 such calls across seventeen papers, and nothing
downstream can read them — not a renderer, not `magi math check`, not an agent
compiling the paper, which would have to guess. The definitions are in the
e-print, one request away.

What this module does with them is deliberately narrow:

* only definitions an author makes for a document — every `.sty`/`.def` in the
  bundle, the preamble of each `.tex` that has a `\\documentclass`, and files
  that preamble `\\input`s — never the document body, where `\\def` is
  usually a local scratch variable;
* a call is expanded in place only inside a formula, only when the definition
  is plain mathematics (no `$`, no environments, no references, no `@`
  internals, no conditionals), and only when its arguments parse;
* a macro that draws a picture becomes a placeholder, not TikZ inside a
  formula;
* whatever is left — and the maths packages the paper loads — goes into
  `<paper>.macros.tex` next to the paper, which `magi math check` adds to the
  preamble it compiles that paper against.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import tarfile
from collections import Counter
from typing import NamedTuple


class Macro(NamedTuple):
    name: str               # without the backslash
    nargs: int
    default: str | None     # the optional first argument's default, if any
    body: str
    source: str             # "macros.sty:117"


_SOURCE_SUFFIXES = (".tex", ".sty", ".def")
_MAX_SOURCE_BYTES = 4_000_000


def read_sources(payload: bytes) -> dict[str, str]:
    """Every .tex/.sty/.def in an arXiv e-print, by name. `{}` for a PDF.

    Read in memory; nothing is extracted to disk, so no member name can
    point anywhere.
    """
    if not payload or payload[:5] == b"%PDF-":
        return {}
    for data in (payload, _gunzip(payload)):
        if data is None:
            continue
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
                out = {}
                for member in tar.getmembers():
                    if not member.isreg() or member.size > _MAX_SOURCE_BYTES:
                        continue
                    if not member.name.lower().endswith(_SOURCE_SUFFIXES):
                        continue
                    handle = tar.extractfile(member)
                    if handle is not None:
                        out[member.name] = handle.read().decode("utf-8", errors="replace")
                return out
        except (tarfile.TarError, OSError, EOFError):
            pass
    single = _gunzip(payload) or payload
    text = single.decode("utf-8", errors="replace")
    if "\\documentclass" in text:
        return {"main.tex": text}
    return {}


def _gunzip(payload: bytes) -> bytes | None:
    try:
        return gzip.decompress(payload)
    except (OSError, EOFError):
        return None


def _strip_comments(text: str) -> str:
    """Comments removed, line numbers kept."""
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


_INPUT_RE = re.compile(r"\\(?:input|include|usepackage)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def definition_texts(sources: dict[str, str]) -> list[tuple[str, str]]:
    """The parts of a source bundle where document-wide definitions live."""
    picked, pulled_in = [], set()
    for name, text in sources.items():
        body = _strip_comments(text)
        if name.lower().endswith((".sty", ".def")):
            picked.append((name, body))
        elif "\\documentclass" in body:
            head = body.split("\\begin{document}", 1)[0]
            picked.append((name, head))
            for m in _INPUT_RE.finditer(head):
                for stem in m.group(1).split(","):
                    pulled_in.add(os.path.splitext(os.path.basename(stem.strip()))[0].lower())
    for name, text in sources.items():
        stem = os.path.splitext(os.path.basename(name))[0].lower()
        if name.lower().endswith(".tex") and stem in pulled_in and "\\documentclass" not in text:
            picked.append((name, _strip_comments(text)))
    return picked


def _match(text: str, i: int, open_ch: str = "{", close_ch: str = "}") -> int:
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


_NAME = r"(?:\{\s*\\([A-Za-z]+)\s*\}|\\([A-Za-z]+))"
_NEWCOMMAND_RE = re.compile(
    r"\\(?:new|renew|provide)command\*?\s*" + _NAME +
    r"\s*(?:\[\s*(\d)\s*\])?\s*(?:\[([^\]]*)\])?\s*\{")
_DECLARE_OP_RE = re.compile(r"\\DeclareMathOperator(\*?)\s*" + _NAME + r"\s*\{")
_DEF_RE = re.compile(r"\\def\s*\\([A-Za-z]+)((?:#\d)*)\s*\{")
_LET_RE = re.compile(r"\\let\s*\\([A-Za-z]+)\s*=?\s*(\\[A-Za-z]+)")


def extract(sources: dict[str, str]) -> dict[str, Macro]:
    """Definitions by name; a later definition of the same name wins, as in TeX."""
    found: dict[str, Macro] = {}
    for fname, text in definition_texts(sources):
        base = os.path.basename(fname)

        def where(pos):
            return f"{base}:{text.count(chr(10), 0, pos) + 1}"

        events = []
        for m in _NEWCOMMAND_RE.finditer(text):
            close = _match(text, m.end() - 1)
            if close < 0:
                continue
            nargs = int(m.group(3) or 0)
            events.append((m.start(), Macro(m.group(1) or m.group(2), nargs,
                                            m.group(4), text[m.end():close], where(m.start()))))
        for m in _DECLARE_OP_RE.finditer(text):
            close = _match(text, m.end() - 1)
            if close < 0:
                continue
            op = "\\operatorname" + m.group(1)
            events.append((m.start(), Macro(m.group(2) or m.group(3), 0, None,
                                            op + "{" + text[m.end():close] + "}", where(m.start()))))
        for m in _DEF_RE.finditer(text):
            params = m.group(2)
            if params and params != "".join(f"#{k}" for k in range(1, len(params) // 2 + 1)):
                continue
            close = _match(text, m.end() - 1)
            if close < 0:
                continue
            events.append((m.start(), Macro(m.group(1), len(params) // 2, None,
                                            text[m.end():close], where(m.start()))))
        for m in _LET_RE.finditer(text):
            if m.group(1) != m.group(2)[1:]:
                events.append((m.start(), Macro(m.group(1), 0, None, m.group(2), where(m.start()))))
        for _, macro in sorted(events, key=lambda e: e[0]):
            if "@" in macro.name:
                continue
            found[macro.name] = macro
    return found


#: Maths packages worth loading for a paper's check. An allowlist: loading an
#: arbitrary package from a bundle is how the checker's own preamble stops
#: compiling, and then every formula in the paper reads as broken.
MATH_PACKAGES = frozenset({
    "amsmath", "amssymb", "amsfonts", "mathtools", "bm", "bbm", "bbold",
    "dsfont", "mathrsfs", "yfonts", "stmaryrd", "xcolor", "cancel", "slashed",
    "esint", "tensor", "ytableau", "youngtab", "mathdots", "extarrows",
    "nicematrix", "tikz-cd", "upgreek", "eucal", "euscript", "leftidx",
    "accents", "empheq", "mathbbol",
})

_USEPACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def packages(sources: dict[str, str]) -> list[str]:
    """The allowlisted maths packages the paper loads, in order."""
    seen: list[str] = []
    for _, text in definition_texts(sources):
        for m in _USEPACKAGE_RE.finditer(text):
            for name in (p.strip() for p in m.group(1).split(",")):
                if name in MATH_PACKAGES and name not in seen:
                    seen.append(name)
    return seen


# --------------------------------------------------------------------------
# expansion
# --------------------------------------------------------------------------

#: A definition that draws something. Expanded, it would put TikZ inside a
#: formula — exactly the leak `latexml_math` removes — so it becomes a
#: placeholder naming the macro instead.
_DRAWS = re.compile(r"\\(?:begin\s*\{tikzpicture\}|tikz|draw|filldraw|includegraphics|put|pgf[A-Za-z]*)(?![A-Za-z])")

#: What a definition must not contain to be expanded inside a formula.
_NOT_MATHS = re.compile(
    r"\$|@|#\s*#|\\(?:begin|end|section|subsection|subsubsection|paragraph|ref|eqref|"
    r"cite[A-Za-z]*|label|footnote|def|let|newcommand|renewcommand|if[A-Za-z]*|else|fi|"
    r"csname|expandafter|input|include|par|noindent|item|onlinecite|url|href)(?![A-Za-z])")


def drawing(macro: Macro) -> bool:
    return bool(_DRAWS.search(macro.body))


def expandable(macro: Macro) -> bool:
    return not drawing(macro) and not _NOT_MATHS.search(macro.body)


_NAME_RE = re.compile(r"[A-Za-z]+")


def _skip_spaces(tex: str, k: int) -> int:
    while k < len(tex) and tex[k] in " \t\n":
        k += 1
    return k


def expand(tex: str, table: dict[str, Macro], *, depth: int = 5) -> tuple[str, Counter]:
    """Expand calls to *table*'s macros in *tex*. (new tex, calls expanded by name)."""
    used: Counter = Counter()
    for _ in range(depth):
        tex, n = _expand_once(tex, table, used)
        if not n or len(tex) > 200_000:
            break
    return tex, used


def _expand_once(tex, table, used):
    out, i, n, count = [], 0, len(tex), 0
    while i < n:
        c = tex[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        m = _NAME_RE.match(tex, i + 1)
        if not m:
            out.append(tex[i:i + 2])
            i += 2
            continue
        name, j = m.group(0), m.end()
        macro = table.get(name)
        if macro is None:
            out.append(tex[i:j])
            i = j
            continue
        args, k, ok = [], j, True
        if macro.default is not None:
            k2 = _skip_spaces(tex, k)
            if k2 < n and tex[k2] == "[":
                close = _match(tex, k2, "[", "]")
                if close < 0:
                    ok = False
                else:
                    args.append(tex[k2 + 1:close])
                    k = close + 1
            else:
                args.append(macro.default)
        while ok and len(args) < macro.nargs:
            k = _skip_spaces(tex, k)
            if k >= n:
                ok = False
            elif tex[k] == "{":
                close = _match(tex, k)
                if close < 0:
                    ok = False
                else:
                    args.append(tex[k + 1:close])
                    k = close + 1
            elif tex[k] == "\\":
                word = _NAME_RE.match(tex, k + 1)
                token = tex[k:word.end()] if word else tex[k:k + 2]
                args.append(token)
                k += len(token)
            else:
                args.append(tex[k])
                k += 1
        if not ok:
            out.append(tex[i:j])
            i = j
            continue
        body = re.sub(r"#(\d)", lambda g: args[int(g.group(1)) - 1]
                      if 0 < int(g.group(1)) <= len(args) else g.group(0), macro.body)
        # `x^\mK` works because TeX expands before `^` takes its argument;
        # `x^\mathcal{K}` does not. A script position gets a group.
        before = "".join(out).rstrip()
        if before.endswith(("^", "_")):
            body = "{" + body + "}"
        out.append(body)
        # A control word at the end of the expansion must not fuse with a
        # letter that followed the call: `\mK x` is fine, `\mKx` was never a call.
        if re.search(r"\\[A-Za-z]+$", body) and k < n and tex[k].isalpha():
            out.append(" ")
        used[name] += 1
        count += 1
        i = k
    return "".join(out), count


def expand_in_math(text, macros, repairs, skipped):
    """Expand the author's macros inside the formulas of a converted document."""
    from magi.ingest.latexml_math import Repair, _replace_all, _line_of, math_spans, placeholder

    table = {name: m for name, m in macros.items() if expandable(m)}
    pictures = {name: m for name, m in macros.items() if drawing(m)}
    first_line: dict[str, int] = {}
    total: Counter = Counter()
    edits = []
    call_re = re.compile(r"\\(" + "|".join(sorted(map(re.escape, {**table, **pictures}), key=len, reverse=True))
                         + r")(?![A-Za-z])") if (table or pictures) else None
    if call_re is None:
        return text
    for start, end in math_spans(text):
        tex = text[start:end]
        if not call_re.search(tex):
            continue
        for m in call_re.finditer(tex):
            first_line.setdefault(m.group(1), _line_of(text, start + m.start()))
        new = tex
        if pictures:
            def picture(m):
                total[m.group(1)] += 1
                return placeholder(m.group(1) + " (a drawing)")
            new = re.sub(r"\\(" + "|".join(map(re.escape, pictures)) + r")(?![A-Za-z])", picture, new)
        if table:
            new, used = expand(new, table)
            total.update(used)
        if new != tex:
            edits.append((start, end, new))
    text = _replace_all(text, edits)
    for name, n in sorted(total.items()):
        macro = macros[name]
        after = placeholder(name + " (a drawing)") if name in pictures else macro.body
        repairs.append(Repair(f"macro: \\{name}", first_line.get(name, 1),
                              f"\\{name}  (x{n})", f"{after}  ({macro.source})"))
    return text


def used_in_math(text: str, macros: dict[str, Macro]) -> list[Macro]:
    """The definitions still called somewhere inside a formula of *text*."""
    from magi.ingest.latexml_math import math_spans

    if not macros:
        return []
    maths = " ".join(text[s:e] for s, e in math_spans(text))
    return [m for name, m in sorted(macros.items())
            if re.search(r"\\" + re.escape(name) + r"(?![A-Za-z])", maths)]


def sidecar_text(macros: list[Macro], package_names: list[str], *, origin: str) -> str:
    """`<paper>.macros.tex`: what `magi math check` adds to this paper's preamble."""
    lines = [f"% Recovered from {origin} by magi. `magi math check` compiles this",
             "% paper's formulas against these lines; nothing else reads them."]
    for name in package_names:
        lines.append(f"\\IfFileExists{{{name}.sty}}{{\\usepackage{{{name}}}}}{{}}")
    for m in macros:
        spec = (f"[{m.nargs}]" if m.nargs else "") + (f"[{m.default}]" if m.default is not None else "")
        lines.append(f"\\providecommand{{\\{m.name}}}{{}}\\renewcommand{{\\{m.name}}}{spec}"
                     f"{{{m.body}}}% {m.source}")
    return "\n".join(lines) + "\n"


def from_eprint(arxiv_id: str, *, timeout: int = 120) -> tuple[dict[str, Macro], list[str], str | None]:
    """(macros, packages, why not) for one arXiv paper. Never raises."""
    from magi.core.http import http_get

    try:
        body, _ctype, _final = http_get(f"https://arxiv.org/e-print/{arxiv_id}", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a missing source is a finding, not a failure
        return {}, [], f"could not fetch the e-print ({exc})"
    sources = read_sources(body)
    if not sources:
        return {}, [], "the e-print has no LaTeX source (a PDF-only submission)"
    return extract(sources), packages(sources), None
