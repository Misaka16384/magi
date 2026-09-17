"""A run: one unattended exploration, kept as a note (docs/design-auto.md).

The requirement this file answers is the author's, 2026-09-17: *the state of an
exploration lives in the CLI, not in whichever agent happens to be driving it —
when one vendor's quota runs out, another opens the same folder and carries
on.* So nothing here is remembered by a process. A run is a `threads/` note of
`kind: run`; its body is the contract a person signed; its Discussion is the
trajectory, one STEP post per action and one RESULT post closing it. Everything
else — the budget, how many things are in flight, what the last session was in
the middle of when it died — is a count over those posts.

The second requirement, same day: *discussing and running are separate states,
or the two drift into each other.* The note's status is the phase. While it is
`discussing` the contract is text anybody may edit and no step can be
registered; signing records a fingerprint of the contract, and from then on a
step is refused the moment the text no longer matches it. There is no way to
move the goalposts quietly: `magi run amend` is the only door back, and it is a
person's.

Pure functions over a `threads.Note`. The commands are in `run_cmd.py`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field

from ..core import vocab

DISCUSSING, RUNNING, REPORTED, DROPPED = "discussing", "running", "reported", "dropped"

#: Statuses at which a run is still somebody's business.
LIVE = (RUNNING, DISCUSSING)

#: Frontmatter that is part of what was signed. The budget is contract, not
#: bookkeeping: a fingerprint over the prose alone would let the agent that may
#: not touch the goals raise its own step count instead.
CONTRACT_FIELDS = ("steps", "max_parallel", "until", "knobs")

#: Steps a spent run may still take to hand in its report: write it, have it
#: read, fix what the reading found. Without a ceiling `--closing` would be a
#: second, unlimited budget with a different name.
CLOSING_STEPS = 3

#: How many steps may be open at once unless the contract says otherwise. The
#: author's transcripts, three separate times: "I hit my usage limit… do not
#: dispatch subagents in parallel".
DEFAULT_PARALLEL = 2

#: The contract's sections, in the order `run start` writes them. English and
#: fixed because `run status` reads them back; what goes under them is in
#: whatever language the person discussed it in.
SECTIONS = (
    ("Motivation", "Why this is worth doing, in the person's words. The report opens with it."),
    ("Directions", "One per line, each a [[question]]. A guess the person holds is its own "
                   "conjectured proposition, with their bet."),
    ("Not worth it", "What not to spend steps on: constants, known results re-derived, edge cases…"),
    ("Stop when", "What counts as enough to write up. What counts as giving a direction up."),
    ("Taste", "Things this person keeps saying, verbatim, and their answers to the alignment quiz."),
    ("Allowed", "May it run scripts? Fetch and ingest new papers? Anything it must not touch."),
)

#: A mode is a name for a set of knobs, not a code path (design-auto §8). The
#: knobs are sentences the mentor is given, so they travel with the note to
#: whichever host picks the run up; `--knob key=value` overrides any of them.
MODES = {
    "deep": {"open_questions": "no", "order": "deepest-first", "abandon_after": "5",
             "fork_width": "2", "review": "every-claim"},
    "balanced": {"open_questions": "a-few", "order": "mixed", "abandon_after": "3",
                 "fork_width": "2", "review": "every-claim"},
    "explore": {"open_questions": "yes", "order": "least-visited", "abandon_after": "2",
                "fork_width": "1", "review": "premises-only"},
}

_KNOB_SAYS = {
    ("open_questions", "no"): "Open no new direction: every step serves one the contract names.",
    ("open_questions", "a-few"): "You may open at most two new directions, each as a "
                                 "[[question]] with a line on why it is worth one.",
    ("open_questions", "yes"): "Open new directions freely, each as a [[question]].",
    ("order", "deepest-first"): "Go deep: the most promising direction, and the deepest open "
                                "claim on it, first.",
    ("order", "mixed"): "Mostly follow the most promising direction; every few steps give "
                        "the least-visited one a turn.",
    ("order", "least-visited"): "Go wide: prefer the direction with the fewest steps so far.",
    ("fork_width", "1"): "At a fork take one branch — the most ambitious that is cheap to try.",
    ("fork_width", "2"): "At a cheap fork take two branches: the worker's favourite and the "
                         "most ambitious one.",
    ("review", "every-claim"): "Have every claim read when it turns supported: "
                               "`magi review <slug>`. What this run produces is a chain "
                               "that stands.",
    ("review", "premises-only"): "Have read only the claims other claims rest on. What this "
                                 "run produces is a map of conjectures: leave a claim at "
                                 "`conjectured` unless it has been read.",
}


def knobs_for(mode: str | None, overrides: dict | None = None, base: dict | None = None) -> dict:
    knobs = dict(base or {})
    if mode:
        knobs.update(MODES[mode])
        knobs["mode"] = mode
    knobs.update(overrides or {})
    return knobs


def instructions(knobs: dict) -> list:
    """The knobs as the sentences a mentor acts on. Unknown knobs are shown as
    they were written — a person may have meant something by them."""
    out = []
    for key, value in (knobs or {}).items():
        if key == "mode":
            continue
        said = _KNOB_SAYS.get((key, str(value)))
        if key == "abandon_after":
            said = (f"Give a direction up after {value} steps in a row that came back "
                    "refuted or empty, and say why in the result.")
        out.append(said or f"{key}: {value}")
    return out


_OVERTURN = re.compile(r"^OVERTURN (?P<n>\d+): ?(?P<text>.*)$", re.S)
_STEP = re.compile(r"^STEP (?P<n>\d+)(?P<closing> \(closing\))?: ?(?P<do>.*)$")
_RESULT = re.compile(r"^RESULT (?P<n>\d+)(?P<abandoned> \(abandoned\))?: ?(?P<text>.*)$", re.S)
_SIGNED = re.compile(r"^SIGNED (?P<mark>[0-9a-f]{16})\b")
_STOP = re.compile(r"^STOP\b")
_AMEND = re.compile(r"^AMEND\b")
_DETAIL = re.compile(r"^(?P<key>because|if-true|if-false|on): ?(?P<value>.*)$")


@dataclass
class Step:
    n: int
    do: str
    at: str = ""
    host: str = ""
    because: str = ""
    if_true: str = ""
    if_false: str = ""
    on: str = ""
    closing: bool = False
    result: str | None = None
    abandoned: bool = False
    resulted_at: str = ""
    #: What the person said this step should have done instead, if they did.
    overturned: str = ""

    @property
    def open(self) -> bool:
        return self.result is None


@dataclass
class Ledger:
    """What the posts on a run note add up to."""
    steps: list = field(default_factory=list)
    #: A person said stop, and nobody has signed since.
    stopped: bool = False
    #: Fingerprints, in the order they were signed.
    marks: list = field(default_factory=list)
    #: What the person said they want changed, while an amendment is pending.
    amended: str = ""

    @property
    def open(self) -> list:
        return [step for step in self.steps if step.open]

    @property
    def used(self) -> int:
        """Steps that count against the budget: every one but the closing ones."""
        return sum(1 for step in self.steps if not step.closing)

    @property
    def closing(self) -> int:
        return sum(1 for step in self.steps if step.closing)

    def get(self, n: int):
        return next((step for step in self.steps if step.n == n), None)


def ledger(note) -> Ledger:
    """Read the trajectory off the note's posts."""
    out = Ledger()
    for post in note.posts:
        lines = (post.text or "").strip().splitlines()
        if not lines:
            continue
        head = lines[0].strip()
        found = _STEP.match(head)
        if found:
            step = Step(n=int(found.group("n")), do=found.group("do").strip(),
                        at=post.at, host=post.host, closing=bool(found.group("closing")))
            for line in lines[1:]:
                detail = _DETAIL.match(line.strip())
                if detail:
                    setattr(step, detail.group("key").replace("-", "_"),
                            detail.group("value").strip())
            out.steps.append(step)
            continue
        found = _RESULT.match((post.text or "").strip())
        if found:
            step = out.get(int(found.group("n")))
            if step is not None and step.open:
                step.result = found.group("text").strip()
                step.abandoned = bool(found.group("abandoned"))
                step.resulted_at = post.at
            continue
        found = _OVERTURN.match((post.text or "").strip())
        if found and post.host == vocab.HUMAN:
            step = out.get(int(found.group("n")))
            if step is not None:
                step.overturned = found.group("text").strip()
            continue
        # Signing and stopping are a person's, and `magi run sign | stop` sign
        # them so. A line that merely reads "SIGNED …" in somebody else's post
        # is a quotation, not a signature — and must not un-stop a run.
        if post.host != vocab.HUMAN:
            continue
        found = _SIGNED.match(head)
        if found:
            out.marks.append(found.group("mark"))
            out.stopped = False
            out.amended = ""
            continue
        if _AMEND.match(head):
            out.amended = " ".join(lines[1:]).strip() or "(no reason given)"
            continue
        if _STOP.match(head):
            out.stopped = True
    return out


