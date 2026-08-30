"""graph.json loading + schema-tolerant node/edge/traversal helpers.

Pure graph-data utilities with no dependency on the MCP surface; reads the project
location from :mod:`graphlore.config`.
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import config


def _out_dir() -> Path:
    return config.PROJECT_DIR / config.OUT_DIR_NAME


def _graph_path() -> Path:
    return _out_dir() / "graph.json"


# Parsed graph.json cache, keyed by path -> (mtime, data). Avoids re-parsing a
# multi-MB graph on every tool call; keyed on path (not just mtime) so distinct
# graphs in tests can't collide on a coarse-resolution mtime.
_GRAPH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _load_graph() -> dict[str, Any] | str:
    """Load graph.json (cached by path+mtime); return an error message if missing."""
    gp = _graph_path()
    if not gp.exists():
        return (
            f"ERROR: {gp} not found. Run the graphlore_build tool first "
            f"(project directory: {config.PROJECT_DIR})."
        )
    try:
        mtime = gp.stat().st_mtime
        key = str(gp)
        cached = _GRAPH_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        data = json.loads(gp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            # Callers guard errors with isinstance(graph, str), so any valid-JSON
            # non-object (a top-level array/string/number) must become an error
            # string here — not leak through and AttributeError inside every tool.
            return f"ERROR: graph.json must be a JSON object, not {type(data).__name__}"
        _GRAPH_CACHE[key] = (mtime, data)
        return data
    except json.JSONDecodeError as e:
        return f"ERROR: failed to parse graph.json: {e}"
    except (OSError, UnicodeDecodeError) as e:
        # stat/read can fail mid-rebuild (file swapped between exists() and stat)
        # or on undecodable bytes; both are "graph unavailable", not a crash.
        return f"ERROR: failed to read graph.json: {e}"


def _nodes_edges(graph: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Schema-tolerant node/edge extraction."""
    nodes = graph.get("nodes") or graph.get("vertices") or []
    edges = graph.get("edges") or graph.get("links") or []
    return nodes, edges


def _first_present(d: dict, keys: tuple[str, ...]) -> Any:
    """First value among ``keys`` that is present and non-empty.

    Key-presence based, NOT an ``or``-chain: a falsy-but-valid value (integer id
    ``0`` in a networkx node-link export) must win, not fall through to the next
    key or a ``"?"`` placeholder.
    """
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return None


def _node_id(n: dict) -> str:
    v = _first_present(n, ("id", "name", "label"))
    return "?" if v is None else str(v)


def _node_label(n: dict) -> str:
    v = _first_present(n, ("label", "name", "id"))
    return "?" if v is None else str(v)


def _node_line(n: dict) -> Any:
    """Source line across schema variants.

    graphify stores the line as ``source_location`` like ``"L295"`` (or a range
    ``"L295-L312"``); other graph schemas use line/lineno/start_line.
    """
    for k in ("line", "lineno", "start_line"):
        v = n.get(k)
        if v not in (None, ""):
            return v
    digits = ""
    for ch in str(n.get("source_location") or "").lstrip("Ll"):
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else ""


def _node_file(n: dict) -> str:
    """Source file of a node across schema variants (file / path / source_file).

    Empty string when the node carries no source file (external-source / concept
    nodes), so callers can skip nodes that don't correspond to a file on disk.
    """
    v = _first_present(n, ("file", "path", "source_file"))
    return "" if v is None else str(v)


def _edge_ends(e: dict) -> tuple[str, str]:
    s = _first_present(e, ("source", "from", "src"))
    t = _first_present(e, ("target", "to", "dst"))
    return ("?" if s is None else str(s), "?" if t is None else str(t))


def _edge_rel(e: dict) -> str:
    v = _first_present(e, ("relation", "label", "type"))
    return "->" if v is None else str(v)


