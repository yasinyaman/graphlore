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

import ast
import json
import os
import shutil
import subprocess
import sys
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
# Opt-in bearer auth for the HTTP transports: when set, every HTTP/WS request must
# carry ``Authorization: Bearer <GRAPHIFY_API_KEY>``. Unset = today's behaviour
# (rely on binding to localhost or a fronting proxy).
API_KEY = os.environ.get("GRAPHIFY_API_KEY", "")

# Tool surface: "full" (default, all tools) | "lean" (core exploration set only).
# A smaller surface can help models pick the right tool; opt-in so the documented
# full surface is unchanged by default.
TOOLSET = os.environ.get("GRAPHIFY_TOOLSET", "full").strip().lower()
# A coherent, mostly dependency-free core that still supports the whole documented
# flow: build -> orient (overview) -> find (search) -> traverse (subgraph/
# neighbors) -> jump to source (node_details). graphify_locate is included too but
# needs the optional [semble] extra, so _effective_lean_tools drops it when absent.
LEAN_TOOLS = frozenset({
    "graphify_build",
    "graphify_overview",
    "graphify_locate",
    "graphify_search",
    "graphify_neighbors",
    "graphify_subgraph",
    "graphify_node_details",
    "graphify_communities",
    "graphify_freshness",
})

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

# Per-file AST span index, keyed by absolute path -> (mtime, spans). A span is
# ``(region_start, end_line, def_line, qualname)`` for each def/class:
# ``region_start`` includes decorator lines, ``def_line`` is where the graph node
# points. Used to resolve a semble chunk to its *containing* symbol by a real
# line range, instead of graphify's single ``source_location`` point. Python only
# (stdlib ``ast``, zero deps); non-Python files yield no spans and fall back to
# the point heuristic.
_SPAN_CACHE: dict[str, tuple[float, list[tuple[int, int, int, str]]]] = {}
# Bound _SPAN_CACHE: under a long-lived HTTP transport with file churn (branch
# switches, generated files, multiple projects) it would otherwise grow for the
# life of the process, retaining entries for deleted/renamed files.
_SPAN_CACHE_MAX = 4096


def _span_cache_put(key: str, value: tuple[float, list[tuple[int, int, int, str]]]) -> None:
    if key not in _SPAN_CACHE and len(_SPAN_CACHE) >= _SPAN_CACHE_MAX:
        _SPAN_CACHE.pop(next(iter(_SPAN_CACHE)), None)  # FIFO: drop the oldest entry
    _SPAN_CACHE[key] = value


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
        # rstrip only: `git status --porcelain` encodes status in leading columns
        # (e.g. " D path"), so a leading space must be preserved for parsing.
        return proc.stdout.rstrip()
    except Exception:
        return None


# Roughly ~4 chars per token; good enough for budgeting display.
def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


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
                # running size estimate instead of re-serializing the whole list (O(n^2))
                running_chars += len(json.dumps(edge, ensure_ascii=False)) + 2
            if nb not in visited:
                visited.add(nb)
                frontier.append((nb, depth + 1))
            if running_chars // 4 >= budget_tokens:
                truncated = True
                frontier.clear()
                break

    return visited, collected_edges, truncated, _approx_tokens(json.dumps(collected_edges))


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


def _norm_relpath(p: object) -> str:
    s = str(p or "").strip()
    return s[2:] if s.startswith("./") else s


# tree-sitter symbol node-type hints (generic across languages). A node counts as
# a definition only if its type matches one of these AND it exposes a "name" field —
# which filters out look-alikes (function_type, class_body, type_annotation, ...).
_TS_SYMBOL_HINTS = (
    "function", "method", "constructor", "class", "struct", "interface",
    "enum", "trait", "impl", "module", "object", "type", "subroutine",
    "procedure", "package", "namespace",
)
# NB: no "_expression" here — function_expression (a named def that carries a
# "name" field) must pass; non-definition *_expression nodes match no symbol hint
# and have no "name" field, so they are already excluded by the caller.
_TS_NON_SYMBOL_SUFFIXES = ("_type", "_body", "_parameters", "_specifier", "_clause")
# Parser cache keyed by language name; value is a parser or None (unavailable).
_TS_PARSERS: dict[str, Any] = {}


def _ts_parser_for(rel: str) -> tuple[Any, str | None]:
    """(parser, language) for a path via the optional tree-sitter backend.

    Returns (None, None) when tree-sitter / the language pack is not installed or
    the language is unknown. tree-sitter ships with graphify, so it's usually
    present; declared as the ``[treesitter]`` extra otherwise. Cached per language.
    """
    try:
        # Build a parser from the core API (Parser + Language) rather than the
        # language pack's get_parser() wrapper — the wrapper's parse() signature
        # has churned across pack releases, while Parser(language).parse(bytes) is
        # stable. tree-sitter ships with graphify.
        from tree_sitter import Parser
        from tree_sitter_language_pack import detect_language_from_path, get_language
    except Exception:
        return None, None
    try:
        lang = detect_language_from_path(rel)
    except Exception:
        lang = None
    if not lang:
        return None, None
    if lang not in _TS_PARSERS:
        try:
            _TS_PARSERS[lang] = Parser(get_language(lang))
        except Exception:
            _TS_PARSERS[lang] = None
    return _TS_PARSERS[lang], lang