# ---------------------------------------------------------------- the contract


def _canonical(key: str, value):
    """One spelling per value, however YAML handed it back.

    `until: 2026-09-18T08:00:00` written by hand comes back a `datetime`, the
    same text written by `yaml.safe_dump` comes back a string, and a
    fingerprint that told them apart would call a contract changed because of
    who typed the quotes.
    """
    if key in ("steps", "max_parallel"):
        return _int(value, 0)
    if key == "until":
        return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value).strip()
    if key == "knobs" and isinstance(value, dict):
        return {str(k): str(v) for k, v in sorted(value.items())}
    return str(value)


def _contract_fields(fields: dict) -> dict:
    return {key: _canonical(key, fields[key]) for key in CONTRACT_FIELDS
            if fields.get(key) not in (None, "", {})}


def fingerprint(note, fields: dict | None = None) -> str:
    """Sixteen hex digits over what was agreed: the prose and the budget.

    Whitespace at line ends and the file's newline convention are not part of
    an agreement, and `lint --fix` touches both, so they are normalised away.
    Everything else counts — one changed word is a different contract.
    `fields` overrides the frontmatter, for signing: the fingerprint has to be
    of the note as it will be once the budget given at signing is written.
    """
    prose = "\n".join(line.rstrip() for line in
                      note.prose.replace("\r\n", "\n").strip().splitlines())
    merged = dict(note.frontmatter)
    merged.update({k: v for k, v in (fields or {}).items() if v is not None})
    agreed = _contract_fields(merged)
    blob = prose + "\n\x1e" + json.dumps(agreed, sort_keys=True, ensure_ascii=False,
                                         default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def contract_intact(note) -> bool:
    signed = str(note.frontmatter.get("signed") or "")
    return bool(signed) and signed == fingerprint(note)


def section(note, name: str) -> str:
    """The text under one `## <name>` heading of the contract."""
    out, inside = [], False
    for line in note.prose.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().casefold() == name.casefold()
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def template() -> str:
    """The empty contract `run start` writes. Guidance is in comments, so an
    unfilled section is visibly unfilled and the guidance never gets signed as
    if it were the person's words."""
    parts = []
    for name, hint in SECTIONS:
        parts.append(f"## {name}\n\n<!-- {hint} -->\n")
    return "\n".join(parts)


def unfilled(note) -> list:
    """Contract sections with nothing in them but the template's comment."""
    empty = []
    for name, _hint in SECTIONS:
        text = re.sub(r"<!--.*?-->", "", section(note, name), flags=re.S).strip()
        if not text:
            empty.append(name)
    return empty


# ---------------------------------------------------------------- the budget


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def steps_allowed(note) -> int:
    return max(0, _int(note.frontmatter.get("steps"), 0))


def parallel_allowed(note) -> int:
    return max(1, _int(note.frontmatter.get("max_parallel"), DEFAULT_PARALLEL))


def parse_until(value):
    """An aware datetime, or None. A bare date means the end of that day; a
    time with no zone is the local one — it is what the person meant by "8 am"."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        when = value
    elif isinstance(value, dt.date):
        when = dt.datetime.combine(value, dt.time(23, 59, 59))
    else:
        text = str(value).strip().replace("Z", "+00:00")
        # The date first: `datetime.fromisoformat("2026-09-18")` succeeds and
        # means midnight at the *start* of the day, which would end a run
        # "until the 18th" a day before the person meant.
        try:
            when = dt.datetime.combine(dt.date.fromisoformat(text), dt.time(23, 59, 59))
        except ValueError:
            try:
                when = dt.datetime.fromisoformat(text)
            except ValueError:
                return None
    return when if when.tzinfo else when.astimezone()


def past_until(note, now=None) -> bool:
    until = parse_until(note.frontmatter.get("until"))
    if until is None:
        return False
    return (now or dt.datetime.now(dt.timezone.utc)) > until


def spent(note, now=None) -> str | None:
    """Why no further exploring step may start, or None while one may.

    Three ways a run's budget ends, and they are told apart because the person
    reading the report will want to know which: it used its steps, the clock
    the person set ran out, or the person said stop.
    """
    book = ledger(note)
    if book.stopped:
        return "a person stopped it"
    allowed = steps_allowed(note)
    if book.used >= allowed:
        return f"all {allowed} steps are used"
    if past_until(note, now):
        return f"it is past {note.frontmatter.get('until')}"
    return None


def refusal(note, now=None, closing: bool = False) -> str | None:
    """Why `run step` must refuse right now, or None when it may go ahead.

    In this order, because each later question only makes sense once the
    earlier one is settled: is this the phase steps belong to; is the contract
    still the one that was signed; is there budget; is there room.

    `closing` is the one step a spent run still owes — writing the report — so
    it skips the budget and nothing else.
    """
    status = note.status
    if status != RUNNING:
        if status == DISCUSSING:
            return ("this run is still being discussed — nothing is authorised until a "
                    f"person signs it: magi run sign {note.slug} --steps N")
        return f"this run is {status}: it takes no more steps"
    if not contract_intact(note):
        return ("the contract was changed after it was signed (signed "
                f"{note.frontmatter.get('signed') or 'nothing'}, now {fingerprint(note)}). "
                f"A signed contract changes one way: magi run amend {note.slug}, edit, "
                "and the person signs again")
    book = ledger(note)
    why = spent(note, now)
    if closing:
        if not why:
            return ("--closing is for a run whose budget is spent; this one still has "
                    "steps — register an ordinary step")
        if book.closing >= CLOSING_STEPS:
            return (f"the report has had its {CLOSING_STEPS} closing steps — hand it in: "
                    f"magi run report {note.slug} {'drafts/runs/<file>.md'}")
    elif why:
        return (f"{why} — what is left is the report: "
                f"magi run step {note.slug} --closing --do 'write the report'")
    room = parallel_allowed(note)
    if len(book.open) >= room:
        named = ", ".join(f"#{step.n}" for step in book.open)
        return (f"{len(book.open)} step(s) still open ({named}) and this run allows "
                f"{room} at once — magi run result {note.slug} <n> --text … "
                "(or --abandoned) first")
    return None


# ---------------------------------------------------------------- what to say


#: First words that make a post on a run note part of its ledger. `thread post`
#: refuses them: a step that did not go through `magi run step` went through
#: none of its gates.
LEDGER_WORDS = ("STEP", "RESULT", "SIGNED", "STOP", "AMEND", "REPORT", "OVERTURN")


def reads_as_ledger(text: str) -> bool:
    first = ((text or "").strip().splitlines() or [""])[0]
    return first.split(" ", 1)[0].rstrip(":") in LEDGER_WORDS


def step_text(n: int, do: str, because: str = "", if_true: str = "", if_false: str = "",
              on: str = "", closing: bool = False) -> str:
    lines = [f"STEP {n}{' (closing)' if closing else ''}: {_one_line(do)}"]
    for key, value in (("because", because), ("if-true", if_true),
                       ("if-false", if_false), ("on", on)):
        if value:
            lines.append(f"{key}: {_one_line(value)}")
    return "\n".join(lines)


def result_text(n: int, text: str, abandoned: bool = False) -> str:
    return f"RESULT {n}{' (abandoned)' if abandoned else ''}: {text.strip()}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def phase_line(note, now=None) -> str:
    """The first line of every entry point while a run is live.

    An agent on any host, walking in cold, has to know before anything else
    which of two very different situations it is in: one where nothing is
    authorised and the job is to talk, and one where it is the mentor of a
    signed contract it may not edit.
    """
    title = note.title
    if note.status == DISCUSSING:
        book = ledger(note)
        if book.marks:
            # Not the same situation as a run nobody has signed yet, and the
            # agent walking in has to be told which: there is a trajectory, work
            # may be in flight, and the person has said what they want changed.
            wanted = book.amended[:160] + ("…" if len(book.amended) > 160 else "")
            return (f"DISCUSSING · run “{title}” ({note.slug}) · the person took the signed "
                    f"contract back: “{wanted}” — no new step registers until they sign "
                    "again; steps already open may be closed")
        return (f"DISCUSSING · run “{title}” ({note.slug}) · not signed — nothing here is "
                "authorised to run; talk, write the contract, the person signs")
    if note.status == RUNNING:
        book = ledger(note)
        if not contract_intact(note):
            return (f"RUNNING · run “{title}” ({note.slug}) · CONTRACT CHANGED AFTER SIGNING "
                    f"— no step will register until a person amends and signs again")
        why = spent(note, now)
        tail = f"{why}: write the report" if why else "you are the mentor; the contract is frozen"
        return (f"RUNNING · run “{title}” ({note.slug}) · step {book.used}/{steps_allowed(note)}"
                f" · {len(book.open)} open — {tail}")
    return f"{str(note.status).upper()} · run “{title}” ({note.slug})"


def live_runs(notes) -> list:
    """Runs somebody should know about on walking in: running first."""
    runs = [note for note in notes
            if note.kind == vocab.RUN and note.status in LIVE]
    return sorted(runs, key=lambda note: (LIVE.index(note.status), note.slug))
