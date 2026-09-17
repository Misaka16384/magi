"""A run's one deliverable: what it is made from, and what it must contain.

docs/design-auto.md §9. The person who signed the contract reads exactly one
thing when they come back, in one sitting, so that thing has a fixed shape —
the sections are the questions this author was measured asking their agents
over and over (what is proved and what is not, what is new, what is the
picture, is this still promising) plus the two they asked for by name: the
decisions, and the failures worth knowing about.

Everything mechanical about it is produced here, from files, so that any agent
on any host writes the same skeleton: the contract's motivation, the claims the
run touched with whether each one *stands*, the chain in reading order, the
steps with their "if it holds / if it does not", the refutations that might be
worth a paragraph, the methods used. What is left for the writer is the prose.

And it is checked here, at the moment it is handed in, because a rule about
what reaches a person's eyes is worth more as a gate than as advice:
- no claim is cited by its slug alone — the statement is in the document;
- every term in its glossary is a concept card, not a name the agent made up;
- a full draft has been read whole by another agent before a person is.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core import familiar, vocab
from . import chain, runs, threads

RUNS_DIR = "drafts/runs"
LECTURES_DIR = "drafts/lectures"
DRAFT, EMPTY = "draft", "empty"

#: `## <heading>` lines a report must have, by form. An empty-handed report is one page
#: (the word in the file is `empty`, not `null` — YAML reads `form: null` as nothing):
#: most runs find nothing big, and a reader who gets ten sections of nothing
#: learns to stop opening them.
HEADINGS = {
    DRAFT: ("Abstract", "1 Motivation", "2 Proved and not proved", "3 New and already known",
            "4 Picture and core insight", "5 Derivation", "6 Decisions",
            "7 Failures worth knowing", "8 Still promising, and next",
            "9 Prerequisites and terms"),
    EMPTY: ("2 Proved and not proved", "6 Decisions", "7 Failures worth knowing",
           "8 Still promising, and next"),
}

_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_VERDICT = re.compile(r"^VERDICT:\s*(stands|restate|refuted|unclear)\b")


def _links(text: str) -> list:
    return [chain.slug_of(found) for found in _LINK.findall(text or "")]


def touched(run_note, by_slug: dict) -> list:
    """Propositions the run's posts link to, in the order they first appear."""
    seen: list = []
    for post in run_note.posts:
        for slug in _links(post.text):
            if slug in by_slug and slug not in seen:
                seen.append(slug)
    return seen


def methods(slugs, by_slug: dict) -> list:
    """Concepts the given claims are stated in terms of, first use first."""
    out: list = []
    for slug in slugs:
        note = by_slug.get(slug)
        for item in threads.as_list(note.frontmatter.get("depends_on")) if note else []:
            name = str(item).strip().strip("[]").split("|")[0].strip()
            if name and familiar.key(name) not in {familiar.key(n) for n in out}:
                out.append(name)
    return out


def concept_cards(root) -> dict:
    """`{familiar-key: path}` for every concept card in the project."""
    base = Path(root) / "wiki" / "concepts"
    if not base.is_dir():
        return {}
    return {familiar.key(path.stem): path for path in base.rglob("*.md")
            if not path.name.startswith("_")}


def lecture_path(root, concept: str) -> Path:
    return Path(root) / LECTURES_DIR / f"{familiar.key(concept)}.md"


def claim_of(note) -> str:
    claim = note.frontmatter.get("claim")
    return " ".join(str(claim if isinstance(claim, str) and claim.strip() else note.title).split())


def failures(slugs, by_slug: dict) -> list:
    """Refuted claims among *slugs* that a reviewer or the author's own concession
    settled — `[(slug, had_a_bet)]`. "The agent got stuck" is not one of these."""
    from .. import review

    out = []
    for slug in slugs:
        note = by_slug.get(slug)
        if note is None or note.status != "refuted":
            continue
        judged = any(post.host == vocab.REVIEWER and (post.text or "").lstrip()
                     .startswith("VERDICT: refuted") for post in note.posts)
        conceded = any(post.is_transition and post.src == "disputed" and post.dst == "refuted"
                       for post in note.posts)
        if judged or conceded or any(review._is_answer(p) for p in note.posts):
            out.append((slug, note.frontmatter.get("bet") in ("supported", "refuted")))
    return out


# ---------------------------------------------------------------- the skeleton


