"""What one person already knows — asked, never inferred (design-auto §9).

The reports and lecture notes MAGI writes are for a reader whose scarce thing
is working memory: a term they have to stop and look up costs more than the
sentence it is in. So a word that is not marked known gets a definition or a
link to notes the first time it appears, and the notice that a report is ready
says how many such words it holds.

There is no model of the reader. They are shown the concepts a run used and
press one of two buttons — "I know this" or "write me notes" — and that is the
whole input. It is kept per user, not per project, because what somebody knows
goes with them: `<config home>/familiar.jsonl`, append-only, last entry for a
concept wins, so the file is also the history of what they asked for and when.

A concept nobody has answered about is treated as unknown. One definition too
many costs a line; one too few costs the reader their place.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .wiki_common import slugify
from .workspace import config_home

KNOWN, NOTES = "known", "notes"
STATES = (KNOWN, NOTES)


def path() -> Path:
    return config_home() / "familiar.jsonl"


def key(concept: str) -> str:
    """One spelling per concept: `[[Kramers–Wannier duality]]`, a card's
    filename and a bare slug are the same thing to the person."""
    text = str(concept or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    text = text.split("|", 1)[0].strip().replace("\\", "/")
    if text.endswith(".md"):
        text = text[:-3]
    return slugify(text.rsplit("/", 1)[-1])


def load() -> dict:
    """`{concept-key: state}` — the latest answer for each."""
    out: dict = {}
    try:
        lines = path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name, state = key(row.get("concept", "")), row.get("state")
        if name and state in STATES:
            out[name] = state
        elif name and state is None:
            out.pop(name, None)
    return out


def record(concept: str, state: str | None, via: str = "cli", project: str = "") -> dict:
    """Append one answer. `state=None` withdraws it."""
    if state is not None and state not in STATES:
        raise ValueError(f"state is one of {STATES}, not {state!r}")
    name = key(concept)
    if not name:
        raise ValueError("a concept needs a name")
    row = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "concept": name, "state": state, "via": via}
    if project:
        row["project"] = project
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def knows(concept: str, ledger: dict | None = None) -> bool:
    return (ledger if ledger is not None else load()).get(key(concept)) == KNOWN


def wants_notes(concept: str, ledger: dict | None = None) -> bool:
    return (ledger if ledger is not None else load()).get(key(concept)) == NOTES
