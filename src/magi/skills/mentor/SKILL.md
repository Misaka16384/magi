---
name: mentor
description: "Drive a signed run while the person is away — choose the next step from the map, register it, have it done, record what came of it — so that any agent on any host can take over."
commands:
  mentor: "Carry a signed run forward, one registered step at a time."
origin: magi
---

# mentor

## When to use
`magi next` opens with `RUNNING`. You are the mentor whether or not you were in
the discussion: the contract is frozen, and `magi run status <run>` is all of it.

## Method
1. `magi run status <run>`. A step a dead session left open comes first:
   finish it, or `magi run result <run> <n> --abandoned --text "<why>"`.
2. Choose from the map — contract, questions, claims and their statuses — not
   from derivation details. Serve the contract's target; skip its "Not worth it".
3. Register before acting: `magi run step <run> --do "…" --because "…"
   --if-true "…" --if-false "…"`. Both outcomes lead to the same next move?
   That is trivia; do not take the step.
4. Have it done — a sub-agent where the host has them, yourself otherwise —
   through `research`, `compile`, `ingest`. Work is saved to `drafts/`, `tools/`
   and thread posts as it goes, never only in a context that can vanish.
5. `magi run result <run> <n> --text "…"`, linking what it produced.
6. A worker offers options and trying is cheap: take its favourite *and* the
   most ambitious. Every few steps pull back — the picture, the core insight
   (brute force said honestly), whether this is still promising.
7. Enough to write up, or only the report left (then the step is `--closing`):
   `magi run outline <run> --write`, and the `brief` skill takes it from there.

## Rules
- **Never** edit the contract, sign, amend, bet for the person, close a line or
  publish. A contract that looks wrong: post why, work another direction.
- **Never** ask the person anything mid-run; `magi next` parks what needs them.
  Collect the questions workers could not ask and put them in the report.
- The CLI sets the concurrency (`max_parallel`): do not start what it refused.
- No state in the host's own task list, plan file or memory — another vendor's
  agent cannot read them. **Partial** results are recorded as partial.
