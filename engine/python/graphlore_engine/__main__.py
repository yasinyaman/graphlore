"""``python -m graphlore_engine``: the engine's command line.

    build ROOT [--out PATH] [--commit SHA] [--no-docs] [--out-dir-name NAME]
    spans FILE [--as PATH]
    languages

``build`` prints its report as JSON on stdout; a failure prints ``ERROR: ...``
on stderr and exits 1. graphlore's server runs builds this way, in a child
process, so a crash in a grammar cannot take the server down.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from . import __version__, build, file_spans, languages

    p = argparse.ArgumentParser(prog="graphlore-engine")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build graph.json for a project")
    b.add_argument("root")
    b.add_argument("--out", help="graph.json to write (default: ROOT/OUT_DIR_NAME/graph.json)")
    b.add_argument("--commit", help="value for built_at_commit")
    b.add_argument("--no-docs", action="store_true", help="skip Markdown / RST headings")
    b.add_argument("--out-dir-name", default="graphify-out")
    b.add_argument("--max-file-bytes", type=int, default=2 * 1024 * 1024)
    b.add_argument("--threads", type=int)
    s = sub.add_parser("spans", help="print the definition spans of one file")
    s.add_argument("file")
    s.add_argument("--as", dest="rel", help="path to detect the language from")
    sub.add_parser("languages", help="list the languages and extensions read")
    args = p.parse_args(argv)

    if args.cmd == "languages":
        for name, exts in languages():
            print(f"{name}: {' '.join('.' + e for e in exts)}")
        return 0
    if args.cmd == "spans":
        src = Path(args.file).read_bytes()
        for row in file_spans(src, args.rel or args.file):
            print("\t".join(map(str, row)))
        return 0
    out = args.out or str(Path(args.root) / args.out_dir_name / "graph.json")
    try:
        report = build(
            args.root, out=out, commit=args.commit, documents=not args.no_docs,
            out_dir_name=args.out_dir_name, max_file_bytes=args.max_file_bytes,
            threads=args.threads,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
