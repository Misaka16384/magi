"""`magi familiar` — what the person already knows, as they told it.

docs/design-auto.md §9. There are two buttons and this is both of them: "I know
this" and "write me notes". The WebUI presses them; in a chat the person says
"I know transfer matrices" and the agent types the command for them. Nothing
is inferred — `core/familiar.py` says why.

    magi familiar list [--run <run>]     the methods a run used, and what was said about each
    magi familiar known "<concept>"      I know this: stop defining it for me
    magi familiar notes "<concept>"      write me lecture notes on it
    magi familiar forget "<concept>"     take the answer back
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import familiar
from .kb import report as report_mod
from .kb import runs, threads
from .kb.thread_cmd import Refused, _root


def methods_of(root, run_note, notes, ledger=None) -> list:
    """The concepts one run's claims are stated in, with where each stands."""
    from .kb import chain

    ledger = familiar.load() if ledger is None else ledger
    by_slug = chain.index(notes)
    every = chain.closure(report_mod.touched(run_note, by_slug), by_slug)
    cards = report_mod.concept_cards(root)
    rows = []
    for name in report_mod.methods(every, by_slug):
        key = familiar.key(name)
        rows.append({"concept": name, "key": key, "state": ledger.get(key),
                     "card": key in cards,
                     "notes_ready": report_mod.lecture_path(root, name).is_file(),
                     "used_in": [slug for slug in every
                                 if key in {familiar.key(str(item).strip().strip("[]"))
                                            for item in threads.as_list(
                                                by_slug[slug].frontmatter.get("depends_on"))}]})
    return rows


def notes_owed(root, notes, ledger=None) -> list:
    """Concepts the person asked for notes on, used by a run here, not written yet."""
    ledger = familiar.load() if ledger is None else ledger
    owed, seen = [], set()
    for note in notes:
        if note.kind != "run" or note.status in (runs.DISCUSSING, runs.DROPPED):
            continue
        for row in methods_of(root, note, notes, ledger):
            if row["state"] == familiar.NOTES and not row["notes_ready"] \
                    and row["key"] not in seen:
                seen.add(row["key"])
                owed.append({**row, "run": note.slug})
    return owed


def _all_notes(root):
    return [threads.read_note(path) for path in threads.note_paths(root)]


def cmd_list(args) -> int:
    root = _root(args)
    notes = _all_notes(root)
    chosen = [note for note in notes if note.kind == "run"
              and (note.slug == args.run if args.run
                   else note.status not in (runs.DISCUSSING, runs.DROPPED))]
    if args.run and not chosen:
        raise Refused(f"no run {args.run} in this project")
    ledger = familiar.load()
    data = [{"run": note.slug, "title": note.title, "status": note.status,
             "methods": methods_of(root, note, notes, ledger)} for note in chosen]
    if args.json:
        print(json.dumps({"runs": data, "ledger": str(familiar.path())}, ensure_ascii=False))
        return 0
    if not data:
        print("no run here has used anything yet")
        return 0
    words = {familiar.KNOWN: "known", familiar.NOTES: "notes asked", None: "—"}
    for item in data:
        print(f"{item['run']} · {item['title']} ({item['status']})")
        if not item["methods"]:
            print("  no concept recorded under depends_on")
        for row in item["methods"]:
            extra = (" · notes ready" if row["notes_ready"] else "") + \
                    ("" if row["card"] else " · no concept card")
            print(f"  {words[row['state']]:<12} {row['concept']}{extra}"
                  f"   (used in {', '.join(row['used_in'])})")
    print('\nmagi familiar known "<concept>"   |   magi familiar notes "<concept>"')
    return 0


def cmd_set(args, state) -> int:
    project = ""
    try:
        project = _root(args).name
    except Refused:
        pass        # what somebody knows is not a fact about a project
    row = familiar.record(args.concept, state, via=os.environ.get("MAGI_HOST") or "cli",
                          project=project)
    said = {familiar.KNOWN: "marked known — it will not be defined for you again",
            familiar.NOTES: "notes asked for — `magi next` will offer it to an agent",
            None: "answer withdrawn"}[state]
    if args.json:
        print(json.dumps(row, ensure_ascii=False))
    else:
        print(f"{row['concept']}: {said}")
    return 0


_VERBS = {
    "list": ("magi familiar list", "The methods a run used, and what the person said of each"),
    "known": ("magi familiar known", "The person knows this concept: stop defining it"),
    "notes": ("magi familiar notes", "The person wants lecture notes on this concept"),
    "forget": ("magi familiar forget", "Withdraw what was said about a concept"),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv and argv[0] in _VERBS else None
    if verb is None:
        print(f"magi familiar: expected one of {', '.join(_VERBS)}", file=sys.stderr)
        return 2
    prog, description = _VERBS[verb]
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--project-dir", "--topic-dir", dest="topic_dir",
                        help="Project root (default: discovered)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    if verb == "list":
        parser.add_argument("--run", help="Only this run (default: every run that has started)")
    else:
        parser.add_argument("concept", help="The concept's name, slug or [[wikilink]]")
    args = parser.parse_args(argv[1:])
    try:
        if verb == "list":
            return cmd_list(args)
        return cmd_set(args, {"known": familiar.KNOWN, "notes": familiar.NOTES,
                              "forget": None}[verb])
    except Refused as refusal:
        print(str(refusal), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