def _is_surprise_edge(e: dict) -> bool:
    """A genuinely flagged surprise edge.

    Note: an "inferred" confidence (graphify's EXTRACTED/INFERRED/AMBIGUOUS) is
    NOT a surprise — only an explicit surprise flag or type counts. Used by both
    graphlore_overview and graphlore_surprises so they agree on one definition.
    """
    return bool(
        e.get("surprise")
        or e.get("is_surprise")
        or str(e.get("type", "")).lower() == "surprise"
    )


# --- computed surprises -----------------------------------------------------
# A graph built by local AST extraction carries no surprise flag and often no
# community info, so the flag check above returns nothing on it. Rather than
# reporting an empty list (which reads as "this codebase has no surprising
# couplings" when it means "this tool computed nothing"), score the edges.

# Containment/import scaffolding: structurally expected, never surprising.
_SURPRISE_STRUCTURAL_RELS = frozenset({"contains", "method", "imports", "imports_from"})
_LANG_FAMILY = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "js", ".tsx": "js",
    ".go": "go", ".rs": "rust", ".java": "jvm", ".kt": "jvm", ".scala": "jvm",
    ".rb": "ruby", ".php": "php", ".cs": "dotnet",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cxx": "c",
}
_TEST_DIR_PARTS = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing", "e2e"})


def _lang_family(path: str) -> str | None:
    dot = path.rfind(".")
    return _LANG_FAMILY.get(path[dot:].lower()) if dot > 0 else None


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    if any(p.lower() in _TEST_DIR_PARTS for p in parts[:-1]):
        return True
    stem = parts[-1].lower().rsplit(".", 1)[0]
    return stem.startswith("test_") or stem.endswith(("_test", ".test", ".spec"))


def _dir_distance(a: str, b: str) -> tuple[int, bool]:
    """(tree distance between the two dirnames, do their top-level dirs differ)."""
    da, db = a.split("/")[:-1], b.split("/")[:-1]
    common = 0
    for x, y in zip(da, db, strict=False):
        if x != y:
            break
        common += 1
    return (len(da) - common) + (len(db) - common), da[:1] != db[:1]


def _is_file_hub_node(n: dict) -> bool:
    """A node standing for a whole file (label == its own basename)."""
    f = _node_file(n)
    return bool(f) and _node_label(n) == f.rsplit("/", 1)[-1]


