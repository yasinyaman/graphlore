#!/usr/bin/env python3
"""Graphify MCP Server — exposes the graphify CLI and graph as MCP tools.

Wraps graphify (https://graphify.net) so an AI assistant can query the
codebase knowledge graph during development.

CLI-backed tools:
  - graphify_build      : build / update the graph from a folder
  - graphify_query      : natural-language graph query
  - graphify_path       : path between two nodes
  - graphify_explain    : full explanation of a single node
  - graphify_add        : add an external source by URL (paper, tweet)

graph.json analysis tools (no CLI needed):
  - graphify_overview   : one-shot orientation (call this first)
  - graphify_god_nodes  : highest-degree nodes
  - graphify_surprises  : unexpected cross-domain connections
  - graphify_communities: Leiden community summaries
  - graphify_search     : node name/label search
  - graphify_neighbors  : 1-hop neighbors of a node
  - graphify_subgraph   : token-budgeted BFS subgraph around a node
  - graphify_node_details: node detail with source file/line refs
  - graphify_freshness  : is the graph stale vs the current git HEAD?

Resources:
  - graphify://report          : GRAPH_REPORT.md
  - graphify://graph           : graph.json
  - graphify://community/{id}  : per-community wiki

Prompts:
  - onboard      : orient an assistant to the codebase
  - trace_bug    : investigate a symptom through the graph
  - explain_flow : explain how a named flow/feature works

Usage:
  GRAPHIFY_PROJECT_DIR=/path/to/repo python server.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter, deque
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import (
    ClientCapabilities,
    SamplingCapability,
    SamplingMessage,
    TextContent,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

__version__ = "0.1.0"

PROJECT_DIR = Path(os.environ.get("GRAPHIFY_PROJECT_DIR", ".")).resolve()
OUT_DIR_NAME = os.environ.get("GRAPHIFY_OUT_DIR", "graphify-out")
GRAPHIFY_BIN = os.environ.get("GRAPHIFY_BIN", "graphify")
CLI_TIMEOUT = int(os.environ.get("GRAPHIFY_TIMEOUT", "600"))

# Opt-in: confine graphify_build's `path` to PROJECT_DIR. Off by default so the
# documented absolute/sibling-repo path keeps working; force-enabled for HTTP.
RESTRICT_PATHS = os.environ.get("GRAPHIFY_RESTRICT_PATHS", "").lower() in ("1", "true", "yes")

# Transport: "stdio" (default) | "streamable-http" | "sse". HTTP binds HOST:PORT.
TRANSPORT = os.environ.get("GRAPHIFY_TRANSPORT", "stdio").lower()
HTTP_HOST = os.environ.get("GRAPHIFY_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("GRAPHIFY_PORT", "8000"))

mcp = FastMCP(
    "graphify",
    instructions=(
        "Graphify knowledge graph tools for understanding a codebase.\n"
        "Recommended flow:\n"
        "  1. Call graphify_overview first for orientation.\n"
        "  2. Use graphify_subgraph / graphify_neighbors / graphify_query for "
        "targeted, token-cheap exploration around a node or question.\n"
        "  3. graphify_build (with update=True) re-syncs after code changes.\n"
        "Most analysis tools read graph.json directly and are read-only; only "
        "graphify_build and graphify_add modify state. Pass as_json=True on "
        "analysis tools when you want structured output to chain on."
    ),
)

# FastMCP doesn't forward a version to the underlying MCP server, so clients
# would otherwise report the mcp library's version. Surface our own instead.
try:  # pragma: no cover - guards against private-attr changes upstream
    mcp._mcp_server.version = __version__
except Exception:
    pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _out_dir() -> Path:
    return PROJECT_DIR / OUT_DIR_NAME


def _graph_path() -> Path:
    return _out_dir() / "graph.json"


def _path_escapes_project(path: str) -> str | None:
    """Opt-in containment for a build path.

    Returns an error string if GRAPHIFY_RESTRICT_PATHS is set and `path` resolves
    outside PROJECT_DIR; otherwise None. Off by default so the documented
    absolute / sibling-repo path keeps working.
    """
    if not RESTRICT_PATHS:
        return None
    p = Path(path)
    resolved = (p if p.is_absolute() else PROJECT_DIR / p).resolve()
    try:
        resolved.relative_to(PROJECT_DIR)
    except ValueError:
        return (
            f"ERROR: path '{path}' escapes the project directory ({PROJECT_DIR}); "
            "GRAPHIFY_RESTRICT_PATHS is enabled. Unset it or pass a contained path."
        )
    return None


def _fmt(payload: Any, as_json: bool, text: str) -> str:
    """Return structured JSON or a human-readable string."""
    if as_json:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return text


def _run_cli(args: list[str], cwd: Path | None = None) -> str:
    """Run the graphify CLI and return stdout+stderr."""
    if shutil.which(GRAPHIFY_BIN) is None:
        return (
            f"ERROR: '{GRAPHIFY_BIN}' not found. Install with: pip install graphifyy && "
            "graphify install. Alternatively set the GRAPHIFY_BIN environment variable."
        )
    try:
        proc = subprocess.run(
            [GRAPHIFY_BIN, *args],
            cwd=str(cwd or PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command did not finish within {CLI_TIMEOUT}s: graphify {' '.join(args)}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"ERROR (exit {proc.returncode}):\n{err or out}"
    return out + (f"\n[stderr]\n{err}" if err else "")


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
            f"(project directory: {PROJECT_DIR})."
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


def _git(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(PROJECT_DIR),
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()
    except Exception:
        return None


# Roughly ~4 chars per token; good enough for budgeting display.
def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# Env var -> graphify backend name, for detecting a user-supplied API key.
_BACKEND_ENV = {
    "GEMINI_API_KEY": "gemini",
    "GOOGLE_API_KEY": "gemini",
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "claude",
    "DEEPSEEK_API_KEY": "deepseek",
    "KIMI_API_KEY": "kimi",
    "MOONSHOT_API_KEY": "kimi",
}


def _detect_backend() -> str | None:
    """Name of the graphify LLM backend a user key is present for, else None."""
    for env, name in _BACKEND_ENV.items():
        if os.environ.get(env):
            return name
    return None


def _client_supports_sampling(ctx: Context) -> bool:
    """Capability test: does the connected MCP client offer host-LLM sampling?"""
    try:
        return ctx.session.check_client_capability(
            ClientCapabilities(sampling=SamplingCapability())
        )
    except Exception:
        return False


def _read_labels() -> dict[str, str]:
    """graphify-out/.graphify_labels.json — community id -> name (CLI-written)."""
    lp = _out_dir() / ".graphify_labels.json"
    if not lp.exists():
        return {}
    try:
        return json.loads(lp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# CLI wrapper tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"title": "Build/update graph", "destructiveHint": False})
def graphify_build(
    path: str = ".",
    mode: str = "",
    update: bool = False,
    cluster_only: bool = False,
    no_viz: bool = True,
) -> str:
    """Build or update a knowledge graph from a folder. (Writes to graphify-out/.)

    Args:
        path: Folder to extract the graph from (relative to the project dir or absolute).
        mode: "deep" -> more aggressive INFERRED edges; empty -> default.
        update: True -> re-extract only changed files and merge into the existing graph.
        cluster_only: True -> rerun clustering only, without re-extraction.
        no_viz: True -> skip the HTML visualization (faster for development).
    """
    err = _path_escapes_project(path)
    if err:
        return err
    args = [path]
    if mode:
        args += ["--mode", mode]
    if update:
        args.append("--update")
    if cluster_only:
        args.append("--cluster-only")
    if no_viz:
        args.append("--no-viz")
    result = _run_cli(args)
    gp = _graph_path()
    if gp.exists():
        result += f"\n\ngraph.json ready: {gp}"
    return result


@mcp.tool(annotations={"title": "Query graph", "readOnlyHint": True})
def graphify_query(question: str, dfs: bool = False, budget: int = 0) -> str:
    """Run a natural-language query against the graph.

    Args:
        question: Natural-language question, e.g. "what connects attention to the optimizer?"
        dfs: True -> trace a specific path in depth.
        budget: If >0, cap the number of tokens returned (e.g. 1500).
    """
    args = ["query", question]
    if dfs:
        args.append("--dfs")
    if budget > 0:
        args += ["--budget", str(budget)]
    gp = _graph_path()
    if gp.exists():
        args += ["--graph", str(gp)]
    return _run_cli(args)


@mcp.tool(annotations={"title": "Path between nodes", "readOnlyHint": True})
def graphify_path(node_a: str, node_b: str) -> str:
    """Find the exact path between two nodes (e.g. "DigestAuth" -> "Response")."""
    return _run_cli(["path", node_a, node_b])


@mcp.tool(annotations={"title": "Explain node", "readOnlyHint": True})
def graphify_explain(node: str) -> str:
    """Return everything Graphify knows about a node."""
    return _run_cli(["explain", node])


@mcp.tool(annotations={"title": "Add external source", "destructiveHint": False})
def graphify_add(url: str, author: str = "", contributor: str = "") -> str:
    """Add an external source to the graph (arXiv paper, tweet, etc.). http/https only.

    Args:
        url: Source URL to add.
        author: Original author tag (optional).
        contributor: Tag for who added it (optional).
    """
    if not url.startswith(("http://", "https://")):
        return "ERROR: only http/https URLs are supported."
    args = ["add", url]
    if author:
        args += ["--author", author]
    if contributor:
        args += ["--contributor", contributor]
    return _run_cli(args)


# ---------------------------------------------------------------------------
# graph.json analysis tools (read-only, no CLI required)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"title": "Codebase overview", "readOnlyHint": True})
def graphify_overview(top_n: int = 8, as_json: bool = False) -> str:
    """One-shot orientation: call this FIRST.

    Returns graph size, top god nodes, community count, surprise-edge count and
    suggested starting questions — enough to plan further exploration cheaply.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    labels = {_node_id(n): _node_label(n) for n in nodes}
    comms = {n.get("community", n.get("cluster")) for n in nodes}
    comms.discard(None)
    surprises = sum(1 for e in edges if _is_surprise_edge(e))
    # Diagnostic: distinct nodes that collapse to one id (e.g. id-less nodes
    # sharing a label) silently distort degrees/adjacency.
    id_collisions = len(nodes) - len({_node_id(n) for n in nodes})
    top = degree.most_common(top_n)
    god = [{"node": labels.get(nid, nid), "degree": d} for nid, d in top]

    payload = {
        "nodes": len(nodes),
        "edges": len(edges),
        "communities": len(comms),
        "surprise_edges": surprises,
        "id_collisions": id_collisions,
        "god_nodes": god,
        "suggested_next": [
            f"graphify_subgraph(\"{god[0]['node']}\")" if god else "graphify_communities()",
            "graphify_communities()",
            "graphify_surprises()",
        ],
    }
    lines = [
        f"{len(nodes)} nodes, {len(edges)} edges, {len(comms)} communities, "
        f"{surprises} surprise edges.\n",
        f"Top {len(god)} god nodes:",
    ]
    lines += [f"  {g['node']} — degree {g['degree']}" for g in god]
    if id_collisions:
        lines.append(
            f"\nWarning: {id_collisions} node id collision(s) — distinct nodes share an "
            "id/label and were merged; degrees/neighbors may be understated."
        )
    lines.append("\nSuggested next steps: " + "; ".join(payload["suggested_next"]))
    return _fmt(payload, as_json, "\n".join(lines))


