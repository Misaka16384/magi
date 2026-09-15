"""The author's own macros, read out of the e-print and used where formulas call them.

LaTeXML keeps `\\mK`, `\\supp` and `\\psising` exactly as the author typed
them; about 110 such calls across seventeen papers were unreadable to every
renderer, to `magi math check`, and to an agent compiling the paper. Their
definitions were in the arXiv source all along.
"""

from __future__ import annotations

import gzip
import io
import tarfile

from magi.ingest import latexml_math as L
from magi.ingest import tex_macros as T

MAIN = r"""\documentclass{revtex4}
\usepackage{amsmath,bbm}
\usepackage{macros}
\input{defs}
\newcommand{\mK}{\mathcal{K}}
\newcommand{\bs}[1]{\boldsymbol{#1}}
\newcommand{\opt}[2][x]{f_{#1}(#2)}
\DeclareMathOperator\supp{supp}
\DeclareMathOperator*{\argmax}{arg\,max}
\def\he{\widehat{e}}
\let\la\langle
\newcommand{\half}{$\frac{1}{2}$ }
\newcommand{\vertdimers}{\begin{tikzpicture}\draw (0,0)--(0,1);\end{tikzpicture}}
% \newcommand{\commented}{nope}
\begin{document}
\def\local{a scratch variable in the body}
\end{document}
"""
STY = "\\newcommand{\\psising}{\\psi_{\\textrm{sing}}}\n\\def\\@internal{x}\n"
DEFS = "\\newcommand{\\mC}{\\mathcal{C}}\n"


def _tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


SOURCES = {"main.tex": MAIN, "macros.sty": STY, "defs.tex": DEFS}


def test_a_bundle_a_single_gzipped_file_and_a_pdf():
    assert set(T.read_sources(_tarball(SOURCES))) == set(SOURCES)
    assert T.read_sources(gzip.compress(MAIN.encode())) == {"main.tex": MAIN}
    assert T.read_sources(b"%PDF-1.5 whatever") == {}


def test_definitions_from_the_preamble_the_packages_and_what_it_inputs():
    macros = T.extract(SOURCES)
    assert macros["mK"].body == "\\mathcal{K}" and macros["mK"].source == "main.tex:5"
    assert (macros["bs"].nargs, macros["opt"].nargs, macros["opt"].default) == (1, 2, "x")
    assert macros["supp"].body == "\\operatorname{supp}"
    assert macros["argmax"].body == "\\operatorname*{arg\\,max}"
    assert macros["he"].body == "\\widehat{e}" and macros["la"].body == "\\langle"
    assert macros["psising"].body == "\\psi_{\\textrm{sing}}"
    assert macros["mC"].body == "\\mathcal{C}"
    # The document body's scratch definitions, comments and @-internals are
    # not the author's notation.
    assert "local" not in macros and "commented" not in macros
    assert not any("@" in name for name in macros)


def test_only_maths_packages_on_the_allowlist():
    assert T.packages(SOURCES) == ["amsmath", "bbm"]


def test_calls_are_expanded_with_their_arguments():
    macros = T.extract(SOURCES)
    table = {k: m for k, m in macros.items() if T.expandable(m)}
    tex, used = T.expand(r"\mK^2+\bs{x}+\opt{y}+\opt[z]{w}+x^\mK+\supp f+\la a\rangle", table)
    assert tex == (r"\mathcal{K}^2+\boldsymbol{x}+f_{x}(y)+f_{z}(w)+x^{\mathcal{K}}"
                   r"+\operatorname{supp} f+\langle a\rangle")
    assert used["mK"] == 2


def test_text_mode_and_drawing_definitions_are_never_expanded_into_a_formula():
    macros = T.extract(SOURCES)
    assert not T.expandable(macros["half"])
    assert T.drawing(macros["vertdimers"]) and not T.expandable(macros["vertdimers"])


def test_inside_formulas_only_and_a_drawing_becomes_a_placeholder():
    macros = T.extract(SOURCES)
    md = "Prose mentions \\mK here.\n\n$\\mK + \\vertdimers$\n"
    new, repairs, _ = L.normalize(md, macros=macros)
    assert "Prose mentions \\mK here." in new
    assert "$\\mathcal{K} + \\text{[omitted: vertdimers (a drawing)]}$" in new
    assert {"macro: \\mK", "macro: \\vertdimers"} <= {r.kind for r in repairs}


def test_what_cannot_be_expanded_is_kept_beside_the_paper_for_the_checker():
    macros = T.extract(SOURCES)
    new, _, _ = L.normalize("$\\half$ and $\\mK$", macros=macros)
    left = T.used_in_math(new, macros)
    assert [m.name for m in left] == ["half"]
    side = T.sidecar_text(left, T.packages(SOURCES), origin="arXiv:2108.10324")
    assert "\\IfFileExists{bbm.sty}{\\usepackage{bbm}}{}" in side
    assert "\\providecommand{\\half}{}\\renewcommand{\\half}{$\\frac{1}{2}$ }" in side


def test_fetching_the_eprint_never_raises(monkeypatch):
    monkeypatch.setattr("magi.core.http.http_get",
                        lambda url, timeout=60: (_tarball(SOURCES), "application/gzip", url))
    macros, packages, why = T.from_eprint("2108.10324")
    assert "mK" in macros and packages == ["amsmath", "bbm"] and why is None

    def refuse(url, timeout=60):
        raise OSError("offline")

    monkeypatch.setattr("magi.core.http.http_get", refuse)
    assert T.from_eprint("2108.10324")[2].startswith("could not fetch")