def _computed_surprises(
    nodes: list[dict], edges: list[dict]
) -> tuple[list[dict], dict[str, Any]]:
    """Score cross-file edges for "surprisingness" when the graph carries no flag.

    Returns (ranked candidates, diagnostics). Diagnostics name *why* edges were
    skipped and which signals the graph lacks, so a caller can distinguish
    "nothing surprising here" from "nothing computable here" — the distinction
    the flag-only check silently collapsed.

    One O(N+E) pass plus a sort of the survivors; no betweenness, so it stays
    usable on large graphs.
    """
    byid = {_node_id(n): n for n in nodes}
    degree: dict[str, int] = {}
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    comm = {nid: n.get("community", n.get("cluster")) for nid, n in byid.items()}
    has_comm = any(c is not None for c in comm.values())
    ranked_deg = sorted(degree.values())
    # Reaching the graph's biggest hub is the least surprising thing in it, so
    # the peripheral->hub bonus stops applying at the god-node cut.
    god_cut = max(20, ranked_deg[int(len(ranked_deg) * 0.99)] if ranked_deg else 20)

    skipped: dict[str, int] = {}
    confidences: set[str] = set()
    scored: list[dict] = []

    for e in edges:
        rel = _edge_rel(e).strip().lower()
        if rel in _SURPRISE_STRUCTURAL_RELS:
            skipped["structural"] = skipped.get("structural", 0) + 1
            continue
        sid, tid = _edge_ends(e)
        nu, nv = byid.get(sid), byid.get(tid)
        if nu is None or nv is None:
            skipped["dangling"] = skipped.get("dangling", 0) + 1
            continue
        fu, fv = _node_file(nu), _node_file(nv)
        if not fu or not fv:
            skipped["no_source_file"] = skipped.get("no_source_file", 0) + 1
            continue
        if fu == fv:
            skipped["same_file"] = skipped.get("same_file", 0) + 1
            continue
        if _is_file_hub_node(nu) or _is_file_hub_node(nv):
            skipped["file_hub"] = skipped.get("file_hub", 0) + 1
            continue

        conf = str(e.get("confidence") or "EXTRACTED").upper()
        confidences.add(conf)
        cat_u = str(nu.get("file_type") or "code").lower()
        cat_v = str(nv.get("file_type") or "code").lower()
        lang_u, lang_v = _lang_family(fu), _lang_family(fv)
        cross_lang = bool(lang_u and lang_v and lang_u != lang_v)
        # An inferred call/uses edge that also crosses a language or code<->doc
        # boundary is usually the resolver guessing, not a real coupling; a
        # test<->source edge is routine. Both withhold the structural bonuses.
        resolver_noise = conf == "INFERRED" and rel in ("calls", "uses", "indirect_call") and (
            cross_lang or {cat_u, cat_v} == {"code", "document"}
        )
        test_bridge = _is_test_path(fu) != _is_test_path(fv)
        suppress = resolver_noise or test_bridge

        score = 0
        why: list[str] = []
        if not resolver_noise:
            score += {"AMBIGUOUS": 3, "INFERRED": 2}.get(conf, 1)
            if conf in ("AMBIGUOUS", "INFERRED"):
                why.append(f"{conf.lower()} link — the resolver guessed this target")
        if not suppress:
            if cat_u != cat_v:
                score += 2
                why.append(f"crosses file types ({cat_u} ↔ {cat_v})")
            dist, cross_top = _dir_distance(fu, fv)
            if cross_top:
                score += 2
                why.append("crosses top-level directories")
            elif dist >= 2:
                score += 1
                why.append("distant directories")
            if has_comm and comm.get(sid) is not None and comm.get(tid) is not None \
                    and comm[sid] != comm[tid]:
                score += 1
                why.append(f"bridges community {comm[sid]} → {comm[tid]}")
        du, dv = degree.get(sid, 0), degree.get(tid, 0)
        if min(du, dv) <= 2 and 5 <= max(du, dv) < god_cut:
            score += 1
            why.append("peripheral node reaches a well-connected node")
        if rel == "semantically_similar_to":
            score = int(score * 1.5)
        if test_bridge:
            why.append("(test ↔ source: routine coupling, structural bonuses withheld)")

        scored.append({
            "from": _node_label(nu), "to": _node_label(nv), "relation": _edge_rel(e),
            "from_file": fu, "to_file": fv, "score": score,
            "strength": "strong" if score >= 5 else ("moderate" if score >= 3 else "weak"),
            "why": why,
        })

    scored.sort(key=lambda c: (-c["score"], c["from_file"], c["to_file"]))
    seen_pairs: set[tuple[str, str, str]] = set()
    per_target: dict[str, int] = {}
    items: list[dict] = []
    for c in scored:
        key = (c["from_file"], c["to_file"], c["relation"])
        if key in seen_pairs or per_target.get(c["to"], 0) >= 2:
            continue  # one row per file pair+relation, and no target may dominate
        seen_pairs.add(key)
        per_target[c["to"]] = per_target.get(c["to"], 0) + 1
        items.append(c)

    diagnostics = {
        "scorable_edges": len(scored),
        "skipped": skipped,
        "has_community_info": has_comm,
        "confidence_values": sorted(confidences),
        "max_score": max((c["score"] for c in items), default=0),
    }
    return items, diagnostics


