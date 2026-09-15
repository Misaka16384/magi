---
name: tidy
description: "Repair what the mechanical passes cannot: broken LaTeX from conversion, sprawling tags, and concepts that are secretly the same concept."
commands:
  tidy: "Fix conversion damage, tag sprawl and duplicate concepts."
origin: magi
---

# tidy

## When to use
`magi math check` reports errors, tags have sprawled into near-duplicates, or
two concept cards are about the same thing.

## Method
**Maths.** Mechanical first, each run undoable with `magi math undo`: for arXiv-HTML papers `magi math repair --dry-run`
then `magi math repair`; then `magi math format --dry-run` and `magi math format`. Then `magi math check --json`:
fix one file's *first* entry and re-check — one unclosed `$$` reports as many errors as it swallows.
`likely-macro` is a package or the author's macro, not a typo: define it in `math.preamble` or `<paper>.macros.tex`,
never in the formula; check the PDF first (`magi ingest crop <pdf>`). A figure inside a formula becomes
`\text{[omitted: <what it was>]}` — that shape exactly.

**Tags.** `magi tags extract .`, read the counts, and write the mapping that
collapses synonyms, acronyms and plurals into one tag. Show it to the human
before `magi tags apply . <tag_map> <alias_map>` — it rewrites every card. Two tags that are aliases of
each other usually mean two cards about one concept.

**Concepts.** `magi link --dedup-only` proposes merges. Confirm each is really
one concept, then `magi wiki refactor-concept --project-dir . --old <a> --new <b>`. A sub-concept
merges into its parent; read it back with `magi wiki context --name <c>` first.

Close with `magi lint --fix` and `magi index`.

## Rules
- **Never** apply a tag mapping the human has not seen. It is not reversible
  by hand.
- **Never** rename a concept by editing filenames; `refactor-concept` is the
  only route that fixes the links too.
- The unit of work is the project, not the file: re-run `magi math check`
  at the end and report what is still broken as **partial**, not as done.
