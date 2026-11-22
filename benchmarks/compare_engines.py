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
  python benchmarks/compare_engines.py REPO --communities

--communities compares the two engines' communities instead: how many, how
big, modularity on the plain graph, how much of each community sits in one
directory, how many test nodes share a community with production code, and
how far the two partitions agree (normalised mutual information).

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
    _is_test_path,
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
C_FAMILY = {"c", "cpp", "c/c++ header"}
TYPE_KINDS = {"class", "struct", "interface", "enum", "type_alias", "module"}
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


def compare_edges(theirs_graph: dict, ours_graph: dict, paired: dict[str, str],
                  pairs: bool = False) -> dict[str, Any]:
    """Edges per group and language: imports as (file, file) pairs, the rest as
    (definition, definition) pairs over the definitions both engines have.
    `pairs` adds every pair only graphify has, as native ids (`only_graphify_pairs`)."""
    out: dict[str, dict[str, Any]] = {}
    sides = {}
    for name, graph in (("graphify", theirs_graph), ("native", ours_graph)):
        nodes, edges = _nodes_edges(graph)
        byid = {_node_id(n): n for n in nodes}
        sides[name] = (byid, edges)
    common = set(paired.values())
    rev = {o: t for t, o in paired.items()}
    # The native graph has definitions graphify folds into the one around them:
    # a function nested in a method (`const abort = () => …`), the setter beside a
    # getter of the same name. An edge from one of those is compared as an edge
    # from the paired definition it belongs to, as graphify records it.
    ours_by, ours_edges = sides["native"]
    parent: dict[str, str] = {}
    for e in ours_edges:
        if _edge_rel(e).strip().lower() in CONTAINMENT:
            a, b = _edge_ends(e)
            parent.setdefault(b, a)

    def name_key(n: dict) -> tuple[str, str]:
        return (_node_file(n), str(n.get("qualname") or _node_label(n)))

    # (in a fixed order: of two definitions with one name in a file the same
    # one stands for both on every run)
    twins = {name_key(ours_by[i]): i for i in sorted(common, reverse=True) if i in ours_by}
    # C, C++: graphify has one node for a member function, the declaration in
    # the class (usually a header), and hangs the calls of the body on it; the
    # native graph has the declaration and the definition written out of the
    # class (`void DBImpl::Work() { … }` in the .cc), and the calls leave from
    # the definition. An edge of that definition is compared as an edge of the
    # one paired declaration with its qualified name.
    declared: dict[str, str | None] = {}
    for i in common:
        n = ours_by.get(i)
        if n is not None and language(_node_file(n) or "") in C_FAMILY and n.get("qualname"):
            q = str(n["qualname"])
            declared[q] = None if q in declared else i

    # C#: a partial type is one type written in several files; graphify has
    # one node for it, the native graph one per file. An edge of a part that
    # is not the paired one is compared as an edge of the part that is.
    whole: dict[str, str | None] = {}
    for i in common:
        n = ours_by.get(i)
        if (n is not None and language(_node_file(n) or "") == "csharp" and n.get("qualname")
                and str(n.get("kind") or "") in TYPE_KINDS):
            q = str(n["qualname"])
            whole[q] = None if q in whole else i

    def lifted(i: str, climb: bool) -> str | None:
        for _ in range(8):
            if i in common:
                return i
            n = ours_by.get(i)
            if n is None:
                return None
            twin = twins.get(name_key(n))
            if twin is not None:
                return twin
            if (language(_node_file(n) or "") == "csharp" and n.get("qualname")
                    and str(n.get("kind") or "") in TYPE_KINDS):
                twin = whole.get(str(n["qualname"]))
                if twin is not None:
                    return twin
            if language(_node_file(n) or "") in C_FAMILY and n.get("qualname"):
                twin = declared.get(str(n["qualname"]))
                if twin is not None:
                    return twin
            if not climb or i not in parent:
                return None
            i = parent[i]
        return None
    sets: dict[tuple[str, str, str], set] = {}
    stated: dict[tuple[str, str], set] = {}
    sure: dict[tuple[str, str], set] = {}
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
                    s2, t2 = lifted(s, True), lifted(t, False)
                    if s2 is None or t2 is None:
                        continue
                    key = (s2, t2)
                    # (the edge is counted where the definition it is compared
                    # as is written: the header of a C++ declaration)
                    lang = language(_node_file(ours_by[s2]) or "") or lang
            sets.setdefault((g, lang, name), set()).add(key)
            # what graphify itself calls EXTRACTED, as opposed to its guesses
            if name == "graphify" and str(e.get("confidence", "")).upper() == "EXTRACTED":
                sure.setdefault((g, lang), set()).add(key)
            if name == "native" and str(e.get("confidence", "")).upper() == "INFERRED":
                inferred[(g, lang)] = inferred.get((g, lang), 0) + 1
            # graphify writes a type a method uses on the method's class, and
            # a construction as a `uses` edge. The native graph says the same
            # with an edge from the method, and a `calls` edge to the class:
            # such an edge states graphify's reference without being it.
            if name == "native" and g in ("references", "calls"):
                to_type = str(tn.get("kind") or "") in TYPE_KINDS
                if g == "references" or to_type:
                    up, seen = s, 0
                    while up is not None and seen < 4:
                        at = lifted(up, False)
                        if at is not None and (at != key[0] or g == "calls"):
                            where = language(_node_file(ours_by[at]) or "") or lang
                            stated.setdefault(("references", where), set()).add((at, key[1]))
                        up, seen = parent.get(up), seen + 1
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

        said = stated.get((g, lang), set()) - ours
        out.setdefault(g, {})[lang] = {
            "graphify": len(theirs), "native": len(ours),
            "both": len(theirs & (ours | said)), "stated_otherwise": len(theirs & said),
            "graphify_extracted": len(sure.get((g, lang), set())),
            "both_extracted": len(sure.get((g, lang), set()) & (ours | said)),
            "only_graphify": len(theirs - ours - said), "only_native": len(ours - theirs),
            "native_inferred": inferred.get((g, lang), 0),
            "samples_only_graphify": show(theirs - ours - said, "graphify"),
            "samples_only_native": show(ours - theirs, "native"),
        }
        if pairs:
            out[g][lang]["only_graphify_pairs"] = sorted(theirs - ours - said)
    return out