# Exact id/label -> node index, keyed by id(nodes) with an identity guard (same
# scheme as _ADJ_CACHE below). _resolve_node runs once per requested node in
# graphlore_fetch and once per call in most node-centric tools, so the exact
# pass — the overwhelmingly common case — should not rescan all N nodes.
_RESOLVE_CACHE: dict[int, tuple[list[dict], dict[str, dict]]] = {}
_RESOLVE_CACHE_MAX = 8


def _exact_node_index(nodes: list[dict]) -> dict[str, dict]:
    cached = _RESOLVE_CACHE.get(id(nodes))
    if cached is not None and cached[0] is nodes:
        return cached[1]
    index: dict[str, dict] = {}
    # setdefault in list order preserves the linear scan's first-match winner
    # even when one node's label collides with a later node's id.
    for n in nodes:
        index.setdefault(_node_id(n), n)
        index.setdefault(_node_label(n), n)
    if id(nodes) not in _RESOLVE_CACHE and len(_RESOLVE_CACHE) >= _RESOLVE_CACHE_MAX:
        _RESOLVE_CACHE.pop(next(iter(_RESOLVE_CACHE)), None)  # FIFO
    _RESOLVE_CACHE[id(nodes)] = (nodes, index)
    return index


def _resolve_node(nodes: list[dict], key: str) -> dict | None:
    """Match a node by exact id/label, else case-insensitive substring."""
    exact = _exact_node_index(nodes).get(key)
    if exact is not None:
        return exact
    k = key.lower()
    for n in nodes:
        if k in _node_label(n).lower() or k in _node_id(n).lower():
            return n
    return None


# node id -> list of (neighbor id, relation); the shape every traversal walks.
_Adj = dict[str, list[tuple[str, str]]]


