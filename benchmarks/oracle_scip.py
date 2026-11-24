# ruff: noqa: E501
"""The native engine's edges against a SCIP index of the same repository.

A SCIP index (``scip-python``, ``scip-typescript``, ``scip-java``,
``rust-analyzer scip``, ``scip-go``, ``scip-dotnet``, …) records, for every
occurrence of a name, the symbol a compiler-grade indexer binds it to and
where that symbol is defined. This script reads one with graphlore's own
reader (``src/graphlore/scip.py``), builds the native graph of the repository
and compares as ``oracle_typescript.py`` does: an edge whose site the index has
an answer for, does it go to the definition the index names; and a use the
index binds to a definition the graph has, is there an edge.

It is the whole-repository oracle for the languages with no compiler oracle
of their own here (Python, JavaScript / TypeScript), and a second opinion for
the others.

    npx @sourcegraph/scip-python index REPO --project-name x --output REPO/index.scip
    python benchmarks/oracle_scip.py REPO                 # reads REPO/index.scip
    python benchmarks/oracle_scip.py REPO --index other.scip --samples 20 --json out.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import oracle_typescript as oracle  # noqa: E402

from graphlore import scip  # noqa: E402

# what the graph is built from besides the documents the index has
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "tsconfig.json",
             "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts")


def uses_of(index: scip.Index) -> list[dict[str, Any]]:
    """The index's occurrences in the shape `oracle.compare` reads."""
    uses = []
    for o in index.refs:
        if o.roles & scip.IMPORT:
            continue
        name = scip.bare_name(o.symbol)
        if not name:
            continue
        decl = index.defs.get(o.symbol)
        decls = [decl] if decl else []
        uses.append({"file": o.file, "line": o.line, "expr": name, "symbol": o.symbol,
                     "decls": decls, "lib": not decls, "alias": False,
                     "declares": bool(o.roles & scip.DEFINITION)})
    return uses


def judge(root: Path, index_path: Path) -> dict[str, Any] | None:
    index = scip.Index()
    scip.read_index(index_path.read_bytes(), root, index, index_path.name)
    if index.errors:
        print("\n".join(index.errors), file=sys.stderr)
    documents = {o.file for o in index.refs} | {f for f, _ in index.defs.values()}
    files: dict[str, list[str]] = {}
    for rel in sorted(documents):
        p = root / rel
        if p.is_file():
            files[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.name in MANIFESTS and p.is_file() and "/." not in "/" + rel and "node_modules" not in rel:
            files.setdefault(rel, p.read_text(encoding="utf-8", errors="replace").splitlines())
    print(f"{root.name}: {index.documents} documents ({', '.join(index.tools) or 'unknown indexer'}), "
          f"{len(index.refs)} uses", file=sys.stderr)
    result = oracle.compare(root.name, files, uses_of(index), slack=(-3, 3))
    if result:
        for row in result["wrong"] + result["missed"]:
            row["case"] = f"{root.name}/{row['file']}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+", help="repositories, each with its SCIP index")
    ap.add_argument("--index", help="the index file (default: REPO/index.scip); one repository only")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    args = ap.parse_args()
    if args.index and len(args.repos) > 1:
        ap.error("--index names the index of one repository")
    results = []
    for repo in args.repos:
        root = Path(repo).resolve()
        path = Path(args.index).resolve() if args.index else root / scip.DEFAULT_NAME
        if not path.is_file():
            print(f"{root.name}: no index at {path}", file=sys.stderr)
            continue
        r = judge(root, path)
        if r:
            results.append(r)
    oracle.report(results, len(args.repos), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
