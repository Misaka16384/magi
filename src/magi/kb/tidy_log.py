"""What a mechanical repair pass changed, written down, and the way back.

`raw/` is conversion output nobody is meant to edit by hand, and the passes
that do rewrite it — `magi math format`, `magi math repair` — used to leave
no trace of what they touched. When one of them was wrong (format once turned
`\\[14.22636pt]` into `[14.22636pt]`, merging two rows of an array) the only
record was whatever the person happened to have in git. A real session wrote
its own JSON log of 382 before/after pairs to be able to undo its repairs;
this is that log, kept by the tool.

One record per run, under `output/tidy/`, tracked like `output/ingest/`: it
cannot be regenerated, because the "before" is exactly what the run replaced.
Each change is a line range in the file as the run left it, so `magi math
undo` can put it back — positionally when the file is untouched since, by
finding the changed text when something else moved it, and not at all when
the changed text itself was edited afterwards.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from magi.core.wiki_common import atomic_write

LOG_DIR = Path("output") / "tidy"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def changes_between(rel: str, before: str, after: str, kind: str) -> list[dict]:
    """The edits that turn *before* into *after*, as undoable line ranges."""
    a, b = before.split("\n"), after.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out.append({
            "file": rel, "kind": kind, "line": j1 + 1,
            "before": "\n".join(a[i1:i2]), "after": "\n".join(b[j1:j2]),
            "before_lines": i2 - i1, "after_lines": j2 - j1,
        })
    return out


def write(root, command: str, changes: list[dict], *, results: dict | None = None,
          note: str = "") -> Path | None:
    """Keep the record of one run. None when there is nothing to keep or nowhere.

    *results* maps each file's recorded name to the text the run left, so
    undo can tell a file nobody has touched since from one somebody has.
    """
    if not changes or root is None:
        return None
    directory = Path(root) / LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")
    path = directory / f"{stamp}-{slug}.json"
    n = 2
    while path.exists():
        path = directory / f"{stamp}-{slug}-{n}.json"
        n += 1
    record = {
        "command": command,
        "when": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "files": {rel: _digest(text) for rel, text in (results or {}).items()},
        "changes": changes,
    }
    atomic_write(path, json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _lines(text: str, n: int) -> list[str]:
    return text.split("\n") if n else []


def undo(log_path, *, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """Put back what one recorded run changed. (restored, skipped, problems)."""
    log_path = Path(log_path).resolve()
    record = json.loads(log_path.read_text(encoding="utf-8"))
    # <root>/output/tidy/<record>.json — the record travels with its project.
    root = log_path.parent.parent.parent
    by_file: dict[str, list[dict]] = {}
    for change in record.get("changes", []):
        by_file.setdefault(change["file"], []).append(change)

    restored = skipped = 0
    problems: list[str] = []
    for rel, changes in by_file.items():
        path = Path(rel) if Path(rel).is_absolute() else root / rel
        try:
            raw = path.read_bytes()
        except OSError as exc:
            skipped += len(changes)
            problems.append(f"{rel}: {exc}")
            continue
        # Written back with the line endings it has: a CRLF paper that came
        # back LF would be a diff on every line of it.
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        untouched = record.get("files", {}).get(rel) == _digest(text)
        lines = text.split("\n")
        # Bottom up, so each range is still where the run left it.
        for change in sorted(changes, key=lambda c: c["line"], reverse=True):
            after = _lines(change["after"], change["after_lines"])
            before = _lines(change["before"], change["before_lines"])
            start = change["line"] - 1
            if not untouched and lines[start:start + len(after)] != after:
                width = len(after)
                hits = ([k for k in range(len(lines) - width + 1)
                         if lines[k:k + width] == after] if width else [])
                if len(hits) != 1:
                    skipped += 1
                    problems.append(f"{rel}:{change['line']}: edited since the run — "
                                    f"left as it is")
                    continue
                start = hits[0]
            lines[start:start + len(after)] = before
            restored += 1
        if not dry_run:
            atomic_write(path, "\n".join(lines), encoding="utf-8",
                         newline="\r\n" if crlf else "\n")

    if not dry_run and restored:
        record["undone"] = datetime.now().isoformat(timespec="seconds")
        atomic_write(log_path, json.dumps(record, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return restored, skipped, problems


def newest(root) -> Path | None:
    """The most recent record under *root* that has not been undone."""
    directory = Path(root) / LOG_DIR
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            if "undone" not in json.loads(path.read_text(encoding="utf-8")):
                return path
        except (OSError, ValueError):
            continue
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="magi math undo",
        description="Put back what a `magi math format` or `magi math repair` run "
                    "changed, from the record it wrote under output/tidy/.")
    parser.add_argument("record", nargs="?", default=None,
                        help="The run's record (default: the newest not yet undone)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Say what would be put back; change nothing")
    args = parser.parse_args(argv)

    if args.record is None:
        from magi.core.workspace import find_workspace_root

        root = find_workspace_root()
        if root is None:
            parser.error("no MAGI workspace here — pass the record's path")
        log = newest(root)
        if log is None:
            print(f"Nothing to undo: no record under {root / LOG_DIR} that is not undone already.")
            return 0
    else:
        log = Path(args.record)
        if not log.is_file():
            print(f"No such record: {log}")
            return 1

    record = json.loads(log.read_text(encoding="utf-8"))
    if "undone" in record:
        print(f"{log} was already undone on {record['undone']}.")
        return 0
    restored, skipped, problems = undo(log, dry_run=args.dry_run)
    verb = "Would put back" if args.dry_run else "Put back"
    print(f"{verb} {restored} change(s) from {record.get('command', 'a run')} "
          f"({record.get('when', '?')}).")
    for line in problems:
        print(f"  skipped {line}")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