def _build_adjacency(edges: list[dict]) -> _Adj:
    """Undirected adjacency: node -> list of (neighbor, relation)."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        s, t = _edge_ends(e)
        rel = _edge_rel(e)
        adj.setdefault(s, []).append((t, rel))
        adj.setdefault(t, []).append((s, rel))
    return adj


# Adjacency cache keyed by id(edges) with an identity guard: if the previous edges
# list was freed and its id recycled, the stored reference won't be `is`-identical
# to the new list, so a stale hit is impossible. _load_graph hands back the SAME
# edges list while the parsed graph stays cached (path+mtime), so repeat
# subgraph/locate/neighbors calls reuse the built adjacency; a changed graph file
# re-parses to a fresh list and misses. Bounded FIFO so old graphs don't pin memory.
_ADJ_CACHE: dict[int, tuple[list[dict], _Adj]] = {}
_ADJ_CACHE_MAX = 8


def _adjacency(edges: list[dict]) -> _Adj:
    """Undirected adjacency for `edges`, cached on the edges-list identity.

    Repeat traversals over an unchanged graph reuse the built adjacency instead of
    rebuilding it every call; a reload (changed file) hands a fresh list, which
    misses and rebuilds. See _ADJ_CACHE.
    """
    cached = _ADJ_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    adj = _build_adjacency(edges)
    if id(edges) not in _ADJ_CACHE and len(_ADJ_CACHE) >= _ADJ_CACHE_MAX:
        _ADJ_CACHE.pop(next(iter(_ADJ_CACHE)), None)  # FIFO: drop the oldest entry
    _ADJ_CACHE[id(edges)] = (edges, adj)
    return adj


def _build_directed_adjacency(edges: list[dict]) -> tuple[_Adj, _Adj]:
    """Directed adjacency, preserving edge orientation that _adjacency flattens away.

    Returns (forward, reverse): ``forward[s]`` lists the targets ``s`` points at
    (what s depends on), ``reverse[t]`` lists the sources pointing at ``t`` (what
    depends on t). Reverse is the blast radius — who breaks if t changes.
    """
    forward: dict[str, list[tuple[str, str]]] = {}
    reverse: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        s, t = _edge_ends(e)
        rel = _edge_rel(e)
        forward.setdefault(s, []).append((t, rel))
        reverse.setdefault(t, []).append((s, rel))
    return forward, reverse


# Same id(edges)+identity-guard scheme as _ADJ_CACHE (see there).
_DIR_ADJ_CACHE: dict[int, tuple[list[dict], tuple[_Adj, _Adj]]] = {}


def _directed_adjacency(edges: list[dict]) -> tuple[_Adj, _Adj]:
    """(forward, reverse) directed adjacency for `edges`, cached on list identity."""
    cached = _DIR_ADJ_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    pair = _build_directed_adjacency(edges)
    if id(edges) not in _DIR_ADJ_CACHE and len(_DIR_ADJ_CACHE) >= _ADJ_CACHE_MAX:
        _DIR_ADJ_CACHE.pop(next(iter(_DIR_ADJ_CACHE)), None)  # FIFO
    _DIR_ADJ_CACHE[id(edges)] = (edges, pair)
    return pair


# Same id(edges)+identity-guard scheme as _ADJ_CACHE (see there).
_EDGE_SET_CACHE: dict[int, tuple[list[dict], set[tuple[str, str, str]]]] = {}


def _edge_set(edges: list[dict]) -> set[tuple[str, str, str]]:
    """Directed ``(source, target, relation)`` triples, cached on list identity.

    The orientation oracle for traversals over the undirected adjacency: lets
    _bfs_subgraph emit each collected edge in its TRUE direction instead of the
    (possibly reversed) direction it happened to be traversed in.
    """
    cached = _EDGE_SET_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    es = {(*_edge_ends(e), _edge_rel(e)) for e in edges}
    if id(edges) not in _EDGE_SET_CACHE and len(_EDGE_SET_CACHE) >= _ADJ_CACHE_MAX:
        _EDGE_SET_CACHE.pop(next(iter(_EDGE_SET_CACHE)), None)  # FIFO
    _EDGE_SET_CACHE[id(edges)] = (edges, es)
    return es


# Token-estimate heuristic. 4.0 chars/token is the common English rule of thumb,
# but code — dotted identifiers, punctuation, camelCase — packs more tokens per
# char, so we use a conservative 3.5 (≈ +14%) to avoid systematically
# UNDER-reporting how much of a budget a subgraph consumes. The result is an
# estimate (±~20%), not an exact tokenizer count.
_CHARS_PER_TOKEN = 3.5
# Fixed allowance for the JSON envelope around the edge array (the wrapper keys
# center/hops/nodes/truncated/approx_tokens), so the budget and the reported token
# count reflect the whole returned payload, not just the bare edge list.
_PAYLOAD_ENVELOPE_CHARS = 96


def _approx_tokens(text: str) -> int:
    """Conservative chars→tokens estimate (see ``_CHARS_PER_TOKEN``); an estimate, not exact."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# Optional exact token counting. _TIKTOKEN_ENC: None = not yet probed, False =
# unavailable (extra not installed), else a cached encoder.
_TIKTOKEN_ENC: Any = None


def _tiktoken_encoder() -> Any:
    global _TIKTOKEN_ENC
    if _TIKTOKEN_ENC is None:
        try:
            import tiktoken

            _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TIKTOKEN_ENC = False
    return _TIKTOKEN_ENC or None


def _count_tokens(text: str) -> int:
    """Token count for ``text``: exact via tiktoken when ``GRAPHLORE_TOKENIZER=tiktoken``
    and the optional ``[tiktoken]`` extra is installed, else the conservative
    chars/3.5 estimate (``_approx_tokens``). Falls back silently if tiktoken is
    requested but unavailable, so it's always safe to call."""
    if config.env("TOKENIZER", "").strip().lower() == "tiktoken":
        enc = _tiktoken_encoder()
        if enc is not None:
            return max(1, len(enc.encode(text)))
    return _approx_tokens(text)