def _parent_dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def community_stats(graph: dict[str, Any]) -> dict[str, Any]:
    """Size, modularity, directory purity and test mixing of a graph's communities,
    over its internal nodes (externals left out, as both engines attach them
    differently)."""
    nodes, edges = _nodes_edges(graph)
    comm: dict[str, Any] = {}
    files: dict[str, str] = {}
    for n in nodes:
        c = n.get("community", n.get("cluster"))
        if c is None or _is_external_node(n):
            continue
        comm[_node_id(n)] = c
        files[_node_id(n)] = _node_file(n) or ""
    if not comm:
        return {"count": 0}
    members: dict[Any, list[str]] = collections.defaultdict(list)
    for nid, c in comm.items():
        members[c].append(nid)
    sizes = sorted((len(m) for m in members.values()), reverse=True)
    # modularity of the partition on the plain, undirected graph
    deg: collections.Counter = collections.Counter()
    inside: collections.Counter = collections.Counter()
    m = 0
    seen: set[tuple[str, str]] = set()
    for e in edges:
        a, b = _edge_ends(e)
        if a == b or a not in comm or b not in comm or (min(a, b), max(a, b)) in seen:
            continue
        seen.add((min(a, b), max(a, b)))
        m += 1
        deg[a] += 1
        deg[b] += 1
        if comm[a] == comm[b]:
            inside[comm[a]] += 1
    tot: collections.Counter = collections.Counter()
    for nid, d in deg.items():
        tot[comm[nid]] += d
    q = sum(inside[c] / m - (tot[c] / (2 * m)) ** 2 for c in members) if m else 0.0
    # how much of each community sits in its main directory
    placed = dominant = 0
    for ids in members.values():
        dirs = collections.Counter(_parent_dir(files[i]) for i in ids if files[i])
        if len(ids) > 1 and dirs:
            placed += sum(dirs.values())
            dominant += dirs.most_common(1)[0][1]
    # test nodes in a community that is mostly production code
    is_test = {i: bool(files[i]) and _is_test_path(files[i]) for i in comm}
    tests = mixed = 0
    for ids in members.values():
        t = sum(is_test[i] for i in ids)
        tests += t
        if t and t * 2 < len(ids):
            mixed += t
    n = len(comm)
    return {
        "count": len(members), "nodes": n,
        "largest_pct": round(100 * sizes[0] / n, 1),
        "singletons_pct": round(100 * sum(1 for x in sizes if x == 1) / n, 1),
        "median_size": sizes[len(sizes) // 2],
        "modularity": round(q, 3),
        "dir_purity_pct": round(100 * dominant / placed, 1) if placed else None,
        "tests_in_production_pct": round(100 * mixed / tests, 1) if tests else None,
    }


def nmi(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Normalised mutual information of two partitions over the nodes both have."""
    import math

    shared = [k for k in a if k in b]
    n = len(shared)
    if not n:
        return None
    ca = collections.Counter(a[k] for k in shared)
    cb = collections.Counter(b[k] for k in shared)
    joint = collections.Counter((a[k], b[k]) for k in shared)
    mi = sum(c / n * math.log(c * n / (ca[x] * cb[y])) for (x, y), c in joint.items())
    ha = -sum(c / n * math.log(c / n) for c in ca.values())
    hb = -sum(c / n * math.log(c / n) for c in cb.values())
    return round(2 * mi / (ha + hb), 3) if ha + hb else 1.0


def compare_communities(theirs: dict[str, Any], ours: dict[str, Any]) -> dict[str, Any]:
    def partition(g: dict[str, Any]) -> dict[str, Any]:
        nodes, _ = _nodes_edges(g)
        return {_node_id(n): n.get("community", n.get("cluster")) for n in nodes
                if n.get("community", n.get("cluster")) is not None and not _is_external_node(n)}

    return {"graphify": community_stats(theirs), "native": community_stats(ours),
            "nmi": nmi(partition(theirs), partition(ours))}


def render_communities(result: dict[str, Any]) -> str:
    rows = [("communities", "count"), ("nodes in them", "nodes"),
            ("largest, % of nodes", "largest_pct"), ("singletons, % of nodes", "singletons_pct"),
            ("median size", "median_size"), ("modularity (plain graph)", "modularity"),
            ("in the main directory, %", "dir_purity_pct"),
            ("test nodes with production, %", "tests_in_production_pct")]
    lines = [f"{'':32} {'graphify':>9} {'native':>9}"]
    for label, key in rows:
        lines.append(f"{label:32} {str(result['graphify'].get(key, '-')):>9} "
                     f"{str(result['native'].get(key, '-')):>9}")
    lines.append(f"agreement (NMI over shared nodes): {result['nmi']}")
    return "\n".join(lines)


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
    ap.add_argument("--communities", action="store_true",
                    help="compare the two engines' communities instead")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        theirs = load(graphify_graph(repo, args.graphify_graph, tmp))
        ours = load(native_graph(repo, args.native_graph, tmp))
    if args.communities:
        cres = compare_communities(theirs, ours)
        print(render_communities(cres))
        if args.json:
            Path(args.json).write_text(json.dumps(cres, indent=2), encoding="utf-8")
        return 0
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
