"""graph.json loading + schema-tolerant node/edge/traversal helpers.

Pure graph-data utilities with no dependency on the MCP surface; reads the project
location from :mod:`graphify_mcp.config`.
"""
from __future__ import annotations

import json
import os
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
            f"ERROR: {gp} not found. Run the graphify_build tool first "
            f"(project directory: {config.PROJECT_DIR})."
        )
    try:
        mtime = gp.stat().st_mtime
        key = str(gp)
        cached = _GRAPH_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        data = json.loads(gp.read_text(encoding="utf-8"))
        _GRAPH_CACHE[key] = (mtime, data)
        return data
    except json.JSONDecodeError as e:
        return f"ERROR: failed to parse graph.json: {e}"


def _nodes_edges(graph: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Schema-tolerant node/edge extraction."""
    nodes = graph.get("nodes") or graph.get("vertices") or []
    edges = graph.get("edges") or graph.get("links") or []
    return nodes, edges


def _node_id(n: dict) -> str:
    return str(n.get("id") or n.get("name") or n.get("label") or "?")


def _node_label(n: dict) -> str:
    return str(n.get("label") or n.get("name") or n.get("id") or "?")


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


def _edge_ends(e: dict) -> tuple[str, str]:
    return (
        str(e.get("source") or e.get("from") or e.get("src") or "?"),
        str(e.get("target") or e.get("to") or e.get("dst") or "?"),
    )


def _edge_rel(e: dict) -> str:
    return str(e.get("relation") or e.get("label") or e.get("type") or "->")


def _is_surprise_edge(e: dict) -> bool:
    """A genuinely flagged surprise edge.

    Note: an "inferred" confidence (graphify's EXTRACTED/INFERRED/AMBIGUOUS) is
    NOT a surprise — only an explicit surprise flag or type counts. Used by both
    graphify_overview and graphify_surprises so they agree on one definition.
    """
    return bool(
        e.get("surprise")
        or e.get("is_surprise")
        or str(e.get("type", "")).lower() == "surprise"
    )


def _resolve_node(nodes: list[dict], key: str) -> dict | None:
    """Match a node by exact id/label, else case-insensitive substring."""
    k = key.lower()
    for n in nodes:
        if _node_id(n) == key or _node_label(n) == key:
            return n
    for n in nodes:
        if k in _node_label(n).lower() or k in _node_id(n).lower():
            return n
    return None


def _adjacency(edges: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """Undirected adjacency: node -> list of (neighbor, relation)."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        s, t = _edge_ends(e)
        rel = _edge_rel(e)
        adj.setdefault(s, []).append((t, rel))
        adj.setdefault(t, []).append((s, rel))
    return adj


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
    """Token count for ``text``: exact via tiktoken when ``GRAPHIFY_TOKENIZER=tiktoken``
    and the optional ``[tiktoken]`` extra is installed, else the conservative
    chars/3.5 estimate (``_approx_tokens``). Falls back silently if tiktoken is
    requested but unavailable, so it's always safe to call."""
    if os.environ.get("GRAPHIFY_TOKENIZER", "").strip().lower() == "tiktoken":
        enc = _tiktoken_encoder()
        if enc is not None:
            return max(1, len(enc.encode(text)))
    return _approx_tokens(text)


def _bfs_subgraph(
    adj: dict[str, list[tuple[str, str]]],
    labels: dict[str, str],
    start_id: str,
    hops: int,
    budget_tokens: int,
) -> tuple[set[str], list[dict], bool, int]:
    """BFS around start_id collecting edges until a token budget is hit.

    Returns (visited_ids, edges, truncated, approx_tokens). Shared by
    graphify_subgraph and graphify_locate.
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
            key = tuple(sorted((cur, nb)) + [rel])  # type: ignore
            if key not in seen_pairs:
                seen_pairs.add(key)
                edge = {"from": labels.get(cur, cur), "to": labels.get(nb, nb), "relation": rel}
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

    # Report the count via _count_tokens (exact under GRAPHIFY_TOKENIZER=tiktoken,
    # else the heuristic). The budget gate above stays on the fast char heuristic,
    # so the cap is approximate while the reported figure can be exact.
    serialized = json.dumps(collected_edges, ensure_ascii=False)
    envelope_tokens = _approx_tokens("x" * _PAYLOAD_ENVELOPE_CHARS)
    return visited, collected_edges, truncated, max(1, _count_tokens(serialized) + envelope_tokens)


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