@mcp.tool(annotations={"title": "God nodes", "readOnlyHint": True})
def graphify_god_nodes(top_n: int = 10, as_json: bool = False) -> str:
    """List the highest-degree (most connected) 'god nodes'."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    labels = {_node_id(n): _node_label(n) for n in nodes}
    types = {_node_id(n): n.get("type", "") for n in nodes}
    items = [
        {"node": labels.get(nid, nid), "type": types.get(nid, ""), "degree": d}
        for nid, d in degree.most_common(top_n)
    ]
    text = [f"Total {len(nodes)} nodes, {len(edges)} edges. Top {top_n} god nodes:\n"]
    for it in items:
        t = f" [{it['type']}]" if it["type"] else ""
        text.append(f"  {it['node']}{t} — degree {it['degree']}")
    return _fmt({"god_nodes": items}, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Surprise edges", "readOnlyHint": True})
def graphify_surprises(limit: int = 20, as_json: bool = False) -> str:
    """List unexpected cross-file/cross-domain connections (surprise edges)."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    flagged = [e for e in edges if _is_surprise_edge(e)]
    fallback = False
    if not flagged:
        comm = {_node_id(n): n.get("community", n.get("cluster")) for n in nodes}
        flagged = [
            e for e in edges
            if comm.get(_edge_ends(e)[0]) is not None
            and comm.get(_edge_ends(e)[0]) != comm.get(_edge_ends(e)[1])
        ]
        fallback = True
    labels = {_node_id(n): _node_label(n) for n in nodes}
    items = []
    for e in flagged[:limit]:
        s, t = _edge_ends(e)
        items.append({"from": labels.get(s, s), "to": labels.get(t, t), "relation": _edge_rel(e)})
    header = (
        f"No flagged surprise edges; first {limit} of {len(flagged)} cross-community edges:"
        if fallback else
        f"First {limit} of {len(flagged)} flagged surprise edges:"
    )
    text = [header] + [f"  {i['from']} —{i['relation']}→ {i['to']}" for i in items]
    return _fmt({"surprises": items, "fallback": fallback}, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Communities", "readOnlyHint": True})
def graphify_communities(as_json: bool = False) -> str:
    """Summarize Leiden communities with sizes and sample members."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, _ = _nodes_edges(graph)
    comms: dict[Any, list[str]] = {}
    for n in nodes:
        c = n.get("community", n.get("cluster"))
        if c is not None:
            comms.setdefault(c, []).append(_node_label(n))
    if not comms:
        return "Nodes carry no community info. Try graphify_build (cluster_only=True)."
    ordered = sorted(comms.items(), key=lambda kv: -len(kv[1]))
    items = [{"id": c, "size": len(m), "members": m} for c, m in ordered]
    text = [f"{len(comms)} communities:\n"]
    for it in items:
        sample = ", ".join(it["members"][:5]) + ("…" if it["size"] > 5 else "")
        text.append(f"  Community {it['id']} ({it['size']} nodes): {sample}")
    return _fmt({"communities": items}, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Sampling/LLM status", "readOnlyHint": True})
def graphify_sampling_status(ctx: Context, as_json: bool = False) -> str:
    """Capability test: how can semantic naming be produced in this session?

    Reports whether the connected client supports host-LLM **sampling** (so the
    server needs no API key), whether a backend **API key** is configured as a
    fallback, and which method graphify_label_communities will pick.
    """
    sampling = _client_supports_sampling(ctx)
    backend = _detect_backend()
    cli = shutil.which(GRAPHIFY_BIN) is not None
    if sampling:
        method = "sampling"
        advice = "graphify_label_communities() will use the host LLM — no API key needed."
    elif backend and cli:
        method = "cli"
        advice = (
            f"Host sampling unsupported; the '{backend}' backend key will be used via "
            'graphify_label_communities(method="cli").'
        )
    else:
        method = "placeholder"
        advice = (
            "No host sampling and no backend key — names stay as 'Community N'. "
            "Set GEMINI_API_KEY / OPENAI_API_KEY / ... or connect a sampling-capable client."
        )
    payload = {
        "host_sampling_supported": sampling,
        "backend_key_detected": backend,
        "graphify_cli_available": cli,
        "preferred_method": method,
        "advice": advice,
    }
    text = (
        f"Host LLM sampling : {'SUPPORTED' if sampling else 'not supported'}\n"
        f"Backend API key   : {backend or 'none detected'}\n"
        f"graphify CLI      : {'available' if cli else 'missing'}\n"
        f"-> preferred method: {method}\n{advice}"
    )
    return _fmt(payload, as_json, text)


@mcp.tool(
    annotations={
        "title": "Name communities (host LLM / key)",
        "readOnlyHint": False,
        "destructiveHint": False,
    }
)
async def graphify_label_communities(
    ctx: Context,
    method: str = "auto",
    limit: int = 12,
    sample_size: int = 18,
    as_json: bool = False,
) -> str:
    """Give the Leiden communities human-readable names.

    Args:
        method: "auto" -> host-LLM sampling if the client supports it, else a
            configured backend key (graphify CLI), else "Community N" placeholders.
            "sampling" -> force host-LLM sampling (no API key needed).
            "cli" -> force the graphify backend (GEMINI_API_KEY/OPENAI_API_KEY/...
            or a local ollama). "placeholder" -> no LLM at all.
        limit: Only the largest `limit` communities are named, to stay cheap.
        sample_size: Member labels per community handed to the model.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, _ = _nodes_edges(graph)
    comms: dict[Any, list[str]] = {}
    for n in nodes:
        c = n.get("community", n.get("cluster"))
        if c is not None:
            comms.setdefault(c, []).append(_node_label(n))
    if not comms:
        return "Nodes carry no community info. Try graphify_build(cluster_only=True)."
    ordered = sorted(comms.items(), key=lambda kv: -len(kv[1]))[:limit]

    sampling_ok = _client_supports_sampling(ctx)
    chosen = method
    if method == "auto":
        if sampling_ok:
            chosen = "sampling"
        elif _detect_backend() and shutil.which(GRAPHIFY_BIN):
            chosen = "cli"
        else:
            chosen = "placeholder"

    names: dict[Any, str] = {}
    note = ""
    if chosen == "sampling":
        if not sampling_ok:
            return (
                "ERROR: method='sampling' but the connected client does not support MCP "
                "sampling. Use method='cli' with a backend key, or call "
                "graphify_sampling_status() to see the options."
            )
        for cid, members in ordered:
            prompt = (
                "Name this software module in 2-4 words from its members. "
                "Reply with ONLY the title.\nMembers: " + ", ".join(members[:sample_size])
            )
            try:
                res = await ctx.session.create_message(
                    messages=[
                        SamplingMessage(
                            role="user",
                            content=TextContent(type="text", text=prompt),
                        )
                    ],
                    system_prompt="You label code modules with a concise Title Case name.",
                    max_tokens=24,
                )
                txt = res.content.text if isinstance(res.content, TextContent) else str(res.content)
                names[cid] = txt.strip().strip('".') or f"Community {cid}"
            except Exception as e:  # noqa: BLE001 - degrade per-community, keep going
                names[cid] = f"Community {cid}"
                note = f"(some names fell back: {type(e).__name__})"
    elif chosen == "cli":
        out = _run_cli(["label", str(PROJECT_DIR)])
        if out.startswith("ERROR"):
            return (
                out + "\n\nNo usable backend for method='cli'. Set GEMINI_API_KEY / "
                "OPENAI_API_KEY / ... (or run ollama), or use a sampling-capable client "
                "with method='sampling'."
            )
        labels = _read_labels()
        names = {cid: labels.get(str(cid), f"Community {cid}") for cid, _ in ordered}
    else:  # placeholder
        names = {cid: f"Community {cid}" for cid, _ in ordered}

    items = [
        {"id": cid, "name": names[cid], "size": len(members), "members": members[:5]}
        for cid, members in ordered
    ]
    payload = {
        "method": chosen,
        "host_sampling_supported": sampling_ok,
        "labeled": len(items),
        "total_communities": len(comms),
        "communities": items,
    }
    head = (
        f"Named the {len(items)} largest of {len(comms)} communities via '{chosen}'"
        + (f" {note}" if note else "")
        + ":"
    )
    text = [head] + [
        f"  [{it['id']}] {it['name']}  ({it['size']} nodes: {', '.join(it['members'])})"
        for it in items
    ]
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Search nodes", "readOnlyHint": True})
def graphify_search(pattern: str, limit: int = 25, as_json: bool = False) -> str:
    """Search nodes by text in their name/label (case-insensitive)."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    p = pattern.lower()
    hits = [n for n in nodes if p in _node_label(n).lower() or p in _node_id(n).lower()]
    if not hits:
        return f"No nodes match '{pattern}'."
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    items = [
        {"node": _node_label(n), "type": n.get("type", ""), "degree": degree.get(_node_id(n), 0)}
        for n in hits[:limit]
    ]
    text = [f"{len(hits)} matches (first {limit}):"]
    for it in items:
        t = f" [{it['type']}]" if it["type"] else ""
        text.append(f"  {it['node']}{t} — degree {it['degree']}")
    return _fmt({"matches": items, "total": len(hits)}, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Node neighbors", "readOnlyHint": True})
def graphify_neighbors(node: str, as_json: bool = False) -> str:
    """List the direct (1-hop) neighbors of a node, with relations."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    n = _resolve_node(nodes, node)
    if n is None:
        return f"No node matching '{node}'. Try graphify_search."
    nid = _node_id(n)
    labels = {_node_id(x): _node_label(x) for x in nodes}
    adj = _adjacency(edges)
    neigh = [{"node": labels.get(t, t), "relation": rel} for t, rel in adj.get(nid, [])]
    text = [f"{_node_label(n)} has {len(neigh)} neighbors:"]
    text += [f"  —{x['relation']}→ {x['node']}" for x in neigh]
    return _fmt({"node": _node_label(n), "neighbors": neigh}, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Token-budgeted subgraph", "readOnlyHint": True})
def graphify_subgraph(
    node: str, hops: int = 2, budget_tokens: int = 1500, as_json: bool = False
) -> str:
    """Extract a BFS subgraph around a node, capped at a token budget.

    This is the token-cheap way to hand the model just the relevant slice of a
    large codebase instead of the whole graph.

    Args:
        node: Center node (exact or fuzzy match).
        hops: BFS depth from the center.
        budget_tokens: Approximate cap on returned size; expansion stops when hit.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    start = _resolve_node(nodes, node)
    if start is None:
        return f"No node matching '{node}'. Try graphify_search."
    labels = {_node_id(x): _node_label(x) for x in nodes}
    adj = _adjacency(edges)
    sid = _node_id(start)

    visited = {sid}
    frontier = deque([(sid, 0)])
    collected_edges: list[dict] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    truncated = False
    running_chars = 2  # the enclosing "[]" of the JSON array

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
                # Maintain a running size estimate instead of re-serializing the
                # whole list on every edge (which is O(n^2)). +2 ≈ the ", " separator.
                running_chars += len(json.dumps(edge, ensure_ascii=False)) + 2
            if nb not in visited:
                visited.add(nb)
                frontier.append((nb, depth + 1))
            # budget check (O(1))
            if running_chars // 4 >= budget_tokens:
                truncated = True
                frontier.clear()
                break

    payload = {
        "center": _node_label(start),
        "hops": hops,
        "nodes": len(visited),
        "edges": collected_edges,
        "truncated": truncated,
        "approx_tokens": _approx_tokens(json.dumps(collected_edges)),
    }
    text = [
        f"Subgraph around {_node_label(start)} (≤{hops} hops, "
        f"~{payload['approx_tokens']} tokens"
        + (", TRUNCATED at budget" if truncated else "") + "):",
        f"{len(visited)} nodes, {len(collected_edges)} edges\n",
    ]
    text += [f"  {e['from']} —{e['relation']}→ {e['to']}" for e in collected_edges]
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Node details", "readOnlyHint": True})
def graphify_node_details(node: str, as_json: bool = False) -> str:
    """Show a node's full metadata: type, source file/line, docstring, community."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, _ = _nodes_edges(graph)
    n = _resolve_node(nodes, node)
    if n is None:
        return f"No node matching '{node}'. Try graphify_search."
    # Common metadata keys across graphify schema variants.
    detail = {
        "id": _node_id(n),
        "label": _node_label(n),
        "type": n.get("type", ""),
        "file": n.get("file") or n.get("path") or n.get("source_file", ""),
        "line": _node_line(n),
        "community": n.get("community", n.get("cluster", "")),
        "doc": n.get("doc") or n.get("docstring") or n.get("summary") or n.get("description", ""),
    }
    # include any other interesting keys verbatim
    extra = {k: v for k, v in n.items() if k not in {
        "id", "name", "label", "type", "file", "path", "source_file",
        "line", "lineno", "start_line", "source_location", "community", "cluster",
        "doc", "docstring", "summary", "description",
    }}
    if extra:
        detail["extra"] = extra
    loc = f"{detail['file']}:{detail['line']}" if detail["file"] else "(no source location)"
    text = [
        f"{detail['label']} [{detail['type'] or 'node'}]",
        f"  location : {loc}",
        f"  community: {detail['community']}",
        f"  doc      : {detail['doc'] or '(none)'}",
    ]
    if extra:
        text.append(f"  other    : {', '.join(extra.keys())}")
    return _fmt(detail, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Graph freshness", "readOnlyHint": True})
def graphify_freshness(as_json: bool = False) -> str:
    """Check whether graph.json is stale relative to the current git HEAD.

    Prefers the commit graphify recorded the graph was built from
    (``built_at_commit``) over the file mtime — robust across checkouts where
    mtime is reset — and flags both modified and newly-added (untracked) files.
    Recommends graphify_build(update=True) if stale.
    """
    gp = _graph_path()
    if not gp.exists():
        return "graph.json missing. Run graphify_build first."
    graph_mtime = gp.stat().st_mtime
    head = _git(["rev-parse", "HEAD"])
    payload: dict[str, Any] = {"graph_exists": True, "git": head is not None}
    if head is None:
        return _fmt(payload, as_json,
                    "graph.json exists, but this is not a git repo (or git unavailable).")

    # Modified AND untracked files — `git diff --name-only HEAD` misses new files.
    # Skip graphify's own output dir so an un-gitignored graphify-out/ doesn't
    # mark the graph perpetually stale.
    status = _git(["status", "--porcelain"]) or ""
    changed_files = []
    for line in status.splitlines():
        f = line[3:].strip()
        if f and f != OUT_DIR_NAME and not f.startswith(OUT_DIR_NAME + "/"):
            changed_files.append(f)

    # Prefer the commit graphify built the graph from; fall back to mtime vs commit.
    built_at = None
    g = _load_graph()
    if isinstance(g, dict):
        built_at = g.get("built_at_commit")
    if built_at:
        behind = not (head.startswith(built_at) or built_at.startswith(head))
        commit_reason = "graph was built from an older commit" if behind else None
    else:
        commit_ts = _git(["log", "-1", "--format=%ct"])
        commit_time = float(commit_ts) if commit_ts else 0.0
        behind = commit_time > graph_mtime
        commit_reason = "HEAD commit is newer than the graph" if behind else None

    stale = behind or bool(changed_files)
    payload.update({
        "head": head[:10],
        "built_at_commit": built_at[:10] if built_at else None,
        "graph_mtime": graph_mtime,
        "stale": stale,
        "uncommitted_or_untracked_files": changed_files[:50],
        "recommendation": "graphify_build(update=True)" if stale else "graph is fresh",
    })
    if not stale:
        text = f"Graph is fresh (HEAD {head[:10]}, no newer commit or pending changes)."
    else:
        why = []
        if commit_reason:
            why.append(commit_reason)
        if changed_files:
            why.append(f"{len(changed_files)} uncommitted/untracked file(s) changed")
        text = (
            f"Graph is STALE: {', '.join(why)}.\n"
            f"Run graphify_build(update=True) to re-sync."
        )
    return _fmt(payload, as_json, text)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("graphify://report")
def report() -> str:
    """GRAPH_REPORT.md — core nodes, surprises and suggested questions."""
    rp = _out_dir() / "GRAPH_REPORT.md"
    if not rp.exists():
        return f"GRAPH_REPORT.md missing ({rp}). Run graphify_build first."
    return rp.read_text(encoding="utf-8")


@mcp.resource("graphify://graph")
def graph_json() -> str:
    """graph.json — the persistent, queryable graph (raw JSON)."""
    gp = _graph_path()
    if not gp.exists():
        return f"graph.json missing ({gp}). Run graphify_build first."
    return gp.read_text(encoding="utf-8")


@mcp.resource("graphify://community/{community_id}")
def community(community_id: str) -> str:
    """Per-community wiki: every node in one Leiden community, with its edges."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)

    def cid(n: dict) -> str:
        return str(n.get("community", n.get("cluster", "")))

    members = [n for n in nodes if cid(n) == str(community_id)]
    if not members:
        return f"No community '{community_id}'. See graphify_communities for valid ids."
    member_ids = {_node_id(n) for n in members}
    labels = {_node_id(n): _node_label(n) for n in nodes}
    internal, boundary = [], []
    for e in edges:
        s, t = _edge_ends(e)
        if s in member_ids and t in member_ids:
            internal.append(e)
        elif s in member_ids or t in member_ids:
            boundary.append(e)
    lines = [f"# Community {community_id} — {len(members)} nodes\n", "## Members"]
    for n in members:
        ty = f" ({n.get('type')})" if n.get("type") else ""
        lines.append(f"- {_node_label(n)}{ty}")
    lines.append(f"\n## Internal edges ({len(internal)})")
    for e in internal:
        s, t = _edge_ends(e)
        lines.append(f"- {labels.get(s, s)} —{_edge_rel(e)}→ {labels.get(t, t)}")
    lines.append(f"\n## Boundary edges to other communities ({len(boundary)})")
    for e in boundary[:50]:
        s, t = _edge_ends(e)
        lines.append(f"- {labels.get(s, s)} —{_edge_rel(e)}→ {labels.get(t, t)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompts (reusable templates that orchestrate the tools)
# ---------------------------------------------------------------------------


@mcp.prompt()
def onboard() -> str:
    """Orient yourself to this codebase using the knowledge graph."""
    return (
        "Help me understand this codebase using the graphify tools.\n"
        "1. Call graphify_overview to get the lay of the land.\n"
        "2. Call graphify_communities to see the major subsystems.\n"
        "3. For the top 2-3 god nodes, call graphify_subgraph to see how they connect.\n"
        "4. Call graphify_surprises and flag anything that looks like a hidden coupling.\n"
        "Then write me a concise architecture summary: subsystems, key types, and risks."
    )


@mcp.prompt()
def trace_bug(symptom: str) -> str:
    """Investigate a bug symptom by tracing it through the graph."""
    return (
        f"I'm debugging this symptom: {symptom}\n"
        "1. Use graphify_search to find nodes related to the symptom.\n"
        "2. Use graphify_subgraph around the most relevant node to see what it touches.\n"
        "3. Use graphify_path between suspect nodes to find the call/data route.\n"
        "4. Check graphify_surprises for unexpected couplings that could explain it.\n"
        "Give me a ranked list of likely root-cause locations with reasoning."
    )


@mcp.prompt()
def explain_flow(flow: str) -> str:
    """Explain how a named flow or feature works end to end."""
    return (
        f"Explain how the '{flow}' flow works in this codebase.\n"
        "1. graphify_query the flow to find its entry points.\n"
        "2. graphify_subgraph around the entry point (hops=2) for the surrounding structure.\n"
        "3. graphify_node_details on each key node for source locations.\n"
        "Produce a step-by-step walkthrough with file:line references."
    )


def main() -> None:
    """Console-script entry point.

    Transport is selected by GRAPHIFY_TRANSPORT (default ``stdio``); ``sse`` and
    ``streamable-http`` serve over HTTP on GRAPHIFY_HOST:GRAPHIFY_PORT. Any HTTP
    transport force-enables path containment (GRAPHIFY_RESTRICT_PATHS), since the
    build tool would otherwise let a network client extract arbitrary paths.
    """
    if TRANSPORT in ("streamable-http", "http", "sse"):
        global RESTRICT_PATHS
        RESTRICT_PATHS = True
        mcp.settings.host = HTTP_HOST
        mcp.settings.port = HTTP_PORT
        mcp.run(transport="sse" if TRANSPORT == "sse" else "streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
