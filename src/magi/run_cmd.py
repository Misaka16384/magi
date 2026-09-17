"""`magi run` — one unattended exploration, driven through the CLI.

docs/design-auto.md is the design; `kb/runs.py` is what a run *is*. This file
is the verbs, and the rule that organises them is that each belongs to one
phase and is refused in the other:

    discussing   start · sign (a person) · stop (a person: drop it)
    running      step · result · report · amend (a person) · stop (a person)
    any          status

`sign`, `amend` and `stop` are a person's and are signed `human` with
`· via <cli>`, the way `magi decide` signs: the agent types, the decision is
not its own. Everything the mentor does is a `step` and its `result`, because
that pair is the whole memory of the run — the next agent to sit down, on
whatever host, reads it with `magi run status` and needs nothing else.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from .core import vocab
from .core.wiki_common import slugify
from .kb import report as report_mod
from .kb import runs, threads
from .kb.thread_cmd import Refused, _root, _slug, host_name, via_name

RUNS_DIR = report_mod.RUNS_DIR


def _path(root: Path, slug: str) -> Path:
    return root / threads.DIRNAME / f"{_slug(slug)}.md"


def _load(args):
    root = _root(args)
    path = _path(root, args.run)
    try:
        note = threads.read_note(path)
    except FileNotFoundError:
        raise Refused(f"no run at {path} — `magi run start --title …` opens one")
    if note.kind != vocab.RUN:
        raise Refused(f"{args.run} is a {note.kind}, not a run")
    return root, path, note


def _say(args, data: dict, text: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(text)
    return 0


def _knobs(pairs) -> dict:
    out = {}
    for pair in pairs or []:
        key, sep, value = str(pair).partition("=")
        if not sep or not key.strip():
            raise Refused(f"--knob takes key=value, not {pair!r}")
        out[key.strip()] = value.strip()
    return out


# ---------------------------------------------------------------- discussing


def cmd_start(args) -> int:
    """Open a run in `discussing`, with an empty contract to talk over."""
    root = _root(args)
    slug = args.slug or slugify("run-" + args.title)
    path = _path(root, slug)
    extra = {}
    knobs = runs.knobs_for(args.mode, _knobs(args.knob))
    if knobs:
        extra["knobs"] = knobs
    try:
        threads.create(path, vocab.RUN, args.title,
                       args.purpose or "One unattended exploration; the body is its contract.",
                       lines=[args.line] if args.line else None,
                       extra=extra, body=runs.template())
    except FileExistsError:
        raise Refused(f"{slug} already exists — a slug is an identity; pick another "
                      "with --slug, or carry on with the run that is there")
    note = threads.read_note(path)
    return _say(args, {"run": slug, "path": str(path), "status": note.status},
                f"{runs.phase_line(note)}\n"
                f"  contract: {path}\n"
                "  Fill its sections from the discussion (skill: discuss). Nothing is "
                "authorised until the person signs:\n"
                f"    magi run sign {slug} --steps N")


def cmd_sign(args) -> int:
    """A person's signature: freeze the contract and authorise the run."""
    root, path, note = _load(args)
    if note.status != runs.DISCUSSING:
        raise Refused(f"{args.run} is {note.status}; only a run under discussion is signed "
                      + ("— to change a signed contract: magi run amend " + args.run
                         if note.status == runs.RUNNING else ""))
    empty = runs.unfilled(note)
    if empty and not args.anyway:
        raise Refused("the contract still has empty sections: " + ", ".join(empty)
                      + ". What is not written down is not carried to the next agent "
                        "(--anyway signs it as it is)")
    steps = args.steps if args.steps is not None else runs.steps_allowed(note)
    if steps <= 0:
        raise Refused("--steps N: how many steps this run may take. There is no default; "
                      "it is the one number the person has to say")
    fields = {"steps": steps,
              "max_parallel": (args.max_parallel if args.max_parallel is not None
                               else runs.parallel_allowed(note))}
    if args.until:
        if runs.parse_until(args.until) is None:
            raise Refused(f"--until takes a moment like 2026-09-18T08:00, not {args.until!r}")
        fields["until"] = args.until
    knobs = runs.knobs_for(args.mode, _knobs(args.knob),
                           base=note.frontmatter.get("knobs") or {})
    if knobs:
        fields["knobs"] = knobs
    before = runs.ledger(note).marks

    def compose(current):
        if current.status != runs.DISCUSSING:
            raise Refused(f"{args.run} is {current.status}; somebody else just moved it")
        mark = runs.fingerprint(current, fields)
        changed = ""
        if before:
            changed = f"\nre-signed after an amendment; was {before[-1]}"
        text = (f"SIGNED {mark}{changed}\n"
                f"steps: {fields['steps']} · at once: {fields['max_parallel']}"
                + (f" · until: {fields['until']}" if fields.get("until") else "")
                + (f"\n{args.text}" if args.text else ""))
        return text, {**fields, "signed": mark}, runs.RUNNING

    threads.transact(path, compose, host=vocab.HUMAN, line=args.line,
                     via=via_name(vocab.HUMAN, args.via))
    note = threads.read_note(path)
    return _say(args, {"run": args.run, "status": note.status,
                       "signed": note.frontmatter.get("signed")},
                f"{runs.phase_line(note)}\n"
                "  The contract is frozen. Unattended: make sure this CLI will not stop to "
                "ask permission while nobody is there to answer.")


