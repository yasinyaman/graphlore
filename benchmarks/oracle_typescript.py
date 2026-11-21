# ruff: noqa: E501
"""The native engine's TypeScript edges against the TypeScript compiler's own answers.

The compiler's test suite keeps, for each of its test programs, a `.symbols`
baseline: the source, and under every line the declaration each name on it
binds to, as the type checker resolved it:

    return this.ships.every(function (val) { return val.isSunk; });
    >this.ships : Symbol(Board.ships, Decl(2dArrays.ts, 7, 13))
    >val.isSunk : Symbol(Ship.isSunk, Decl(2dArrays.ts, 3, 12))

That is an oracle nobody here wrote. This script rebuilds each program from
the test's own source (the baseline says which lines carry which names),
builds the native graph of it, and compares:

    precision  an edge of the graph whose site is a line where the compiler
               binds that name: does it go to the declaration the compiler
               names? (an edge the baseline has no entry for is not judged)
    recall     a use the compiler binds to a declaration the graph has a node
               for: is there an edge to that node from the definition around
               the use?

    git clone --depth 1 --filter=blob:none --sparse https://github.com/microsoft/TypeScript ts
    git -C ts sparse-checkout set --no-cone '/tsc/testdata/baselines/reference/compiler/*.symbols' \
        '/tsc/testdata/tests/cases/compiler/*'
    python benchmarks/oracle_typescript.py ts/tsc/testdata/baselines/reference/compiler
    python benchmarks/oracle_typescript.py DIR --limit 500 --samples 10 --json out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SECTION = re.compile(r"^=== (.+?) ===$")
ENTRY = re.compile(r"^>(.*?) : Symbol\((.*)\)$")
DECL = re.compile(r"Decl\(([^,()]+), (\d+|--), (\d+|--)\)")
RELATIONS = {"calls", "indirect_call", "references", "uses", "inherits", "implements",
             "reads", "writes"}
SOURCE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")


OPTION = re.compile(r"^\s*//\s*@(\w+)\s*:\s*(.*?)\s*$")


def units(case: str, default: str) -> dict[str, list[str]]:
    """The files of a test case as the compiler's harness cuts them: `// @filename:`
    starts one, and the other `// @option:` lines belong to none."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for raw in case.splitlines():
        m = OPTION.match(raw)
        if m:
            if m.group(1).lower() == "filename":
                cur = m.group(2)
                out[cur] = []
            continue
        if cur is None:
            cur = default
            out[cur] = []
        out[cur].append(raw)
    return out


def parse(text: str, source: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[dict[str, Any]]] | None:
    """The files of a baseline, and every binding: file, line (1-based), the
    expression, the symbol, its declarations as (file, line 1-based). The
    baseline repeats the program with a blank line of its own after some
    entries, which nothing tells from a blank line of the program: the lines
    are counted on the test's own source, walked in step."""
    files: dict[str, list[str]] = {}
    uses: list[dict[str, Any]] = []
    cur: str | None = None
    at_line = 0
    after_entries = False
    for raw in text.splitlines():
        m = SECTION.match(raw)
        if m:
            cur = m.group(1)
            base = cur.rsplit("/", 1)[-1]
            cur = cur if cur in source else next((k for k in source if k.rsplit("/", 1)[-1] == base), cur)
            if cur not in source:
                return None
            files[cur] = source[cur]
            at_line = 0
            after_entries = False
            continue
        if cur is None:
            continue
        m = ENTRY.match(raw)
        if m and at_line:
            at = [(f, int(ln), int(col)) for f, ln, col in DECL.findall(m.group(2)) if ln != "--"]
            lib = "lib." in m.group(2) and not at
            uses.append({"file": cur, "line": at_line, "expr": m.group(1),
                         "symbol": m.group(2).split(",", 1)[0], "at": at, "lib": lib})
            after_entries = True
            continue
        lines = files[cur]
        if at_line < len(lines) and lines[at_line] == raw:
            at_line += 1
        elif not (after_entries and raw == ""):
            return None  # the baseline and the source do not walk in step
        after_entries = False
    known = {f.rsplit("/", 1)[-1]: f for f in files}
    for u in uses:
        u["at"] = [(known.get(f.rsplit("/", 1)[-1], f), ln, col) for f, ln, col in u["at"]]
    # `Decl(file, line, col)` is where the declaration's node starts, the blank
    # lines and comments before it included: the declaration is written on
    # the first line of code from there on (and its name, a line or two below
    # for one under a decorator or a doc comment)
    def code_line(file: str, line0: int, col: int) -> int:
        lines = files.get(file, [])
        in_block = False
        for i in range(line0, len(lines)):
            text = lines[i][col:] if i == line0 else lines[i]
            t = text.strip()
            if in_block:
                if "*/" in t:
                    in_block = False
                    t = t.split("*/", 1)[1].strip()
                else:
                    continue
            if t.startswith("/*") and "*/" not in t:
                in_block = True
                continue
            if t and not t.startswith("//") and not (t.startswith("/*") and t.endswith("*/")):
                return i + 1
        return line0 + 1

    for u in uses:
        u["decls"] = [(d[0], code_line(*d)) for d in u["at"]]
    for u in uses:
        # one of the symbol's own declarations (an overload's signature, a
        # namespace merged into a class, the setter beside a getter): no use
        u["declares"] = (u["file"], u["line"]) in u["decls"]
        # the compiler names the import that binds the name, not what it
        # imports: such a row says nothing of where the edge should go
        u["alias"] = any(
            re.match(r"\s*(import|export)\b", files.get(f, [""] * ln)[ln - 1])
            and ("from" in files[f][ln - 1] or "require(" in files[f][ln - 1]
                 or re.match(r"\s*import\b", files[f][ln - 1]))
            for f, ln in u["decls"] if ln - 1 < len(files.get(f, [])))
    return files, uses


