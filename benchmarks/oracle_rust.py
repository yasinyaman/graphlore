# ruff: noqa: E501
"""The native engine's Rust edges against rust-analyzer's own answers.

`rust-analyzer lsif` resolves a whole Cargo workspace the way the compiler
front end does (macros expanded, traits solved) and writes, for every name,
the declaration it is bound to. This script runs it on a real repository
(it needs `rust-analyzer` and the crates the workspace depends on), builds
the native graph of the same repository and compares, as
`oracle_typescript.py` does: an edge whose site rust-analyzer has an answer
for, does it go to the declaration it names; and a use it binds to a
definition the graph has, is there an edge.

Like `oracle_go.py` this is no test suite: it is the language's own
resolver, on the whole program.

    rustup component add rust-analyzer
    python benchmarks/oracle_rust.py /tmp/polyglot/ureq --samples 20
    python benchmarks/oracle_rust.py --dump PROJECT   # one line per binding (verify_cases.py --binding)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_typescript as oracle  # noqa: E402


def bindings(root: Path) -> list[dict[str, Any]]:
    done = subprocess.run(["rust-analyzer", "lsif", "."], cwd=root, capture_output=True, text=True)
    if not done.stdout:
        raise SystemExit(f"rust-analyzer wrote no index for {root}: {done.stderr[-400:]}")
    docs: dict[int, str] = {}
    ranges: dict[int, tuple[int, int, int]] = {}
    doc_of: dict[int, int] = {}
    nxt: dict[int, int] = {}
    definition: dict[int, int] = {}
    items: dict[int, list[int]] = {}
    for line in done.stdout.splitlines():
        o = json.loads(line)
        if o["type"] == "vertex":
            if o["label"] == "document":
                docs[o["id"]] = o["uri"]
            elif o["label"] == "range":
                ranges[o["id"]] = (o["start"]["line"], o["start"]["character"], o["end"]["character"])
        elif o["label"] == "contains":
            for r in o["inVs"]:
                doc_of[r] = o["outV"]
        elif o["label"] == "next":
            nxt[o["outV"]] = o["inV"]
        elif o["label"] == "textDocument/definition":
            definition[o["outV"]] = o["inV"]
        elif o["label"] == "item":
            items.setdefault(o["outV"], []).extend(o["inVs"])
    prefix = root.resolve().as_uri() + "/"

    def rel(doc: int) -> str | None:
        uri = docs.get(doc, "")
        return uri[len(prefix):] if uri.startswith(prefix) else None

    sources: dict[str, list[str]] = {}
    out = []
    for r, (line, start, end) in ranges.items():
        here = rel(doc_of.get(r, -1))
        targets = items.get(definition.get(nxt.get(r, -1), -1), [])
        if here is None or not targets or r in targets:
            continue
        if here not in sources:
            sources[here] = (root / here).read_text(encoding="utf-8", errors="replace").splitlines()
        text = sources[here][line][start:end] if line < len(sources[here]) else ""
        decls = [(f, ranges[t][0] + 1) for t in targets if t in ranges
                 for f in [rel(doc_of.get(t, -1))] if f is not None]
        out.append({"file": here, "line": line + 1, "expr": text, "symbol": text, "decls": decls,
                    "lib": not decls, "alias": False, "declares": (here, line + 1) in decls})
    return out


def judge(root: Path) -> dict[str, Any] | None:
    uses = bindings(root)
    files: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and (p.suffix == ".rs" or p.name == "Cargo.toml") \
                and not rel.startswith("target/") and "/." not in "/" + rel:
            files[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
    result = oracle.compare(root.name, files, uses, slack=(-3, 3))
    if result:
        for row in result["wrong"] + result["missed"]:
            row["case"] = f"{root.name}/{row['file']}"
    return result


def dump(root: Path) -> int:
    """Each use rust-analyzer binds to a declaration of the project, one JSON
    line in `oracle_go.py`'s shape (`file`, `line`, `expr`, `to_file`,
    `to_line`): what `verify_cases.py --binding` reads."""
    for u in bindings(root):
        for to_file, to_line in u["decls"]:
            print(json.dumps({"file": u["file"], "line": u["line"], "expr": u["expr"],
                              "to_file": to_file, "to_line": to_line}))
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--dump"] and len(sys.argv) == 3:
        return dump(Path(sys.argv[2]).resolve())
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+", help="Cargo workspaces")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    args = ap.parse_args()
    results = [r for r in (judge(Path(repo).resolve()) for repo in args.repos) if r]
    oracle.report(results, len(args.repos), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
