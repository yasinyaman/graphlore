# ruff: noqa: E501
"""The native engine's Kotlin edges against the Kotlin compiler's own answers.

The Kotlin compiler's Analysis API tests keep, for each program under
`analysis/analysis-api/testData/components/resolver/allByPsi`, a
`.symbol.txt`: every element of the source, where it is (character offsets)
and the symbol the compiler resolved it to:

    KtNameReferenceExpression(142,149): 'Another'
      KaSimpleSymbolResolutionSuccess:
        symbols = [
          /Another
        ]

The same file lists the declarations (`KtClass(108,126): 'open class
Another'`), so a symbol's name can be put back on the line that declares it.
This script does that, builds the native graph of each program and compares,
as `oracle_typescript.py` does: an edge whose site the compiler has an answer
for, does it go to the declaration the compiler names; and a use the compiler
binds to a definition the graph has, is there an edge.

    git clone --depth 1 --filter=blob:none --no-checkout https://github.com/JetBrains/kotlin
    git -C kotlin sparse-checkout set --no-cone '/analysis/analysis-api/testData/components/resolver/allByPsi/*'
    git -C kotlin checkout
    python benchmarks/oracle_kotlin.py kotlin/analysis/analysis-api/testData/components/resolver/allByPsi --samples 20
"""
from __future__ import annotations

import argparse
import bisect
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_typescript as oracle  # noqa: E402

ELEMENT = re.compile(r"^(Kt\w+)\((\d+),(\d+)\): '(.*)'$")
CONTAINERS = {"KtClass", "KtObjectDeclaration", "KtEnumEntry"}
DECLARES = {"KtClass": r"\b(?:class|interface)\s+(`[^`]+`|\w+)",
            "KtObjectDeclaration": r"\bobject\s+(`[^`]+`|\w+)",
            "KtNamedFunction": r"\bfun\s+(?:<[^(]*?>\s*)?(?:[^(]*\.)?(`[^`]+`|\w+)\s*\(",
            "KtProperty": r"\bva[lr]\s+(?:<[^:=]*?>\s*)?(?:[^:=(]*\.)?(`[^`]+`|\w+)",
            "KtTypeAlias": r"\btypealias\s+(`[^`]+`|\w+)",
            "KtEnumEntry": r"^\s*(`[^`]+`|\w+)"}


def elements(dump: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in dump.splitlines():
        m = ELEMENT.match(raw)
        if m:
            out.append({"kind": m.group(1), "start": int(m.group(2)), "end": int(m.group(3)),
                        "text": m.group(4), "symbols": [], "ok": False})
        elif out and raw.startswith("  KaSimpleSymbolResolutionSuccess"):
            out[-1]["ok"] = True
        elif out and raw.startswith("      ") and out[-1]["ok"]:
            out[-1]["symbols"].append(raw.strip())
    return out


def qualified(symbol: str, package: str) -> str | None:
    """`pkg/Outer.Inner.name(…)` -> `Outer.Inner.name`; a constructor names
    its class; a local or a parameter names nothing a graph holds."""
    m = re.match(r"<constructor>\(.*\): ([\w.]+)", symbol)
    if m:
        name = m.group(1)
        return name[len(package) + 1:] if package and name.startswith(package + ".") else name
    m = re.match(r"([\w.]*)/([\w.`<>$ -]+?)(?:\(|$|:)", symbol)
    return m.group(2).strip() if m else None


def judge(source: Path, dump: Path) -> dict[str, Any] | None:
    text = source.read_text(encoding="utf-8", errors="replace")
    rendered = dump.read_text(encoding="utf-8", errors="replace")
    if rendered.count("KtFile: ") != 1 or "// FILE:" in text:
        return None  # several files in one: the offsets are each file's own
    els = elements(rendered)
    # the offsets are of the file as written: check on a few elements
    probe = [e for e in els if e["kind"] == "KtNameReferenceExpression"][:5]
    if any(text[e["start"]:e["end"]] != e["text"] for e in probe):
        return None
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)

    def line(offset: int) -> int:
        return bisect.bisect_right(starts, offset)

    m = re.search(r"^\s*package\s+([\w.]+)", text, re.M)
    package = m.group(1) if m else ""
    rel = source.name
    # every declaration: its qualified name, from the containers around it
    declared: dict[str, list[tuple[str, int]]] = {}
    open_: list[tuple[int, str]] = []
    for e in els:
        pattern = DECLARES.get(e["kind"])
        if not pattern:
            continue
        while open_ and open_[-1][0] <= e["start"]:
            open_.pop()
        found = re.search(pattern, e["text"])
        name = found.group(1).strip("`") if found else ("Companion" if "companion" in e["text"] else "")
        if not name:
            continue
        qual = ".".join([n for _, n in open_] + [name])
        # (the line its name is on: annotations and modifiers can sit above)
        at = text.find(name, e["start"], e["end"])
        declared.setdefault(qual, []).append((rel, line(at if at >= 0 else e["start"])))
        if e["kind"] in CONTAINERS:
            open_.append((e["end"], name))
    uses = []
    for e in els:
        if e["kind"] != "KtNameReferenceExpression" or len(e["symbols"]) != 1:
            continue
        symbol = e["symbols"][0]
        qual = qualified(symbol, package)
        if qual is None:
            continue
        library = bool(re.match(r"(kotlin|java|javax)[/.]", symbol))
        decls = [] if library else declared.get(qual, [])
        if not decls and not library:
            continue  # nothing the source declares under that name: no answer
        at = (rel, line(e["start"]))
        uses.append({"file": rel, "line": at[1], "expr": e["text"], "symbol": symbol, "decls": decls,
                     "lib": library, "alias": False, "declares": at in decls})
    if not uses:
        return None
    return oracle.compare(source.stem, {rel: text.splitlines()}, uses, slack=(-3, 3))


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("tests", help="the directory of the compiler's resolver tests (allByPsi)")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    dumps = sorted(Path(args.tests).rglob("*.symbol.txt"))

    def one(d: Path) -> dict[str, Any] | None:
        source = d.with_name(d.name.replace(".symbol.txt", ".kt"))
        if not source.exists():
            return None
        try:
            return judge(source, d)
        except Exception as exc:  # a test the script cannot read is skipped, and counted
            return {"case": d.stem, "error": repr(exc)[:200]}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = [r for r in pool.map(one, dumps) if r is not None]
    oracle.report(results, len(dumps), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