def _is_ts_symbol(node_type: str) -> bool:
    t = node_type.lower()
    if t.endswith(_TS_NON_SYMBOL_SUFFIXES):
        return False
    return any(h in t for h in _TS_SYMBOL_HINTS)


def _spans_python(src: bytes) -> list[tuple[int, int, int, str]]:
    """Decorator-aware def/class spans from Python source (stdlib ast).

    ``region_start`` includes decorator lines; ``def_line`` is the ``def``/``class``
    line a graph node's ``source_location`` points at.
    """
    spans: list[tuple[int, int, int, str]] = []
    try:
        tree = ast.parse(src)
    except Exception:
        return spans

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                def_line = child.lineno
                decos = [d.lineno for d in getattr(child, "decorator_list", [])]
                region_start = min([def_line, *decos])
                end = getattr(child, "end_lineno", def_line) or def_line
                spans.append((region_start, end, def_line, qual))
                walk(child, qual + ".")
            else:
                walk(child, prefix)

    try:
        walk(tree, "")
    except RecursionError:
        pass
    return spans


def _ts_region_start(child: Any, def_line: int) -> int:
    """Extend a symbol's region upward over its leading doc-comment / decorator /
    annotation siblings (Go/Java/JS doc style), mirroring Python's decorator-aware
    region — so a chunk that starts on the doc comment still resolves to the symbol
    it documents (instead of falling outside the span)."""
    start = def_line
    prev = child.prev_named_sibling
    while prev is not None:
        ptype = prev.type.lower()
        if not ("comment" in ptype or "decorator" in ptype or "annotation" in ptype):
            break
        prev_end = prev.end_point[0] + 1
        if prev_end < start - 1:  # a blank-line gap -> detached, not a doc comment
            break
        start = prev.start_point[0] + 1
        prev = prev.prev_named_sibling
    return start


def _spans_treesitter(src: bytes, rel: str) -> list[tuple[int, int, int, str]]:
    """def/class/etc. spans for any tree-sitter-supported language (optional dep).

    Generic: a named node whose type hints at a definition and that exposes a
    ``name`` field becomes a span ``(region_start, end, def_line, qualname)``,
    where ``region_start`` absorbs leading doc-comment/decorator/annotation lines
    and the qualname chains enclosing named symbols. Empty when the tree-sitter
    backend or the language is unavailable (caller then uses the point heuristic).
    """
    parser, _lang = _ts_parser_for(rel)
    if parser is None:
        return []
    try:
        root = parser.parse(src).root_node
    except Exception:
        return []
    spans: list[tuple[int, int, int, str]] = []

    def walk(node: Any, prefix: str) -> None:
        for child in node.named_children:
            is_sym = _is_ts_symbol(child.type)
            name_node = child.child_by_field_name("name") if is_sym else None
            if name_node is not None:
                qual = f"{prefix}{name_node.text.decode('utf-8', 'replace')}"
                def_line = child.start_point[0] + 1
                end = child.end_point[0] + 1
                spans.append((_ts_region_start(child, def_line), end, def_line, qual))
                walk(child, qual + ".")
            elif is_sym and (type_node := child.child_by_field_name("type")) is not None:
                # a definition-like block with no `name` field but a `type` field —
                # e.g. Rust `impl Pool { ... }` — contributes its type to the
                # qualname chain (so methods read `Pool.acquire`) without a span.
                walk(child, f"{prefix}{type_node.text.decode('utf-8', 'replace')}.")
            else:
                walk(child, prefix)

    try:
        walk(root, "")
    except RecursionError:
        pass
    return spans


def _spans_for_file(file_path: str) -> list[tuple[int, int, int, str]]:
    """def/class/etc. spans for a source file under PROJECT_DIR, across languages.

    Returns ``[(region_start, end_line, def_line, qualname), ...]`` sorted by
    ``region_start``. Python uses stdlib ``ast`` (decorator-aware, zero deps); any
    other language uses the optional tree-sitter backend when present. Empty for
    unsupported / missing / unparseable files (cached either way so a broken file
    isn't re-parsed). Cached by (path, mtime).
    """
    rel = _norm_relpath(file_path)
    if not rel:
        return []
    # Confine to PROJECT_DIR: this is the only code that reads a source file from
    # a (semble-supplied) chunk path, so an absolute or ``..`` path must not parse
    # files outside the project, even though the output is only line/name metadata.
    try:
        full = (PROJECT_DIR / rel).resolve()
        full.relative_to(PROJECT_DIR.resolve())
    except (ValueError, OSError):
        return []
    try:
        mtime = full.stat().st_mtime
    except OSError:
        return []
    key = str(full)
    cached = _SPAN_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    # Read bytes so a BOM / coding cookie is honored. Span extraction is
    # best-effort: any failure degrades to [] so the caller falls back to the
    # point heuristic rather than breaking the whole locate.
    try:
        src = full.read_bytes()
    except OSError:
        _span_cache_put(key, (mtime, []))
        return []
    spans = _spans_python(src) if rel.lower().endswith(".py") else _spans_treesitter(src, rel)
    spans.sort(key=lambda s: s[0])
    _span_cache_put(key, (mtime, spans))
    return spans