# ---------------------------------------------------------------- running


def cmd_step(args) -> int:
    """Register what is about to be done. Refused unless the run may take it."""
    root, path, note = _load(args)
    if not args.closing and not (args.if_true and args.if_false):
        raise Refused("--if-true and --if-false: what changes if it holds, and if it does "
                      "not. A step whose two outcomes lead to the same next move is not "
                      "worth a step")
    taken = {}

    def compose(current):
        why = runs.refusal(current, closing=args.closing)
        if why:
            raise Refused(why)
        number = len(runs.ledger(current).steps) + 1
        taken["n"] = number
        return (runs.step_text(number, args.do, because=args.because or "",
                               if_true=args.if_true or "", if_false=args.if_false or "",
                               on=args.on or "", closing=args.closing), None, None)

    host = host_name(args.host)
    threads.transact(path, compose, host=host, line=args.line)
    note = threads.read_note(path)
    left = max(0, runs.steps_allowed(note) - runs.ledger(note).used)
    return _say(args, {"run": args.run, "step": taken["n"], "left": left},
                f"step {taken['n']} registered ({left} left). Save as you go; close it with\n"
                f"  magi run result {args.run} {taken['n']} --text '<what came of it>'")


def cmd_result(args) -> int:
    """Close a step: what came of it, in a few sentences, where the next agent reads."""
    root, path, note = _load(args)
    if note.status not in (runs.RUNNING, runs.DISCUSSING):
        raise Refused(f"{args.run} is {note.status}: its steps are closed")

    def compose(current):
        step = runs.ledger(current).get(args.step)
        if step is None:
            raise Refused(f"{args.run} has no step {args.step}")
        if not step.open:
            raise Refused(f"step {args.step} already has its result")
        return runs.result_text(args.step, args.text, abandoned=args.abandoned), None, None

    threads.transact(path, compose, host=host_name(args.host), line=args.line)
    return _say(args, {"run": args.run, "step": args.step, "abandoned": args.abandoned},
                f"step {args.step} {'abandoned' if args.abandoned else 'closed'}")


def cmd_amend(args) -> int:
    """A person takes the signed contract back to the table."""
    root, path, note = _load(args)
    if note.status != runs.RUNNING:
        raise Refused(f"{args.run} is {note.status}; there is nothing signed to amend")

    def compose(current):
        if current.status != runs.RUNNING:
            raise Refused(f"{args.run} is {current.status}; somebody else just moved it")
        return f"AMEND\n{args.text}", None, runs.DISCUSSING

    threads.transact(path, compose, host=vocab.HUMAN, line=args.line,
                     via=via_name(vocab.HUMAN, args.via))
    note = threads.read_note(path)
    return _say(args, {"run": args.run, "status": note.status},
                f"{runs.phase_line(note)}\n"
                "  Open steps may still be closed; no new one registers until the person "
                f"signs again: magi run sign {args.run}")


def cmd_overturn(args) -> int:
    """The person, reading afterwards: this step should have gone the other way.

    The one kind of steering no contract can hold in advance — an intent that
    formed only on seeing the result (design-auto §10 measured it: missed five
    times out of five, with every document available). It cannot be prevented,
    so it is recorded, against the step it is about, where the slow loop can
    count how often it happens and propose a line for the next contract.
    """
    root, path, note = _load(args)
    if note.status in (runs.DISCUSSING, runs.DROPPED):
        raise Refused(f"{args.run} is {note.status}: it has taken no step to overturn")

    def compose(current):
        if runs.ledger(current).get(args.step) is None:
            raise Refused(f"{args.run} has no step {args.step}")
        return f"OVERTURN {args.step}: {args.text.strip()}", None, None

    threads.transact(path, compose, host=vocab.HUMAN, line=args.line,
                     via=via_name(vocab.HUMAN, args.via))
    return _say(args, {"run": args.run, "step": args.step},
                f"step {args.step} of {args.run}: recorded that it should have gone "
                "otherwise")


