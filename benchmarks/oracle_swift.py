# ruff: noqa: E501
"""The native engine's Swift edges against the Swift compiler's own answers.

The Swift compiler's index tests (`test/Index/*.swift`) state, under a line
of a program, each name on it with the symbol the compiler indexed it as (its
USR) and whether the line declares or uses it:

    let x = 2
    // CHECK: [[@LINE-1]]:5 | variable(internal)/Swift | x | s:14swift_ide_test1xSivp | Def | rel: 0
    var y = x + 1
    // CHECK: [[@LINE-2]]:9 | variable/Swift | x | s:14swift_ide_test1xSivp | Ref,Read,RelCont | rel: 1

This script builds the native graph of each test file and compares, as
`oracle_typescript.py` does: an edge whose site the compiler has an answer
for, does it go to the declaration the compiler names; and a use the compiler
binds to a definition the graph has, is there an edge. The tests assert what
their authors chose to: a line with no `CHECK` says nothing.

    git clone --depth 1 --filter=blob:none --sparse https://github.com/swiftlang/swift
    git -C swift sparse-checkout set test/Index
    python benchmarks/oracle_swift.py swift/test/Index --samples 20
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_typescript as oracle  # noqa: E402

CHECK = re.compile(r"//\s*[A-Z][\w-]*:\s*\[\[@LINE(?:([+-])(\d+))?\]\]:(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*(\S+)\s*\|\s*([\w,]+)\s*\|")


def parse(rel: str, text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """The program, and every symbol occurrence the test states: the line it
    is on, its name, its USR, whether it declares."""
    lines = text.splitlines()
    notes: list[dict[str, Any]] = []
    for n, raw in enumerate(lines, 1):
        m = CHECK.search(raw)
        if not m:
            continue
        sign, off, _col, kind, name, usr, roles = m.groups()
        line = n + (int(off) if sign == "+" else -int(off) if sign else 0)
        role = set(roles.split(","))
        # (an accessor the compiler adds, a call it writes itself: nothing the source names)
        if "Impl" in role or "acc-" in kind or name.startswith(("getter:", "setter:")):
            continue
        bare = re.split(r"[(<]", name, maxsplit=1)[0]
        # FileCheck: `[[X_USR:.*]]` names the symbol where it is first seen and
        # `[[X_USR]]` after; `{{.*}}` says nothing of which symbol it is
        var = re.fullmatch(r"\[\[(\w+)(?::.*)?\]\]", usr)
        if var:
            usr = "$" + var.group(1)
        elif "{{" in usr or "[[" in usr:
            continue
        notes.append({"file": rel, "line": line, "expr": bare, "symbol": usr, "kind": kind,
                      "declares": bool(role & {"Def", "Decl"}), "ref": "Ref" in role})
    return lines, notes


def judge(path: Path) -> dict[str, Any] | None:
    rel = path.name
    lines, notes = parse(rel, path.read_text(encoding="utf-8", errors="replace"))
    declared: dict[str, list[tuple[str, int]]] = {}
    for u in notes:
        if u["declares"]:
            declared.setdefault(u["symbol"], []).append((u["file"], u["line"]))
    uses = []
    for u in notes:
        if not u["ref"] or not u["expr"]:
            continue
        decls = declared.get(u["symbol"], [])
        # a symbol of the test's own module whose declaration the test does
        # not state: no answer to compare with
        own = u["symbol"].startswith("$") or "swift_ide_test" in u["symbol"]
        if not decls and own:
            continue
        uses.append({**u, "decls": decls, "lib": not decls, "alias": False,
                     "declares": (u["file"], u["line"]) in decls})
    if not uses:
        return None
    result = oracle.compare(path.stem, {rel: lines}, uses, slack=(-3, 3))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("tests", help="the directory of the compiler's index tests (test/Index)")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    paths = sorted(Path(args.tests).glob("*.swift"))

    def one(p: Path) -> dict[str, Any] | None:
        try:
            return judge(p)
        except Exception as exc:  # a test the script cannot read is skipped, and counted
            return {"case": p.stem, "error": repr(exc)[:200]}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = [r for r in pool.map(one, paths) if r is not None]
    oracle.report(results, len(paths), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