def _enclosing_spans(
    file_path: str, line: int, end_line: int
) -> list[tuple[int, int, int, str]]:
    """Spans enclosing a semble chunk, most-specific first.

    If a symbol's real range contains the chunk *start*, return those
    innermost-first (decorator-aware true containment). Otherwise the chunk
    starts in module-level code spanning several defs — return the symbols whose
    definitions begin inside the chunk, earliest first (the first symbol the
    chunk introduces), never a tiny method that only the chunk's tail grazes.
    """
    spans = _spans_for_file(file_path)
    if not spans:
        return []
    containing = [s for s in spans if s[0] <= line <= s[1]]
    if containing:
        # innermost = smallest region; tie-break the later (more specific) def
        containing.sort(key=lambda s: (s[1] - s[0], -s[2]))
        return containing
    in_chunk = [s for s in spans if line < s[2] <= end_line]
    in_chunk.sort(key=lambda s: s[2])  # earliest definition first
    return in_chunk


def _span_qualname(file_path: str, line: int, end_line: int | None = None) -> str | None:
    """Fully-qualified name of the most specific symbol enclosing a chunk, if any."""
    hi = end_line if end_line is not None else line
    spans = _enclosing_spans(file_path, line, hi)
    return spans[0][3] if spans else None


def _node_for_location(
    nodes: list[dict], file_path: str, line: int, end_line: int | None = None
) -> dict | None:
    """Map a semble chunk ``(file_path, line[..end_line])`` to its graph node.

    Resolution order:
      1. **Span containment** (Python source available): find the most specific
         def/class whose real line range encloses the chunk, then the code node
         that *owns* that symbol; walk outward to the next enclosing symbol that
         has a node (so a chunk inside a nested closure with no node resolves to
         the enclosing function that does). A node owns the span that most
         tightly encloses its *own* line, so a node whose ``source_location``
         points into a body (LLM-origin nodes) still binds to its real symbol,
         not to a nested child or an enclosing class it merely sits inside.
         Decorator-aware and using real end-lines, this never misattributes a
         chunk to a previous function that already ended — graphify nodes carry
         only a single ``source_location`` point, with no span of their own.
      2. **Point heuristic** (fallback: non-Python, no source on disk, or no
         enclosing span carried a node): prefer a ``file_type == "code"`` node,
         pick the enclosing definition (greatest line <= start), else a def
         starting inside the chunk, else the closest. ``None`` only if the file
         has no nodes.
    """
    target = _norm_relpath(file_path)
    if not target:
        return None
    same_file = [
        n for n in nodes
        if _norm_relpath(n.get("file") or n.get("path") or n.get("source_file")) == target
    ]
    if not same_file:
        return None
    hi = end_line if end_line is not None else line
    code = [n for n in same_file if str(n.get("file_type", "")).lower() == "code"]

    # 1. canonical span join: a node belongs to the span most tightly enclosing its
    #    own line; resolve to the node owned by the most specific span enclosing the
    #    chunk, walking outward to the nearest enclosing symbol that owns a node.
    spans = _spans_for_file(file_path)
    if spans and code:
        def _owning_def(ln: int) -> int | None:
            here = [s for s in spans if s[0] <= ln <= s[1]]
            return min(here, key=lambda s: (s[1] - s[0], -s[2]))[2] if here else None

        owner: dict[int, int | None] = {}
        for n in code:
            try:
                owner[id(n)] = _owning_def(int(_node_line(n)))
            except (TypeError, ValueError):
                owner[id(n)] = None
        for _rs, _end, def_line, _qual in _enclosing_spans(file_path, line, hi):
            owned = [n for n in code if owner.get(id(n)) == def_line]
            if owned:
                return min(owned, key=lambda n: abs(int(_node_line(n)) - def_line))
        # spans exist but none own a code node -> fall through to point heuristic

    def _pick(cands: list[dict]) -> dict | None:
        lined: list[tuple[dict, int]] = []
        for n in cands:
            try:
                lined.append((n, int(_node_line(n))))
            except (TypeError, ValueError):
                pass
        if not lined:
            return cands[0] if cands else None
        encl = [(n, ln) for n, ln in lined if ln <= line]
        if encl:
            return max(encl, key=lambda x: x[1])[0]
        in_chunk = [(n, ln) for n, ln in lined if line < ln <= hi]
        if in_chunk:
            return min(in_chunk, key=lambda x: x[1])[0]
        return min(lined, key=lambda x: abs(x[1] - line))[0]

    return _pick(code) or _pick(same_file)


