# ruff: noqa: E501
"""The native engine's Scala edges against the Scala compiler's own answers.

The Scala 3 compiler's test suite keeps, for each program under
`tests/semanticdb/expect`, the source with every name followed by the symbol
the compiler bound it to (SemanticDB): `<-` where the name is declared, `->`
where it is used:

    class Access/*<-example::Access#*/ {
      private[Access/*->example::Access#*/] def m3/*<-example::Access#m3().*/ = ???/*->scala::Predef.`???`().*/

This script takes the annotations out, builds the native graph of the whole
directory as one project (the programs use each other) and compares, as
`oracle_typescript.py` does: an edge whose site the compiler has an answer
for, does it go to the declaration the compiler names; and a use the compiler
binds to a definition the graph has, is there an edge.

    git clone --depth 1 --filter=blob:none --sparse https://github.com/scala/scala3
    git -C scala3 sparse-checkout set tests/semanticdb/expect
    python benchmarks/oracle_scala.py scala3/tests/semanticdb/expect --samples 20
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_typescript as oracle  # noqa: E402

NOTE = re.compile(r"/\*(<-|->)(.*?)\*/")
TOKEN = re.compile(r"([A-Za-z_$][\w$]*|`[^`]+`|[^\s\w()\[\]{},.;`]+)$")


def parse(rel: str, text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """The program without its annotations, and each annotation: the name
    before it, the line, `<-` or `->`, the symbol."""
    lines: list[str] = []
    notes: list[dict[str, Any]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        clean = ""
        last = 0
        for m in NOTE.finditer(raw):
            clean += raw[last:m.start()]
            last = m.end()
            tok = TOKEN.search(clean)
            notes.append({"file": rel, "line": n, "expr": tok.group(1).strip("`") if tok else "",
                          "declares": m.group(1) == "<-", "symbol": m.group(2)})
        lines.append(clean + raw[last:])
    return lines, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("expect", help="the directory of the compiler's *.expect.scala files")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    args = ap.parse_args()
    files: dict[str, list[str]] = {}
    notes: list[dict[str, Any]] = []
    paths = sorted(Path(args.expect).rglob("*.expect.scala"))
    for p in paths:
        rel = str(p.relative_to(args.expect)).replace(".expect.scala", ".scala")
        files[rel], found = parse(rel, p.read_text(encoding="utf-8", errors="replace"))
        notes.extend(found)
    # (a `local3` is numbered within its file)
    def key(u: dict[str, Any]) -> str:
        return f"{u['file']}:{u['symbol']}" if u["symbol"].startswith("local") else u["symbol"]

    declared: dict[str, list[tuple[str, int]]] = {}
    for u in notes:
        if u["declares"]:
            declared.setdefault(key(u), []).append((u["file"], u["line"]))
    uses = []
    for u in notes:
        if u["declares"] or not u["expr"]:
            continue
        local = u["symbol"].startswith("local")
        decls = declared.get(key(u), [])
        # the companion a case class is given, and its `apply`: the class
        # is what the source declares
        if not decls and not local:
            sym = re.sub(r"\.(apply|unapply|copy)\(\+?\d*\)\.$", ".", u["symbol"])
            if sym.endswith("."):
                decls = declared.get(sym[:-1] + "#", [])
        uses.append({**u, "decls": decls, "lib": not decls and not local, "alias": False,
                     "declares": (u["file"], u["line"]) in decls})
    result = oracle.compare("scala3-semanticdb", files, uses, slack=(-3, 3))
    if result is None:
        print("the engine built no graph", file=sys.stderr)
        return 1
    for row in result["wrong"] + result["missed"]:
        row["case"] = row["file"]
    oracle.report([result], len(paths), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