def _bfs_subgraph(
    adj: dict[str, list[tuple[str, str]]],
    labels: Any,  # id -> display label; dict or server._DisplayLabels (duck-typed .get)
    start_id: str,
    hops: int,
    budget_tokens: int,
    edge_set: set[tuple[str, str, str]] | None = None,
) -> tuple[set[str], list[dict], bool, int]:
    """BFS around start_id collecting edges until a token budget is hit.

    Returns (visited_ids, edges, truncated, approx_tokens). Shared by
    graphlore_subgraph and graphlore_locate. ``edge_set`` (from :func:`_edge_set`)
    restores the true orientation of each collected edge: the undirected
    adjacency flattens direction, and an edge reached via its reverse entry must
    not be reported as ``B —calls→ A`` when the graph says ``A —calls→ B``.
    """
    visited = {start_id}
    frontier = deque([(start_id, 0)])
    collected_edges: list[dict] = []
    seen_pairs: set[tuple[str, ...]] = set()
    truncated = False
    running_chars = 2 + _PAYLOAD_ENVELOPE_CHARS  # "[]" of the edge array + JSON envelope

    while frontier:
        cur, depth = frontier.popleft()
        if depth >= hops:
            continue
        for nb, rel in adj.get(cur, []):
            src, dst = cur, nb
            if (edge_set is not None and (src, dst, rel) not in edge_set
                    and (dst, src, rel) in edge_set):
                src, dst = dst, src  # traversed backwards: emit the true direction
            key = tuple(sorted((cur, nb)) + [rel])  # type: ignore
            if key not in seen_pairs:
                seen_pairs.add(key)
                edge = {"from": labels.get(src, src), "to": labels.get(dst, dst), "relation": rel}
                collected_edges.append(edge)
                # running size estimate instead of re-serializing the whole list (O(n^2))
                running_chars += len(json.dumps(edge, ensure_ascii=False)) + 2
            if nb not in visited:
                visited.add(nb)
                frontier.append((nb, depth + 1))
            if running_chars / _CHARS_PER_TOKEN >= budget_tokens:
                truncated = True
                frontier.clear()
                break

    # Report the count via _count_tokens (exact under GRAPHLORE_TOKENIZER=tiktoken,
    # else the heuristic). The budget gate above stays on the fast char heuristic,
    # so the cap is approximate while the reported figure can be exact.
    serialized = json.dumps(collected_edges, ensure_ascii=False)
    envelope_tokens = _approx_tokens("x" * _PAYLOAD_ENVELOPE_CHARS)
    return visited, collected_edges, truncated, max(1, _count_tokens(serialized) + envelope_tokens)