def _semble_index() -> Any:
    """Return a semble index for PROJECT_DIR, or None if the optional dep is absent."""
    try:
        from semble import SembleIndex
    except ImportError:
        return None
    return SembleIndex.from_path(str(PROJECT_DIR))


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
    except (json.JSONDecodeError, UnicodeDecodeError):
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

    suggested = [
        f"graphify_subgraph(\"{god[0]['node']}\")" if god else "graphify_communities()",
        "graphify_communities()",
        "graphify_surprises()",
    ]
    # Don't steer toward a tool the active surface has dropped (e.g. lean mode).
    active = _registered_tool_names()
    if active:
        suggested = [s for s in suggested if s.split("(", 1)[0] in active]
    payload = {
        "nodes": len(nodes),
        "edges": len(edges),
        "communities": len(comms),
        "surprise_edges": surprises,
        "id_collisions": id_collisions,
        "god_nodes": god,
        "suggested_next": suggested,
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
    if suggested:
        lines.append("\nSuggested next steps: " + "; ".join(suggested))
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
            and comm.get(_edge_ends(e)[1]) is not None
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
            "Name them yourself with graphify_set_labels (assistant-driven, no key), or "
            "set GEMINI_API_KEY / OPENAI_API_KEY / ... or run a local ollama."
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
                "sampling. Name them yourself with graphify_set_labels (assistant-driven, "
                "no key/sampling needed), use method='cli' with a backend key/ollama, or "
                "call graphify_sampling_status() for the options."
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
    if chosen == "placeholder":
        text.append(
            "\nNo automatic naming available. Name these yourself and persist them with "
            'graphify_set_labels({"<id>": "<name>", ...}).'
        )
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Set community names", "destructiveHint": False})
def graphify_set_labels(
    names: dict[str, str], regenerate: bool = True, as_json: bool = False
) -> str:
    """Persist assistant-provided community names — the sampling-free way to name
    communities in clients without MCP sampling.

    The calling assistant is already an LLM in the loop: it names the communities
    itself (e.g. from graphify_communities members) and pushes them here. Names are
    written to graphify-out/.graphify_labels.json and, when regenerate=True, baked
    into the existing graph.html in place so the visualization shows them.

    Args:
        names: {community_id: name}, e.g. {"0": "Authentication", "2": "Test server"}.
        regenerate: True -> also patch graph.html with the new names (if it exists).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, _ = _nodes_edges(graph)
    valid_ids = {
        str(c) for c in (n.get("community", n.get("cluster")) for n in nodes) if c is not None
    }
    provided = {str(k): str(v) for k, v in names.items()}
    applied = {k: v for k, v in provided.items() if k in valid_ids}
    unknown = [k for k in provided if k not in valid_ids]
    if not applied:
        sample = sorted(valid_ids, key=lambda x: (len(x), x))[:6]
        return (
            f"No valid community ids in {list(provided)}. Ids come from "
            f"graphify_communities (e.g. {sample})."
        )

    # 1) update the label store (source of truth)
    labels = _read_labels() or {cid: f"Community {cid}" for cid in valid_ids}
    labels.update(applied)
    (_out_dir() / ".graphify_labels.json").write_text(
        json.dumps(labels, ensure_ascii=False), encoding="utf-8"
    )

    # 2) patch graph.html in place (quoted-exact: '"Community 1"' != '"Community 10"')
    gh = _out_dir() / "graph.html"
    patched = None
    viz_note = "graph.html not found (built with --no-viz?) — labels saved, viz unchanged."
    if regenerate and gh.exists():
        try:
            html: str | None = gh.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            html = None
        if html is None:
            viz_note = "graph.html has invalid encoding — labels saved, viz left unchanged."
        else:
            patched = 0
            for cid, nm in applied.items():
                old = f'"Community {cid}"'
                patched += html.count(old)
                html = html.replace(old, json.dumps(nm, ensure_ascii=False))
            gh.write_text(html, encoding="utf-8")
            viz_note = (
                f"graph.html patched ({patched} spots)." if patched else
                "graph.html has no 'Community N' placeholders (already named or a "
                "different format) — labels saved, viz unchanged."
            )

    payload = {
        "labeled": len(applied),
        "total_communities": len(valid_ids),
        "unknown_ids": unknown,
        "graph_html_patched": patched,
        "names": applied,
    }
    lines = [f"Set {len(applied)} community name(s); .graphify_labels.json updated."]
    if regenerate:
        lines.append(viz_note)
    if unknown:
        lines.append(f"Ignored unknown ids: {', '.join(unknown)}")
    return _fmt(payload, as_json, "\n".join(lines))


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

    visited, collected_edges, truncated, approx_tokens = _bfs_subgraph(
        adj, labels, sid, hops, budget_tokens
    )

    payload = {
        "center": _node_label(start),
        "hops": hops,
        "nodes": len(visited),
        "edges": collected_edges,
        "truncated": truncated,
        "approx_tokens": approx_tokens,
    }
    text = [
        f"Subgraph around {_node_label(start)} (≤{hops} hops, "
        f"~{payload['approx_tokens']} tokens"
        + (", TRUNCATED at budget" if truncated else "") + "):",
        f"{len(visited)} nodes, {len(collected_edges)} edges\n",
    ]
    text += [f"  {e['from']} —{e['relation']}→ {e['to']}" for e in collected_edges]
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations={"title": "Locate + structural context", "readOnlyHint": True})
def graphify_locate(
    query: str,
    top_k: int = 3,
    hops: int = 2,
    budget_tokens: int = 1500,
    related_k: int = 8,
    as_json: bool = False,
) -> str:
    """Semantic search (semble) -> graph structure, in one call, with a cross-check.

    Finds the code most relevant to `query`, maps the top hit to its enclosing
    graph node, returns the token-budgeted subgraph around it, AND lists
    semantically-similar code elsewhere — flagging `hidden_links`: cousins that
    are similar but NOT structurally connected to the seed (duplication /
    missing-abstraction / implicit-coupling candidates). Needs the optional
    `semble` extra: pip install 'graphify-mcp[semble]'.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)

    index = _semble_index()
    if index is None:
        return (
            "ERROR: graphify_locate needs the optional 'semble' extra. "
            "Install with: pip install 'graphify-mcp[semble]'."
        )
    hits = index.search(query, top_k=top_k)
    if not hits:
        return f"No semantic matches for '{query}'."

    def _loc(h: Any) -> tuple[str, int, int]:
        c = h.chunk
        return str(c.file_path), int(c.start_line), int(c.end_line)

    semantic_hits = []
    for h in hits:
        fp, sl, el = _loc(h)
        n = _node_for_location(nodes, fp, sl, el)
        semantic_hits.append(
            {"file": fp, "lines": f"{sl}-{el}", "node": _node_label(n) if n else None}
        )

    fp0, sl0, el0 = _loc(hits[0])
    seed = _node_for_location(nodes, fp0, sl0, el0)
    if seed is None:
        payload = {
            "query": query,
            "seed": None,
            "semantic_hits": semantic_hits,
            "note": "top hit did not map to a graph node; showing semantic results only",
        }
        text = f"Top match {fp0}:{sl0} has no graph node. Semantic hits:\n" + "\n".join(
            f"  {h['file']}:{h['lines']}" for h in semantic_hits
        )
        return _fmt(payload, as_json, text)

    labels = {_node_id(x): _node_label(x) for x in nodes}
    adj = _adjacency(edges)
    seed_id = _node_id(seed)
    visited, sub_edges, truncated, tokens = _bfs_subgraph(
        adj, labels, seed_id, hops, budget_tokens
    )
    distmap = _hop_distances(adj, seed_id, max(hops, 4))

    cousins = []
    seen_nodes = {seed_id}
    for r in index.find_related(hits[0], top_k=related_k):
        fp, sl, el = _loc(r)
        cn = _node_for_location(nodes, fp, sl, el)
        if cn is None:
            continue
        cid = _node_id(cn)
        if cid in seen_nodes:
            continue
        seen_nodes.add(cid)
        d = distmap.get(cid)
        cousins.append(
            {
                "node": _node_label(cn),
                "file": fp,
                "lines": f"{sl}-{el}",
                "distance": d if d is not None else "unreachable",
                "linked": d is not None and d <= hops,
            }
        )

    def _rank(c: dict) -> tuple[int, int]:
        # reachable production parallels first (nearest distance first); 'unreachable'
        # cousins (often test-file noise) sink to the bottom.
        d = c["distance"]
        return (1, 0) if d == "unreachable" else (0, int(d))

    hidden = sorted((c for c in cousins if not c["linked"]), key=_rank)

    seed_file = seed.get("file") or seed.get("path") or seed.get("source_file") or ""
    # FQN of the RESOLVED seed node (its own line), not the chunk's innermost symbol:
    # when resolution walked outward to an enclosing function, the qualname must name
    # that function, never a deeper closure that carries no node.
    try:
        seed_qual = _span_qualname(str(seed_file), int(_node_line(seed)))
    except (TypeError, ValueError):
        seed_qual = None
    seed_obj = {"node": _node_label(seed), "file": seed_file, "line": _node_line(seed)}
    if seed_qual and seed_qual != _node_label(seed):
        seed_obj["qualname"] = seed_qual  # span-recovered FQN, e.g. Client._send_single_request
    payload = {
        "query": query,
        "seed": seed_obj,
        "structure": {
            "nodes": len(visited),
            "edges": sub_edges,
            "truncated": truncated,
            "approx_tokens": tokens,
        },
        "semantic_hits": semantic_hits,
        "semantic_cousins": cousins,
        "hidden_links": hidden,
    }
    text = [
        f"Query: {query!r}",
        f"Seed: {_node_label(seed)}"
        + (f" [{seed_qual}]" if seed_qual and seed_qual != _node_label(seed) else "")
        + f"  ({seed_file}:{_node_line(seed)})",
        f"Structure: {len(visited)} nodes, {len(sub_edges)} edges"
        + (" (TRUNCATED)" if truncated else ""),
    ]
    if hidden:
        text.append(f"\nHidden links — similar but structurally distant ({len(hidden)}):")
        text += [
            f"  {c['node']}  ({c['file']}:{c['lines']})  distance={c['distance']}"
            for c in hidden
        ]
    linked = [c for c in cousins if c["linked"]]
    if linked:
        text.append(f"\nCousins already connected ({len(linked)}):")
        text += [f"  {c['node']}  distance={c['distance']}" for c in linked]
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