def outline(root, run_note, notes, form: str = DRAFT) -> str:
    """The report with everything mechanical filled in."""
    by_slug = chain.index(notes)
    seen = touched(run_note, by_slug)
    findings = [slug for slug in seen if by_slug[slug].status == "supported"]
    spine = chain.closure(findings, by_slug)
    every = chain.closure(seen, by_slug)
    book = runs.ledger(run_note)

    def claim_line(slug: str) -> str:
        note = by_slug[slug]
        return (f"- [[{slug}]] — {claim_of(note)} — **{chain.label(slug, by_slug)}**"
                f" ({note.status})")

    out = ["---", "type: run-report", f"run: {run_note.slug}", f"form: {form}"]
    if run_note.lines:
        out.append("line: [" + ", ".join(run_note.lines) + "]")
    out += ["---", "", f"# {run_note.title}", ""]

    def head(name: str, *body) -> None:
        out.append(f"## {name}")
        out.append("")
        out.extend(body)
        out.append("")

    if form == DRAFT:
        head("Abstract", "<!-- Five lines. What was asked, what was found, how sure. -->")
        head("1 Motivation", runs.section(run_note, "Motivation") or "<!-- from the contract -->")
    head("2 Proved and not proved",
         *([claim_line(slug) for slug in every] or ["Nothing was claimed."]),
         "", "<!-- stands = read by a strong reviewer, and so is everything under it. "
             "Say in a sentence what is NOT shown. -->")
    if form == DRAFT:
        head("3 New and already known",
             "<!-- One novelty check against the literature. Which of the above is new, "
             "which is a known result in other words. -->")
        head("4 Picture and core insight",
             "<!-- The physical picture. The one idea the argument turns on. If it was "
             "brute force or a lucky observation, say so. -->")
        steps = []
        for slug in spine:
            note = by_slug[slug]
            rests = ", ".join(f"[[{p}]]" for p in chain.premises(note))
            where = ", ".join(str(d) for d in threads.as_list(note.frontmatter.get("derivation")))
            steps += [f"### [[{slug}]] — {claim_of(note)}", "",
                      f"Rests on: {rests or 'nothing else here'}. "
                      f"Worked out in: {where or '—'}.", "",
                      "<!-- The step, in reading order. Keep what was decided or is not "
                      "obvious; routine algebra goes to an appendix. -->", ""]
        head("5 Derivation", *(steps or ["<!-- no supported claim to derive -->"]))

    decisions = []
    for post in run_note.posts:
        first = ((post.text or "").strip().splitlines() or [""])[0]
        if post.host == vocab.HUMAN and first:
            rest = " ".join((post.text or "").strip().splitlines()[1:])[:300]
            decisions.append(f"- **The person** ({post.at}): {first}" + (f" — {rest}" if rest else ""))
    for step in book.steps:
        line = f"- Step {step.n} ({step.host}): {step.do}"
        if step.because:
            line += f" — because {step.because}"
        if step.if_true or step.if_false:
            line += f". If it held: {step.if_true or '—'}; if not: {step.if_false or '—'}"
        result = ("abandoned: " if step.abandoned else "") + (step.result or "never closed")
        decisions += [line, f"  → {result}"]
        if step.overturned:
            decisions.append(f"  ✗ the person, afterwards: {step.overturned}")
    head("6 Decisions", *(decisions or ["No step was taken."]),
         "", "<!-- Where there was a fork: what was chosen, what was dropped, why. -->")

    lost = failures(every, by_slug)
    head("7 Failures worth knowing",
         *[f"<!-- candidate: [[{slug}]] — {claim_of(by_slug[slug])}"
           + (" — the person had bet on this" if bet else "") + " -->" for slug, bet in lost],
         "<!-- At most five, three lines each: what was tried / the step it fails at or "
         "the counterexample / what it rules out. Only refutations that were reviewed, and "
         "only ones on a direction the person bet on or at a fork of the chain. -->")
    head("8 Still promising, and next", "<!-- An honest answer, then what to do next. -->")

    if form == DRAFT:
        cards = concept_cards(root)
        terms = []
        for name in methods(every, by_slug):
            known = " (you marked this known)" if familiar.knows(name) else ""
            missing = "" if familiar.key(name) in cards else "  <!-- no concept card yet -->"
            terms.append(f"- [[{name}]] — <!-- one-line definition -->{known}{missing}")
        notes_ready = [f"- [[{LECTURES_DIR}/{familiar.key(name)}]]"
                       for name in methods(every, by_slug)
                       if lecture_path(root, name).is_file()]
        disputes = [claim_line(slug) for slug in spine + [s for s in every if s not in spine]
                    if by_slug[slug].status in ("disputed", vocab.CONFLICT)]
        head("9 Prerequisites and terms",
             "### Terms", "", *(terms or ["<!-- none recorded under depends_on -->"]), "",
             "### Lecture notes", "", *(notes_ready or ["None yet."]), "",
             "### Disputes that need you", "",
             *(disputes or ["None on the chain."]))
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- the gate


