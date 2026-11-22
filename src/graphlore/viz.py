"""graph.html: an offline, self-contained view of graph.json.

graphlore writes it next to graph.json after every build (``no_viz=True`` skips
it), whichever engine built the graph, and again when community names change.
The page is one file: the drawing code is graphlore's own (``viewer.html``,
canvas and plain JavaScript), the graph is embedded as JSON, and a Content
Security Policy forbids every request, so opening it reaches no network.
``viewer.html`` with no graph in it is a viewer of its own: it opens any
graph.json the reader picks or drops (read in the browser), through its
``compact()``, the JavaScript twin of :func:`page_data` — the two give the
same answer (``test_the_viewer_reads_a_raw_graph_as_python_does``).

A large codebase does not fit on one screen, so the page starts from the
communities (one bubble each, the edges between them counted) and opens one
community, or one node's neighbourhood, at a time. Labels, paths and names come
from the repository: the page puts them on the canvas and in the DOM as text
only (``textContent``, ``fillText``), never as markup, and the JSON is embedded
with ``<``, ``>`` and ``&`` escaped so it cannot close its ``<script>``.

Imports only ``graph``.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .graph import (
    _CALL_RELATIONS,
    _CONTAINMENT_RELATIONS,
    _COUPLING_RELATIONS,
    _HTTP_RELATIONS,
    _IMPORT_RELATIONS,
    _STATE_RELATIONS,
    _edge_ends,
    _edge_rel,
    _is_external_node,
    _is_file_hub_node,
    _is_test_path,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _node_type,
    _nodes_edges,
)

TEMPLATE = Path(__file__).with_name("viewer.html")
# How a graph.html says graphlore wrote it (graphify's is patched, not rewritten).
GENERATOR = '<meta name="generator" content="graphlore">'
# Past this many nodes the page would be tens of megabytes and slow to open:
# graph.html is not written, and the build says so.
MAX_NODES = 150_000
# The communities the overview draws (largest first); the rest are counted.
MAX_COMMUNITIES = 300

_INHERITANCE = frozenset({"inherits", "implements", "mixes_in", "embeds", "extends"})
# Edge groups: the page colours and filters edges by these, in this order.
GROUPS = ("structure", "imports", "calls", "inheritance", "references", "state",
          "http", "other")


def relation_group(rel: str) -> str:
    """The group a relation is drawn and filtered with."""
    if rel in _CONTAINMENT_RELATIONS:
        return "structure"
    if rel in _IMPORT_RELATIONS:
        return "imports"
    if rel in _CALL_RELATIONS or rel == "dispatches_to":
        return "calls"
    if rel in _INHERITANCE:
        return "inheritance"
    if rel == "references":
        return "references"
    if rel in _STATE_RELATIONS or rel in _COUPLING_RELATIONS:
        return "state"
    if rel in _HTTP_RELATIONS:
        return "http"
    return "other"


def _kind(n: dict) -> str:
    if _is_external_node(n):
        return "external"
    if _is_file_hub_node(n):
        return "file"
    kind = n.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    return _node_type(n) or "node"


def _is_test(n: dict) -> bool:
    """Test code as the graph says it, without reading any file: the native
    engine's ``test`` mark, or a test path."""
    return n.get("test") is True or _is_test_path(_node_file(n))


