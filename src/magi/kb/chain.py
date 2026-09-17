"""Which propositions rest on which, and which of them actually stand.

`depends_on:` points a proposition at the *concepts* it is stated in terms of.
Nothing pointed it at the other *propositions* it takes as given, and an
unattended run needs exactly that (docs/design-auto.md §6): with nobody
watching, the expensive mistake is not a wrong claim but four more built on
it. `premises: [[other-proposition]]` records the dependency, and three things
follow from it, all derived, none stored:

- a proposition **stands** when a reviewer at the strong tier said so *and*
  everything it rests on stands — being "supported" is the author's word;
- a proposition whose premise has since been refuted owes a second look;
- the **chain** of a finding — its premises, theirs, and so on, in the order
  they have to be read — is the spine of the run's report, and the measure of
  what is worth a person's attention: a dispute that is on no chain is not
  between them and anything they are about to read.
"""

from __future__ import annotations

from ..core import vocab
from . import threads

STANDS, UNREVIEWED, CONTESTED = "stands", "unreviewed", "contested"

#: Statuses at which a claim has been given up or is being fought over.
_CONTESTED = frozenset({"refuted", "disputed", vocab.CONFLICT})


def slug_of(link) -> str:
    """`[[threads/p-gap|the gap]]` → `p-gap`. A bare slug is returned as it is."""
    text = str(link or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    text = text.split("|", 1)[0].strip().replace("\\", "/")
    if text.endswith(".md"):
        text = text[:-3]
    return text.rsplit("/", 1)[-1]


def premises(note) -> list:
    return [slug for slug in (slug_of(item) for item in
                              threads.as_list(note.frontmatter.get("premises"))) if slug]


def index(notes) -> dict:
    return {note.slug: note for note in notes if note.kind == vocab.PROPOSITION}


def reviewed_strongly(note) -> bool:
    """Supported, and answered `stands` since it last claimed to be.

    A verdict from the cheap tier does not count: measured on this project
    (2026-09-03), that tier gave twelve verdicts and passed all four real
    errors. A post that names no tier is from before tiers were recorded and
    is taken at its word, as `weakly_reviewed` does.
    """
    from .. import review

    if note.kind != vocab.PROPOSITION or note.status != "supported":
        return False
    last_claim = -1
    for position, post in enumerate(note.posts):
        if post.is_transition and post.dst == "supported":
            last_claim = position
    answers = [post for post in note.posts[last_claim + 1:] if review._is_answer(post)]
    if not answers:
        return False
    last = answers[-1]
    first = (last.text or "").lstrip().splitlines()[0]
    return first.startswith("VERDICT: stands") and review.tier_of_post(last) != "cheap"


def stands(slug: str, by_slug: dict, _seen=None) -> bool:
    """Reviewed at the strong tier, and so is everything underneath it.

    A cycle does not stand: two claims each resting on the other have no
    ground under either.
    """
    seen = set() if _seen is None else _seen
    if slug in seen:
        return False
    note = by_slug.get(slug)
    if note is None or not reviewed_strongly(note):
        return False
    seen = seen | {slug}
    return all(stands(premise, by_slug, seen) for premise in premises(note))


def label(slug: str, by_slug: dict) -> str:
    """What the report says beside a claim: stands / unreviewed / contested."""
    note = by_slug.get(slug)
    if note is None:
        return UNREVIEWED
    if note.status in _CONTESTED:
        return CONTESTED
    return STANDS if stands(slug, by_slug) else UNREVIEWED


def closure(slugs, by_slug: dict) -> list:
    """The given claims and everything they rest on, premises before what
    rests on them. Unknown slugs and cycles are walked past, not raised on."""
    ordered: list = []
    done: set = set()

    def visit(slug: str, trail: tuple) -> None:
        if slug in done or slug in trail or slug not in by_slug:
            return
        for premise in premises(by_slug[slug]):
            visit(premise, trail + (slug,))
        done.add(slug)
        ordered.append(slug)

    for slug in slugs:
        visit(slug, ())
    return ordered


def dependents(by_slug: dict) -> dict:
    """`{premise: [claims that rest on it]}`."""
    out: dict = {}
    for slug, note in by_slug.items():
        for premise in premises(note):
            out.setdefault(premise, []).append(slug)
    return out


def is_orphan(note, rests_on_it: dict) -> bool:
    """Answers no question and nothing rests on it: work with no stated use."""
    if threads.as_list(note.frontmatter.get("answers")):
        return False
    return not rests_on_it.get(note.slug)


def _refuted_at(note):
    """The post that refuted it, or None."""
    for post in reversed(note.posts):
        if post.is_transition and post.dst == "refuted":
            return post
    return None


def fallen(notes) -> list:
    """`[(claim, premise, when)]`: a premise was refuted and nobody has looked
    at what rests on it since.

    "Looked at" is any post on the dependent after the refutation, in time
    order — the same standard the rest of the bookkeeping uses: say what
    happened. The status is not moved for anybody. Whether a claim survives
    the loss of a premise is a judgement, and the lifecycle table is there to
    catch bookkeeping mistakes, not to do research.
    """
    by_slug = index(notes)
    out = []
    for slug, note in sorted(by_slug.items()):
        if note.status in ("refuted", "superseded"):
            continue
        for premise in premises(note):
            ground = by_slug.get(premise)
            if ground is None or ground.status != "refuted":
                continue
            blow = _refuted_at(ground)
            if blow is None:
                continue
            answered = any(str(post.at) > str(blow.at) for post in note.posts)
            if not answered:
                out.append((slug, premise, str(blow.at)))
    return out