def _ts_skeleton(root: Any) -> tuple | None:
    """A structural fingerprint of a tree-sitter tree that ignores comments (and,
    since whitespace isn't a token, formatting).

    Walks ALL children — anonymous operator/keyword/punctuation tokens included —
    so that an operator flip (``+``→``-``, ``==``→``!=``, ``&&``→``||``), a
    ``sync``→``async`` or ``let``→``const`` change, and any rename/value edit are
    all structural; only comment and whitespace edits compare equal. ``None`` on
    overflow."""
    parts: list = []

    def walk(n: Any) -> None:
        if "comment" in n.type:  # comments are cosmetic in every grammar
            return
        if n.child_count == 0:
            parts.append((n.type, bytes(n.text)))  # leaf (named OR anonymous token)
        else:
            parts.append((n.type,))
            for c in n.children:
                walk(c)

    try:
        walk(root)
    except RecursionError:
        return None
    return tuple(parts)


def _structurally_equal(rel: str, old_src: Any, new_src: Any) -> bool | None:
    """True if two source versions differ only cosmetically.

    Python uses ``ast.dump`` equality; any other language uses a comment-stripped
    tree-sitter skeleton. ``None`` when it can't be determined (unparseable, or no
    backend for the language).
    """
    if rel.lower().endswith(".py"):
        try:
            return ast.dump(ast.parse(old_src)) == ast.dump(ast.parse(new_src))
        except (SyntaxError, ValueError, RecursionError):
            return None
    parser, _lang = _ts_parser_for(rel)
    if parser is None:
        return None

    def _b(s: Any) -> bytes:
        return s if isinstance(s, (bytes, bytearray)) else str(s).encode("utf-8", "replace")

    try:
        a = _ts_skeleton(parser.parse(_b(old_src)).root_node)
        b = _ts_skeleton(parser.parse(_b(new_src)).root_node)
    except Exception:
        return None
    if a is None or b is None:
        return None
    return a == b


