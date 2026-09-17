---
name: discuss
description: "Talk a run over with the person until it can be written down — motivation, directions, what is not worth it, when to stop, their taste — and they sign it."
commands:
  discuss: "Align with the person and write the contract of a run."
origin: magi
---

# discuss

## When to use
`magi next` opens with `DISCUSSING`, or the person wants something explored
while they are away. Nothing is authorised in this phase: the job is to talk.

## Method
1. `magi run start --title "<what it is after>"` opens the run and its empty
   contract in `threads/`. Read what the project already knows first (`ask`).
2. Converge on a few directions. Each becomes a `[[question]]`; a guess the
   person holds becomes its own conjectured proposition carrying their bet:
   `magi thread bet <slug> <supported|refuted|unknown>`.
3. Fill the contract's sections in the person's language and, where you can,
   their words: Motivation, Directions, Not worth it, Stop when, Taste, Allowed.
4. End on an alignment quiz — 3 to 5 hypothetical forks about risk: "only a
   known result in new words: go on or drop it?", "the worker's safe first
   choice or its radical last one?". The answers go under Taste, verbatim.
5. Read the contract back to them. They sign, or say so and you type it:
   `magi run sign <run> --steps N --mode <deep|balanced|explore>` — the number
   and the mode are theirs to say (deep: a chain that stands; explore: a map).

## Rules
- **Never** register a step, open exploratory propositions or start deriving
  here: work done before the signature is in no trajectory and nobody
  authorised it. Reading, ingesting and compiling seed papers is fine.
- **Never** fill a section with what you suppose they want — ask. Whatever is
  not written down is lost the moment another agent takes the run over.
- That agent may be another vendor's, with none of this conversation: the
  contract is all it gets. Write it for that reader.
- A signed contract changes one way, and it is the person's:
  `magi run amend <run> --text "<what changes>"`, edit, sign again.