def cmd_stop(args) -> int:
    """A person ends it: a run under discussion is dropped, a running one is
    told to write up what it has."""
    root, path, note = _load(args)
    if note.status == runs.DISCUSSING:
        def compose(current):
            return f"STOP\n{args.text or 'dropped before it was signed'}", None, runs.DROPPED
    elif note.status == runs.RUNNING:
        def compose(current):
            return f"STOP\n{args.text or 'stopped by the person'}", None, None
    else:
        raise Refused(f"{args.run} is already {note.status}")
    threads.transact(path, compose, host=vocab.HUMAN, line=args.line,
                     via=via_name(vocab.HUMAN, args.via))
    note = threads.read_note(path)
    tail = ("" if note.status == runs.DROPPED else
            "\n  No new exploring step will register. What is left is the report.")
    return _say(args, {"run": args.run, "status": note.status},
                f"{runs.phase_line(note)}{tail}")


def cmd_outline(args) -> int:
    """The report's skeleton, with everything mechanical already in it."""
    root, path, note = _load(args)
    if note.status in (runs.DISCUSSING, runs.DROPPED):
        raise Refused(f"{args.run} is {note.status}: there is nothing to report yet")
    notes = [threads.read_note(p) for p in threads.note_paths(root)]
    form = report_mod.EMPTY if args.empty else report_mod.DRAFT
    text = report_mod.outline(root, note, notes, form=form)
    if not args.write:
        print(text, end="")
        return 0
    target = root / RUNS_DIR / f"{note.slug}.md"
    if target.exists():
        raise Refused(f"{RUNS_DIR}/{note.slug}.md already exists — it may hold somebody's "
                      "writing; print the outline without --write and merge by hand")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return _say(args, {"run": args.run, "path": f"{RUNS_DIR}/{note.slug}.md", "form": form},
                f"wrote {RUNS_DIR}/{note.slug}.md ({form}). Fill the prose (skill: brief), "
                + (f"have it read — magi review {RUNS_DIR}/{note.slug}.md — " if form == "draft" else "")
                + f"then magi run report {args.run} {RUNS_DIR}/{note.slug}.md")


def cmd_report(args) -> int:
    """Hand in the run's one deliverable and end the run."""
    root, path, note = _load(args)
    if note.status != runs.RUNNING:
        raise Refused(f"{args.run} is {note.status}; a report ends a running run")
    target = (root / args.path).resolve() if not Path(args.path).is_absolute() \
        else Path(args.path).resolve()
    try:
        relative = target.relative_to(root.resolve()).as_posix()
    except ValueError:
        raise Refused(f"{args.path} is outside the project")
    if not target.is_file():
        raise Refused(f"{relative} does not exist — write it first (skill: brief)")
    if not relative.startswith(RUNS_DIR + "/"):
        raise Refused(f"a run's report lives under {RUNS_DIR}/, not at {relative}")

    in_flight = runs.ledger(note).open
    if in_flight:
        # Before anything about the document: a report handed in over open work
        # hides what was in flight, however well it is written.
        named = ", ".join(f"#{step.n}" for step in in_flight)
        raise Refused(f"step(s) {named} are still open — close or abandon them; a "
                      "report handed in over open work hides what was in flight")
    text = target.read_text(encoding="utf-8", errors="replace")
    notes = [threads.read_note(p) for p in threads.note_paths(root)]
    wrong = report_mod.problems(root, note, notes, relative, text)
    if wrong and not args.anyway:
        raise Refused(f"{relative} is not ready for a person to read:\n"
                      + "\n".join(f"  - {item}" for item in wrong)
                      + "\n(--anyway hands it in as it is, and the hand-in says so)")
    cost = report_mod.reading_cost(root, text)

    def compose(current):
        still = runs.ledger(current).open
        if still:
            named = ", ".join(f"#{step.n}" for step in still)
            raise Refused(f"step(s) {named} are still open — close or abandon them; a "
                          "report handed in over open work hides what was in flight")
        said = (f"REPORT [[{relative}]]\n{report_mod.form_of(text)} · "
                f"{report_mod.cost_line(cost)}")
        if wrong:
            said += (f"\nhanded in with {len(wrong)} check(s) unmet:\n"
                     + "\n".join(f"- {item}" for item in wrong))
        return said, {"report": relative}, runs.REPORTED

    threads.transact(path, compose, host=host_name(args.host), line=args.line)
    return _say(args, {"run": args.run, "status": runs.REPORTED, "report": relative,
                       "reading_cost": cost, "unmet": wrong},
                f"REPORTED · {args.run} → {relative} ({report_mod.cost_line(cost)})")


