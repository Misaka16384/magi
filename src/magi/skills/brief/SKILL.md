---
name: brief
description: "Write the one thing a person reads after an unattended run — its report — and the lecture notes that prepare them for it, in words they already have."
commands:
  brief: "Write a run's report, or lecture notes on a method it used."
origin: magi
---

# brief

## When to use
A run has enough to write up, or only its report is left; or `magi next` lists
lecture notes the person asked for.

## Method
1. Report: `magi run outline <run> --write` (add `--empty` when nothing big was
   found: one page). The claims and whether each stands, the chain, the steps
   and the glossary are in it already; every `<!-- … -->` is yours to replace.
2. Write for one sitting, start to end: motivation → what is and is not shown →
   what is new → the picture and the core insight (brute force said honestly) →
   the derivation in chain order, keeping what was decided or is not obvious →
   decisions → failures worth knowing (at most 5, three lines each) → is it
   still promising.
3. `magi review drafts/runs/<run>.md` has another agent read it whole. Fix what
   it names, then `magi run report <run> drafts/runs/<run>.md`; the gate says
   what a reader would still trip on.
4. Lecture notes: `magi familiar list` shows what was asked for. Write
   `drafts/lectures/<concept>.md` from the concept card, the `raw/` sources and
   *the place a claim of this run uses it* — "in [[claim]] it enters like this" —
   pitched at what the list marks known. Inside a run, register a step first.

## Rules
- **Never** coin a name. A term the reader meets is a concept card
  (`magi wiki add-concept --name "…" --source "…" --content "…"`) or plain words.
- **Never** cite a claim by its slug alone: quote its statement where it first
  appears. A slug means nothing to the person reading.
- A term `magi familiar list` does not mark known is defined at first use, or
  linked to its lecture notes.
- Present nothing as established that section 2 marks unreviewed or contested.
