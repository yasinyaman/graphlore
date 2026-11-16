#!/usr/bin/env python3
"""Compare the native engine's graph of a repository with graphify's.

For each language: the definitions each engine found, how many match (same
file, same definition line and bare name; then the same name within three
lines; then the same name anywhere in the file), what only one of them has, and
whether matched definitions hang off the same parent with the same relation.
It is the check that decides when the native engine can become the default
(docs/benchmark-polyglot.md runs every tool; this compares the graphs).

  python benchmarks/compare_engines.py REPO
  python benchmarks/compare_engines.py REPO --graphify-graph path/to/graph.json
  python benchmarks/compare_engines.py tests/fixtures/polyglot \\
      --graphify-graph tests/fixtures/polyglot/graph.json --fail-under 90

The native graph is built into a temporary directory (the repository is not
written to) unless --native-graph names one. graphify's side is
--graphify-graph, else REPO/graphify-out/graph.json, else `graphify update .`
run in a temporary copy when the CLI is installed. --fail-under exits 1 when
the share of graphify's code definitions the native engine matches exactly is
below the given percentage.
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from graphlore.graph import (  # noqa: E402
    _bare_name,
    _edge_ends,
    _edge_rel,
    _is_external_node,
    _is_file_hub_node,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _nodes_edges,
)

# The report's language names, shared with the polyglot audit.
EXT_LANG = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".mts": "typescript",
    ".cts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".scala": "scala", ".cs": "csharp", ".rs": "rust", ".swift": "swift",
    ".c": "c", ".h": "c/c++ header", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hxx": "cpp", ".rb": "ruby", ".php": "php",
}
CONTAINMENT = {"contains", "method", "case_of", "defines"}
NEAR = 3
SAMPLES = 5


def language(path: str) -> str | None:
    dot = path.rfind(".")
    return EXT_LANG.get(path[dot:].lower()) if dot >= 0 else None


def load(path: Path) -> dict[str, Any]:
    graph = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise SystemExit(f"{path}: not a graph.json object")
    return graph


def definitions(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Code definitions by id: no file hubs, external names or document nodes."""
    nodes, _ = _nodes_edges(graph)
    out = {}
    for n in nodes:
        file = _node_file(n)
        if not file or _is_external_node(n) or _is_file_hub_node(n) or not language(file):
            continue
        if str(n.get("file_type") or "code").lower() != "code":
            continue
        try:
            line = int(_node_line(n))
        except (TypeError, ValueError):
            continue
        out[_node_id(n)] = {"file": file, "line": line, "name": _bare_name(_node_label(n)),
                            "label": _node_label(n)}
    return out


