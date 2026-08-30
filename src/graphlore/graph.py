"""graph.json loading + schema-tolerant node/edge/traversal helpers.

Pure graph-data utilities with no dependency on the MCP surface; reads the project
location from :mod:`graphlore.config`.
"""
from __future__ import annotations

import json
from collections import deque
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
