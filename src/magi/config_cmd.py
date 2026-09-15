"""`magi config` — what a project says about itself, and its settings.

`magi init` writes a title and a scope into `config.md`, and the protocol
block every agent reads at session start is rendered from them. Given no
`--name`, that was "My Project / A research project." — and nothing but a text
editor could change it afterwards, which also meant editing `config.md` by
hand and then remembering that AGENTS.md would not follow until the next
`magi install`. A real session shipped with the placeholders for that reason.

    magi config get                      # title, scope, and where settings live
    magi config get research.coaching    # one config.yaml key, as the project sees it
    magi config set title "Mobility fusion"
    magi config set scope "Fusion rules of fracton mobility"
    magi config set ollama.keep_alive 30m

`title` and `scope` live in `config.md`; every other key is `section.key` in
the project's `config.yaml`. Either way the AGENTS.md block is re-rendered.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from magi.core.workspace import find_workspace_root

IDENTITY = ("title", "scope")

_FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def read_identity(root: Path) -> dict:
    """`{"title": …, "scope": …}` from config.md, missing keys left out."""
    try:
        text = (Path(root) / "config.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return {}
    m = _FRONT_RE.match(text)
    if not m:
        return {}
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return {k: str(front[k]) for k in IDENTITY if front.get(k) is not None}


def set_identity(root: Path, key: str, value: str) -> str | None:
    """Write *key* into config.md. Returns what it said before.

    The heading and the Scope section `magi init` wrote follow the frontmatter
    when they still say what it said — a person's own rewrite of either is
    theirs and stays.
    """
    if key not in IDENTITY:
        raise ValueError(f"{key!r} is not one of {', '.join(IDENTITY)}")
    value = " ".join(str(value).split())
    if not value:
        raise ValueError(f"an empty {key} says nothing")
    root = Path(root)
    path = root / "config.md"
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else ""
    m = _FRONT_RE.match(text)
    front = (yaml.safe_load(m.group(1)) or {}) if m else {}
    body = text[m.end():] if m else ("\n" + text if text else "\n")
    old = front.get(key)
    front[key] = value
    if old is not None:
        old = str(old)
        if key == "title":
            body = re.sub(rf"^# {re.escape(old)}[ \t]*$", lambda _: f"# {value}", body,
                          count=1, flags=re.M)
        else:
            body = body.replace(f"## Scope\n\n{old}\n", f"## Scope\n\n{value}\n", 1)
    dumped = yaml.safe_dump(front, allow_unicode=True, sort_keys=False,
                            default_flow_style=False, width=10_000)
    path.write_text("---\n" + dumped + "---\n" + body, encoding="utf-8")

    # The root index opens with the title too.
    index = root / "_index.md"
    if key == "title" and old is not None and index.is_file():
        itext = index.read_text(encoding="utf-8")
        new_index = re.sub(rf"^# {re.escape(old)}[ \t]*$", lambda _: f"# {value}", itext,
                           count=1, flags=re.M)
        if new_index != itext:
            index.write_text(new_index, encoding="utf-8")
    return old


def _rerender(root: Path) -> str:
    from magi.install_cmd import install_protocol

    return install_protocol(root)


def cmd_get(args) -> int:
    root = _root(args)
    if root is None:
        return 1
    if args.key in IDENTITY or args.key is None:
        ident = read_identity(root)
        if args.key:
            value = ident.get(args.key)
            if args.json:
                print(json.dumps({args.key: value}, ensure_ascii=False))
            else:
                print(value if value is not None else "")
            return 0 if value is not None else 1
        if args.json:
            print(json.dumps({**ident, "config_md": str(root / "config.md"),
                              "config_yaml": str(root / "config.yaml")}, ensure_ascii=False))
            return 0
        print(f"title: {ident.get('title', '(not set)')}")
        print(f"scope: {ident.get('scope', '(not set)')}")
        print(f"\nidentity: {root / 'config.md'}")
        print(f"settings: {root / 'config.yaml'}  (magi config get <section.key>)")
        return 0

    from magi.core.config_loader import get as cfg_get, load_config

    missing = object()
    value = cfg_get(load_config(start=root), args.key, missing)
    if value is missing:
        print(f"{args.key} is not set here (nor in the user config)", file=sys.stderr)
        return 1
    print(json.dumps({args.key: value}, ensure_ascii=False, default=str) if args.json
          else (yaml.safe_dump(value, allow_unicode=True).strip()
                if isinstance(value, (dict, list)) else value))
    return 0


def cmd_set(args) -> int:
    root = _root(args)
    if root is None:
        return 1
    if args.key in IDENTITY:
        try:
            old = set_identity(root, args.key, args.value)
        except ValueError as exc:
            print(f"magi config set: {exc}", file=sys.stderr)
            return 1
        said = f" (was: {old})" if old is not None else ""
        print(f"{args.key} set in config.md{said}")
    else:
        from magi.core.config_edit import ConfigEditError, set_config_value

        if "." not in args.key:
            print(f"magi config set: {args.key!r} is neither title/scope nor section.key",
                  file=sys.stderr)
            return 2
        try:
            value = yaml.safe_load(args.value)
        except yaml.YAMLError:
            value = args.value
        try:
            set_config_value(root / "config.yaml", args.key, value)
        except ConfigEditError as exc:
            print(f"magi config set: {exc}", file=sys.stderr)
            return 1
        print(f"{args.key}: {value!r} set in config.yaml")
    print(f"  {_rerender(root)}")
    return 0


def _root(args) -> Path | None:
    root = Path(args.project_dir).resolve() if args.project_dir else find_workspace_root()
    if root is None:
        print("no project found (run inside one, or pass --project-dir)", file=sys.stderr)
    return root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="magi config", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="config_command", required=True)

    get = sub.add_parser("get", help="Show the title and scope, or one key")
    get.add_argument("key", nargs="?", help="title, scope, or section.key in config.yaml")
    get.add_argument("--project-dir", "--topic-dir", dest="project_dir")
    get.add_argument("--json", action="store_true")
    get.set_defaults(func=cmd_get)

    put = sub.add_parser("set", help="Set the title, the scope, or a config.yaml key")
    put.add_argument("key", help="title, scope, or section.key in config.yaml")
    put.add_argument("value", help="The value; YAML, so 7 is a number and true a boolean")
    put.add_argument("--project-dir", "--topic-dir", dest="project_dir")
    put.set_defaults(func=cmd_set)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
