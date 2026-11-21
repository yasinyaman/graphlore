# ruff: noqa: E501
"""graphify's edges, checked by the language's own compiler before they count.

`compare_engines.py` reports the share of graphify's edges the native graph
has too, as if every edge of graphify's were right. It is not a judge: most of
its calls are guesses by name (`INFERRED`). Where a compiler can be asked
about a whole repository (Go: `go/types`, Rust: rust-analyzer, C#: Roslyn; the
same bindings `oracle_go.py`, `oracle_rust.py` and `oracle_csharp.py` read),
this script asks it about each of graphify's call, base and reference edges:

  confirmed   the compiler binds the target's name, on a line the edge is
              seen at, to the declaration the edge points to
  refuted     it binds that name there to something else (another
              declaration, or a library's)
  dispatch    it binds that name to a trait's method and the edge points to
              an implementation of that method: the call can run it, the
              compiler does not say which
  unanswered  it has no binding of that name on those lines

An edge to a constructor is confirmed by the class, which is what the
compiler binds the name written in `new Foo()` to.

and then repeats the comparison for each: the share of the confirmed edges
the native graph has is the number that says what the native graph lacks; a
refuted edge it does not have is no gap. A confirmed edge to an enum's member
that the native graph writes as an edge to the enum (its rule: a member named
is a reference to its enum) is counted apart, `as the enum`.

    python benchmarks/verify_graphify.py /tmp/polyglot/cobra /tmp/polyglot/ripgrep /tmp/polyglot/restsharp
    python benchmarks/verify_graphify.py REPO... --native-dir DIR   # DIR/<repo>.json, built before
    python benchmarks/verify_graphify.py REPO... --markdown         # the table of docs/native-engine.md

A repository is read in the language its manifest names (`go.mod`,
`Cargo.toml`, a `.csproj` / `.sln`), and needs graphify's graph in
`REPO/graphify-out/graph.json` (or the `graphify` CLI).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_engines as ce  # noqa: E402
import oracle_csharp  # noqa: E402
import oracle_go  # noqa: E402
import oracle_rust  # noqa: E402
from oracle_typescript import last_name  # noqa: E402

GROUPS = ("calls", "bases", "references")
VERDICTS = ("confirmed", "refuted", "dispatch", "unanswered")
SLACK = 4  # lines between a definition's first line and the line its name is on


def languages(root: Path) -> list[str]:
    out = []
    if (root / "go.mod").exists():
        out.append("go")
    if (root / "Cargo.toml").exists():
        out.append("rust")
    if any(root.glob("*.sln")) or any(root.rglob("*.csproj")):
        out.append("csharp")
    return out


def line_of(loc: Any) -> int:
    digits = "".join(ch for ch in str(loc or "").lstrip("L").split("-")[0] if ch.isdigit())
    return int(digits) if digits else 0


def implementations(ours: dict[str, Any]) -> Any:
    """From the native graph: is the definition at `impl` a method of a type
    that implements the trait whose method is declared at `decl`?"""
    nodes, edges = ce._nodes_edges(ours)
    at: dict[tuple[str, int], str] = {}
    kind: dict[str, str] = {}
    for n in nodes:
        if n.get("qualname") and ce._node_file(n):
            at[(ce._node_file(n), line_of(n.get("source_location")))] = ce._node_id(n)
            kind[ce._node_id(n)] = str(n.get("kind") or "")
    parent = {child: up for child, (up, _) in ce.parents(ours).items()}
    implements: dict[str, set[str]] = collections.defaultdict(set)
    for e in edges:
        if ce._group(ce._edge_rel(e).strip().lower()) == "bases":
            a, b = ce._edge_ends(e)
            implements[a].add(b)

    def near(place: tuple[str, int]) -> str | None:
        return next((at[(place[0], place[1] + k)] for k in range(-SLACK, SLACK + 1)
                     if (place[0], place[1] + k) in at), None)

    def check(impl: tuple[str, int], decl: tuple[str, int]) -> bool:
        a, b = near(impl), near(decl)
        if a is None or b is None:
            return False
        trait = parent.get(b)
        return trait is not None and kind.get(trait) == "interface" and trait in implements.get(parent.get(a, ""), set())
    return check


def verdicts(graph: dict[str, Any], uses: list[dict[str, Any]], lang: str, implements: Any
             ) -> tuple[dict[tuple[str, str, str], str], list[dict[str, Any]]]:
    """Each of graphify's edges in `lang`, by (source, target, group), with
    what the compiler says of it; and the rows, for samples."""
    defs = ce.definitions(graph)
    up = ce.parents(graph)
    by_site: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for u in uses:
        if not u["alias"]:
            by_site[(u["file"], u["line"])].append(u)
    out: dict[tuple[str, str, str], str] = {}
    rows = []
    rank = {"confirmed": 3, "dispatch": 2, "refuted": 1, "unanswered": 0}
    _, edges = ce._nodes_edges(graph)
    for e in edges:
        g = ce._group(ce._edge_rel(e).strip().lower())
        if g not in GROUPS:
            continue
        s, t = ce._edge_ends(e)
        target = defs.get(t)
        if target is None or s not in defs:
            continue
        sites = [(str(e.get("source_file") or ""), line_of(e.get("source_location")))]
        sites += [(str(a.get("source_file") or ""), line_of(a.get("source_location")))
                  for a in e.get("also_seen") or [] if isinstance(a, dict)]
        if ce.language(sites[0][0]) != lang:
            continue
        named = [u for site in sites for u in by_site.get(site, [])
                 if last_name(u["expr"]) == target["name"]]
        # (a constructor stands for its class: the name written in `new Foo()`
        # is the class's to the compiler)
        places = [(target["file"], target["line"])]
        owner = defs.get(up.get(t, ("", ""))[0])
        if owner is not None and owner["name"] == target["name"]:
            places.append((owner["file"], owner["line"]))
        decls = [d for u in named for d in u["decls"]]
        if not named:
            verdict = "unanswered"
        elif any(f == pf and abs(ln - pl) <= SLACK for f, ln in decls for pf, pl in places):
            verdict = "confirmed"
        elif any(implements(places[0], (f, ln)) for f, ln in decls):
            verdict = "dispatch"
        else:
            verdict = "refuted"
        key = (s, t, g)
        if key not in out or rank[verdict] > rank[out[key]]:
            out[key] = verdict
        row = {"group": g, "verdict": verdict, "confidence": str(e.get("confidence", "")).upper(),
               "file": sites[0][0], "line": sites[0][1],
               "from": defs[s]["label"], "to": f"{target['label']} ({target['file']}:{target['line']})"}
        if verdict == "refuted":
            u = named[0]
            row["compiler"] = "a library's" if u["lib"] else f"{u['decls'][0][0]}:{u['decls'][0][1]}"
        rows.append(row)
    return out, rows


def restricted(graph: dict[str, Any], keep: Any) -> dict[str, Any]:
    """`graph` with only the edges `keep` accepts (the nodes are shared)."""
    key = "links" if "links" in graph else "edges"
    return {**graph, key: [e for e in graph[key] if keep(e)]}


def judge(root: Path, lang: str, uses: list[dict[str, Any]], native: Path | None,
          tmp: Path) -> dict[str, Any]:
    theirs = ce.load(ce.graphify_graph(root, None, tmp))
    ours = ce.load(ce.native_graph(root, str(native) if native else None, tmp))
    said, rows = verdicts(theirs, uses, lang, implementations(ours))
    our_nodes, our_edges = ce._nodes_edges(ours)
    by_id = {ce._node_id(n): n for n in our_nodes}
    label = {i: ce._node_label(n) for i, n in by_id.items()}
    where = {i: f"{ce._node_file(n)}:{line_of(n.get('source_location'))}" for i, n in by_id.items()}
    up = {child: parent for child, (parent, _) in ce.parents(ours).items()}
    linked = {ce._edge_ends(e) for e in our_edges if ce._group(ce._edge_rel(e).strip().lower()) in GROUPS}

    def to_enum(pair: tuple[str, str]) -> bool:
        """The native graph has the edge to the enum the member is a case of,
        from the definition or from one written inside it."""
        a, b = pair
        if str(by_id.get(b, {}).get("kind") or "") != "enum_member" or b not in up:
            return False
        for s, t in linked:
            if t != up[b]:
                continue
            for _ in range(4):
                if s == a:
                    return True
                if s not in up:
                    break
                s = up[s]
        return False

    exact, loose, _ = ce.match(ce.definitions(theirs), ce.definitions(ours))
    paired = {**exact, **loose}
    name = {"go": "go", "rust": "rust", "csharp": "csharp"}[lang]

    def of(verdict: str | None) -> dict[str, dict[str, int]]:
        def keep(e: dict[str, Any]) -> bool:
            g = ce._group(ce._edge_rel(e).strip().lower())
            if g not in GROUPS:
                return True
            s, t = ce._edge_ends(e)
            got = said.get((s, t, g))
            return got is not None and (verdict is None or got == verdict)
        cmp = ce.compare_edges(restricted(theirs, keep), ours, paired, pairs=True)
        out = {}
        for g in GROUPS:
            row = cmp.get(g, {}).get(name, {})
            only = [tuple(p) for p in row.get("only_graphify_pairs", [])]
            as_enum = [p for p in only if to_enum(p)]
            out[g] = {"graphify": row.get("graphify", 0), "both": row.get("both", 0),
                      "as_enum": len(as_enum),
                      "only_graphify": [f"{label[a]} -> {label[b]} ({where[b]})"
                                        for a, b in only if (a, b) not in set(as_enum)]}
        return out

    counts: dict[str, dict[str, dict[str, int]]] = {g: {} for g in GROUPS}
    for r in rows:
        c = counts[r["group"]].setdefault(r["confidence"] or "?", dict.fromkeys(VERDICTS, 0))
        c[r["verdict"]] += 1
    return {"repo": root.name, "language": lang, "by_confidence": counts,
            "all": of(None), **{v: of(v) for v in VERDICTS},
            "refuted_rows": [r for r in rows if r["verdict"] == "refuted"]}


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def total(results: list[dict[str, Any]], lang: str, which: str, g: str) -> tuple[int, int]:
    rows = [r[which][g] for r in results if r["language"] == lang]
    return sum(r["both"] for r in rows), sum(r["graphify"] for r in rows)


def render(results: list[dict[str, Any]], markdown: bool, samples: int) -> str:
    out = []
    langs = [x for x in ("go", "rust", "csharp") if any(r["language"] == x for r in results)]
    titles = {"go": "Go", "rust": "Rust", "csharp": "C#"}
    if markdown:
        out.append("| Language | edges | graphify's, compared | confirmed | refuted | to an implementation | no answer | native has, of all | of the confirmed | with an enum for its member | of the refuted |")
        out.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for lang in langs:
        repos = [r["repo"] for r in results if r["language"] == lang]
        if not markdown:
            out.append(f"== {titles[lang]} ({', '.join(repos)})")
        for g in GROUPS:
            both, n = total(results, lang, "all", g)
            cb, cn = total(results, lang, "confirmed", g)
            ce_ = sum(r["confirmed"][g]["as_enum"] for r in results if r["language"] == lang)
            rb, rn = total(results, lang, "refuted", g)
            ub, un = total(results, lang, "unanswered", g)
            db, dn = total(results, lang, "dispatch", g)
            if not n:
                continue
            if markdown:
                out.append(f"| {titles[lang]} | {g} | {n:,} | {cn:,} | {rn:,} | {dn:,} | {un:,} | "
                           f"{both:,} ({pct(both, n)}) | {cb:,} ({pct(cb, cn)}) | {cb + ce_:,} ({pct(cb + ce_, cn)}) | {rb:,} ({pct(rb, rn)}) |")
                continue
            out.append(f"  {g}: graphify {n:,} edges between definitions both graphs have; native has {both:,} ({pct(both, n)})")
            out.append(f"     confirmed  {cn:>6,}  native has {cb:>6,} ({pct(cb, cn)})"
                       + (f", {ce_:,} more as the enum ({pct(cb + ce_, cn)})" if ce_ else ""))
            out.append(f"     refuted    {rn:>6,}  native has {rb:>6,} ({pct(rb, rn)})")
            out.append(f"     dispatch   {dn:>6,}  native has {db:>6,} ({pct(db, dn)})")
            out.append(f"     unanswered {un:>6,}  native has {ub:>6,} ({pct(ub, un)})")
            out.append(f"     of what the compiler answers, graphify is right in {pct(cn, cn + rn)}")
            conf: dict[str, dict[str, int]] = {}
            for r in results:
                if r["language"] == lang:
                    for c, v in r["by_confidence"][g].items():
                        acc = conf.setdefault(c, dict.fromkeys(VERDICTS, 0))
                        for k in VERDICTS:
                            acc[k] += v[k]
            for c, v in sorted(conf.items()):
                out.append(f"       {c:<10} confirmed {v['confirmed']:,}, refuted {v['refuted']:,} "
                           f"({pct(v['confirmed'], v['confirmed'] + v['refuted'])} right), dispatch {v['dispatch']:,}, "
                           f"unanswered {v['unanswered']:,}")
        if not markdown and samples:
            lacking = [f"{r['repo']}: {x} [{g}]" for r in results if r["language"] == lang
                       for g in GROUPS for x in r["confirmed"][g]["only_graphify"]]
            for x in lacking[:samples]:
                out.append(f"     confirmed, not in the native graph: {x}")
            shown = [row for r in results if r["language"] == lang for row in r["refuted_rows"]][:samples]
            for row in shown:
                out.append(f"     refuted: {row['file']}:{row['line']} {row['from']} -> {row['to']} "
                           f"[{row['group']}, {row['confidence']}]; the compiler: {row['compiler']}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+", help="Go, Rust or C# repositories")
    ap.add_argument("--native-dir", help="native graphs built before, as DIR/<repo name>.json")
    ap.add_argument("--samples", type=int, default=4,
                    help="per language: confirmed edges the native graph lacks, and refuted edges, shown")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", help="write every repository's counts and refuted edges here")
    args = ap.parse_args()
    results = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        go_tool = cs_tool = None
        for repo in args.repos:
            root = Path(repo).resolve()
            native = Path(args.native_dir) / f"{root.name}.json" if args.native_dir else None
            for lang in languages(root):
                if lang == "go":
                    if go_tool is None:
                        go_tool = tmp / "go"
                        go_tool.mkdir()
                        failed = oracle_go.setup(go_tool)
                        if failed:
                            print(failed, file=sys.stderr)
                            return 1
                    uses = oracle_go.uses_of(root, go_tool)
                elif lang == "rust":
                    uses = oracle_rust.bindings(root)
                else:
                    cs_tool = cs_tool or oracle_csharp.setup(tmp)
                    uses = oracle_csharp.uses_of(root, cs_tool)
                results.append(judge(root, lang, uses, native, tmp))
    if not results:
        print("no Go, Rust or C# repository among them", file=sys.stderr)
        return 1
    print(render(results, args.markdown, args.samples))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