def _strongly_connected_components(forward: _Adj, node_ids: list[str]) -> list[list[str]]:
    """Tarjan's SCC, iterative (no recursion limit on deep/large graphs).

    Each returned component is a maximal set of mutually-reachable nodes; a component
    of size >= 2 (or a single node with a self-edge) contains a cycle.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = 0
    out: list[list[str]] = []
    for root in node_ids:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            recursed = False
            neigh = forward.get(v, [])
            j = pi
            while j < len(neigh):
                w = neigh[j][0]
                if w not in index:
                    work[-1] = (v, j + 1)
                    work.append((w, 0))
                    recursed = True
                    break
                if on_stack.get(w):
                    low[v] = min(low[v], index[w])
                j += 1
            if recursed:
                continue
            if low[v] == index[v]:  # v is an SCC root: pop the component
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
            work.pop()
            if work:  # fold v's lowlink back into its parent
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
    return out


def _find_cycles(forward: _Adj) -> tuple[list[list[str]], list[str]]:
    """(cycles, self_loops) from a directed adjacency.

    ``cycles`` are SCCs of size >= 2 (mutually-dependent node groups), largest first;
    ``self_loops`` are nodes with an edge to themselves (reported separately, since a
    self-edge is a degenerate 1-node cycle).
    """
    ids: set[str] = set(forward)
    for lst in forward.values():
        for w, _ in lst:
            ids.add(w)
    self_loops = {s for s in forward for w, _ in forward[s] if w == s}
    sccs = _strongly_connected_components(forward, sorted(ids))
    cycles = sorted((c for c in sccs if len(c) >= 2), key=lambda c: (-len(c), min(c)))
    return cycles, sorted(self_loops)


def _hop_distances(
    adj: dict[str, list[tuple[str, str]]], start_id: str, max_hops: int
) -> dict[str, int]:
    """Shortest hop distance from start_id to each node reachable within max_hops."""
    dist = {start_id: 0}
    frontier = deque([(start_id, 0)])
    while frontier:
        cur, d = frontier.popleft()
        if d >= max_hops:
            continue
        for nb, _rel in adj.get(cur, []):
            if nb not in dist:
                dist[nb] = d + 1
                frontier.append((nb, d + 1))
    return dist


# node id -> list of (neighbor id, the whole edge dict). _directed_adjacency keeps
# only the relation, which is all hop-counting needs; impact also wants the
# traversed edge's own recorded position (the reference site), so it needs the edge.
_EAdj = dict[str, list[tuple[str, dict]]]


def _build_directed_edge_adjacency(edges: list[dict]) -> tuple[_EAdj, _EAdj]:
    """(forward, reverse) directed adjacency carrying each traversed edge."""
    forward: _EAdj = {}
    reverse: _EAdj = {}
    for e in edges:
        s, t = _edge_ends(e)
        forward.setdefault(s, []).append((t, e))
        reverse.setdefault(t, []).append((s, e))
    return forward, reverse


# Same id(edges)+identity-guard scheme as _ADJ_CACHE (see there).
_DIR_EDGE_ADJ_CACHE: dict[int, tuple[list[dict], tuple[_EAdj, _EAdj]]] = {}


def _directed_edge_adjacency(edges: list[dict]) -> tuple[_EAdj, _EAdj]:
    """(forward, reverse) edge-carrying adjacency, cached on the edges-list identity."""
    cached = _DIR_EDGE_ADJ_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    pair = _build_directed_edge_adjacency(edges)
    if id(edges) not in _DIR_EDGE_ADJ_CACHE and len(_DIR_EDGE_ADJ_CACHE) >= _ADJ_CACHE_MAX:
        _DIR_EDGE_ADJ_CACHE.pop(next(iter(_DIR_EDGE_ADJ_CACHE)), None)  # FIFO
    _DIR_EDGE_ADJ_CACHE[id(edges)] = (edges, pair)
    return pair


def _hop_records(
    adj: _EAdj,
    start_id: str,
    max_hops: int,
    relations: frozenset[str] | None = None,
    seeds: Iterable[str] = (),
) -> dict[str, tuple[int, str, dict | None]]:
    """Like :func:`_hop_distances`, but records HOW each node was reached.

    Returns ``{node_id: (distance, reached_via_id, discovery_edge)}`` — the edge
    that first reached the node on a shortest path. ``relations=None`` walks
    every edge type, so the distances match _hop_distances exactly. ``seeds`` are
    extra depth-0 entries (used to keep a class's own members reachable when
    containment edges are filtered out); they carry ``None`` as their edge so
    callers can tell them from real hits.
    """
    out: dict[str, tuple[int, str, dict | None]] = {start_id: (0, "", None)}
    frontier = deque([(start_id, 0)])
    for s in seeds:
        if s not in out:
            out[s] = (0, "", None)
            frontier.append((s, 0))
    while frontier:
        cur, d = frontier.popleft()
        if d >= max_hops:
            continue
        for nb, e in adj.get(cur, []):
            if relations is not None and _edge_rel(e).strip().lower() not in relations:
                continue
            if nb in out:
                continue
            out[nb] = (d + 1, cur, e)
            frontier.append((nb, d + 1))
    return out