def _ast_equivalent(path: str, ref: str) -> bool | None:
    """True if ``path``'s working tree differs only cosmetically from git ``ref``.

    A cosmetic change — comments, blank lines, reformatting — leaves graph
    structure intact (Python docstrings live in the AST, so a docstring edit is
    structural). Python is compared via ``ast``; other languages via a
    comment-stripped tree-sitter skeleton (optional dep). Returns ``None`` when
    the comparison can't be made (file absent at ``ref``, unreadable, unparseable,
    or no language backend), so the caller treats it as a structural change.

    Note: the comparison ignores line numbers, so a cosmetic edit that shifts code
    down (e.g. a comment added at the top) leaves nodes' ``source_location`` lines
    slightly stale until the next build. That's by design — the graph *structure*
    is unchanged, and graphify_locate re-resolves locations from real spans at
    query time — but it's why "fresh" here means structurally, not line-, current.
    """
    old_src = _git(["show", f"{ref}:{path}"])
    if old_src is None:
        return None
    try:
        new_src = (PROJECT_DIR / path).read_bytes()
    except OSError:
        return None
    return _structurally_equal(path, old_src, new_src)


@mcp.tool(annotations={"title": "Graph freshness", "readOnlyHint": True})
def graphify_freshness(as_json: bool = False) -> str:
    """Check whether graph.json is stale relative to the current git HEAD.

    Prefers the commit graphify recorded the graph was built from
    (``built_at_commit``) over the file mtime — robust across checkouts where
    mtime is reset — and flags both modified and newly-added (untracked) files.

    Returns a ``recommended_action`` (fresh / update / rebuild) with a ``reason``:
    deletions, renames, or a large change set call for a full rebuild, since
    incremental update can't drop nodes for code that no longer exists.
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
    # `-z`: NUL-separated with paths printed verbatim. Default porcelain C-quotes
    # paths containing spaces or non-ASCII bytes (e.g. `"my file.py"`), which would
    # leave the literal quotes in the path and break the `git show ref:path` AST
    # diff below (every such file would look structurally changed).
    status = _git(["status", "--porcelain", "-z"]) or ""
    changed_files: list[str] = []
    removed: list[str] = []  # deleted/renamed -> old nodes linger under incremental update
    fields = iter(status.split("\0"))
    for entry in fields:
        if not entry:
            continue  # trailing empty field after the final NUL separator
        code = entry[:2]
        path = entry[3:]  # "XY <path>"; verbatim, no unquoting needed
        old = path
        # `-z` emits a rename/copy as two fields — new path, then original path —
        # not the `old -> new` of default porcelain. Consume the paired field.
        if "R" in code or "C" in code:
            old = next(fields, path)
        if path == OUT_DIR_NAME or path.startswith(OUT_DIR_NAME + "/"):
            continue
        changed_files.append(path)
        if "D" in code or "R" in code:
            removed.append(old)

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

    # Classify pending changes: cosmetic (comment/whitespace/format-only, AST-equal
    # to HEAD) vs structural. Cosmetic-only edits don't change the graph, so they
    # shouldn't drive an update/rebuild. Skip the per-file AST diff for a large set —
    # that already routes to a full rebuild below.
    cosmetic: list[str] = []
    structural: list[str] = list(changed_files)
    if changed_files and len(changed_files) <= 25:
        cosmetic, structural = [], []
        for f in changed_files:
            (cosmetic if _ast_equivalent(f, head) is True else structural).append(f)

    stale = behind or bool(structural)

    # Pick an action. Incremental `update` never shrinks the graph, so deletions/
    # renames (or a large change set) need a full rebuild to avoid phantom nodes.
    if not stale:
        if cosmetic:
            action = "fresh"
            reason = (
                f"only cosmetic changes ({len(cosmetic)} file(s): comments/whitespace/"
                "formatting, AST-identical to HEAD) — no regraph needed"
            )
        else:
            action, reason = "fresh", "graph matches HEAD with no pending changes"
    elif removed:
        action = "rebuild"
        reason = (
            f"{len(removed)} file(s) deleted/renamed — incremental update keeps phantom "
            "nodes for removed code, so a full rebuild is recommended"
        )
    elif len(structural) > 25:
        action = "rebuild"
        reason = f"{len(structural)} files changed (large change set) — full rebuild is safer"
    else:
        action = "update"
        bits = [commit_reason] if commit_reason else []
        if structural:
            extra = f" ({len(cosmetic)} cosmetic skipped)" if cosmetic else ""
            bits.append(f"{len(structural)} file(s) changed structurally, no deletions{extra}")
        reason = "; ".join(bits) or "graph is behind HEAD"
    command = {
        "fresh": "graph is fresh",
        "update": "graphify_build(update=True)",
        "rebuild": 'graphify_build(".")  # full rebuild',
    }[action]

    payload.update({
        "head": head[:10],
        "built_at_commit": built_at[:10] if built_at else None,
        "graph_mtime": graph_mtime,
        "stale": stale,
        "uncommitted_or_untracked_files": changed_files[:50],
        "structural_changes": structural[:50],
        "cosmetic_changes": cosmetic[:50],
        "deleted_or_renamed": removed[:50],
        "recommended_action": action,
        "reason": reason,
        "recommendation": command,
    })
    if not stale:
        suffix = f" ({len(cosmetic)} cosmetic-only change(s) ignored)" if cosmetic else ""
        text = f"Graph is fresh (HEAD {head[:10]}, no structural changes){suffix}."
    else:
        text = f"Graph is STALE: {reason}.\nRecommended: {command}"
    return _fmt(payload, as_json, text)


@mcp.tool(annotations={"title": "Validate graph", "readOnlyHint": True})
def graphify_validate(limit: int = 15, as_json: bool = False) -> str:
    """Lint graph.json for structural problems (read-only).

    Reports edges whose endpoints aren't in the node set (dangling), duplicate
    edges, self-loops, and orphan (degree-0) nodes — so you know how much to
    trust the graph or whether a rebuild is warranted. Does not modify anything.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph
    nodes, edges = _nodes_edges(graph)
    node_ids = {_node_id(n) for n in nodes}
    labels = {_node_id(n): _node_label(n) for n in nodes}

    dangling: list[dict] = []
    self_loops: list[dict] = []
    duplicates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        rel = _edge_rel(e)
        degree[s] += 1
        degree[t] += 1
        missing = [x for x in (s, t) if x not in node_ids]
        if missing:
            dangling.append({"from": s, "to": t, "relation": rel, "missing": missing})
        if s == t:
            self_loops.append({"node": labels.get(s, s), "relation": rel})
        key = (s, t, rel)
        if key in seen:
            duplicates.append({"from": labels.get(s, s), "to": labels.get(t, t), "relation": rel})
        else:
            seen.add(key)
    orphans = [_node_label(n) for n in nodes if degree.get(_node_id(n), 0) == 0]

    issues = {
        "dangling_edges": len(dangling),
        "self_loops": len(self_loops),
        "duplicate_edges": len(duplicates),
        "orphan_nodes": len(orphans),
    }
    total = sum(issues.values())
    payload = {
        "nodes": len(nodes),
        "edges": len(edges),
        "total_issues": total,
        "healthy": total == 0,
        "issues": issues,
        "examples": {
            "dangling": dangling[:limit],
            "self_loops": self_loops[:limit],
            "duplicate_edges": duplicates[:limit],
            "orphan_nodes": orphans[:limit],
        },
    }
    if total == 0:
        text = (
            f"Graph looks healthy: {len(nodes)} nodes, {len(edges)} edges, "
            "no dangling/duplicate/self-loop edges or orphan nodes."
        )
    else:
        lines = [f"{total} structural issue(s) in {len(nodes)} nodes / {len(edges)} edges:"]
        if dangling:
            lines.append(f"  {len(dangling)} dangling edge(s) (endpoint not in node set), e.g.:")
            lines += [
                f"    {labels.get(d['from'], d['from'])} —{d['relation']}→ "
                f"{labels.get(d['to'], d['to'])}  (missing: {', '.join(d['missing'])})"
                for d in dangling[:5]
            ]
        if self_loops:
            lines.append(f"  {len(self_loops)} self-loop(s)")
        if duplicates:
            lines.append(f"  {len(duplicates)} duplicate edge(s)")
        if orphans:
            lines.append(
                f"  {len(orphans)} orphan node(s) (degree 0), e.g.: " + ", ".join(orphans[:8])
            )
        text = "\n".join(lines)
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