# ---------------------------------------------------------------- status


def brief(root: Path, note, recent: int = 5) -> dict:
    """Everything the next agent to sit down needs, from the note alone."""
    book = runs.ledger(note)
    allowed = runs.steps_allowed(note)
    data = {
        "run": note.slug, "title": note.title, "status": note.status,
        "phase": runs.phase_line(note),
        "line": note.lines,
        "steps": {"used": book.used, "allowed": allowed,
                  "open": [step.n for step in book.open],
                  "at_once": runs.parallel_allowed(note)},
        "until": note.frontmatter.get("until"),
        "knobs": note.frontmatter.get("knobs") or {},
        "how_to_explore": runs.instructions(note.frontmatter.get("knobs") or {}),
        "contract_intact": (runs.contract_intact(note)
                            if note.status == runs.RUNNING else None),
        "spent": runs.spent(note) if note.status == runs.RUNNING else None,
        "contract": {name: runs.section(note, name) for name, _ in runs.SECTIONS},
        "open_steps": [vars(step) for step in book.open],
        "recent": [vars(step) for step in book.steps[-recent:]],
    }
    if book.open:
        since = min(step.at for step in book.open)
        data["changed_since_oldest_open_step"] = [
            name for name in _changed_since(root, since)
            if name != f"{threads.DIRNAME}/{note.path.name}"]
    return data


def _changed_since(root: Path, stamp: str, limit: int = 12) -> list:
    """Files an interrupted step may have left behind: touched after it began."""
    try:
        began = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return []
    found = []
    for folder in ("drafts", "tools", "threads"):
        base = root / folder
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            try:
                if item.is_file() and item.stat().st_mtime >= began:
                    found.append((item.stat().st_mtime, item.relative_to(root).as_posix()))
            except OSError:
                continue
    return [name for _, name in sorted(found, reverse=True)[:limit]]


def render_brief(data: dict) -> str:
    out = [data["phase"], ""]
    for name, text in data["contract"].items():
        shown = " ".join(re.sub(r"<!--.*?-->", "", text, flags=re.S).split())
        out.append(f"{name}: {shown[:300] + ('…' if len(shown) > 300 else '') if shown else '(empty)'}")
    if data["knobs"]:
        mode = data["knobs"].get("mode")
        out.append("\nHow to explore" + (f" (mode: {mode})" if mode else "") + ":")
        out.extend(f"  - {line}" for line in data.get("how_to_explore") or [])
    steps = data["steps"]
    out.append(f"\nSteps: {steps['used']}/{steps['allowed']} used, "
               f"{len(steps['open'])} open, at most {steps['at_once']} at once"
               + (f", until {data['until']}" if data["until"] else ""))
    if data["open_steps"]:
        out.append("\nIn flight — registered and never closed. Redo them, or close them "
                   "with --abandoned:")
        for step in data["open_steps"]:
            out.append(f"  #{step['n']} ({step['host']}, {step['at']}) {step['do']}")
        changed = data.get("changed_since_oldest_open_step") or []
        if changed:
            out.append("  files touched since the oldest of them began: " + ", ".join(changed))
    closed = [step for step in data["recent"] if step["result"] is not None]
    if closed:
        out.append("\nLatest results:")
        for step in closed:
            mark = " (abandoned)" if step["abandoned"] else ""
            out.append(f"  #{step['n']}{mark} {step['do']}\n      → {step['result'][:400]}")
    return "\n".join(out)


def cmd_status(args) -> int:
    root = _root(args)
    if args.run:
        _, _, note = _load(args)
        chosen = [note]
    else:
        notes = [threads.read_note(path) for path in threads.note_paths(root)]
        chosen = runs.live_runs(notes)
        if not chosen:
            return _say(args, {"runs": []}, "no run is live in this project "
                                            "(magi run start --title … opens one)")
    data = [brief(root, note) for note in chosen]
    if args.json:
        print(json.dumps({"runs": data}, ensure_ascii=False, default=str))
        return 0
    print("\n\n".join(render_brief(item) for item in data))
    return 0


# ---------------------------------------------------------------- dispatch

