#!/usr/bin/env python
"""Make a folder a MAGI project, in one step.

It took eight on a real machine: `magi skills install` refused without
`--host`; `--host auto` then installed into a folder that was not a project;
`magi init` said to run `magi install`, which installed the same skills again
plus the hooks; `magi sync` suggested indexing an empty project and a `pm init`
that would have committed into the enclosing repository; and the title and
scope stayed "My Project / A research project." because nothing could set them.

`magi init` now does the whole of it: the scaffold, the project's title and
scope (asked for on a terminal, the folder's name otherwise), and — unless
told not to — this project's skills, protocol block and hooks in every agent
CLI on the machine. It still only adds: an existing file is left alone, an
existing `.gitignore` gets MAGI's lines appended, and material already in the
folder is named and pointed at `magi adopt`, never moved.
"""

import os
import shutil
import sys
import argparse
from datetime import date, datetime
from pathlib import Path

from magi.core import managed

def keep_a_copy(p: Path) -> Path:
    """Put the current contents of *p* somewhere recoverable, and say where.

    `.backup` is the convention the rest of the workspace already uses — every
    scanner skips a path with it as a component, and `magi wiki
    refactor-concept` and `magi link` both write there. A second-granularity
    stamp is not a unique name, so take the first free one rather than
    overwriting the copy an earlier call in this same run just made.
    """
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = p.parent / ".backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{p.stem}_{stamp}{p.suffix}"
    n = 2
    while dest.exists():
        dest = backup_dir / f"{p.stem}_{stamp}-{n}{p.suffix}"
        n += 1
    shutil.copy2(p, dest)
    return dest

DECISIONS_STARTER = """# Decisions

Only what a person decided, and why in their own words. An agent transcribes
here; nothing writes to this file on its own initiative. A decision that is not
in this file was not made — it was assumed.
"""

NOTES_STARTER = """# Notes

Anything, in any order, whenever it occurs to you. Append; do not tidy. The
next `magi next` files each line where it belongs — a question, a proposition,
a decision, a post on an existing note, or a task — and leaves a link back.
"""

CLAUDE_POINTER = "@AGENTS.md\n"

#: What the protocol block says when nobody has said what the project is about.
#: Reported at the end of init with the command that replaces it.
PLACEHOLDER_SCOPE = "A research project."

#: Set by the test suite: a scaffolding test must not depend on which agent
#: CLIs the machine running it happens to have.
NO_INSTALL_ENV = "MAGI_INIT_NO_INSTALL"


def safe_write(p: Path, content: str, force: bool):
    if p.exists():
        if not force:
            print(f"Skipping existing {p}")
            return
        # --force is the only way to reach this line, and what it lands on is
        # ORIGINAL: config.md is the scope a person wrote, log.md is the
        # running record, config.yaml is their settings. "Overwrite existing
        # config/log/index files" is an honest description of the flag and
        # still leaves no way back from a mistyped one.
        kept = keep_a_copy(p)
        print(f"Overwriting {p} (previous contents kept at {kept})")
    p.write_text(content, encoding="utf-8")


#: The first line of what `merge_gitignore` appends, so a person reading the
#: file can tell their lines from MAGI's.
GITIGNORE_MARK = "# Added by `magi init`: what magi will make again. Nothing above was changed."