def _bearer_auth_asgi(app: Any, api_key: str) -> Any:
    """Wrap an ASGI app to require ``Authorization: Bearer <api_key>``.

    Enforced on HTTP and WebSocket scopes (lifespan passes through). The token is
    compared in constant time; failure returns 401 without invoking the app.
    """
    import hmac

    # Compare raw bytes: an Authorization header may contain any byte, and
    # hmac.compare_digest raises TypeError on a non-ASCII str — which would turn a
    # bad credential into a 500 instead of a clean 401.
    expected = b"Bearer " + api_key.encode("utf-8")

    async def guarded(scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"")
            if not hmac.compare_digest(provided, expected):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    })
                    await send({"type": "http.response.body", "body": b"Unauthorized\n"})
                return
        await app(scope, receive, send)

    return guarded


def _registered_tool_names() -> set[str]:
    """Names of tools currently registered (reflects any GRAPHIFY_TOOLSET trim)."""
    try:
        return {t.name for t in mcp._tool_manager.list_tools()}
    except Exception:  # pragma: no cover - guards against private-attr changes
        return set()


def _effective_lean_tools() -> set[str]:
    """LEAN_TOOLS minus tools whose optional dependency is absent.

    graphify_locate needs the [semble] extra; in a default install it would only
    return an install-this error, so it's dropped from the lean surface rather than
    advertised as a core tool.
    """
    import importlib.util

    lean = set(LEAN_TOOLS)
    if importlib.util.find_spec("semble") is None:
        lean.discard("graphify_locate")
    return lean