_VERBS = {
    "start": ("magi run start", "Open a run and its empty contract (phase: discussing)", cmd_start),
    "sign": ("magi run sign", "The person signs: freeze the contract, authorise the run", cmd_sign),
    "step": ("magi run step", "Register what is about to be done (refused unless the run may)", cmd_step),
    "result": ("magi run result", "Close a step with what came of it", cmd_result),
    "amend": ("magi run amend", "The person takes a signed contract back to discussing", cmd_amend),
    "outline": ("magi run outline", "The report's skeleton, with the mechanical parts filled in", cmd_outline),
    "report": ("magi run report", "Hand in the run's report and end the run", cmd_report),
    "status": ("magi run status", "The takeover brief: phase, contract, budget, what is in flight", cmd_status),
    "stop": ("magi run stop", "The person ends it: drop a discussion, or have a run write up", cmd_stop),
    "overturn": ("magi run overturn", "The person, afterwards: this step should have gone otherwise", cmd_overturn),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv and argv[0] in _VERBS else None
    if verb is None:
        print(f"magi run: expected one of {', '.join(_VERBS)}", file=sys.stderr)
        return 2
    prog, description, handler = _VERBS[verb]
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--project-dir", "--topic-dir", dest="topic_dir",
                        help="Project root (default: discovered)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--line", help="Research line, for the post's signature")
    via_help = "Who typed this for the person (default: $MAGI_HOST, else 'cli')"

    if verb == "start":
        parser.add_argument("--title", required=True, help="What this run is after, as a name")
        parser.add_argument("--slug", help="The note's permanent id (default: from the title)")
        parser.add_argument("--purpose", help="One line: what this run is for")
        parser.add_argument("--mode", choices=sorted(runs.MODES),
                            help="deep: a chain that stands · balanced · explore: a map "
                                 "of conjectures")
        parser.add_argument("--knob", action="append", metavar="KEY=VALUE",
                            help="Override one knob of the mode (repeatable)")
    else:
        parser.add_argument("run", nargs="?" if verb == "status" else None,
                            help="The run's slug" + (" (default: every live run)"
                                                      if verb == "status" else ""))
    if verb == "sign":
        parser.add_argument("--steps", type=int, help="How many steps it may take. No default")
        parser.add_argument("--max-parallel", type=int,
                            help=f"How many steps may be open at once (default {runs.DEFAULT_PARALLEL})")
        parser.add_argument("--until", help="No new step after this moment, e.g. 2026-09-18T08:00")
        parser.add_argument("--mode", choices=sorted(runs.MODES),
                            help="Set or change the mode at signing")
        parser.add_argument("--knob", action="append", metavar="KEY=VALUE")
        parser.add_argument("--anyway", action="store_true",
                            help="Sign with contract sections still empty")
        parser.add_argument("--text", help="Anything the person said on signing")
        parser.add_argument("--via", help=via_help)
    if verb == "step":
        parser.add_argument("--do", required=True, help="What is about to be done")
        parser.add_argument("--because", help="Why this, now")
        parser.add_argument("--if-true", dest="if_true", help="What changes if it holds")
        parser.add_argument("--if-false", dest="if_false", help="What changes if it does not")
        parser.add_argument("--on", help="The proposition or direction it is about")
        parser.add_argument("--closing", action="store_true",
                            help="The report step: allowed once the budget is spent")
        parser.add_argument("--host", help="Signature (default: $MAGI_HOST, else 'cli')")
    if verb == "result":
        parser.add_argument("step", type=int, help="The step's number")
        parser.add_argument("--text", required=True,
                            help="What came of it, with links to what it produced")
        parser.add_argument("--abandoned", action="store_true",
                            help="It was not finished and will not be")
        parser.add_argument("--host", help="Signature (default: $MAGI_HOST, else 'cli')")
    if verb == "overturn":
        parser.add_argument("step", type=int, help="The step's number")
        parser.add_argument("--text", required=True,
                            help="What it should have done instead, in the person's words")
        parser.add_argument("--via", help=via_help)
    if verb == "amend":
        parser.add_argument("--text", required=True, help="What the person wants changed")
        parser.add_argument("--via", help=via_help)
    if verb == "stop":
        parser.add_argument("--text", help="Why, in the person's words")
        parser.add_argument("--via", help=via_help)
    if verb == "outline":
        parser.add_argument("--empty", action="store_true",
                            help="The one-page form, for a run that found nothing big")
        parser.add_argument("--write", action="store_true",
                            help=f"Write it to {RUNS_DIR}/<run>.md instead of printing it")
    if verb == "report":
        parser.add_argument("path", help=f"The report, under {RUNS_DIR}/")
        parser.add_argument("--anyway", action="store_true",
                            help="Hand it in with checks unmet; the hand-in records which")
        parser.add_argument("--host", help="Signature (default: $MAGI_HOST, else 'cli')")

    args = parser.parse_args(argv[1:])
    try:
        return handler(args)
    except Refused as refusal:
        print(str(refusal), file=sys.stderr)
        return 1
    except threads.IllegalTransition as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