def merge_gitignore(path: Path, template: str, force: bool) -> None:
    """Give an existing `.gitignore` MAGI's rules without replacing its own.

    Skipping an existing file — what `safe_write` does — left MAGI's rules out
    of it entirely, so `scratch/` and the databases went into the person's
    history. Replacing it would throw away theirs. Appending the lines it does
    not already have is the only answer that keeps both.
    """
    if force or not path.exists():
        safe_write(path, template, force)
        return
    raw = path.read_bytes()
    existing = raw.decode("utf-8", errors="replace")
    have = {line.strip() for line in existing.splitlines()}
    rules = [line for line in template.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    missing = [rule for rule in rules if rule.strip() not in have]
    if not missing:
        print(f"Skipping existing {path} (it already ignores what magi rebuilds)")
        return
    newline = "\r\n" if b"\r\n" in raw else "\n"
    block = newline.join(["", GITIGNORE_MARK, *missing, ""])
    path.write_bytes((existing.rstrip("\r\n") + newline + block).encode("utf-8"))
    print(f"Added {len(missing)} line(s) to the existing {path.name} — what magi "
          "rebuilds; nothing already there was changed")


def create_minimal_index(path: Path, title: str, today: str, force: bool = False):
    """Scaffold one directory's `_index.md` using the shared renderer.

    The hand-rolled version here claimed "Generated by local magi lint",
    which was never true of this command, and emitted a `## Recent Changes`
    section with one bootstrap line that nothing ever added to. Between them,
    `magi init`, `magi lint --fix` and `magi wiki reindex` wrote three
    different formats into the same file.
    """
    from magi.core.wiki_common import render_index

    safe_write(path, render_index(path.parent, today=today), force)


def _material_already_here(root: Path) -> list[str]:
    """What the folder held before init: anything that is not MAGI's own."""
    from magi.adopt import SCAFFOLD

    try:
        return sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir()
                      if p.name not in SCAFFOLD and not p.name.startswith("."))
    except OSError:
        return []


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _identity(args, root: Path, existing: bool) -> tuple[str, str]:
    """The title and scope this run writes, and whether they came from a person.

    A terminal is asked; anything else gets the folder's name — a real name,
    unlike "My Project" — and a placeholder scope that the end of the run
    names, with the command that replaces it.
    """
    if existing and not args.force:
        # Init only adds. A project that already says who it is keeps saying
        # it — `magi config set` is the command that changes that, and init
        # names it rather than doing it quietly.
        from magi.config_cmd import read_identity

        current = read_identity(root)
        title = current.get("title") or root.name
        scope = current.get("scope") or PLACEHOLDER_SCOPE
        for key, given, have in (("title", args.name, title), ("scope", args.scope, scope)):
            if given and given != have:
                print(f"config.md already gives the {key} as {have!r}; left as it is — "
                      f'magi config set {key} "{given}" changes it')
        return title, scope
    title, scope = args.name, args.scope
    if sys.stdin.isatty() and sys.stdout.isatty():
        if title is None:
            title = _ask(f"Project title [{root.name}]: ")
        if scope is None:
            scope = _ask("What is it about, in one sentence? [later] ")
    return (title or root.name), (scope or PLACEHOLDER_SCOPE)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="magi init",
        description="Make this folder a MAGI project: the scaffold, its title and scope, "
                    "and this project's skills, protocol block and hooks in every agent "
                    "CLI found on the machine. Adds files; replaces none.")
    parser.add_argument("--project-dir", "--topic-dir", dest="topic_dir", default=".", help="Project directory path (default: current directory)")
    parser.add_argument("--title", "--name", dest="name", default=None,
                        help="Project title (default: asked on a terminal, otherwise the folder's name)")
    parser.add_argument("--scope", default=None,
                        help="What the project is about, in one sentence (default: asked on a terminal)")
    parser.add_argument("--coaching", default="light",
                        choices=["off", "light", "strict"],
                        help="How hard the project asks its human for a prediction. "
                             "strict refuses to start a derivation without one.")
    parser.add_argument("--host", action="append", default=[],
                        help="Install into this agent CLI only (repeatable; default: every one found)")
    parser.add_argument("--no-install", action="store_true",
                        help="Scaffold only; `magi install` does the rest later")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config/log/index files")
    args = parser.parse_args(argv)

    topic_path = Path(args.topic_dir).resolve()
    topic_path.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    # Looked at before anything is written, so "already here" means the
    # person's material and not the scaffold this run is about to add.
    already_here = _material_already_here(topic_path)
    existing_config = (topic_path / "config.md").is_file()
    title, scope = _identity(args, topic_path, existing_config)

    # 1. Folders to create
    subdirs = [
        "raw",
        "raw/articles",
        "raw/papers",
        "raw/repos",
        # No raw/notes. A folder by that name beside the sources invited a
        # person's own notes into the one tree nobody works on again; theirs
        # belong in drafts/ or inbox/notes.md.
        "raw/data",
        "wiki",
        "wiki/concepts",
        "wiki/topics",
        "wiki/references",
        # v2: research state lives in files, not in a task tracker. `threads/`
        # holds propositions, questions and lines; `drafts/` holds the working
        # out they point at. `wiki/theses/` is retired into the two of them —
        # existing workspaces keep theirs until `magi migrate` moves it.
        "threads",
        "drafts",
        # Verification scripts a proposition's `evidence:` names. Inside the
        # library so the reviewer can read them; a CLI's own scratch directory
        # is outside it, and evidence left there is evidence nobody can check.
        "tools",
        "output",
        "inbox",
        "inbox/.processed",
        "scratch"
    ]

    for d in subdirs:
        p = topic_path / d
        p.mkdir(parents=True, exist_ok=True)

    # 2. config.md
    config_content = f"""---
title: "{title}"
scope: "{scope}"
created: {today}
---

# {title}

## Scope

{scope}

## Conventions

- Source files go in `raw/` under the appropriate subdirectory
- Compiled articles go in `wiki/references/` (papers) and `wiki/concepts/` (terms)
- All compiled pages use YAML frontmatter and `[[double-bracket]]` concept links
"""
    safe_write(topic_path / "config.md", config_content, args.force)

    # 3. No log.md. The record is the posts in `threads/`, read with `magi
    #    feed`. A workspace that already has one keeps it — nothing writes to
    #    it any more, and nothing reads it either.

    # 4. Root _index.md
    root_index_content = f"""# {title}

> Topic wiki initialized on {today}.

## Quick Navigation

| Directory | Purpose |
|-----------|---------|
| [raw/](raw/) | Raw source materials |
| [wiki/](wiki/) | Compiled knowledge base |
| [output/](output/) | Generated artifacts — except `output/ingest/`, `output/radar/`, `output/reflect/`, `output/adopt/`, `output/tidy/` and `output/llm-ledger.jsonl`, which record decisions you made, what they cost, or how to undo a change, and cannot be rebuilt |
| [inbox/](inbox/) | Pending ingestion |

## Statistics

- Raw sources: 0
- Compiled articles: 0
- Concepts: 0
"""
    safe_write(topic_path / "_index.md", root_index_content, args.force)

    # 5. Minimal _index.md files for subdirectories
    dir_titles = [
        ("raw", "Raw"),
        ("raw/articles", "Articles"),
        ("raw/papers", "Papers"),
        ("raw/repos", "Repos"),
        ("raw/data", "Data"),
        ("wiki", "Wiki"),
        ("wiki/concepts", "Concepts"),
        ("wiki/topics", "Topics"),
        ("wiki/references", "References"),
        ("output", "Output"),
    ]

    for rel_path, dir_title in dir_titles:
        p = topic_path / rel_path / "_index.md"
        create_minimal_index(p, dir_title, today, args.force)

    # 5b. The human's two surfaces. `decisions.md` is the only file MAGI
    # never writes on its own initiative — an agent transcribes what a person
    # decided and nothing else. `inbox/notes.md` is the opposite: anything,
    # unsorted, and `magi next` files it.
    safe_write(topic_path / "decisions.md", DECISIONS_STARTER, args.force)
    safe_write(topic_path / "inbox" / "notes.md", NOTES_STARTER, args.force)

    # 6. Agent entry protocol: CLAUDE.md (Claude Code) + AGENTS.md (Codex
    # et al.) share one body so every host gets the same onboarding.
    # The protocol is one text, in `core/managed.py`, because it is read at
    # the start of every session on every host — and because `magi install`
    # has to be able to rewrite it in place when it changes.
    protocol = managed.body(title, scope, args.coaching)
    # The protocol goes in a marked block so upgrades can rewrite it without
    # touching what a person added around it. CLAUDE.md holds one line so
    # there is a single text to keep current, not two that drift.
    managed.write(topic_path / "AGENTS.md", protocol)
    safe_write(topic_path / "CLAUDE.md", CLAUDE_POINTER, args.force)

    # 7. Starter config.yaml (workspace-level; wins over ~/.config/magi/)
    # `research:` is written out even at the default, so the level the gate
    # reads is visible in the file rather than implied by its absence — and so
    # `--coaching strict` means the same thing to the gate as it does to the
    # protocol text the agent was just handed.
    config_yaml = """# MAGI workspace configuration (discovered by upward walk from cwd)
research:
  coaching: light      # off | light | strict — how hard the close gate pushes
  wip_limit: 7         # open propositions per line before it asks a person
                       # seven is a working-memory number: past it a person can
                       # no longer hold what is open and the line stops being one
  stall_days: 21       # days of silence before a line is called quiet — long
                       # enough that a week off is not a flag, short enough that
                       # a forgotten line surfaces within a month; half of it is
                       # how long `magi next` lets an open proposition sit before
                       # it starts asking for it to move
  review_host:         # empty = probe PATH for a CLI that is not the author;
                       # a host that fails to answer falls back to the next one
  review_model:        # empty = that host's strong tier (opus, flash-high, …)
                       # — measured: the cheap tier passes errors it did not read
                       # one string for every vendor: pin review_host too, or
                       # put `model:` on a research.hosts record instead
  review_effort:       # low | medium | high; empty = the strong tier's own
  llm_calls: true      # master switch for MAGI's own calls (there is no weekly
                       # budget; calls are recorded in output/llm-ledger.jsonl)
  rule_budget: 7       # lines the AGENTS.md rule section may hold
  rules: []            # rules this library earned; `magi reflect promote` adds them
  hosts: []            # agent CLIs beyond the built-in ones; `magi skills where` lists all
  search_projects: []       # other registered libraries this project's searches reach
                       # empty = just this one; `magi search --scope all` reads
                       # every enabled library, `magi kb list` names them
ollama:
  base_url: "http://127.0.0.1:11434"
  autostart: true      # start a stopped local Ollama on demand
  keep_alive: release  # release = unload the model when the magi command ends;
                       # a duration ("30m") keeps it warm between commands, -1 forever
  embed_batch: 16      # chunks per embedding request; raise on a roomy machine
models:
  ocr: "glm-ocr:q8_0"
  embedding: "qwen3-embedding:0.6b"
ocr:
  # Deliberately absent, not empty. Settings layer: this file overrides
  # ~/.config/magi/config.yaml key by key, and an explicit "" is a value —
  # it would shadow a token set once for you and leave every new workspace
  # unable to reach MinerU for no visible reason. Put the token in the user
  # file; uncomment here only to give this one topic a different key.
  # mineru_api_token: ""
  use_mineru: false
  timeout: 180
  dpi: 150
math:
  preamble: []         # lines `magi math check` adds to its LaTeX preamble, for a
                       # package or macro the whole library uses: ['\\usepackage{braket}']
semantic_link:
  threshold: 0.75
  merge_threshold: 0.85
  auto_merge_threshold: 0.95

# Literature radar (magi radar harvest)
radar:
  # A Semantic Scholar key is free and optional; without one the radar
  # shares an anonymous quota with everybody. $SEMANTIC_SCHOLAR_API_KEY
  # wins over this file. Deliberately absent rather than empty — see the
  # note on ocr.mineru_api_token above for why.
  # s2_api_key: ""
  arxiv_categories:
    - cond-mat.str-el
    - hep-th
  # Papers that describe what you care about — often other people's. May stay
  # empty: every arXiv ID on a reference card is merged in automatically.
  seed_arxiv_ids: []   # e.g. ["2606.25340"]
  days: 7              # the arXiv leg only; S2 recommends within a fixed 60 days
  max_candidates: 40
  min_relevance:       # drop candidates below this score; empty = keep all
  # A third leg: keyword search over the whole Semantic Scholar corpus.
  # The other two can only ever see new papers — S2 recommends within 60
  # days and the arXiv leg reads a rolling listing — so this is the only
  # one that can find something published last year. Empty = off.
  bulk_queries: []     # e.g. ["fracton topological order"]
  bulk_years:          # e.g. "2023-"; empty = any year
  # Your own papers, for `magi radar citation-gap`. Left empty it falls
  # back to seed_arxiv_ids above — which are usually other people's, and
  # the report then asks who failed to cite them.
  own_arxiv_ids: []
"""
    if args.coaching != "light":
        config_yaml = config_yaml.replace("coaching: light",
                                          f"coaching: {args.coaching}", 1)
    safe_write(topic_path / "config.yaml", config_yaml, args.force)

    # 8. .gitignore. A topic workspace is a directory of markdown a researcher
    #    will very reasonably push somewhere, and nothing here said which parts
    #    of it are rebuildable and which are the only copy of a decision.
    #
    #    The two exclusions are the point. `output/` looks entirely generated
    #    and mostly is, but `output/ingest/` records what was queued, converted
    #    and approved, `output/radar/` records which candidates a person said
    #    no to, and `output/reflect/` holds what the slow loop understood from
    #    transcripts that have since rotated away — re-running rebuilds none of
    #    them, because the upstream windows have moved on. A blanket `output/`
    #    would quietly drop all of it.
    gitignore = """# What `magi` will make again. Everything else here is yours.

scratch/
output/*.db
output/*.db-wal
output/*.db-shm
output/.lint_cache.json
output/.embeddings_cache*
# One line per sub-agent spawn, per session. It nags once while the session is
# running and answers nothing afterwards — and a per-session counter in git is
# a merge conflict with no upside.
output/fanout.jsonl

# Deliberately NOT ignored, and not an oversight:
#   output/ingest/           what was queued, converted, decided and committed
#   output/radar/            which candidates you already said no to
#   output/reflect/          what the slow loop noticed, and what you did about it
#   output/tidy/             what each `magi math repair`/`format` run changed — the undo
#   output/llm-ledger.jsonl  what MAGI's own model calls cost you this week
# None of it can be regenerated — a re-run returns a different world, and the
# transcripts the slow loop read have rotated away — so they are tracked
# alongside the library they describe.

# Ingest leaves the originals here until they are filed.
inbox/.processed/

# Backups taken before a pass that rewrites cards in place.
**/.backup/

.DS_Store
Thumbs.db
"""
    merge_gitignore(topic_path / ".gitignore", gitignore, args.force)

    print(f"Project initialized at: {topic_path}")

    # Put it in the global registry now rather than as a side effect of the
    # first `magi index`. Until that ran, a freshly created workspace was
    # absent from /api/kb — so it did not appear in the WebUI's workspace
    # picker or to the browser extension, and the two surfaces that exist to
    # get material *into* a new library were the two that could not see it.
    # register_kb is idempotent on the resolved path, and takes no name so it
    # picks the same one `magi index` would.
    try:
        from magi.kb_registry import register_kb

        register_kb(topic_path, quiet=True)
        print("Registered — it will show up in 'magi kb list' and in the "
              "WebUI project picker.")
    except Exception as exc:  # non-fatal: the workspace itself is scaffolded
        print(f"Warning: could not register this project: {exc}")

    # There is no step 8. A topic under `<hub>/topics/` used to be registered
    # in that hub as well, by shelling out to `magi hub register` — a command
    # retired in M3 along with hubs themselves, when a research line became a
    # view over one project rather than a directory in a tree. The call stayed
    # behind and printed "unknown command 'hub'" as a warning on every init in
    # such a directory, which is how a person learns to ignore warnings. What
    # still matters is `register_kb` above: it is what `magi kb list` and the
    # WebUI picker read, and it has already run.

    # 9. The agent CLIs. `magi install`, not `magi skills install`: the narrow
    #    one installs skills and asks which host; this one does skills, the
    #    AGENTS.md block and all three hooks for every CLI it finds. Sending a
    #    first-timer off to run it — which is what init used to end with — is
    #    how somebody ends up with skills and no end-of-session gate.
    if args.no_install or os.environ.get(NO_INSTALL_ENV):
        installed = None
    else:
        from magi import install_cmd

        install_argv = ["--project-dir", str(topic_path)]
        for host in args.host:
            install_argv += ["--host", host]
        print("\nInstalling this project into the agent CLIs found here "
              "(skills, protocol block, hooks):")
        installed = install_cmd.main(install_argv) == 0
        if not installed:
            print("  not installed — once an agent CLI is set up: magi install")

    # 10. What was there before, and where the folder sits.
    if already_here:
        shown = ", ".join(already_here[:5]) + (", …" if len(already_here) > 5 else "")
        print(f"\nThis folder already held {len(already_here)} item(s) ({shown}); "
              "none of them was touched.")
        print("  To bring them into the project: magi adopt survey .")
    try:
        from magi.pm import enclosing_repo

        outer = enclosing_repo(topic_path)
    except Exception:  # noqa: BLE001 — a note, never a failure
        outer = None
    if outer is not None:
        print(f"\nThis project is a folder inside the git repository at {outer}: "
              "its files are that repository's to commit, and `magi pm init` "
              "would commit into it.")

    print("\nNext:")
    if installed is None and not os.environ.get(NO_INSTALL_ENV):
        print("  magi install                              # skills, protocol block, hooks")
    if scope == PLACEHOLDER_SCOPE:
        print('  magi config set scope "<one sentence>"    # the protocol block still says a placeholder')
    print("  magi ingest url <arXiv id, DOI or link>   # bring in sources")
    print("  magi next                                 # from here on, it says what to do")

if __name__ == "__main__":
    sys.exit(main())