def _lean_removals(names: list[str], lean: set[str] | frozenset[str] = LEAN_TOOLS) -> list[str]:
    """Tool names to drop for the lean surface (everything outside ``lean``)."""
    return [n for n in names if n not in lean]


def _apply_toolset() -> None:
    """If GRAPHIFY_TOOLSET=lean, unregister the non-core tools (no-op otherwise)."""
    if TOOLSET != "lean":
        return
    lean = _effective_lean_tools()
    for name in _lean_removals(list(_registered_tool_names()), lean):
        mcp.remove_tool(name)


def main() -> None:
    """Console-script entry point.

    Transport is selected by GRAPHIFY_TRANSPORT (default ``stdio``); ``sse`` and
    ``streamable-http`` serve over HTTP on GRAPHIFY_HOST:GRAPHIFY_PORT. Any HTTP
    transport force-enables path containment (GRAPHIFY_RESTRICT_PATHS), since the
    build tool would otherwise let a network client extract arbitrary paths. Set
    GRAPHIFY_API_KEY to require bearer auth on HTTP; GRAPHIFY_TOOLSET=lean trims the
    surface to the core exploration tools.
    """
    _apply_toolset()
    if TRANSPORT in ("streamable-http", "http", "sse"):
        global RESTRICT_PATHS
        RESTRICT_PATHS = True
        mcp.settings.host = HTTP_HOST
        mcp.settings.port = HTTP_PORT
        transport = "sse" if TRANSPORT == "sse" else "streamable-http"
        if API_KEY:
            import uvicorn

            base = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
            app = _bearer_auth_asgi(base, API_KEY)
            uvicorn.run(
                app, host=HTTP_HOST, port=HTTP_PORT,
                log_level=mcp.settings.log_level.lower(),
            )
        else:
            if HTTP_HOST not in ("127.0.0.1", "localhost", "::1"):
                print(
                    f"WARNING: serving HTTP on {HTTP_HOST} without GRAPHIFY_API_KEY — "
                    "anyone who can reach this port can drive the server. Set "
                    "GRAPHIFY_API_KEY to require bearer auth.",
                    file=sys.stderr,
                )
            mcp.run(transport=transport)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