def _frontmatter(text: str) -> dict:
    from ..core.wiki_common import parse_frontmatter_text, split_frontmatter_text

    split = split_frontmatter_text(text)
    return parse_frontmatter_text(split[0]) if split else {}


def form_of(text: str) -> str:
    return EMPTY if str(_frontmatter(text).get("form") or "").strip() == EMPTY else DRAFT


def terms(text: str) -> list:
    """Glossary entries: the first wikilink of each bullet under `### Terms`."""
    found, inside = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.strip().casefold() == "### terms"
            continue
        if inside and line.lstrip().startswith("- "):
            links = _LINK.findall(line)
            if links:
                found.append(links[0].split("|")[0].strip())
    return found


def reading_cost(root, text: str, ledger: dict | None = None) -> dict:
    ledger = familiar.load() if ledger is None else ledger
    listed = terms(text)
    new = [name for name in listed if not familiar.knows(name, ledger)]
    with_notes = [name for name in new if lecture_path(root, name).is_file()]
    return {"terms": len(listed), "new": len(new), "with_notes": len(with_notes)}


def cost_line(cost: dict) -> str:
    if not cost["terms"]:
        return "no glossary"
    return (f"{cost['new']} term(s) new to you, {cost['with_notes']} of them with "
            f"lecture notes")


def reviewed(run_note, relative: str) -> str | None:
    """The last whole-draft verdict posted on the run for this report."""
    verdict = None
    for post in run_note.posts:
        if post.host != vocab.REVIEWER or relative not in (post.text or ""):
            continue
        first = (post.text or "").lstrip().splitlines()[0]
        found = _VERDICT.match(first)
        if found:
            verdict = found.group(1)
    return verdict


def problems(root, run_note, notes, relative: str, text: str) -> list:
    """Why this report may not be handed in yet. Empty when it may."""
    out: list = []
    front = _frontmatter(text)
    if str(front.get("run") or "") != run_note.slug:
        out.append(f"its frontmatter says `run: {front.get('run')}`, not `{run_note.slug}` — "
                   f"start from `magi run outline {run_note.slug}`")
    form = form_of(text)
    have = [line[3:].strip().casefold() for line in text.splitlines() if line.startswith("## ")]
    for heading in HEADINGS[form]:
        if heading.casefold() not in have:
            out.append(f"no `## {heading}` section")
    leftover = [line for line in text.splitlines()
                if "<!--" in line and "candidate:" not in line and "no concept card" not in line]
    if leftover:
        out.append(f"{len(leftover)} template comment(s) are still in it — an unwritten "
                   "section reads as a written one")

    by_slug = chain.index(notes)
    flat = " ".join(text.split()).casefold()
    for slug in dict.fromkeys(_links(text)):
        note = by_slug.get(slug)
        if note is not None and claim_of(note).casefold() not in flat:
            out.append(f"[[{slug}]] is cited without its statement — a slug means nothing "
                       f"to a reader; quote the claim: “{claim_of(note)[:80]}”")

    if form == DRAFT:
        cards = concept_cards(root)
        for name in terms(text):
            if familiar.key(name) not in cards:
                out.append(f"term [[{name}]] is not a concept card — a name that is not in "
                           "wiki/concepts/ is a name the reader has never seen: "
                           "`magi wiki add-concept` first, or use the card's name")
        verdict = reviewed(run_note, relative)
        if verdict is None:
            out.append(f"nobody has read it whole: magi review {relative}")
        elif verdict in ("refuted", "unclear"):
            out.append(f"its whole-draft review came back `{verdict}` — fix what it names, "
                       f"then magi review {relative} again")
    return out