def parents(graph: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """child id -> (parent id, relation) over the containment relations."""
    _, edges = _nodes_edges(graph)
    out = {}
    for e in edges:
        rel = _edge_rel(e).strip().lower()
        if rel in CONTAINMENT:
            src, dst = _edge_ends(e)
            out.setdefault(dst, (src, rel))
    return out


def _plain(label: str) -> str:
    """A label's bare name without type arguments: `Target<'a>` -> `Target`."""
    return _bare_name(label.split("<", 1)[0]) if label else ""


def match(theirs: dict, ours: dict) -> tuple[dict[str, str], dict[str, str], collections.Counter]:
    """Pair graphify's definitions with native ones: exact, then near, then by
    name in the same file. Returns (exact, looser, line offsets of near pairs)."""
    by_key: dict[tuple, list[str]] = collections.defaultdict(list)
    by_name: dict[tuple, list[str]] = collections.defaultdict(list)
    for nid, d in ours.items():
        by_key[d["file"], d["line"], d["name"]].append(nid)
        by_name[d["file"], d["name"]].append(nid)
    used: set[str] = set()
    exact: dict[str, str] = {}
    loose: dict[str, str] = {}
    offsets: collections.Counter = collections.Counter()
    for tid, d in theirs.items():
        cand = [c for c in by_key.get((d["file"], d["line"], d["name"]), []) if c not in used]
        if cand:
            exact[tid] = cand[0]
            used.add(cand[0])
    for tid, d in theirs.items():
        if tid in exact:
            continue
        cand = [c for c in by_name.get((d["file"], d["name"]), []) if c not in used]
        if not cand:
            continue
        best = min(cand, key=lambda c: abs(ours[c]["line"] - d["line"]))
        delta = ours[best]["line"] - d["line"]
        if abs(delta) <= NEAR:
            offsets[delta] += 1
        loose[tid] = best
        used.add(best)
    return exact, loose, offsets


def compare(theirs_graph: dict, ours_graph: dict) -> dict[str, Any]:
    theirs, ours = definitions(theirs_graph), definitions(ours_graph)
    exact, loose, offsets = match(theirs, ours)
    paired = {**exact, **loose}
    their_parent, our_parent = parents(theirs_graph), parents(ours_graph)
    their_labels = {_node_id(n): _node_label(n) for n in _nodes_edges(theirs_graph)[0]}
    our_labels = {_node_id(n): _node_label(n) for n in _nodes_edges(ours_graph)[0]}
    their_defs_by_id = {
        _node_id(n) for n in _nodes_edges(theirs_graph)[0]
        if not _is_file_hub_node(n) and _node_file(n)}
    rev = {o: t for t, o in paired.items()}
    langs: dict[str, dict[str, Any]] = collections.defaultdict(lambda: {
        "graphify": 0, "native": 0, "exact": 0, "near": 0, "same_file": 0,
        "only_graphify": [], "only_native": [],
        "parent_same": 0, "parent_differs": [], "relation_differs": [],
    })
    for tid, d in theirs.items():
        row = langs[language(d["file"]) or "?"]
        row["graphify"] += 1
        if tid in exact:
            row["exact"] += 1
        elif tid in loose:
            near = abs(ours[loose[tid]]["line"] - d["line"]) <= NEAR
            row["near" if near else "same_file"] += 1
        else:
            row["only_graphify"].append(f"{d['file']}:{d['line']} {d['label']}")
        oid = paired.get(tid)
        if oid is None or tid not in their_parent:
            continue
        tp, trel = their_parent[tid]
        op, orel = our_parent.get(oid, (None, None))
        # A parent that is a matched definition must map to ours. Otherwise it is
        # a file hub (ours must be one too) or a node only graphify has, such as
        # the `Target<'a>` it makes of an impl target: then the names decide.
        tp_mapped = paired.get(tp)
        if tp_mapped:
            same = op == tp_mapped
        elif tp in their_defs_by_id:
            same = op is not None and _plain(their_labels.get(tp, "")) == _plain(
                our_labels.get(op, ""))
        else:
            same = op is not None and op not in ours
        if same:
            row["parent_same"] += 1
            if trel != orel:
                row["relation_differs"].append(f"{d['label']}: {trel} vs {orel}")
        else:
            row["parent_differs"].append(f"{d['file']}:{d['line']} {d['label']}")
    for oid, d in ours.items():
        if oid not in rev:
            langs[language(d["file"]) or "?"]["only_native"].append(
                f"{d['file']}:{d['line']} {d['label']}")
    for d in ours.values():
        langs[language(d["file"]) or "?"]["native"] += 1
    total = sum(r["graphify"] for r in langs.values())
    matched = sum(r["exact"] for r in langs.values())
    docs = {
        name: sum(1 for n in _nodes_edges(g)[0] if str(n.get("file_type", "")) == "document")
        for name, g in (("graphify", theirs_graph), ("native", ours_graph))
    }
    return {
        "edges": compare_edges(theirs_graph, ours_graph, paired),
        "languages": dict(sorted(langs.items())),
        "graphify_definitions": total,
        "native_definitions": len(ours),
        "exact": matched,
        "exact_pct": round(100 * matched / total, 1) if total else 100.0,
        "near_offsets": dict(sorted(offsets.items())),
        "document_nodes": docs,
    }


EDGE_GROUPS = {
    "imports": {"imports", "imports_from", "re_exports", "dynamic_import", "requires"},
    "calls": {"calls", "indirect_call"},
    "bases": {"inherits", "extends", "implements", "mixes_in", "embeds"},
    "references": {"references", "uses"},
}


def _group(rel: str) -> str | None:
    return next((g for g, rels in EDGE_GROUPS.items() if rel in rels), None)


def compare_edges(theirs_graph: dict, ours_graph: dict, paired: dict[str, str]) -> dict[str, Any]:
    """Edges per group and language: imports as (file, file) pairs, the rest as
    (definition, definition) pairs over the definitions both engines have."""
    out: dict[str, dict[str, Any]] = {}
    sides = {}
    for name, graph in (("graphify", theirs_graph), ("native", ours_graph)):
        nodes, edges = _nodes_edges(graph)
        byid = {_node_id(n): n for n in nodes}
        sides[name] = (byid, edges)
    common = set(paired.values())
    rev = {o: t for t, o in paired.items()}
    sets: dict[tuple[str, str, str], set] = {}
    inferred = {}
    for name, (byid, edges) in sides.items():
        for e in edges:
            g = _group(_edge_rel(e).strip().lower())
            if g is None:
                continue
            s, t = _edge_ends(e)
            sn, tn = byid.get(s), byid.get(t)
            if sn is None or tn is None:
                continue
            sf, tf = _node_file(sn), _node_file(tn)
            lang = language(sf) if sf else None
            if not lang:
                continue
            if g == "imports":
                if not tf or sf == tf or _is_external_node(tn):
                    continue
                key = (sf, tf)
            else:
                if name == "graphify":
                    s2, t2 = paired.get(s), paired.get(t)
                    if s2 is None or t2 is None:
                        continue
                    key = (s2, t2)
                else:
                    if s not in common or t not in common:
                        continue
                    key = (s, t)
            sets.setdefault((g, lang, name), set()).add(key)
            if name == "native" and str(e.get("confidence", "")).upper() == "INFERRED":
                inferred[(g, lang)] = inferred.get((g, lang), 0) + 1
    labels = {
        name: {i: _node_label(n) for i, n in byid.items()} for name, (byid, _) in sides.items()
    }
    for g, lang in sorted({k[:2] for k in sets}):
        theirs = sets.get((g, lang, "graphify"), set())
        ours = sets.get((g, lang, "native"), set())

        def show(pairs: set, side: str, g: str = g) -> list[str]:
            rows = []
            for a, b in sorted(pairs)[:SAMPLES]:
                if g == "imports":
                    rows.append(f"{a} -> {b}")
                else:
                    la = labels["native"].get(a) or labels["graphify"].get(rev.get(a, a), a)
                    lb = labels["native"].get(b) or labels["graphify"].get(rev.get(b, b), b)
                    rows.append(f"{la} -> {lb}")
            return rows

        out.setdefault(g, {})[lang] = {
            "graphify": len(theirs), "native": len(ours), "both": len(theirs & ours),
            "only_graphify": len(theirs - ours), "only_native": len(ours - theirs),
            "native_inferred": inferred.get((g, lang), 0),
            "samples_only_graphify": show(theirs - ours, "graphify"),
            "samples_only_native": show(ours - theirs, "native"),
        }
    return out


def render(result: dict[str, Any]) -> str:
    lines = [
        f"{'language':14} {'graphify':>8} {'native':>7} {'exact':>6} {'near':>5} "
        f"{'file':>5} {'only-g':>7} {'only-n':>7} {'parent':>7}",
    ]
    for name, r in result["languages"].items():
        same = r["parent_same"]
        differs = len(r["parent_differs"])
        lines.append(
            f"{name:14} {r['graphify']:8} {r['native']:7} {r['exact']:6} {r['near']:5} "
            f"{r['same_file']:5} {len(r['only_graphify']):7} {len(r['only_native']):7} "
            f"{same:>3}/{same + differs:<3}"
        )
    lines.append(
        f"\nexact matches: {result['exact']} of graphify's {result['graphify_definitions']} "
        f"code definitions ({result['exact_pct']}%); native has {result['native_definitions']}."
    )
    if result["near_offsets"]:
        lines.append(f"near matches by line offset (native - graphify): {result['near_offsets']}")
    lines.append(f"document nodes: {result['document_nodes']}")
    for g, per_lang in result.get("edges", {}).items():
        lines.append(f"\n{g} edges ({'file pairs' if g == 'imports' else 'definition pairs'}):")
        lines.append(f"  {'language':14} {'graphify':>8} {'native':>7} {'both':>6} {'only-g':>7} "
                     f"{'only-n':>7} {'inferred':>8}")
        for lang, r in per_lang.items():
            lines.append(f"  {lang:14} {r['graphify']:8} {r['native']:7} {r['both']:6} "
                         f"{r['only_graphify']:7} {r['only_native']:7} {r['native_inferred']:8}")
    for name, r in result["languages"].items():
        for key, what in (("only_graphify", "only in graphify"), ("only_native", "only native"),
                          ("parent_differs", "different parent"),
                          ("relation_differs", "different relation")):
            if r[key]:
                more = f" (+{len(r[key]) - SAMPLES} more)" if len(r[key]) > SAMPLES else ""
                lines.append(f"  {name} {what}: {'; '.join(r[key][:SAMPLES])}{more}")
    return "\n".join(lines)


def graphify_graph(repo: Path, explicit: str | None, tmp: Path) -> Path:
    if explicit:
        return Path(explicit)
    existing = repo / "graphify-out" / "graph.json"
    if existing.exists():
        return existing
    graphify = shutil.which("graphify")
    if graphify is None:
        raise SystemExit(
            "no graphify graph: pass --graphify-graph, build REPO/graphify-out/graph.json, "
            "or install the graphify CLI (pip install graphifyy)")
    copy = tmp / "graphify-copy"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git", "graphify-out"))
    proc = subprocess.run([graphify, "update", "."], cwd=copy, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"graphify update failed:\n{proc.stderr or proc.stdout}")
    return copy / "graphify-out" / "graph.json"


def native_graph(repo: Path, explicit: str | None, tmp: Path) -> Path:
    if explicit:
        return Path(explicit)
    try:
        import graphlore_engine
    except ImportError:
        raise SystemExit("the native engine is not installed: "
                         "maturin develop -m engine/Cargo.toml --release") from None
    out = tmp / "native" / "graph.json"
    graphlore_engine.build(str(repo), out=str(out))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo")
    ap.add_argument("--graphify-graph")
    ap.add_argument("--native-graph")
    ap.add_argument("--json", help="write the full comparison here")
    ap.add_argument("--fail-under", type=float)
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        theirs = load(graphify_graph(repo, args.graphify_graph, tmp))
        ours = load(native_graph(repo, args.native_graph, tmp))
    result = compare(theirs, ours)
    print(render(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.fail_under is not None and result["exact_pct"] < args.fail_under:
        print(f"\nFAIL: {result['exact_pct']}% < {args.fail_under}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