def page_data(graph: dict, names: dict[str, str] | None = None) -> dict[str, Any]:
    """The compact form of ``graph`` the page reads: nodes and edges as arrays of
    indices into the relation, kind and community tables."""
    nodes, edges = _nodes_edges(graph)
    names = names or {}
    index: dict[str, int] = {}
    for i, n in enumerate(nodes):
        index.setdefault(_node_id(n), i)

    degree: Counter[int] = Counter()
    flat_edges: list[int] = []
    rels: dict[str, int] = {}
    for e in edges:
        s, t = _edge_ends(e)
        si, ti = index.get(s), index.get(t)
        if si is None or ti is None:
            continue  # a dangling edge: graphlore_validate reports it
        rel = _edge_rel(e) or "related"
        r = rels.setdefault(rel, len(rels))
        inferred = str(e.get("confidence", "")).upper() == "INFERRED"
        flat_edges += [si, ti, r, 1 if inferred else 0]
        degree[si] += 1
        degree[ti] += 1

    comm_ids: dict[str, int] = {}
    comm_size: Counter[int] = Counter()
    comm_name: dict[int, str] = {}
    kinds: dict[str, int] = {}
    rows: list[list[Any]] = []
    for i, n in enumerate(nodes):
        raw = n.get("community", n.get("cluster"))
        c = -1
        if raw is not None and raw != "":
            key = str(raw)
            c = comm_ids.setdefault(key, len(comm_ids))
            comm_size[c] += 1
            if c not in comm_name:
                given = names.get(key) or n.get("community_name")
                comm_name[c] = str(given) if given else f"Community {key}"
        k = kinds.setdefault(_kind(n), len(kinds))
        flags = (1 if _is_test(n) else 0) | (2 if _is_external_node(n) else 0)
        line = str(_node_line(n))
        rows.append([_node_id(n), _node_label(n), _node_file(n),
                     int(line) if line.isdigit() else 0, c, k, flags, degree[i]])

    # edges between communities, counted: the overview's links
    between: Counter[tuple[int, int]] = Counter()
    for j in range(0, len(flat_edges), 4):
        a, b = rows[flat_edges[j]][4], rows[flat_edges[j + 1]][4]
        if a >= 0 and b >= 0 and a != b:
            between[(a, b) if a < b else (b, a)] += 1
    comms = [[cid, comm_name.get(c, f"Community {cid}"), comm_size[c]]
             for cid, c in comm_ids.items()]
    header = graph.get("graph")
    meta: dict[str, Any] = header if isinstance(header, dict) else {}
    return {
        "meta": {
            "engine": "graphlore" if meta.get("engine") == "graphlore" else "graphify",
            "commit": str(graph.get("built_at_commit") or meta.get("built_at_commit") or "")[:12],
            "nodes": len(nodes), "edges": len(flat_edges) // 4,
        },
        "groups": list(GROUPS),
        "rels": [[rel, GROUPS.index(relation_group(rel))] for rel in rels],
        "kinds": list(kinds),
        "comms": comms,
        "maxComms": MAX_COMMUNITIES,
        "nodes": rows,
        "edges": flat_edges,
        "cedges": [x for (a, b), w in sorted(between.items()) for x in (a, b, w)],
    }


def _embed(data: dict[str, Any]) -> str:
    """JSON that can sit inside a ``<script>``: no ``<``, ``>`` or ``&`` left raw."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render(graph: dict, title: str, names: dict[str, str] | None = None) -> str:
    """graph.html for ``graph``."""
    page = TEMPLATE.read_text(encoding="utf-8")
    return (page.replace("__GRAPHLORE_TITLE__", html.escape(title))
                .replace("/*__GRAPHLORE_DATA__*/", _embed(page_data(graph, names))))


def is_ours(path: Path) -> bool:
    """Whether the graph.html at ``path`` is one graphlore wrote."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return GENERATOR in fh.read(2048)
    except OSError:
        return False


def write(graph: dict, out: Path, title: str, names: dict[str, str] | None = None) -> str:
    """Write graph.html for ``graph`` to ``out``; a one-line note saying what was
    done, for the build's reply."""
    nodes, _ = _nodes_edges(graph)
    if len(nodes) > MAX_NODES:
        return (f"graph.html not written: {len(nodes):,} nodes is more than the page "
                f"draws ({MAX_NODES:,}).")
    try:
        out.write_text(render(graph, title, names), encoding="utf-8")
    except OSError as e:
        return f"graph.html not written: {e}"
    return f"graph.html written: {out} (open it in a browser; it reaches no network)"