def last_name(expr: str) -> str:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", expr)
    return m.group(1) if m else ""


def build(files: dict[str, list[str]]) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        for rel, lines in files.items():
            if rel.startswith("/") or ".." in rel:
                return None
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = Path(tmp) / "g.json"
        done = subprocess.run([sys.executable, "-m", "graphlore_engine", "build", str(root),
                               "--out", str(out)], capture_output=True, text=True)
        if done.returncode != 0 or not out.exists():
            return None
        return json.loads(out.read_text(encoding="utf-8"))


def line_of(loc: Any) -> int:
    m = re.match(r"L?(\d+)", str(loc or ""))
    return int(m.group(1)) if m else 0


def judge(name: str, text: str, case: str) -> dict[str, Any] | None:
    stem = name.split("(", 1)[0]
    parsed = parse(text, units(case, stem + ".ts"))
    if parsed is None:
        return None
    files, uses = parsed
    files = {f: ls for f, ls in files.items() if f.endswith(SOURCE_EXT)}
    if not files or any(f.endswith(".d.ts") for f in files):
        return None
    return compare(name, files, uses)


def compare(name: str, files: dict[str, list[str]], uses: list[dict[str, Any]],
            slack: tuple[int, int] = (0, 3)) -> dict[str, Any] | None:
    """The native graph of `files` against the compiler's bindings: each use
    has `file`, `line`, `expr`, `symbol`, `decls` (file, line), and the flags
    `lib`, `declares`, `alias`."""
    graph = build(files)
    if graph is None:
        return None
    nodes = {n["id"]: n for n in graph["nodes"]}
    code = {i: n for i, n in nodes.items() if n.get("qualname") and n.get("source_file")}

    def bare(n: dict[str, Any]) -> str:
        return str(n["qualname"]).rsplit(".", 1)[-1]

    at: dict[tuple[str, int, str], str] = {}
    for i, n in code.items():
        at[(n["source_file"], line_of(n.get("source_location")), bare(n))] = i
    by_site: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for u in uses:
        by_site[(u["file"], u["line"])].append(u)

    # the definition a line is in: the innermost span around it
    spans = sorted(((n["source_file"], int(n.get("region_start") or line_of(n.get("source_location"))),
                     int(n.get("end_line") or 0), i) for i, n in code.items() if n.get("end_line")),
                   key=lambda s: (s[0], s[2] - s[1]))

    def around(file: str, line: int) -> set[str]:
        return {i for f, a, b, i in spans if f == file and a <= line <= b}

    right, wrong, missed, found = [], [], [], 0
    have: set[tuple[str, str]] = set()
    hubs = {n.get("source_file"): i for i, n in nodes.items()
            if not n.get("qualname") and n.get("source_file")}
    for e in graph["links"]:
        if e.get("relation") not in RELATIONS:
            continue
        t = code.get(e["target"])
        if t is None:
            continue
        have.add((e["source"], e["target"]))
        site = (str(e.get("source_file") or ""), line_of(e.get("source_location")))
        named = [u for u in by_site.get(site, [])
                 if last_name(u["expr"]) == bare(t) and not u["alias"]]
        if not named:
            continue
        target = (t["source_file"], line_of(t.get("source_location")))
        row = {"case": name, "file": site[0], "line": site[1], "relation": e["relation"],
               "confidence": e.get("confidence"), "to": t["qualname"],
               "src": files.get(site[0], [""] * site[1])[site[1] - 1].strip()[:110]}
        # (the graph's line is the definition's first line; a decorator or a
        # modifier line above the name is within three lines of it)
        if any(f == target[0] and slack[0] <= target[1] - ln <= slack[1] for u in named for f, ln in u["decls"]):
            right.append(row)
        else:
            row["compiler"] = (named[0]["symbol"] + (" (library)" if named[0]["lib"] else "")
                               + f" at {named[0]['decls'][:2]}, graph: {target}")
            wrong.append(row)
    for u in uses:
        name_ = last_name(u["expr"])
        if not name_ or u["expr"].strip() in ("this", "super"):
            continue
        if u["declares"] or u["alias"]:
            continue
        for decl in u["decls"]:
            target_id = next((at[(decl[0], decl[1] + k, name_)] for k in range(slack[0], slack[1] + 1)
                              if (decl[0], decl[1] + k, name_) in at), None)
            if target_id is None:
                continue  # nothing of the graph's stands for it (a local, a parameter, a member of a type literal)
            sources = around(u["file"], u["line"]) | ({hubs[u["file"]]} if u["file"] in hubs else set())
            if target_id in sources and len(sources) <= 2:
                continue  # a definition naming itself
            if any((s, target_id) in have for s in sources):
                found += 1
            else:
                missed.append({"case": name, "file": u["file"], "line": u["line"],
                               "expr": u["expr"], "to": code[target_id]["qualname"],
                               "kind": code[target_id].get("kind"),
                               "src": files[u["file"]][u["line"] - 1].strip()[:110]})
            break
    return {"case": name, "right": right, "wrong": wrong, "missed": missed, "found": found}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("baselines", help="a directory of the compiler's .symbols baselines")
    ap.add_argument("--cases", help="the directory of the tests' sources (default: beside the baselines)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N baselines")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    paths = sorted(Path(args.baselines).glob("*.symbols"))
    if args.limit:
        paths = paths[:args.limit]

    cases = Path(args.cases) if args.cases else Path(args.baselines).parents[2] / "tests/cases/compiler"

    def one(p: Path) -> dict[str, Any] | None:
        try:
            stem = p.stem.split("(", 1)[0]
            src = next((cases / (stem + ext) for ext in (".ts", ".tsx") if (cases / (stem + ext)).exists()), None)
            if src is None:
                return None
            return judge(p.stem, p.read_text(encoding="utf-8", errors="replace"),
                         src.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # a baseline the script cannot read is skipped, and counted
            return {"case": p.stem, "error": repr(exc)[:200]}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = [r for r in pool.map(one, paths) if r is not None]
    report(results, len(paths), args.samples, args.json)
    return 0


def report(results: list[dict[str, Any]], total: int, samples: int, out: str | None) -> None:
    errors = [r for r in results if "error" in r]
    results = [r for r in results if "error" not in r]
    right = sum(len(r["right"]) for r in results)
    wrong = [w for r in results for w in r["wrong"]]
    missed = [m for r in results for m in r["missed"]]
    found = sum(r["found"] for r in results)
    print(f"{len(results)} programs of the compiler's test suite ({total} baselines, "
          f"{len(errors)} unreadable)")
    print(f"precision: {right} of {right + len(wrong)} edges the compiler has an answer for go where "
          f"it says ({100 * right / max(1, right + len(wrong)):.1f}%)")
    print(f"recall:    {found} of {found + len(missed)} uses the compiler binds to a definition of "
          f"the graph have an edge ({100 * found / max(1, found + len(missed)):.1f}%)")
    by_rel = collections.Counter((w["relation"], w["confidence"]) for w in wrong)
    print("wrong, by relation:", dict(by_rel.most_common()))
    by_kind = collections.Counter(m["kind"] for m in missed)
    print("missed, by the kind of what was meant:", dict(by_kind.most_common()))
    for label, rows in (("wrong", wrong), ("missed", missed)):
        print(f"\n--- {label} (a sample) ---")
        step = max(1, len(rows) // max(1, samples))
        for row in rows[::step][:samples]:
            extra = f"compiler: {row['compiler']}" if "compiler" in row else f"expr: {row['expr']}"
            print(f"  {row['case']}:{row['line']}  -> {row['to']}  [{row.get('relation', row.get('kind'))}]  {extra}\n      {row['src']}")
    if out:
        Path(out).write_text(json.dumps({"right": right, "found": found, "wrong": wrong,
                                         "missed": missed}, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
