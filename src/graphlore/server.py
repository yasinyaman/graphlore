#!/usr/bin/env python3
"""Graphify MCP Server — exposes the graphify CLI and graph as MCP tools.

Wraps graphify (https://graphify.net) so an AI assistant can query the
codebase knowledge graph during development.

CLI-backed tools:
  - graphlore_build      : build / update the graph from a folder
  - graphlore_query      : natural-language graph query
  - graphlore_path       : path between two nodes
  - graphlore_explain    : full explanation of a single node
  - graphlore_add        : add an external source by URL (paper, tweet)

graph.json analysis tools (no CLI needed):
  - graphlore_overview   : one-shot orientation (call this first)
  - graphlore_locate     : semantic search (semble) -> enclosing node -> token-budgeted
                          subgraph + hidden_links  [needs the optional [semble] extra]
  - graphlore_duplication_scan: repo-wide hidden-link / duplication audit  [needs [semble]]
  - graphlore_god_nodes  : highest-degree nodes
  - graphlore_surprises  : unexpected cross-domain connections
  - graphlore_communities: Leiden community summaries
  - graphlore_search     : node name/label search
  - graphlore_neighbors  : 1-hop neighbors of a node
  - graphlore_subgraph   : token-budgeted BFS subgraph around a node
  - graphlore_impact     : reverse-dependency / blast-radius (what breaks if this changes)
  - graphlore_node_details: node detail with source file/line refs
  - graphlore_fetch       : hydrate nodes into their real source code (token-budgeted)
  - graphlore_skeleton    : def/class signatures (bodies stripped) for a file/node/community
  - graphlore_freshness  : is the graph stale vs git HEAD? (cosmetic-vs-structural aware)
  - graphlore_diff       : structural changeset between two git refs (file-level)
  - graphlore_prune      : drop phantom nodes for deleted/renamed source files
  - graphlore_validate   : lint graph.json (dangling / duplicate / self-loop / orphan)
  - graphlore_cycles     : circular dependencies (SCCs) in the directed graph
  - graphlore_package_apis: symbol-level external API surface (which names each
                          package is actually used for — upgrade-audit input)
  - graphlore_routes     : framework route -> handler table (FastAPI/Flask/Django,
                          Express/NestJS, gin/chi/net-http, Spring)

Community naming:
  - graphlore_label_communities : name Leiden clusters (host-LLM sampling / backend key)
  - graphlore_sampling_status   : report which naming options are available
  - graphlore_set_labels        : assistant-pushed {id: name} (no key, no sampling)

Resources:
  - graphlore://report          : GRAPH_REPORT.md
  - graphlore://graph           : graph.json
  - graphlore://community/{id}  : per-community wiki

Prompts:
  - onboard      : orient an assistant to the codebase
  - trace_bug    : investigate a symptom through the graph
  - explain_flow : explain how a named flow/feature works

Internal layout: config.py (shared PROJECT_DIR), graph.py (graph.json load +
node/edge/traversal helpers), spans.py (tree-sitter/ast span engine + structural
diff), and this module (the MCPServer surface: tools, resources, prompts, main).

Usage:
  GRAPHLORE_PROJECT_DIR=/path/to/repo python server.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Protocol, overload

import anyio.to_thread
from mcp import MCPDeprecationWarning
from mcp.server.mcpserver import Context, MCPServer, Resolve, Sample
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    ClientCapabilities,
    CreateMessageResult,
    SamplingCapability,
    SamplingMessage,
    TextContent,
    ToolAnnotations,
)

from . import config
from .apis import (  # noqa: F401  (re-exported for the tools + tests)
    _API_CACHE,
    _API_CACHE_MAX,
    _api_uses_for_file,
    _api_uses_python,
)
from .graph import (  # noqa: F401  (re-exported for the tools + tests)
    _CHARS_PER_TOKEN,
    _GRAPH_CACHE,
    _PAYLOAD_ENVELOPE_CHARS,
    _adjacency,
    _approx_tokens,
    _bfs_subgraph,
    _count_tokens,
    _directed_adjacency,
    _edge_ends,
    _edge_rel,
    _edge_set,
    _find_cycles,
    _graph_path,
    _hop_distances,
    _is_surprise_edge,
    _load_graph,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _nodes_edges,
    _out_dir,
    _resolve_node,
)
from .routes import (  # noqa: F401  (re-exported for the tools + tests)
    _ROUTES_CACHE,
    _ROUTES_CACHE_MAX,
    _routes_for_file,
    _routes_python,
    routes_for_source,
)
from .spans import (  # noqa: F401
    _SPAN_CACHE,
    _SPAN_CACHE_MAX,
    _TS_PARSERS,
    _enclosing_spans,
    _is_ts_symbol,
    _node_for_location,
    _norm_relpath,
    _resolve_in_project,
    _span_qualname,
    _spans_for_file,
    _spans_python,
    _spans_treesitter,
    _structurally_equal,
    _ts_parser_for,
    _ts_skeleton,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

__version__ = "0.2.0"

GRAPHIFY_BIN = config.env("BIN", "graphify")
CLI_TIMEOUT = int(config.env("TIMEOUT", "600"))

# Opt-in: confine graphlore_build's `path` to config.PROJECT_DIR. Off by default so the
# documented absolute/sibling-repo path keeps working; force-enabled for HTTP.
RESTRICT_PATHS = config.env("RESTRICT_PATHS", "").lower() in ("1", "true", "yes")

# Transport: "stdio" (default) | "streamable-http" | "sse". HTTP binds HOST:PORT.
TRANSPORT = config.env("TRANSPORT", "stdio").lower()
HTTP_HOST = config.env("HOST", "127.0.0.1")
HTTP_PORT = int(config.env("PORT", "8000"))
# Opt-in bearer auth for the HTTP transports: when set, every HTTP/WS request must
# carry ``Authorization: Bearer <GRAPHLORE_API_KEY>``. Unset = today's behaviour
# (rely on binding to localhost or a fronting proxy).
API_KEY = config.env("API_KEY", "")

# Tool surface: "full" (default, all tools) | "lean" (core exploration set only)
# | "locate" (minimal locate-first surface). A smaller surface can help models
# pick the right tool; opt-in so the documented full surface is unchanged by
# default.
TOOLSET = config.env("TOOLSET", "full").strip().lower()
# A coherent, mostly dependency-free core that still supports the whole documented
# flow: build -> orient (overview) -> find (search) -> traverse (subgraph/
# neighbors) -> jump to source (node_details). graphlore_locate is included too but
# needs the optional [semble] extra, so _effective_lean_tools drops it when absent.
LEAN_TOOLS = frozenset({
    "graphlore_build",
    "graphlore_overview",
    "graphlore_locate",
    "graphlore_search",
    "graphlore_neighbors",
    "graphlore_subgraph",
    "graphlore_node_details",
    "graphlore_communities",
    "graphlore_freshness",
})
# Mega-tool-style surface: one way in (locate), one way to the code (fetch), plus
# the minimum to stay oriented and in sync. Requires a semantic backend — without
# one the server falls back to the lean surface at boot (see
# _effective_toolset_tools) rather than advertising a locate tool that can only
# return an install hint.
LOCATE_TOOLS = frozenset({
    "graphlore_locate",
    "graphlore_fetch",
    "graphlore_overview",
    "graphlore_build",
    "graphlore_freshness",
})
# None = no trim (full surface). Unknown GRAPHLORE_TOOLSET values behave as full.
TOOLSETS: dict[str, frozenset[str] | None] = {
    "full": None,
    "lean": LEAN_TOOLS,
    "locate": LOCATE_TOOLS,
}

mcp = MCPServer(
    "graphlore",
    version=__version__,
    instructions=(
        "Graphify knowledge graph tools for understanding a codebase.\n"
        "Recommended flow:\n"
        "  1. Call graphlore_overview first for orientation.\n"
        "  2. To find code by what it DOES, call graphlore_locate('<natural-language "
        "question>') — one call returns the enclosing node, its token-budgeted "
        "subgraph, and hidden_links (similar-but-disconnected code).\n"
        "  3. Otherwise use graphlore_subgraph / graphlore_neighbors / graphlore_query "
        "for targeted, token-cheap exploration around a node or question.\n"
        "  4. graphlore_build (with update=True) re-syncs after code changes.\n"
        "Most analysis tools read graph.json directly and are read-only; only "
        "graphlore_build and graphlore_add modify state. Pass as_json=True on "
        "analysis tools when you want structured output to chain on."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path_escapes_project(path: str) -> str | None:
    """Opt-in containment for a build path.

    Returns an error string if GRAPHLORE_RESTRICT_PATHS is set and `path` resolves
    outside config.PROJECT_DIR; otherwise None. Off by default so the documented
    absolute / sibling-repo path keeps working.
    """
    if not RESTRICT_PATHS:
        return None
    p = Path(path)
    resolved = (p if p.is_absolute() else config.PROJECT_DIR / p).resolve()
    try:
        resolved.relative_to(config.PROJECT_DIR)
    except ValueError:
        return (
            f"ERROR: path '{path}' escapes the project directory ({config.PROJECT_DIR}); "
            "GRAPHLORE_RESTRICT_PATHS is enabled. Unset it or pass a contained path."
        )
    return None


def _fmt(payload: Any, as_json: bool, text: str) -> str:
    """Return structured JSON or a human-readable string."""
    if as_json:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return text


def _err(message: str, as_json: bool) -> str:
    """Error / no-match reply that honors ``as_json``.

    The documented contract is "pass as_json=True … to chain on", so a structured
    consumer running json.loads on the reply must get parseable JSON on EVERY
    path — error and no-match included — not bare prose.
    """
    if as_json:
        return json.dumps({"error": message}, ensure_ascii=False)
    return message


def _run_cli(args: list[str], cwd: Path | None = None) -> str:
    """Run the graphify CLI and return stdout+stderr."""
    if shutil.which(GRAPHIFY_BIN) is None:
        return (
            f"ERROR: '{GRAPHIFY_BIN}' not found. Install with: pip install graphifyy && "
            "graphify install. Alternatively set the GRAPHLORE_BIN environment variable."
        )
    try:
        # Argument list + shell=False (the default): every element is passed as a
        # literal argv entry, so a build `path` or query string can never inject a
        # shell command. Never switch this to a joined string / shell=True.
        proc = subprocess.run(
            [GRAPHIFY_BIN, *args],
            cwd=str(cwd or config.PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command did not finish within {CLI_TIMEOUT}s: graphify {' '.join(args)}"
    except OSError as e:
        # shutil.which succeeded but exec failed (broken shebang after a venv
        # move, wrong-arch binary, missing cwd) — a clean error, not a traceback.
        return f"ERROR: failed to run '{GRAPHIFY_BIN}': {e}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"ERROR (exit {proc.returncode}):\n{err or out}"
    return out + (f"\n[stderr]\n{err}" if err else "")


def _git(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(config.PROJECT_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        # rstrip only: `git status --porcelain` encodes status in leading columns
        # (e.g. " D path"), so a leading space must be preserved for parsing.
        return proc.stdout.rstrip()
    except Exception:
        return None


def _graph_age() -> str | None:
    """Lightweight 'how stale is the graph' note for embedding in frequent tool
    outputs, so staleness is visible even without a separate graphlore_freshness
    call. Git-only and cheap (no AST/structural diff). Returns None when it can't
    be determined (no recorded build commit, or not a git repo)."""
    g = _load_graph()
    if not isinstance(g, dict):
        return None
    built_at = g.get("built_at_commit")
    if not built_at or not isinstance(built_at, str):
        return None
    head = _git(["rev-parse", "HEAD"])
    if head is None:
        return None
    if head.startswith(built_at) or built_at.startswith(head):
        return "built at HEAD"
    if _git(["cat-file", "-e", f"{built_at}^{{commit}}"]) is None:
        return "built at an unreachable commit (rebuild recommended)"
    ahead = _git(["rev-list", "--count", f"{built_at}..HEAD"])
    if ahead and ahead.isdigit() and int(ahead) > 0:
        n = int(ahead)
        return f"built {n} commit{'' if n == 1 else 's'} ago"
    return "built at a divergent commit"


class SemanticIndex(Protocol):
    """Pluggable semantic-search backend (semble is the default).

    Implement this and point ``GRAPHLORE_SEMANTIC_BACKEND`` at ``your.module:Factory``
    to swap in a stronger backend — local sentence-transformers, an OpenAI-compatible
    or on-prem vLLM endpoint, etc. ``Factory`` is called ``Factory.from_path(project)``
    (or ``Factory(project)``). Each result of both methods MUST expose
    ``.chunk.file_path`` / ``.chunk.start_line`` / ``.chunk.end_line``, so the graph
    join (``_node_for_location``) keeps working regardless of backend.
    """

    def search(self, query: str, top_k: int = 3) -> list: ...

    def find_related(self, hit: Any, top_k: int = 8) -> list: ...


def _semble_index() -> Any:
    """Return a semble index for config.PROJECT_DIR, or None if the optional dep is absent."""
    try:
        from semble import SembleIndex
    except ImportError:
        return None
    try:
        return SembleIndex.from_path(str(config.PROJECT_DIR))
    except Exception:  # noqa: BLE001 - corrupt/incompatible on-disk index degrades to "no index"
        return None


def _load_custom_semantic_index(spec: str) -> Any:
    """Load a custom SemanticIndex from a ``module.path:Factory`` spec.

    Returns the constructed index, or None if the spec is malformed, the module/attr
    can't be imported, or construction raises — so locate/duplication_scan degrade the
    same way a missing optional dep does, instead of crashing.
    """
    if ":" not in spec:
        return None
    mod_name, _, attr = spec.partition(":")
    try:
        import importlib

        factory = getattr(importlib.import_module(mod_name), attr)
    except Exception:  # noqa: BLE001 - anything raised while importing degrades like a missing dep
        return None
    try:
        ctor = factory.from_path if hasattr(factory, "from_path") else factory
        return ctor(str(config.PROJECT_DIR))
    except Exception:  # noqa: BLE001 - any backend init failure degrades to "no index"
        return None


def _semantic_index() -> Any:
    """The active semantic index, dispatched by ``GRAPHLORE_SEMANTIC_BACKEND``.

    Default (unset or ``semble``) keeps today's offline behaviour. Any other value is
    treated as a ``module.path:Factory`` spec implementing :class:`SemanticIndex`. The
    semble path stays a separate ``_semble_index`` call so it remains the offline-first
    default and so tests can stub it directly.
    """
    backend = config.env("SEMANTIC_BACKEND", "").strip()
    if not backend or backend.lower() == "semble":
        return _semble_index()
    return _load_custom_semantic_index(backend)


def _no_semantic_index_error(tool: str) -> str:
    """Why _semantic_index() returned None, with the right fix for the active config.

    With a custom GRAPHLORE_SEMANTIC_BACKEND configured, the failure is that spec —
    telling the user to install semble would misdiagnose it (semble may even be
    installed but deliberately bypassed).
    """
    backend = config.env("SEMANTIC_BACKEND", "").strip()
    if backend and backend.lower() != "semble":
        return (
            f"ERROR: {tool}: semantic backend '{backend}' failed to load. Check the "
            "GRAPHLORE_SEMANTIC_BACKEND 'module.path:Factory' spec (or unset it to use "
            "the default semble backend)."
        )
    return (
        f"ERROR: {tool} needs the optional 'semble' extra. "
        "Install with: pip install 'graphlore[semble]'."
    )


class _DisplayLabels:
    """Node-id -> display-label map that qualifies ambiguous labels lazily.

    graphify labels methods bare (``.auth_flow()``), so distinct nodes across
    classes/files render identically in subgraph arrows and node lists. For a
    label shared by more than one node, the first render resolves the
    span-recovered FQN (``DigestAuth.auth_flow()``); when no span is available
    it falls back to ``label (file:Lline)``, and a node with no source file at
    all keeps its bare label. Unique labels — the overwhelming majority — are
    returned untouched with no span work, and resolution is memoized, so only
    the ambiguous ids that actually get rendered pay the parse cost (served
    from the bounded span cache thereafter).
    """

    def __init__(self, nodes: list[dict]) -> None:
        self._label: dict[str, str] = {}
        self._node: dict[str, dict] = {}
        counts: Counter[str] = Counter()
        for n in nodes:
            nid = _node_id(n)
            self._label[nid] = lbl = _node_label(n)
            self._node[nid] = n
            counts[lbl] += 1
        self._counts = counts
        self._resolved: dict[str, str] = {}

    # dict.get-style overloads: with a str default the result is always str,
    # so call sites like `labels.get(n, n)` type-check as str.
    @overload
    def get(self, nid: str) -> str | None: ...
    @overload
    def get(self, nid: str, default: str) -> str: ...

    def get(self, nid: str, default: str | None = None) -> str | None:
        base = self._label.get(nid)
        if base is None:
            return default
        if self._counts.get(base, 0) <= 1:
            return base
        got = self._resolved.get(nid)
        if got is None:
            self._resolved[nid] = got = self._qualified(nid, base)
        return got

    def __getitem__(self, nid: str) -> str:
        got = self.get(nid)
        if got is None:
            raise KeyError(nid)
        return got

    def __contains__(self, nid: str) -> bool:
        return nid in self._label

    def __len__(self) -> int:
        return len(self._label)

    def _qualified(self, nid: str, base: str) -> str:
        n = self._node[nid]
        file, line = _node_file(n), _node_line(n)
        if not file:
            return base  # nothing to qualify by
        qual = None
        try:
            if line not in (None, ""):
                qual = _span_qualname(str(file), int(line))
        except Exception:
            qual = None
        if qual and qual != base:
            # keep the call-marker convention of method labels: `.foo()` -> `Bar.foo()`
            return f"{qual}()" if base.startswith(".") and base.endswith("()") else qual
        loc = f"{file}:L{line}" if line not in (None, "") else str(file)
        return f"{base} ({loc})"


# Same id(nodes)+identity-guard scheme as graph._ADJ_CACHE: _load_graph hands back
# the SAME nodes list while the parsed graph stays cached, so repeat calls reuse
# one _DisplayLabels (and its memoized qualifications) instead of rebuilding the
# map in every tool body.
_LABELS_CACHE: dict[int, tuple[list[dict], _DisplayLabels]] = {}
_LABELS_CACHE_MAX = 8


def _display_labels(nodes: list[dict]) -> _DisplayLabels:
    """Display-label map for `nodes`, cached on the nodes-list identity."""
    cached = _LABELS_CACHE.get(id(nodes))
    if cached is not None and cached[0] is nodes:
        return cached[1]
    labels = _DisplayLabels(nodes)
    if id(nodes) not in _LABELS_CACHE and len(_LABELS_CACHE) >= _LABELS_CACHE_MAX:
        _LABELS_CACHE.pop(next(iter(_LABELS_CACHE)), None)  # FIFO: drop the oldest
    _LABELS_CACHE[id(nodes)] = (nodes, labels)
    return labels


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
    """Capability test: does the connected MCP client offer host-LLM sampling?

    Advertising the sampling capability is sufficient on every protocol
    revision: naming goes through a `Sample` resolver, which the SDK carries
    over the legacy in-call back-channel or, on >= 2026-07-28 (where that
    back-channel is gone), as input-required rounds of the tool call itself.
    """
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
        data = json.loads(lp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    # A valid-JSON non-object (hand-edited or from another writer) must degrade
    # like a malformed file, not AttributeError in the label tools.
    return data if isinstance(data, dict) else {}


def _node_file_missing(rel: object) -> bool:
    """True if `rel` is a project-relative source path that's gone from disk.

    False for an empty path, a path that escapes config.PROJECT_DIR (can't safely
    verify, so never prune it), or a file that's still present — so only a
    genuinely-removed in-project file is treated as a phantom.
    """
    full = _resolve_in_project(rel)
    return full is not None and not full.exists()


def _files_with_nodes(nodes: list[dict], files: list[str]) -> list[str]:
    """Subset of `files` that still have at least one graph node (order-preserving).

    Lets graphlore_freshness force a rebuild only for deletions whose phantom nodes
    actually linger — so once graphlore_prune drops them, the deletion stops driving
    a rebuild.
    """
    have = {_norm_relpath(_node_file(n)) for n in nodes if _node_file(n)}
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        nf = _norm_relpath(f)
        if nf in have and nf not in seen:
            seen.add(nf)
            out.append(f)
    return out


# Extensions the graph can actually represent. A change to any other file kind
# (.DS_Store, .env, lockfiles, images, editor swap files) cannot alter graph
# structure, so it must never drive staleness or a watch-mode rebuild.
_SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".java", ".kt", ".kts", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cxx", ".hxx", ".cs", ".rb", ".php", ".swift", ".scala", ".m", ".mm",
    ".lua", ".zig", ".ex", ".exs",
})


def _graph_relevant_file(rel: object, graph_files: set[str]) -> bool:
    """Can a change to this file affect the graph?

    True when the file already has nodes in the graph, or when its extension is a
    source language the extractors understand (a NEW source file has no nodes yet
    but still matters). Everything else — junk files, configs, assets — is noise
    for freshness and watch mode.
    """
    nf = _norm_relpath(rel)
    if not nf:
        return False
    if nf in graph_files:
        return True
    dot = nf.rfind(".")
    return dot >= 0 and nf[dot:].lower() in _SOURCE_EXTS


def _read_source_lines(
    file_path: object, lo: int, hi: int
) -> tuple[list[str], int, int] | None:
    """Read lines [lo, hi] (1-indexed, inclusive, clamped to the file) of a source file.

    Returns (lines, clamped_lo, clamped_hi), or None when the path is empty, escapes
    config.PROJECT_DIR, is unreadable, or clamps to an empty range. This is the only
    place that returns raw source text, so it must not read outside the project.
    """
    full = _resolve_in_project(file_path)
    if full is None:
        return None
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    all_lines = text.splitlines()
    lo = max(1, lo)
    hi = min(len(all_lines), hi)
    if lo > hi:
        return None
    return all_lines[lo - 1:hi], lo, hi


# How many lines past the def/class line a signature may continue (black-style
# multi-line parameter lists) before _skeleton_lines gives up extending it.
_SIG_CONT_MAX = 40


def _skeleton_lines(file_path: str, prefix: str | None = None) -> list[tuple[str, list[str]]]:
    """(qualname, header_lines) for each def/class in a file — bodies stripped.

    ``header_lines`` is region_start..def_line — the decorators/annotations plus the
    def/class line — extended past the def line while its parentheses stay open, so
    a black-style multi-line signature keeps its parameters and return annotation
    instead of being cut at ``def f(``. Built on the same span engine as
    locate/fetch, so it works across languages. With ``prefix`` set, only that
    symbol and its nested members (``qual == prefix`` or ``qual`` under
    ``prefix + "."``) are returned.
    """
    out: list[tuple[str, list[str]]] = []
    for region_start, _end, def_line, qual in _spans_for_file(file_path):
        if prefix is not None and not (qual == prefix or qual.startswith(prefix + ".")):
            continue
        block = _read_source_lines(file_path, region_start, def_line + _SIG_CONT_MAX)
        if block is None:
            continue
        lines, lo, _hi = block
        header = lines[:max(1, def_line - lo + 1)]
        # Parentheses only (not braces): a Go/Java body opener `{` on the def line
        # must not drag the whole body in, while `def f(\n ...\n) -> X:` extends.
        depth = sum(ln.count("(") - ln.count(")") for ln in header)
        for ln in lines[len(header):]:
            if depth <= 0:
                break
            header.append(ln)
            depth += ln.count("(") - ln.count(")")
        out.append((qual, [ln.rstrip() for ln in header]))
    return out


# ---------------------------------------------------------------------------
# CLI wrapper tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(title="Build/update graph", destructive_hint=False))
def graphlore_build(
    path: str = ".",
    mode: str = "",
    update: bool = False,
    cluster_only: bool = False,
    no_viz: bool = True,
    code_only: bool = False,
) -> str:
    """Build or update a knowledge graph from a folder. (Writes to graphify-out/.)

    Args:
        path: Folder to extract the graph from (relative to the project dir or absolute).
        mode: "deep" -> more aggressive INFERRED edges; empty -> default.
        update: True -> re-extract only changed files and merge into the existing graph.
        cluster_only: True -> rerun clustering only, without re-extraction.
        no_viz: True -> skip the HTML visualization (faster for development).
        code_only: True -> index only code via local AST (no LLM key needed); skips
            doc/paper/image files that would otherwise require semantic extraction.
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
    if code_only:
        args.append("--code-only")
    result = _run_cli(args)
    gp = _graph_path()
    # The CLI rewrote graph.json; evict eagerly so a same-second re-read (coarse
    # mtime granularity) can't serve the pre-build graph — same guard as prune.
    _GRAPH_CACHE.pop(str(gp), None)
    if gp.exists():
        result += f"\n\ngraph.json ready: {gp}"
    return result


@mcp.tool(annotations=ToolAnnotations(title="Query graph", read_only_hint=True))
def graphlore_query(question: str, dfs: bool = False, budget: int = 0) -> str:
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


@mcp.tool(annotations=ToolAnnotations(title="Path between nodes", read_only_hint=True))
def graphlore_path(node_a: str, node_b: str) -> str:
    """Find the exact path between two nodes (e.g. "DigestAuth" -> "Response")."""
    return _run_cli(["path", node_a, node_b])


@mcp.tool(annotations=ToolAnnotations(title="Explain node", read_only_hint=True))
def graphlore_explain(node: str) -> str:
    """Return everything Graphify knows about a node."""
    return _run_cli(["explain", node])


@mcp.tool(annotations=ToolAnnotations(title="Add external source", destructive_hint=False))
def graphlore_add(url: str, author: str = "", contributor: str = "") -> str:
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


@mcp.tool(annotations=ToolAnnotations(title="Codebase overview", read_only_hint=True))
def graphlore_overview(top_n: int = 8, as_json: bool = False) -> str:
    """One-shot orientation: call this FIRST.

    Returns graph size, top god nodes, community count, surprise-edge count and
    suggested starting questions — enough to plan further exploration cheaply.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    labels = _display_labels(nodes)
    comms = {n.get("community", n.get("cluster")) for n in nodes}
    comms.discard(None)
    surprises = sum(1 for e in edges if _is_surprise_edge(e))
    # Diagnostic: distinct nodes that collapse to one id (e.g. id-less nodes
    # sharing a label) silently distort degrees/adjacency.
    id_collisions = len(nodes) - len({_node_id(n) for n in nodes})
    top = degree.most_common(top_n)
    god = [{"node": labels.get(nid, nid), "degree": d} for nid, d in top]

    suggested = [
        f"graphlore_subgraph(\"{god[0]['node']}\")" if god else "graphlore_communities()",
        "graphlore_communities()",
        "graphlore_surprises()",
    ]
    # Don't steer toward a tool the active surface has dropped (e.g. lean mode).
    active = _registered_tool_names()
    if active:
        suggested = [s for s in suggested if s.split("(", 1)[0] in active]
        # locate is the recommended way in whenever it's live — and under the
        # locate toolset it's the only suggestion left standing.
        if "graphlore_locate" in active:
            suggested.insert(0, 'graphlore_locate("<natural-language question>")')
    age = _graph_age()
    payload = {
        "nodes": len(nodes),
        "edges": len(edges),
        "communities": len(comms),
        "surprise_edges": surprises,
        "id_collisions": id_collisions,
        "graph_age": age,
        "god_nodes": god,
        "suggested_next": suggested,
    }
    lines = [
        f"{len(nodes)} nodes, {len(edges)} edges, {len(comms)} communities, "
        f"{surprises} surprise edges.\n",
        f"Top {len(god)} god nodes:",
    ]
    lines += [f"  {g['node']} — degree {g['degree']}" for g in god]
    if age:
        lines.append(f"\nGraph age: {age} (graphlore_freshness for detail).")
    if id_collisions:
        lines.append(
            f"\nWarning: {id_collisions} node id collision(s) — distinct nodes share an "
            "id/label and were merged; degrees/neighbors may be understated."
        )
    if suggested:
        lines.append("\nSuggested next steps: " + "; ".join(suggested))
    return _fmt(payload, as_json, "\n".join(lines))


@mcp.tool(annotations=ToolAnnotations(title="God nodes", read_only_hint=True))
def graphlore_god_nodes(top_n: int = 10, as_json: bool = False) -> str:
    """List the highest-degree (most connected) 'god nodes'."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    labels = _display_labels(nodes)
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


@mcp.tool(annotations=ToolAnnotations(title="Surprise edges", read_only_hint=True))
def graphlore_surprises(limit: int = 20, as_json: bool = False) -> str:
    """List unexpected cross-file/cross-domain connections (surprise edges)."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
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
    labels = _display_labels(nodes)
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


@mcp.tool(annotations=ToolAnnotations(title="Communities", read_only_hint=True))
def graphlore_communities(as_json: bool = False) -> str:
    """Summarize Leiden communities with sizes and sample members."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _ = _nodes_edges(graph)
    comms: dict[Any, list[str]] = {}
    for n in nodes:
        c = n.get("community", n.get("cluster"))
        if c is not None:
            comms.setdefault(c, []).append(_node_label(n))
    if not comms:
        return _err(
            "Nodes carry no community info. Try graphlore_build(cluster_only=True).",
            as_json,
        )
    ordered = sorted(comms.items(), key=lambda kv: -len(kv[1]))
    items = [{"id": c, "size": len(m), "members": m} for c, m in ordered]
    text = [f"{len(comms)} communities:\n"]
    for it in items:
        sample = ", ".join(it["members"][:5]) + ("…" if it["size"] > 5 else "")
        text.append(f"  Community {it['id']} ({it['size']} nodes): {sample}")
    return _fmt({"communities": items}, as_json, "\n".join(text))


@mcp.tool(annotations=ToolAnnotations(title="Sampling/LLM status", read_only_hint=True))
def graphlore_sampling_status(ctx: Context, as_json: bool = False) -> str:
    """Capability test: how can semantic naming be produced in this session?

    Reports whether the connected client supports host-LLM **sampling** (so the
    server needs no API key), whether a backend **API key** is configured as a
    fallback, and which method graphlore_label_communities will pick.
    """
    sampling = _client_supports_sampling(ctx)
    backend = _detect_backend()
    cli = shutil.which(GRAPHIFY_BIN) is not None
    if sampling:
        method = "sampling"
        advice = "graphlore_label_communities() will use the host LLM — no API key needed."
    elif backend and cli:
        method = "cli"
        advice = (
            f"Host sampling unsupported; the '{backend}' backend key will be used via "
            'graphlore_label_communities(method="cli").'
        )
    else:
        method = "placeholder"
        advice = (
            "No host sampling and no backend key — names stay as 'Community N'. "
            "Name them yourself with graphlore_set_labels (assistant-driven, no key), or "
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


def _communities_for_naming(limit: int) -> tuple[list[tuple[Any, list[str]]], int] | str:
    """Largest-first (community id, member labels) pairs to name, plus the total count.

    Returns an error/notice string when the graph is unavailable or carries no
    community info. Called from both the Sample resolver and the tool body (the
    graph read is cached). The ordering is deterministic for a given graph.json,
    which keeps the sampling prompt identical across input-required retry
    rounds — a >= 2026-07-28 protocol requirement.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return graph  # helper contract: plain string, callers wrap with _err
    nodes, _ = _nodes_edges(graph)
    comms: dict[Any, list[str]] = {}
    for n in nodes:
        c = n.get("community", n.get("cluster"))
        if c is not None:
            comms.setdefault(c, []).append(_node_label(n))
    if not comms:
        return "Nodes carry no community info. Try graphlore_build(cluster_only=True)."
    return sorted(comms.items(), key=lambda kv: -len(kv[1]))[:limit], len(comms)


def _naming_request(
    ordered: list[tuple[Any, list[str]]], sample_size: int
) -> tuple[list[SamplingMessage], str, int]:
    """The batched naming request: (messages, system_prompt, max_tokens).

    Shared by the Sample resolver (modern transport) and the tool body's
    legacy-protocol path so both send byte-identical prompts. One request
    carries every community (a JSON name map) rather than one request each:
    required on the modern transport, and a single round-trip on the legacy one.
    """
    lines = "\n".join(f"{cid}: {', '.join(m[:sample_size])}" for cid, m in ordered)
    prompt = (
        "Name each software module in 2-4 Title Case words from its member symbols. "
        "Reply with ONLY a JSON object mapping each id to its name, "
        'e.g. {"0": "Auth Layer", "1": "Graph Engine"}.\n'
        f"Modules:\n{lines}"
    )
    messages = [SamplingMessage(role="user", content=TextContent(type="text", text=prompt))]
    return messages, "You label code modules with concise Title Case names.", 48 * len(ordered) + 64


def _resolve_host_naming(
    ctx: Context, method: str = "auto", limit: int = 12, sample_size: int = 18
) -> Sample | None:
    """Resolver: the batched `sampling/createMessage` where only a resolver can.

    Used solely on protocols without an in-call back-channel (>= 2026-07-28):
    there the SDK carries the Sample marker as input-required rounds and
    injects the result as the tool's `host_naming` argument. When the session
    can send in-call requests (`can_send_request` — the legacy protocols),
    this returns None and the tool body samples directly instead: that path
    can catch a failing host model and degrade to placeholder names, which a
    resolver cannot (Sample has no error arm — a failure would error the whole
    tool call). Also None when sampling isn't the (potential) method, the
    client can't sample, or there is nothing to name.
    """
    if method not in ("auto", "sampling") or not _client_supports_sampling(ctx):
        return None
    if ctx.session.can_send_request:
        return None
    got = _communities_for_naming(limit)
    if isinstance(got, str):
        return None
    ordered, _total = got
    if not ordered:
        return None
    messages, system, max_tokens = _naming_request(ordered, sample_size)
    return Sample(messages, system_prompt=system, max_tokens=max_tokens)


def _names_from_sampling(
    res: CreateMessageResult | None, ordered: list[tuple[Any, list[str]]]
) -> tuple[dict[Any, str], str]:
    """Parse the batched naming reply; placeholders + a note for anything unusable."""
    if not ordered:
        return {}, ""
    placeholders = {cid: f"Community {cid}" for cid, _ in ordered}
    if res is None or not isinstance(res.content, TextContent):
        return placeholders, "(sampling returned no usable text; placeholder names)"
    text = res.content.text
    start, end = text.find("{"), text.rfind("}")  # tolerate ``` fences / prose around
    if start < 0 or end <= start:
        return placeholders, "(sampling reply had no JSON object; placeholder names)"
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return placeholders, "(sampling reply was not valid JSON; placeholder names)"
    names: dict[Any, str] = {}
    missing = 0
    for cid, _members in ordered:
        val = raw.get(str(cid)) if isinstance(raw, dict) else None
        name = str(val).strip().strip('".') if val is not None else ""
        if not name:
            name = f"Community {cid}"
            missing += 1
        names[cid] = name
    note = f"({missing} name(s) missing from the reply; placeholders kept)" if missing else ""
    return names, note


@mcp.tool(
    annotations=ToolAnnotations(
        title="Name communities (host LLM / key)",
        read_only_hint=False,
        destructive_hint=False,
    )
)
async def graphlore_label_communities(
    ctx: Context,
    method: str = "auto",
    limit: int = 12,
    sample_size: int = 18,
    as_json: bool = False,
    # Framework-filled (absent from the tool's input schema): the batched
    # host-LLM naming result, or None when sampling doesn't apply. Keyword-only
    # with NO default on purpose: a `= None` default makes Python 3.10's
    # get_type_hints wrap the annotation in an implicit Optional, burying the
    # Resolve marker in a union — which the SDK rejects with InvalidSignature.
    *,
    host_naming: Annotated[CreateMessageResult | None, Resolve(_resolve_host_naming)],
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
    got = _communities_for_naming(limit)
    if isinstance(got, str):
        return _err(got, as_json)
    ordered, total = got

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
            return _err(
                "ERROR: method='sampling' but the connected client does not support MCP "
                "sampling. Name them yourself with graphlore_set_labels (assistant-driven, "
                "no key/sampling needed), use method='cli' with a backend key/ollama, or "
                "call graphlore_sampling_status() for the options.",
                as_json,
            )
        fail_note = ""
        if host_naming is None and ordered and ctx.session.can_send_request:
            # Legacy protocol: sample from the body, where a failing host model
            # can be caught and degraded — the Sample resolver path can't (no
            # error arm), and on >= 2026-07-28 no in-call request is possible
            # at all, so the resolver already handled that case.
            messages, system, max_tokens = _naming_request(ordered, sample_size)
            try:
                with warnings.catch_warnings():
                    # Deliberate legacy-only use of the deprecated API; modern
                    # protocols go through the Sample resolver instead.
                    warnings.simplefilter("ignore", MCPDeprecationWarning)
                    host_naming = await ctx.session.create_message(
                        messages=messages, system_prompt=system, max_tokens=max_tokens
                    )
            except Exception as e:  # noqa: BLE001 - degrade, keep the tool alive
                fail_note = f"(sampling failed: {type(e).__name__}; placeholder names)"
        names, note = _names_from_sampling(host_naming, ordered)
        if fail_note:
            note = fail_note
    elif chosen == "cli":
        # The CLI labeling job can run for minutes; _run_cli is blocking, and an
        # async tool body executes ON the event loop (the SDK only threadpools
        # sync tools) — run it in a worker thread so the whole server (other
        # sessions' requests, pings, cancellations) doesn't freeze meanwhile.
        out = await anyio.to_thread.run_sync(_run_cli, ["label", str(config.PROJECT_DIR)])
        if out.startswith("ERROR"):
            return _err(
                out + "\n\nNo usable backend for method='cli'. Set GEMINI_API_KEY / "
                "OPENAI_API_KEY / ... (or run ollama), or use a sampling-capable client "
                "with method='sampling'.",
                as_json,
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
        "total_communities": total,
        "communities": items,
    }
    head = (
        f"Named the {len(items)} largest of {total} communities via '{chosen}'"
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
            'graphlore_set_labels({"<id>": "<name>", ...}).'
        )
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations=ToolAnnotations(title="Set community names", destructive_hint=False))
def graphlore_set_labels(
    names: dict[str, str], regenerate: bool = True, as_json: bool = False
) -> str:
    """Persist assistant-provided community names — the sampling-free way to name
    communities in clients without MCP sampling.

    The calling assistant is already an LLM in the loop: it names the communities
    itself (e.g. from graphlore_communities members) and pushes them here. Names are
    written to graphify-out/.graphify_labels.json and, when regenerate=True, baked
    into the existing graph.html in place so the visualization shows them.

    Args:
        names: {community_id: name}, e.g. {"0": "Authentication", "2": "Test server"}.
        regenerate: True -> also patch graph.html with the new names (if it exists).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _ = _nodes_edges(graph)
    valid_ids = {
        str(c) for c in (n.get("community", n.get("cluster")) for n in nodes) if c is not None
    }
    provided = {str(k): str(v) for k, v in names.items()}
    applied = {k: v for k, v in provided.items() if k in valid_ids}
    unknown = [k for k in provided if k not in valid_ids]
    if not applied:
        sample = sorted(valid_ids, key=lambda x: (len(x), x))[:6]
        return _err(
            f"No valid community ids in {list(provided)}. Ids come from "
            f"graphlore_communities (e.g. {sample}).",
            as_json,
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


@mcp.tool(annotations=ToolAnnotations(title="Search nodes", read_only_hint=True))
def graphlore_search(pattern: str, limit: int = 25, as_json: bool = False) -> str:
    """Search nodes by text in their name/label (case-insensitive)."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    p = pattern.lower()
    hits = [n for n in nodes if p in _node_label(n).lower() or p in _node_id(n).lower()]
    if not hits:
        return _err(f"No nodes match '{pattern}'.", as_json)
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


@mcp.tool(annotations=ToolAnnotations(title="Node neighbors", read_only_hint=True))
def graphlore_neighbors(node: str, as_json: bool = False) -> str:
    """List the direct (1-hop) neighbors of a node, with relations."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    n = _resolve_node(nodes, node)
    if n is None:
        return _err(f"No node matching '{node}'. Try graphlore_search.", as_json)
    nid = _node_id(n)
    labels = _display_labels(nodes)
    adj = _adjacency(edges)
    es = _edge_set(edges)
    # The adjacency is undirected; recover each edge's true orientation so an
    # incoming "calls" edge isn't drawn as if this node were the caller.
    neigh = [
        {
            "node": labels.get(t, t),
            "relation": rel,
            "direction": "out" if (nid, t, rel) in es else "in",
        }
        for t, rel in adj.get(nid, [])
    ]
    text = [f"{_node_label(n)} has {len(neigh)} neighbors:"]
    text += [
        (f"  —{x['relation']}→ {x['node']}" if x["direction"] == "out"
         else f"  ←{x['relation']}— {x['node']}")
        for x in neigh
    ]
    return _fmt({"node": _node_label(n), "neighbors": neigh}, as_json, "\n".join(text))


@mcp.tool(annotations=ToolAnnotations(title="Token-budgeted subgraph", read_only_hint=True))
def graphlore_subgraph(
    node: str, hops: int = 2, budget_tokens: int = 1500, as_json: bool = False
) -> str:
    """Extract a BFS subgraph around a node, capped at a token budget.

    This is the token-cheap way to hand the model just the relevant slice of a
    large codebase instead of the whole graph.

    Args:
        node: Center node (exact or fuzzy match).
        hops: BFS depth from the center.
        budget_tokens: Approximate cap on returned size; expansion stops when hit.
            ``approx_tokens`` is a conservative estimate (~3.5 chars/token, ±~20%);
            set ``GRAPHLORE_TOKENIZER=tiktoken`` (with the ``[tiktoken]`` extra) for an
            exact count. The cap itself stays heuristic, so it's fast either way.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    start = _resolve_node(nodes, node)
    if start is None:
        return _err(f"No node matching '{node}'. Try graphlore_search.", as_json)
    labels = _display_labels(nodes)
    adj = _adjacency(edges)
    sid = _node_id(start)

    visited, collected_edges, truncated, approx_tokens = _bfs_subgraph(
        adj, labels, sid, hops, budget_tokens, edge_set=_edge_set(edges)
    )

    age = _graph_age()
    payload = {
        "center": _node_label(start),
        "hops": hops,
        "nodes": len(visited),
        "edges": collected_edges,
        "truncated": truncated,
        "approx_tokens": approx_tokens,
        "graph_age": age,
    }
    text = [
        f"Subgraph around {_node_label(start)} (≤{hops} hops, "
        f"~{payload['approx_tokens']} est. tokens"
        + (", TRUNCATED at budget" if truncated else "") + "):"
        + (f"  [graph age: {age}]" if age else ""),
        f"{len(visited)} nodes, {len(collected_edges)} edges\n",
    ]
    text += [f"  {e['from']} —{e['relation']}→ {e['to']}" for e in collected_edges]
    return _fmt(payload, as_json, "\n".join(text))


@mcp.tool(annotations=ToolAnnotations(title="Impact / blast radius", read_only_hint=True))
def graphlore_impact(
    node: str,
    direction: str = "dependents",
    hops: int = 3,
    budget_tokens: int = 1500,
    as_json: bool = False,
) -> str:
    """Reverse-dependency / blast-radius analysis: what's affected if `node` changes.

    Edges are directed (source → target ≈ "source uses target"), which the undirected
    subgraph/neighbors flatten away. This keeps the orientation:
      - direction="dependents"   (default) -> nodes that reference `node` — what could
        break if you change it (the blast radius; reverse edges).
      - direction="dependencies"           -> what `node` itself references (forward).
      - direction="both"                    -> either, by nearest hop distance.
    Results are ordered by hop distance and capped at a token budget — a graph-native
    query pure vector/embedding retrieval can't answer. Note the blast radius includes
    any INFERRED / surprise edges in the graph, so it can be wider than call-graph-only.

    Args:
        node: Center node (exact or fuzzy match).
        direction: "dependents" | "dependencies" | "both".
        hops: Max dependency hops to walk out from `node`.
        budget_tokens: Approximate cap on the returned list; trimmed when hit.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    start = _resolve_node(nodes, node)
    if start is None:
        return _err(f"No node matching '{node}'. Try graphlore_search.", as_json)
    direction = direction.strip().lower()
    if direction not in ("dependents", "dependencies", "both"):
        return _err(
            "ERROR: direction must be 'dependents' (what references this node), "
            "'dependencies' (what this node references), or 'both'.",
            as_json,
        )
    labels = _display_labels(nodes)
    sid = _node_id(start)
    forward, reverse = _directed_adjacency(edges)

    if direction == "dependents":
        dist = _hop_distances(reverse, sid, hops)
    elif direction == "dependencies":
        dist = _hop_distances(forward, sid, hops)
    else:
        dist = dict(_hop_distances(reverse, sid, hops))
        for nid, d in _hop_distances(forward, sid, hops).items():
            if nid not in dist or d < dist[nid]:
                dist[nid] = d  # nearest hop in either direction

    ranked = sorted(
        ((nid, d) for nid, d in dist.items() if nid != sid),
        key=lambda kv: (kv[1], labels.get(kv[0], kv[0])),
    )
    impacted: list[dict[str, Any]] = []
    truncated = False
    running_chars = 2 + _PAYLOAD_ENVELOPE_CHARS
    for nid, d in ranked:
        item = {"node": labels.get(nid, nid), "distance": d}
        running_chars += len(json.dumps(item, ensure_ascii=False)) + 2
        if impacted and running_chars / _CHARS_PER_TOKEN >= budget_tokens:
            truncated = True
            break
        impacted.append(item)

    approx = _count_tokens(json.dumps(impacted, ensure_ascii=False))
    payload = {
        "node": _node_label(start),
        "direction": direction,
        "hops": hops,
        "count": len(impacted),
        "impacted": impacted,
        "truncated": truncated,
        "approx_tokens": approx,
    }
    verb = {
        "dependents": "depend on",
        "dependencies": "are used by",
        "both": "are connected to",
    }[direction]
    if not impacted:
        text = f"Nothing {verb} {_node_label(start)} within {hops} hop(s)."
    else:
        head = (
            f"{len(impacted)} node(s) {verb} {_node_label(start)} "
            f"(≤{hops} hops, direction={direction})"
            + (", TRUNCATED at budget" if truncated else "") + ":"
        )
        text = "\n".join(
            [head] + [f"  {it['node']}  (distance {it['distance']})" for it in impacted]
        )
    return _fmt(payload, as_json, text)


@mcp.tool(annotations=ToolAnnotations(title="Locate + structural context", read_only_hint=True))
def graphlore_locate(
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
    `semble` extra: pip install 'graphlore[semble]'.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)

    index = _semantic_index()
    if index is None:
        return _err(_no_semantic_index_error("graphlore_locate"), as_json)
    hits = index.search(query, top_k=top_k)
    if not hits:
        return _err(f"No semantic matches for '{query}'.", as_json)

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
        payload: dict[str, Any] = {
            "query": query,
            "seed": None,
            "semantic_hits": semantic_hits,
            "note": "top hit did not map to a graph node; showing semantic results only",
        }
        note_text = f"Top match {fp0}:{sl0} has no graph node. Semantic hits:\n" + "\n".join(
            f"  {h['file']}:{h['lines']}" for h in semantic_hits
        )
        return _fmt(payload, as_json, note_text)

    labels = _display_labels(nodes)
    adj = _adjacency(edges)
    seed_id = _node_id(seed)
    visited, sub_edges, truncated, tokens = _bfs_subgraph(
        adj, labels, seed_id, hops, budget_tokens, edge_set=_edge_set(edges)
    )
    dist_cap = max(hops, 4)
    distmap = _hop_distances(adj, seed_id, dist_cap)
    # The BFS stops at dist_cap, so a missing distance means "farther than the cap
    # OR disconnected" — never claim "unreachable" for what is only a depth cutoff.
    far_label = f">{dist_cap}"

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
                "distance": d if d is not None else far_label,
                "linked": d is not None and d <= hops,
            }
        )

    def _rank(c: dict) -> tuple[int, int]:
        # reachable production parallels first (nearest distance first); far
        # cousins (often test-file noise) sink to the bottom.
        d = c["distance"]
        return (1, 0) if isinstance(d, str) else (0, int(d))

    hidden = sorted((c for c in cousins if not c["linked"]), key=_rank)

    seed_file = seed.get("file") or seed.get("path") or seed.get("source_file") or ""
    # FQN of the RESOLVED seed node (its own line), not the chunk's innermost symbol:
    # when resolution walked outward to an enclosing function, the qualname must name
    # that function, never a deeper closure that carries no node.
    try:
        seed_qual = _span_qualname(str(seed_file), int(_node_line(seed)))
    except (TypeError, ValueError):
        seed_qual = None
    seed_obj: dict[str, Any] = {
        "node": _node_label(seed), "file": seed_file, "line": _node_line(seed),
    }
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
    text: list[str] = [
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


@mcp.tool(annotations=ToolAnnotations(title="Duplication scan", read_only_hint=True))
def graphlore_duplication_scan(
    node_budget: int = 50,
    related_k: int = 8,
    min_distance: int = 3,
    max_pairs: int = 40,
    as_json: bool = False,
) -> str:
    """Repo-wide hidden-link / duplication audit — the batch form of locate's hidden_links.

    graphlore_locate surfaces "similar but structurally disconnected" cousins around ONE
    seed; this sweeps the most-connected nodes and collects every such pair across the
    repo — duplication, missing abstraction, or sync/async twins that retrieval-only
    tools (which match "similar shape", not "similar yet structurally far") can't surface.

    For each seed it asks semble for semantically-related code, then keeps only cousins
    that are structurally far (beyond the search cap, or >= min_distance hops). Cost scales with
    node_budget (one semble round-trip per seed), so it's intentionally outside the lean
    surface — call it deliberately. Needs the optional `semble` extra.

    Note: each seed is anchored by searching its label and taking the related set — an
    approximate node→chunk bridge, since the graph stores nodes while semble stores
    chunks. Seeds whose search doesn't resolve are skipped (counted in seeds_scanned).

    Args:
        node_budget: Max seed nodes to scan (highest-degree first).
        related_k: Semantic neighbours fetched per seed.
        min_distance: Min structural hop distance for a cousin to count as "distant".
        max_pairs: Cap on reported pairs (most distant first).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    index = _semantic_index()
    if index is None:
        return _err(_no_semantic_index_error("graphlore_duplication_scan"), as_json)
    labels = _display_labels(nodes)
    adj = _adjacency(edges)
    degree: Counter[str] = Counter()
    for e in edges:
        s, t = _edge_ends(e)
        degree[s] += 1
        degree[t] += 1
    seeds = sorted(
        (n for n in nodes if _node_file(n)),
        key=lambda n: -degree.get(_node_id(n), 0),
    )[:max(0, node_budget)]

    dist_cap = max(min_distance, 6)
    far_label = f">{dist_cap}"  # beyond the BFS cap or disconnected — not proven unreachable
    pairs: dict[frozenset[str], dict[str, Any]] = {}
    scanned = 0
    for seed in seeds:
        sid = _node_id(seed)
        hits = index.search(_node_label(seed), top_k=1)
        if not hits:
            continue
        scanned += 1
        distmap = _hop_distances(adj, sid, dist_cap)
        for r in index.find_related(hits[0], top_k=related_k):
            c = r.chunk
            cn = _node_for_location(
                nodes, str(c.file_path), int(c.start_line), int(c.end_line)
            )
            if cn is None:
                continue
            cid = _node_id(cn)
            if cid == sid:
                continue
            d = distmap.get(cid)
            if d is not None and d < min_distance:
                continue  # structurally close -> a real link, not a hidden one
            key = frozenset((sid, cid))
            if key not in pairs:  # symmetric pair recorded once
                pairs[key] = {
                    "a": labels.get(sid, sid),
                    "b": labels.get(cid, cid),
                    "distance": d if d is not None else far_label,
                }

    def _rank(p: dict) -> tuple[int, int]:
        d = p["distance"]
        return (0, 0) if isinstance(d, str) else (1, -int(d))  # most distant first

    ranked = sorted(pairs.values(), key=_rank)
    shown = ranked[:max_pairs]
    payload = {
        "seeds_scanned": scanned,
        "pair_count": len(ranked),
        "pairs": shown,
        "truncated": len(ranked) > len(shown),
    }
    if not shown:
        return _fmt(
            payload, as_json,
            f"No hidden-link / duplication candidates found across {scanned} seed(s).",
        )
    head = (
        f"{len(ranked)} hidden-link candidate(s) from {scanned} seed(s)"
        + (f", showing {len(shown)}" if len(ranked) > len(shown) else "") + ":"
    )
    lines = [head] + [
        f"  {p['a']}  ~  {p['b']}   (structural distance {p['distance']})" for p in shown
    ]
    return _fmt(payload, as_json, "\n".join(lines))


@mcp.tool(annotations=ToolAnnotations(title="Node details", read_only_hint=True))
def graphlore_node_details(node: str, as_json: bool = False) -> str:
    """Show a node's full metadata: type, source file/line, docstring, community."""
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _ = _nodes_edges(graph)
    n = _resolve_node(nodes, node)
    if n is None:
        return _err(f"No node matching '{node}'. Try graphlore_search.", as_json)
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


@mcp.tool(annotations=ToolAnnotations(title="Fetch node source", read_only_hint=True))
def graphlore_fetch(
    nodes: list[str],
    context_lines: int = 0,
    budget_tokens: int = 2000,
    as_json: bool = False,
) -> str:
    """Hydrate graph nodes into their real source code, under a shared token budget.

    The map→code other half of graphlore_locate / graphlore_subgraph: those return a
    cheap navigation map (file:line + neighbours); this reads the actual code for the
    nodes you've zeroed in on, so the agent needn't make a separate raw-file read.

    For each node: resolve it, find the def/class span enclosing its source line, and
    return exactly those lines (± ``context_lines``). One budget is shared across all
    nodes — once it's hit the remaining nodes are dropped and ``truncated`` is set
    (the first code block is always included, so the result is never empty). Falls
    back to a point read (the node's line ± ``context_lines``) when no span is
    available (no source on disk, or an unsupported language).

    Args:
        nodes: Node names/labels, exact or fuzzy (e.g. ["Client._send_single_request"]).
        context_lines: Extra lines to include above and below each span.
        budget_tokens: Approximate shared cap on the total code returned.
    """
    if not nodes:
        return _err("ERROR: graphlore_fetch needs at least one node name.", as_json)
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    all_nodes, _ = _nodes_edges(graph)

    fetched: list[dict[str, Any]] = []
    not_found: list[str] = []
    seen_ids: set[str] = set()
    running = 0
    code_blocks = 0
    truncated = False

    for q in nodes:
        n = _resolve_node(all_nodes, q)
        if n is None:
            not_found.append(q)
            continue
        nid = _node_id(n)
        if nid in seen_ids:
            continue
        seen_ids.add(nid)

        f = _node_file(n)
        try:
            line = int(_node_line(n))
        except (TypeError, ValueError):
            line = 0

        qual: str | None = None
        spanned = False
        lo = hi = line
        if f and line > 0:
            encl = _enclosing_spans(f, line, line)
            if encl:
                region_start, end, _def_line, qual = encl[0]
                lo, hi, spanned = region_start, end, True

        block = _read_source_lines(f, lo - context_lines, hi + context_lines) if (
            f and lo > 0
        ) else None
        if block is None:
            fetched.append({
                "node": _node_label(n),
                "qualname": qual if qual and qual != _node_label(n) else None,
                "file": f or None, "line": line or None, "lines": None,
                "code": None, "tokens": 0, "spanned": spanned,
                "note": "source unavailable (no file on disk or outside the project)",
            })
            continue

        lines, clo, chi = block
        code = "\n".join(lines)
        toks = _count_tokens(code)
        # The first code block always goes in (so output is never empty); after that,
        # stop at the shared budget — same truncate-at-boundary contract as subgraph.
        if code_blocks and running + toks > budget_tokens:
            truncated = True
            break
        running += toks
        code_blocks += 1
        fetched.append({
            "node": _node_label(n),
            "qualname": qual if qual and qual != _node_label(n) else None,
            "file": f, "line": line or None, "lines": f"{clo}-{chi}",
            "code": code, "tokens": toks, "spanned": spanned,
        })

    payload = {
        "fetched": fetched,
        "not_found": not_found,
        "truncated": truncated,
        "approx_tokens": running,
    }
    parts: list[str] = []
    for it in fetched:
        head = it["qualname"] or it["node"]
        loc = (
            f"{it['file']}:{it['lines']}" if it.get("lines")
            else (f"{it['file']}:{it['line']}" if it.get("file") else "(no source location)")
        )
        if it["code"] is None:
            parts.append(f"# {head}  ({loc}) — {it.get('note', 'source unavailable')}")
        else:
            parts.append(f"# {head}  ({loc})\n{it['code']}")
    summary = (
        f"Fetched {code_blocks} node(s), ~{running} est. tokens"
        + (", TRUNCATED at budget" if truncated else "")
        + (f"; {len(not_found)} not found: {', '.join(not_found)}" if not_found else "")
    )
    text = summary + ("\n\n" + "\n\n".join(parts) if parts else "")
    return _fmt(payload, as_json, text)


@mcp.tool(annotations=ToolAnnotations(title="Signature skeleton", read_only_hint=True))
def graphlore_skeleton(
    file: str = "",
    node: str = "",
    community: str = "",
    budget_tokens: int = 1500,
    as_json: bool = False,
) -> str:
    """Signature skeleton — def/class headers (+ decorators), bodies stripped.

    The middle layer between the navigation map and full source (graphlore_fetch): read
    what a file / symbol / community *declares* without the bodies. Built on the same
    span engine as locate/fetch (ast for Python, tree-sitter otherwise), so it spans
    languages. Provide exactly one of:
      - file:      skeleton of one source file.
      - node:      that node's symbol and its nested defs/methods only.
      - community: every file holding a member of that Leiden community.

    Args:
        file / node / community: the scope (provide exactly one).
        budget_tokens: Approximate cap on returned size; trimmed at a file boundary.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _ = _nodes_edges(graph)
    if sum(bool(x) for x in (file, node, community)) != 1:
        return _err("ERROR: provide exactly one of file=, node=, or community=.", as_json)

    targets: list[tuple[str, str | None]] = []  # (file, qualname prefix or None)
    if file:
        targets = [(file, None)]
    elif node:
        n = _resolve_node(nodes, node)
        if n is None:
            return _err(f"No node matching '{node}'. Try graphlore_search.", as_json)
        nf = _node_file(n)
        if not nf:
            return _err(f"Node '{_node_label(n)}' has no source file to skeletonize.", as_json)
        try:
            prefix = _span_qualname(nf, int(_node_line(n)))
        except (TypeError, ValueError):
            prefix = None
        targets = [(nf, prefix)]
    else:  # community
        members = [
            n for n in nodes
            if str(n.get("community", n.get("cluster", ""))) == str(community)
        ]
        if not members:
            return _err(
                f"No community '{community}'. See graphlore_communities for valid ids.",
                as_json,
            )
        seen: set[str] = set()
        for n in members:
            nf = _norm_relpath(_node_file(n))
            if nf and nf not in seen:
                seen.add(nf)
                targets.append((nf, None))

    sections: list[dict[str, Any]] = []
    running = 0
    truncated = False
    for tf, prefix in targets:
        entries = _skeleton_lines(tf, prefix)
        if not entries:
            continue
        toks = _count_tokens("\n".join("\n".join(h) for _q, h in entries))
        if sections and running + toks > budget_tokens:
            truncated = True
            break
        running += toks
        sections.append({
            "file": _norm_relpath(tf),
            "symbols": [{"qualname": q, "header": "\n".join(h)} for q, h in entries],
        })

    payload = {
        "scope": file or node or f"community {community}",
        "sections": sections,
        "truncated": truncated,
        "approx_tokens": running,
    }
    if not sections:
        return _fmt(
            payload, as_json, "No signatures found (no parseable defs/classes in scope)."
        )
    parts: list[str] = []
    for section in sections:
        parts.append(f"# {section['file']}")
        parts += [sym["header"] for sym in section["symbols"]]
    if truncated:
        parts.append("\n… TRUNCATED at budget.")
    return _fmt(payload, as_json, "\n".join(parts))


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
    is unchanged, and graphlore_locate re-resolves locations from real spans at
    query time — but it's why "fresh" here means structurally, not line-, current.
    """
    # `ref:./path` is CWD-relative (cwd is PROJECT_DIR) while bare `ref:path` is
    # repo-root-relative — the working-tree read below is PROJECT_DIR-relative, so
    # both sides must agree even when PROJECT_DIR is a subdirectory of the repo.
    old_src = _git(["show", f"{ref}:./{path}"])
    if old_src is None:
        return None
    try:
        new_src = (config.PROJECT_DIR / path).read_bytes()
    except OSError:
        return None
    return _structurally_equal(path, old_src, new_src)


def _ast_equivalent_refs(path_a: str, ref_a: str, path_b: str, ref_b: str) -> bool | None:
    """Like _ast_equivalent but between two git refs (no working tree).

    True if ``path_a@ref_a`` and ``path_b@ref_b`` differ only cosmetically. ``None``
    when either blob is absent/unreadable or the language has no structural backend
    (caller treats that as a structural change). Language is detected from ``path_b``.
    """
    old_src = _git(["show", f"{ref_a}:{path_a}"])
    new_src = _git(["show", f"{ref_b}:{path_b}"])
    if old_src is None or new_src is None:
        return None
    return _structurally_equal(path_b, old_src, new_src)


@mcp.tool(annotations=ToolAnnotations(title="Graph freshness", read_only_hint=True))
def graphlore_freshness(as_json: bool = False) -> str:
    """Check whether graph.json is stale relative to the current git HEAD.

    Prefers the commit graphify recorded the graph was built from
    (``built_at_commit``) over the file mtime — robust across checkouts where
    mtime is reset — and flags both modified and newly-added (untracked) files.

    Returns a ``recommended_action`` (fresh / update / rebuild) with a ``reason``:
    deletions, renames, or a large change set call for a full rebuild, since
    incremental update can't drop nodes for code that no longer exists. If
    ``built_at_commit`` is recorded but unreachable in this clone (shallow clone,
    gc, rebase or squash), incremental update can't trust its base, so a full
    rebuild is recommended rather than a crash or a misleading "older commit".
    """
    gp = _graph_path()
    if not gp.exists():
        return _err("graph.json missing. Run graphlore_build first.", as_json)
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
    status = _git(["status", "--porcelain", "-z"])
    if status is None:
        # A failed/timed-out `git status` must NOT read as a clean tree — that
        # would report "fresh" over real pending changes.
        payload["error"] = "git status failed"
        return _fmt(
            payload, as_json,
            "ERROR: `git status` failed or timed out — freshness cannot be "
            "determined (not assuming the tree is clean).",
        )

    # Porcelain paths are REPO-ROOT-relative; PROJECT_DIR may be a subdirectory of
    # the repo, so re-anchor them (dropping changes outside the project) before
    # any working-tree read or node-path comparison.
    prefix = ""
    top = _git(["rev-parse", "--show-toplevel"])
    if top:
        try:
            rel_root = config.PROJECT_DIR.resolve().relative_to(Path(top).resolve())
            prefix = "" if str(rel_root) == "." else rel_root.as_posix() + "/"
        except (ValueError, OSError):
            prefix = ""

    def _reanchor(p: str) -> str | None:
        if not prefix:
            return p
        return p[len(prefix):] if p.startswith(prefix) else None

    changed_files: list[str] = []
    removed: list[str] = []  # deleted/renamed -> old nodes linger under incremental update
    fields = iter(status.split("\0"))
    for entry in fields:
        if not entry:
            continue  # trailing empty field after the final NUL separator
        code = entry[:2]
        raw_path = entry[3:]  # "XY <path>"; verbatim, no unquoting needed
        raw_old = raw_path
        # `-z` emits a rename/copy as two fields — new path, then original path —
        # not the `old -> new` of default porcelain. Consume the paired field.
        if "R" in code or "C" in code:
            raw_old = next(fields, raw_path)
        path = _reanchor(raw_path)
        if path is None:
            continue  # changed outside PROJECT_DIR
        old = _reanchor(raw_old)
        if path == config.OUT_DIR_NAME or path.startswith(config.OUT_DIR_NAME + "/"):
            continue
        changed_files.append(path)
        if ("D" in code or "R" in code) and old is not None:
            removed.append(old)

    # Prefer the commit graphify built the graph from; fall back to mtime vs commit.
    nodes: list[dict] = []
    built_at = None
    g = _load_graph()
    if isinstance(g, dict):
        built_at = g.get("built_at_commit")
        if not isinstance(built_at, str):
            built_at = None  # schema drift: a non-string commit is no provenance
        nodes, _ = _nodes_edges(g)
    built_unreachable = False
    if built_at:
        # `built_at` has only been a string so far. Verify git actually knows the
        # commit: a shallow clone, gc, rebase or squash can leave a recorded build
        # commit that no longer exists in this clone. An incremental `update` against
        # an unknown base can't be trusted, so treat an unreachable build commit like
        # missing provenance and steer to a full rebuild (rather than reporting it as
        # merely "an older commit" and offering update).
        reachable = _git(["cat-file", "-e", f"{built_at}^{{commit}}"]) is not None
        if reachable:
            behind = not (head.startswith(built_at) or built_at.startswith(head))
            commit_reason = "graph was built from an older commit" if behind else None
        else:
            built_unreachable = True
            behind = True
            commit_reason = "build commit unknown or unreachable"
    else:
        commit_ts = _git(["log", "-1", "--format=%ct"])
        commit_time = float(commit_ts) if commit_ts else 0.0
        behind = commit_time > graph_mtime
        commit_reason = "HEAD commit is newer than the graph" if behind else None

    # Classify pending changes: cosmetic (comment/whitespace/format-only, AST-equal
    # to HEAD) vs structural. Cosmetic-only edits don't change the graph, so they
    # shouldn't drive an update/rebuild. Skip the per-file AST diff for a large set —
    # that already routes to a full rebuild below.
    # Deletions/renames are handled by the phantom-node check below (rebuild/prune),
    # not by re-extraction, so keep them out of the cosmetic/structural split.
    removed_norm = {_norm_relpath(f) for f in removed}
    to_classify = [f for f in changed_files if _norm_relpath(f) not in removed_norm]
    # Files the graph can't represent (junk/config/assets) can't make it stale —
    # without this gate an untracked .DS_Store reports the graph stale forever.
    graph_files = {_norm_relpath(_node_file(n)) for n in nodes if _node_file(n)}
    non_source = [f for f in to_classify if not _graph_relevant_file(f, graph_files)]
    to_classify = [f for f in to_classify if _graph_relevant_file(f, graph_files)]
    cosmetic: list[str] = []
    structural: list[str] = list(to_classify)
    if to_classify and len(to_classify) <= 25:
        cosmetic, structural = [], []
        for f in to_classify:
            (cosmetic if _ast_equivalent(f, head) is True else structural).append(f)

    # A deletion/rename only forces a rebuild while its phantom nodes still linger;
    # graphlore_prune drops them, after which the deletion no longer drives a rebuild.
    phantom_removed = _files_with_nodes(nodes, removed)
    stale = behind or bool(structural) or bool(phantom_removed)

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
    elif built_unreachable:
        action = "rebuild"
        reason = (
            "the commit the graph was built from is unknown or unreachable in this "
            "clone (shallow clone, gc, rebase or squash) — incremental update can't "
            "trust its base, so a full rebuild is recommended"
        )
    elif phantom_removed:
        action = "rebuild"
        reason = (
            f"{len(phantom_removed)} file(s) deleted/renamed with nodes still in the "
            "graph — incremental update can't drop them, so a full rebuild (or "
            "graphlore_prune, then update) is recommended"
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
        "update": "graphlore_build(update=True)",
        "rebuild": 'graphlore_build(".")  # full rebuild',
    }[action]

    payload.update({
        "head": head[:10],
        "built_at_commit": built_at[:10] if built_at else None,
        "built_commit_reachable": (not built_unreachable) if built_at else None,
        "graph_mtime": graph_mtime,
        "stale": stale,
        "uncommitted_or_untracked_files": changed_files[:50],
        "structural_changes": structural[:50],
        "cosmetic_changes": cosmetic[:50],
        "non_source_changes": non_source[:50],
        "deleted_or_renamed": removed[:50],
        "phantom_files": phantom_removed[:50],
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


@mcp.tool(annotations=ToolAnnotations(title="Structural diff", read_only_hint=True))
def graphlore_diff(
    ref_a: str = "HEAD~1",
    ref_b: str = "HEAD",
    budget_tokens: int = 2000,
    as_json: bool = False,
) -> str:
    """Structural changeset between two git refs: what changed in a PR / commit range.

    Reuses the freshness engine (git + the comment-stripped ast / tree-sitter compare)
    to classify every changed file between ``ref_a`` and ``ref_b`` as added, removed,
    renamed, structurally-modified, or cosmetic-only (comments/formatting, which leave
    the graph unchanged). Good for review and audit: "what structurally moved here?".

    Scope note: this is a FILE-level structural changeset, not a node/edge-level graph
    diff — a true node/edge diff would require building the graph at both refs (the
    graphify CLI), which this doesn't do. It tells you which files changed structurally
    (so which to re-extract / review), not which individual nodes appeared or vanished.

    Args:
        ref_a / ref_b: Git commit-ish endpoints (default HEAD~1..HEAD). Both must exist.
        budget_tokens: Approximate cap on the listed files; structural changes are kept
            over cosmetic ones when trimming.
    """
    if _git(["rev-parse", "HEAD"]) is None:
        return _err("ERROR: not a git repo (or git unavailable).", as_json)
    for ref in (ref_a, ref_b):
        if _git(["cat-file", "-e", f"{ref}^{{commit}}"]) is None:
            return _err(f"ERROR: git ref '{ref}' not found in this repo.", as_json)
    raw = _git(["diff", "--name-status", "-M", "-z", ref_a, ref_b])
    if raw is None:
        return _err(f"ERROR: could not diff {ref_a}..{ref_b}.", as_json)

    out_prefix = config.OUT_DIR_NAME + "/"
    fields = iter(raw.split("\0"))
    # records ordered structural-first so a budget trim drops cosmetic noise last
    structural: list[dict[str, Any]] = []
    cosmetic: list[dict[str, Any]] = []
    for status in fields:
        if not status:
            continue
        code = status[0]
        if code in ("R", "C"):
            old = next(fields, "")
            new = next(fields, "")
        else:
            old = new = next(fields, "")
        path = new or old
        if path == config.OUT_DIR_NAME or path.startswith(out_prefix):
            continue
        if code == "A":
            structural.append({"kind": "added", "path": new})
        elif code == "D":
            structural.append({"kind": "removed", "path": old})
        elif code in ("R", "C"):
            same = _ast_equivalent_refs(old, ref_a, new, ref_b) is True
            rec = {"kind": "renamed", "from": old, "to": new, "structural": not same}
            (cosmetic if same else structural).append(rec)
        else:  # M, T, U, ...
            same = _ast_equivalent_refs(path, ref_a, path, ref_b) is True
            (cosmetic if same else structural).append({"kind": "modified", "path": path})

    # ordered structural-first, so trimming to the budget drops cosmetic noise last
    max_files = max(10, budget_tokens // 20)
    kept = (structural + cosmetic)[:max_files]
    n_struct_kept = min(len(structural), len(kept))
    kept_structural = kept[:n_struct_kept]
    kept_cosmetic = kept[n_struct_kept:]
    payload = {
        "ref_a": ref_a,
        "ref_b": ref_b,
        "structural_change_count": len(structural),
        "cosmetic_change_count": len(cosmetic),
        "structural": kept_structural,
        "cosmetic": kept_cosmetic,
        "truncated": len(structural) + len(cosmetic) > len(kept),
    }
    truncated = payload["truncated"]

    def _fmt_rec(r: dict) -> str:
        if r["kind"] == "renamed":
            tail = "" if r["structural"] else " (no structural change)"
            return f"  renamed  {r['from']} -> {r['to']}{tail}"
        return f"  {r['kind']:8} {r['path']}"

    if not structural and not cosmetic:
        return _fmt(payload, as_json, f"No changes between {ref_a} and {ref_b}.")
    lines = [
        f"{ref_a}..{ref_b}: {len(structural)} structural, {len(cosmetic)} cosmetic-only "
        f"file change(s)" + (" (TRUNCATED)" if truncated else "") + ":"
    ]
    if kept_structural:
        lines.append("Structural:")
        lines += [_fmt_rec(r) for r in kept_structural]
    if kept_cosmetic:
        lines.append("Cosmetic-only (graph unaffected):")
        lines += [_fmt_rec(r) for r in kept_cosmetic]
    return _fmt(payload, as_json, "\n".join(lines))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Prune phantom nodes", read_only_hint=False, destructive_hint=True
    )
)
def graphlore_prune(dry_run: bool = True, as_json: bool = False) -> str:
    """Drop phantom nodes for source files that no longer exist on disk.

    Incremental ``graphlore_build(update=True)`` re-extracts changed files but never
    *removes* nodes for deleted or renamed code, so the graph keeps phantom nodes
    after a delete/rename — the one case graphlore_freshness otherwise has to resolve
    with a full rebuild. This surgically removes every node whose source file is gone
    from the working tree, plus every edge touching one, and rewrites graph.json.
    Afterwards graphlore_freshness no longer forces a rebuild for those deletions.

    Only nodes whose source path resolves *inside* the project and is missing on disk
    are touched; external-source / concept nodes (no file) and files outside the
    project are never pruned. Pruning whole-file removals only — a symbol deleted from
    a still-present file is re-synced by ``graphlore_build(update=True)``.

    Args:
        dry_run: True (default) -> report what *would* be pruned, write nothing, so an
            agent can preview safely. False -> rewrite graph.json with the phantom
            nodes/edges removed.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)

    doomed_ids: set[str] = set()
    doomed_files: Counter[str] = Counter()
    for n in nodes:
        f = _node_file(n)
        if f and _node_file_missing(f):
            doomed_ids.add(_node_id(n))
            doomed_files[_norm_relpath(f)] += 1

    def _incident(e: dict) -> bool:
        s, t = _edge_ends(e)
        return s in doomed_ids or t in doomed_ids

    dropped_edges = sum(1 for e in edges if _incident(e))
    files_sorted = [{"file": f, "nodes": c} for f, c in doomed_files.most_common()]
    payload: dict[str, Any] = {
        "dry_run": dry_run,
        "removable_nodes": len(doomed_ids),
        "removable_edges": dropped_edges,
        "files": files_sorted,
        "remaining_nodes": len(nodes) - len(doomed_ids),
        "remaining_edges": len(edges) - dropped_edges,
    }

    if not doomed_ids:
        return _fmt(
            payload, as_json,
            "Nothing to prune: every node's source file is still present "
            "(or the node has no in-project file).",
        )

    verb = "Would remove" if dry_run else "Removed"
    lines = [
        f"{verb} {len(doomed_ids)} phantom node(s) and {dropped_edges} incident edge(s) "
        f"across {len(files_sorted)} missing file(s):"
    ]
    lines += [f"  {it['file']} — {it['nodes']} node(s)" for it in files_sorted[:20]]
    if len(files_sorted) > 20:
        lines.append(f"  … and {len(files_sorted) - 20} more")

    if dry_run:
        lines.append("\nDry run — graph.json unchanged. Re-run with dry_run=False to apply.")
        return _fmt(payload, as_json, "\n".join(lines))

    # Rewrite a NEW graph dict — never mutate the object _load_graph cached, or other
    # tools would observe a half-pruned graph (and a failed write would leave it
    # corrupted). Preserve whichever schema keys this graph actually uses.
    node_key = "nodes" if "nodes" in graph else ("vertices" if "vertices" in graph else "nodes")
    edge_key = "edges" if "edges" in graph else ("links" if "links" in graph else "edges")
    new_graph = dict(graph)
    new_graph[node_key] = [n for n in nodes if _node_id(n) not in doomed_ids]
    new_graph[edge_key] = [e for e in edges if not _incident(e)]

    gp = _graph_path()
    # Write-then-rename so a concurrent reader (another session, or a tool call
    # racing the watch-mode regraph thread) never parses a half-written file.
    tmp = gp.with_name(gp.name + ".tmp")
    tmp.write_text(json.dumps(new_graph, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(gp)
    # The path+mtime cache self-heals on the new mtime, but clear eagerly so an
    # immediate same-second re-read (coarse mtime) can't return the stale object.
    _GRAPH_CACHE.pop(str(gp), None)

    lines.append(f"\ngraph.json rewritten: {gp}")
    lines.append("Run graphlore_freshness to confirm the deletions no longer force a rebuild.")
    return _fmt(payload, as_json, "\n".join(lines))


@mcp.tool(annotations=ToolAnnotations(title="Validate graph", read_only_hint=True))
def graphlore_validate(limit: int = 15, as_json: bool = False) -> str:
    """Lint graph.json for structural problems (read-only).

    Reports edges whose endpoints aren't in the node set (dangling), duplicate
    edges, self-loops, and orphan (degree-0) nodes — so you know how much to
    trust the graph or whether a rebuild is warranted. Does not modify anything.
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    node_ids = {_node_id(n) for n in nodes}
    labels = _display_labels(nodes)

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
    orphans = [
        labels.get(_node_id(n), _node_label(n))
        for n in nodes
        if degree.get(_node_id(n), 0) == 0
    ]

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


@mcp.tool(annotations=ToolAnnotations(title="Dependency cycles", read_only_hint=True))
def graphlore_cycles(max_cycles: int = 20, as_json: bool = False) -> str:
    """Detect circular dependencies (strongly-connected components) in the graph.

    A pure analysis on the directed edges: any group of nodes all mutually reachable
    forms a dependency cycle — an architectural smell (no clean layering, hard to
    test or extract in isolation). Members are reported as a set, not a path (the SCC
    proves mutual reachability, not one specific route). Self-loops (a node depending
    on itself) are listed separately. Complements graphlore_validate, which inspects
    dangling/duplicate/self-loop edges rather than cycles.

    Args:
        max_cycles: Cap on the number of cycle groups returned (largest first).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, edges = _nodes_edges(graph)
    labels = _display_labels(nodes)
    forward, _reverse = _directed_adjacency(edges)
    cycles, self_loops = _find_cycles(forward)
    total = len(cycles)
    shown_cycles = cycles[:max_cycles]  # list[list[node_id]]
    self_loop_labels = [labels.get(n, n) for n in self_loops]
    payload = {
        "cycle_count": total,
        "self_loop_count": len(self_loops),
        "cycles": [
            {"size": len(c), "nodes": [labels.get(n, n) for n in c]} for c in shown_cycles
        ],
        "self_loops": self_loop_labels,
        "truncated": total > len(shown_cycles),
    }
    if not total and not self_loops:
        return _fmt(
            payload, as_json, "No dependency cycles found (the directed graph is acyclic)."
        )
    lines: list[str] = []
    if total:
        suffix = f", showing the {len(shown_cycles)} largest" if total > len(shown_cycles) else ""
        lines.append(f"{total} dependency cycle(s){suffix}:")
        lines += [
            f"  [{len(c)} nodes] " + ", ".join(labels.get(n, n) for n in c)
            for c in shown_cycles
        ]
    if self_loops:
        head = self_loop_labels[:20]
        more = f" (+{len(self_loops) - len(head)} more)" if len(self_loops) > len(head) else ""
        lines.append(f"\n{len(self_loops)} self-loop(s): " + ", ".join(head) + more)
    return _fmt(payload, as_json, "\n".join(lines))


def _first_party_prefixes() -> list[str]:
    """Package-name prefixes that belong to the project itself.

    Top-level python packages/modules under the root or ``src/``, plus the Go
    module path from ``go.mod`` when present — so self-imports don't show up as
    external API surface.
    """
    prefixes: list[str] = []
    root = config.PROJECT_DIR
    for base in (root, root / "src"):
        try:
            entries = list(base.iterdir()) if base.is_dir() else []
        except OSError:
            entries = []
        for p in entries:
            if p.is_dir() and (p / "__init__.py").exists():
                prefixes.append(p.name)
            elif p.suffix == ".py":
                prefixes.append(p.stem)
    gomod = root / "go.mod"
    try:
        if gomod.is_file():
            for ln in gomod.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip().startswith("module "):
                    prefixes.append(ln.strip().split()[1])
                    break
    except OSError:
        pass
    return prefixes


_API_SURFACE_NOTE = (
    "lower bound: dynamic import / getattr / import * / wrapper indirection are "
    "invisible; zero visible symbols means no visibility, not no usage"
)


@mcp.tool(annotations=ToolAnnotations(title="External package API surface", read_only_hint=True))
def graphlore_package_apis(package: str = "", limit: int = 20, as_json: bool = False) -> str:
    """Symbol-level external API surface: which names each package is actually used for.

    The difference between "this module imports fastapi" (package level) and "this
    module uses Depends, APIRouter, CORSMiddleware from fastapi" (symbol level). A
    version upgrade is audited at the symbol level — a breaking change in a release
    note only matters if it touches a symbol you use, so cross this list with the
    changelog before bumping a dependency.

    Symbols come from real use sites in the files the graph knows: from-imports
    (``from fastapi import Depends``) plus attribute access through import aliases
    (``np.array(...)`` -> numpy: array). ``qualified_paths`` resolves full chains
    (``numpy.linalg.norm``) — the precise input for a version-diff check. Python is
    parsed with the stdlib ast; JS/TS, Go and Java need the optional [treesitter]
    extra. First-party packages (the project's own modules) are excluded and
    reported in ``first_party_skipped``.

    The result is a LOWER BOUND ("at least these must be audited"): dynamic import,
    ``getattr(pkg, name)``, ``import *`` and use through a wrapper are invisible. A
    package with zero visible symbols reads "no visibility", not "no usage".

    Args:
        package: Report one package in detail (per-symbol file lists). Empty = all.
        limit: Cap on packages listed in the summary view (most-used first).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _edges = _nodes_edges(graph)
    files = sorted({_norm_relpath(_node_file(n)) for n in nodes} - {""})

    first_party = _first_party_prefixes()
    skipped: set[str] = set()

    def _internal(pkg: str) -> bool:
        return any(pkg == fp or pkg.startswith(fp + "/") for fp in first_party)

    pkg_files: dict[str, set[str]] = {}
    pkg_syms: dict[str, set[str]] = {}
    pkg_paths: dict[str, set[str]] = {}
    sym_files: dict[str, dict[str, set[str]]] = {}
    for f in files:
        packages, symbols, paths = _api_uses_for_file(f)
        for pkg in packages:
            if _internal(pkg):
                skipped.add(pkg)
                continue
            pkg_files.setdefault(pkg, set()).add(f)
            for s in symbols.get(pkg, ()):
                pkg_syms.setdefault(pkg, set()).add(s)
                sym_files.setdefault(pkg, {}).setdefault(s, set()).add(f)
            pkg_paths.setdefault(pkg, set()).update(paths.get(pkg, ()))

    if package:
        match = next((p for p in pkg_files if p.lower() == package.lower()), None)
        if match is None:
            known = ", ".join(sorted(pkg_files)[:30]) or "(none found)"
            return _err(
                f"No external package '{package}' in the scanned files. Known: {known}",
                as_json,
            )
        per_symbol = {s: sorted(fs) for s, fs in sorted(sym_files.get(match, {}).items())}
        payload: dict[str, Any] = {
            "package": match,
            "files": sorted(pkg_files[match]),
            "symbols": sorted(pkg_syms.get(match, ())),
            "qualified_paths": sorted(pkg_paths.get(match, ())),
            "symbol_files": per_symbol,
            "note": _API_SURFACE_NOTE,
        }
        lines = [
            f"{match} — {len(payload['symbols'])} symbol(s) across "
            f"{len(payload['files'])} file(s) ({_API_SURFACE_NOTE}):"
        ]
        lines += [f"  {s}: {', '.join(fs)}" for s, fs in per_symbol.items()]
        if not per_symbol:
            lines.append("  no symbols visible (package-level import only)")
        if payload["qualified_paths"]:
            lines.append("qualified paths: " + ", ".join(payload["qualified_paths"]))
        return _fmt(payload, as_json, "\n".join(lines))

    ranked = sorted(
        pkg_files,
        key=lambda p: (-len(pkg_files[p]), -len(pkg_syms.get(p, ())), p),
    )
    shown = ranked[:limit]
    payload = {
        "files_scanned": len(files),
        "packages": [
            {
                "package": p,
                "file_count": len(pkg_files[p]),
                "symbols": sorted(pkg_syms.get(p, ())),
                "qualified_paths": sorted(pkg_paths.get(p, ())),
            }
            for p in shown
        ],
        "first_party_skipped": sorted(skipped),
        "truncated": len(ranked) > len(shown),
        "note": _API_SURFACE_NOTE,
    }
    if not ranked:
        return _fmt(
            payload, as_json,
            f"No external package use visible in {len(files)} graph file(s) "
            f"({_API_SURFACE_NOTE}).",
        )
    lines = [
        f"External API surface across {len(files)} graph file(s) ({_API_SURFACE_NOTE}):"
    ]
    for p in shown:
        syms = sorted(pkg_syms.get(p, ()))
        if syms:
            head = ", ".join(syms[:8]) + (f" (+{len(syms) - 8} more)" if len(syms) > 8 else "")
            lines.append(f"  {p} — {len(syms)} symbol(s) in {len(pkg_files[p])} file(s): {head}")
        else:
            lines.append(
                f"  {p} — imported in {len(pkg_files[p])} file(s), no symbols visible "
                "(surface unknown)"
            )
    if payload["truncated"]:
        lines.append(f"  … +{len(ranked) - len(shown)} more package(s); raise `limit`.")
    if skipped:
        lines.append("first-party (skipped): " + ", ".join(sorted(skipped)))
    return _fmt(payload, as_json, "\n".join(lines))


_ROUTES_NOTE = (
    "lower bound: non-literal patterns, dynamic registration and chained builders "
    "(router.route().get(), gorilla .Methods()) are invisible; zero routes means "
    "no visibility, not no routes"
)


def _route_candidate_files(graph_files: set[str]) -> set[str]:
    """Well-known route files the graph may not know about, beyond ``graph_files``.

    Django's ``urls.py`` is the one file shape that systematically holds zero
    extracted symbols (it's all module-level calls), so it can be absent from the
    graph entirely. A pruned, capped walk picks those up; anything else outside
    the graph (say a route-only ``routes.ts``) stays a documented limitation —
    this must not grow into a filesystem scanner.
    """
    skip = {".git", "node_modules", ".venv", "venv", "dist", "build", "target",
            "__pycache__", config.OUT_DIR_NAME}
    found: set[str] = set()
    root = config.PROJECT_DIR
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            if "urls.py" in filenames:
                try:
                    rel = (Path(dirpath) / "urls.py").resolve().relative_to(root.resolve())
                except (ValueError, OSError):
                    continue
                found.add(rel.as_posix())
                if len(found) >= 200:
                    break
    except OSError:
        pass
    return found - graph_files


@mcp.tool(annotations=ToolAnnotations(title="Framework routes", read_only_hint=True))
def graphlore_routes(
    framework: str = "",
    method: str = "",
    pattern: str = "",
    limit: int = 50,
    as_json: bool = False,
) -> str:
    """Framework route -> handler table: which URL patterns hit which code.

    Scans the files the graph knows (plus any ``urls.py`` the graph missed) for
    the common registration idioms — Python: Flask ``@app.route`` / FastAPI-style
    verb decorators / Django ``path()``; JS/TS: Express-style ``app.get('/x', h)``
    and NestJS ``@Controller``+``@Get``; Go: gin/echo/chi verb methods,
    ``HandleFunc`` (Go 1.22 ``"GET /x"`` patterns split), chi ``Route`` nesting;
    Java: Spring ``@GetMapping``-family and ``@RequestMapping``. Each row is
    joined back to its graph node and qualified name, so a route answers "where
    is this endpoint" in one hop. Python is parsed with the stdlib ast; JS/TS,
    Go and Java need the optional [treesitter] extra.

    The result is a LOWER BOUND: non-literal patterns, dynamic registration and
    chained builders (``router.route().get()``, gorilla ``.Methods()``) are
    invisible; gorilla chains surface as method ``ANY``. Zero routes reads "no
    visibility", not "no routes".

    Args:
        framework: Keep only this framework label (exact, case-insensitive) —
            e.g. "fastapi", "flask", "django", "express", "nestjs", "gin",
            "chi", "net-http", "spring".
        method: Keep only this HTTP method (exact, case-insensitive; "ANY" for
            rows the idiom doesn't pin to one method).
        pattern: Keep only URL patterns containing this substring (case-insensitive).
        limit: Cap on routes listed (sorted by file then line).
    """
    graph = _load_graph()
    if isinstance(graph, str):
        return _err(graph, as_json)
    nodes, _edges = _nodes_edges(graph)
    graph_files = {_norm_relpath(_node_file(n)) for n in nodes} - {""}
    files = sorted(graph_files | _route_candidate_files(graph_files))

    rows: list[dict[str, Any]] = []
    for f in files:
        for r in _routes_for_file(f):
            node = _node_for_location(nodes, f, r["line"])
            rows.append({
                **r,
                "file": f,
                "node": _node_label(node) if node is not None else "",
                "qualname": _span_qualname(f, r["line"]) or "",
            })

    if framework:
        rows = [r for r in rows if r["framework"].lower() == framework.lower()]
    if method:
        rows = [r for r in rows if r["method"].lower() == method.lower()]
    if pattern:
        rows = [r for r in rows if pattern.lower() in r["pattern"].lower()]

    rows.sort(key=lambda r: (r["file"], r["line"]))
    shown = rows[:limit]
    payload: dict[str, Any] = {
        "files_scanned": len(files),
        "routes": shown,
        "count": len(rows),
        "truncated": len(rows) > len(shown),
        "note": _ROUTES_NOTE,
    }
    if not rows:
        return _fmt(
            payload, as_json,
            f"No framework routes visible in {len(files)} scanned file(s) "
            f"({_ROUTES_NOTE}).",
        )
    lines = [f"{len(rows)} route(s) across {len(files)} scanned file(s) ({_ROUTES_NOTE}):"]
    for r in shown:
        lines.append(
            f"  {r['method']} {r['pattern']} -> {r['handler']}  "
            f"({r['file']}:{r['line']})  [{r['framework']}]"
        )
    if payload["truncated"]:
        lines.append(f"  … +{len(rows) - len(shown)} more route(s); raise `limit`.")
    return _fmt(payload, as_json, "\n".join(lines))


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("graphlore://report")
def report() -> str:
    """GRAPH_REPORT.md — core nodes, surprises and suggested questions."""
    rp = _out_dir() / "GRAPH_REPORT.md"
    if not rp.exists():
        return f"GRAPH_REPORT.md missing ({rp}). Run graphlore_build first."
    return rp.read_text(encoding="utf-8")


@mcp.resource("graphlore://graph")
def graph_json() -> str:
    """graph.json — the persistent, queryable graph (raw JSON)."""
    gp = _graph_path()
    if not gp.exists():
        return f"graph.json missing ({gp}). Run graphlore_build first."
    return gp.read_text(encoding="utf-8")


@mcp.resource("graphlore://community/{community_id}")
def community(community_id: str) -> str:
    """Per-community wiki: every node in one Leiden community, with its edges."""
    graph = _load_graph()
    if isinstance(graph, str):
        return graph  # resource: always plain text, no as_json param
    nodes, edges = _nodes_edges(graph)

    def cid(n: dict) -> str:
        return str(n.get("community", n.get("cluster", "")))

    members = [n for n in nodes if cid(n) == str(community_id)]
    if not members:
        return f"No community '{community_id}'. See graphlore_communities for valid ids."
    member_ids = {_node_id(n) for n in members}
    labels = _display_labels(nodes)
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
        "1. Call graphlore_overview to get the lay of the land.\n"
        "2. Call graphlore_communities to see the major subsystems.\n"
        "3. For the top 2-3 god nodes, call graphlore_subgraph to see how they connect.\n"
        "4. Call graphlore_surprises and flag anything that looks like a hidden coupling.\n"
        "Then write me a concise architecture summary: subsystems, key types, and risks."
    )


@mcp.prompt()
def trace_bug(symptom: str) -> str:
    """Investigate a bug symptom by tracing it through the graph."""
    return (
        f"I'm debugging this symptom: {symptom}\n"
        "1. Use graphlore_search to find nodes related to the symptom.\n"
        "2. Use graphlore_subgraph around the most relevant node to see what it touches.\n"
        "3. Use graphlore_path between suspect nodes to find the call/data route.\n"
        "4. Check graphlore_surprises for unexpected couplings that could explain it.\n"
        "Give me a ranked list of likely root-cause locations with reasoning."
    )


@mcp.prompt()
def explain_flow(flow: str) -> str:
    """Explain how a named flow or feature works end to end."""
    return (
        f"Explain how the '{flow}' flow works in this codebase.\n"
        "1. graphlore_query the flow to find its entry points.\n"
        "2. graphlore_subgraph around the entry point (hops=2) for the surrounding structure.\n"
        "3. graphlore_node_details on each key node for source locations.\n"
        "Produce a step-by-step walkthrough with file:line references."
    )


def _transport_security() -> TransportSecuritySettings | None:
    """GRAPHLORE_ALLOWED_HOSTS -> the SDK's DNS-rebinding settings, or None.

    Unset returns None, i.e. the SDK default: protection auto-enabled with a
    loopback-only Host allowlist whenever the server binds a loopback host.
    That default rejects reverse-proxied requests whose Host header wasn't
    rewritten (e.g. nginx on a public name proxying to 127.0.0.1), so:
    "*" disables the protection entirely (for a trusted proxy in front), and a
    comma-separated list allowlists those hosts — entries may carry ports,
    ":*" port wildcards included — plus their derived http/https origins.
    """
    raw = config.env("ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    if raw == "*":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
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
    """Names of tools currently registered (reflects any GRAPHLORE_TOOLSET trim)."""
    try:
        return {t.name for t in mcp._tool_manager.list_tools()}
    except Exception:  # pragma: no cover - guards against private-attr changes
        return set()


def _semantic_backend_available() -> bool:
    """Whether graphlore_locate has a working backend (semble or a custom one).

    A custom GRAPHLORE_SEMANTIC_BACKEND is only "available" when its
    ``module.path:Factory`` spec is well-formed and the module resolves — a typo'd
    spec must NOT advertise a locate surface whose every call can only error.
    """
    import importlib.util

    backend = config.env("SEMANTIC_BACKEND", "").strip()
    if backend and backend.lower() != "semble":
        if ":" not in backend:
            return False
        mod_name = backend.partition(":")[0]
        try:
            return importlib.util.find_spec(mod_name) is not None
        except (ImportError, ValueError):
            return False
    return importlib.util.find_spec("semble") is not None


def _effective_lean_tools() -> set[str]:
    """LEAN_TOOLS minus tools whose optional dependency is absent.

    graphlore_locate needs the [semble] extra; in a default install it would only
    return an install-this error, so it's dropped from the lean surface rather than
    advertised as a core tool.
    """
    lean = set(LEAN_TOOLS)
    if not _semantic_backend_available():
        lean.discard("graphlore_locate")
    return lean


def _effective_toolset_tools() -> set[str] | None:
    """Tool names the configured GRAPHLORE_TOOLSET keeps, or None for no trim.

    ``locate`` is built around graphlore_locate, so without a semantic backend the
    whole surface would be inert — fall back to the lean surface (the documented
    degraded mode) with a stderr warning instead of erroring at boot.
    """
    keep = TOOLSETS.get(TOOLSET)
    if keep is None:
        return None
    if TOOLSET == "locate" and not _semantic_backend_available():
        print(
            "graphlore: GRAPHLORE_TOOLSET=locate needs the [semble] extra "
            "(or GRAPHLORE_SEMANTIC_BACKEND); falling back to the lean toolset.",
            file=sys.stderr,
            flush=True,
        )
        return _effective_lean_tools()
    if TOOLSET == "lean":
        return _effective_lean_tools()
    return set(keep)


def _lean_removals(names: list[str], lean: set[str] | frozenset[str] = LEAN_TOOLS) -> list[str]:
    """Tool names to drop for a trimmed surface (everything outside ``lean``)."""
    return [n for n in names if n not in lean]


def _apply_toolset() -> None:
    """Unregister the tools the configured GRAPHLORE_TOOLSET drops (no-op for full)."""
    keep = _effective_toolset_tools()
    if keep is None:
        return
    for name in _lean_removals(list(_registered_tool_names()), keep):
        mcp.remove_tool(name)


# ---------------------------------------------------------------------------
# Watch mode (opt-in: GRAPHLORE_WATCH=1) — proactive freshness
# ---------------------------------------------------------------------------


# Directory names whose churn must never trigger a regraph: VCS internals
# (git rewrites .git/index on every status/fetch), package/venv trees, and
# build outputs.
_WATCH_SKIP_PARTS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
})


def _structural_changes(paths: list[str], ref: str) -> tuple[list[str], list[str]]:
    """Split `paths` (PROJECT_DIR-relative) into (structural, removed) vs git ``ref``.

    structural = on disk and NOT cosmetic-equal to ``ref`` (or new/unparseable);
    removed = gone from disk. Cosmetic-only edits (comments/formatting) are dropped —
    they don't change the graph, so they shouldn't trigger a regraph — as are the
    output dir's own files, VCS/venv/build directories, and files the graph can't
    represent at all (junk/config/assets). The same cosmetic-vs-structural test
    graphlore_freshness uses.
    """
    structural: list[str] = []
    removed: list[str] = []
    out_prefix = config.OUT_DIR_NAME + "/"
    g = _load_graph()
    graph_files = (
        {_norm_relpath(_node_file(n)) for n in _nodes_edges(g)[0] if _node_file(n)}
        if isinstance(g, dict) else set()
    )
    for p in paths:
        rel = _norm_relpath(p)
        if not rel or rel == config.OUT_DIR_NAME or rel.startswith(out_prefix):
            continue
        if any(part in _WATCH_SKIP_PARTS for part in rel.split("/")):
            continue
        if not _graph_relevant_file(rel, graph_files):
            continue
        if not (config.PROJECT_DIR / rel).exists():
            removed.append(rel)
        elif _ast_equivalent(rel, ref) is not True:  # structural / new / unparseable
            structural.append(rel)
    return structural, removed


class _GraphWatcher:
    """Decide whether a batch of changed paths warrants a regraph, and trigger it.

    Reuses the structural-vs-cosmetic check so comment/format-only edits don't rebuild.
    ``trigger(structural, removed)`` runs when a regraph is warranted; the default prunes
    first if anything was deleted, then runs an incremental ``graphlore_build(update=True)``.
    """

    def __init__(self, ref: str = "HEAD", trigger: Any = None) -> None:
        self._ref = ref
        self._trigger = trigger or self._default_trigger

    def maybe_trigger(self, changed_paths: list[str]) -> bool:
        structural, removed = _structural_changes(list(changed_paths), self._ref)
        if not structural and not removed:
            return False
        self._trigger(structural, removed)
        return True

    @staticmethod
    def _default_trigger(structural: list[str], removed: list[str]) -> None:
        if removed:
            graphlore_prune(dry_run=False)
        graphlore_build(update=True)


def _start_watch() -> Any:
    """Start the opt-in filesystem watcher; return the observer, or None if off/unavailable.

    Enabled by ``GRAPHLORE_WATCH`` in (1/true/yes). Needs the optional ``watchdog`` extra;
    if it's missing, log and skip rather than fail. Events are debounced
    (``GRAPHLORE_WATCH_DEBOUNCE`` seconds, default 2) and only structural changes regraph.
    """
    if config.env("WATCH", "").strip().lower() not in ("1", "true", "yes"):
        return None
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "GRAPHLORE_WATCH is set but the 'watchdog' extra isn't installed; "
            "install with: pip install 'graphlore[watch]'.",
            file=sys.stderr,
        )
        return None

    import threading

    watcher = _GraphWatcher()
    debounce = float(config.env("WATCH_DEBOUNCE", "2.0"))
    pending: set[str] = set()
    lock = threading.Lock()
    timer: dict[str, Any] = {}

    def _flush() -> None:
        with lock:
            paths = list(pending)
            pending.clear()
        try:
            if paths:
                watcher.maybe_trigger(paths)
        except Exception as e:  # noqa: BLE001 - a background regraph must never crash the server
            print(f"graphlore watch: regraph failed ({type(e).__name__}: {e})", file=sys.stderr)

    class _Handler(FileSystemEventHandler):  # type: ignore[misc]
        def on_any_event(self, event: Any) -> None:
            if getattr(event, "is_directory", False):
                return
            # watchdog reports ABSOLUTE paths; _structural_changes' filters and
            # git compare need PROJECT_DIR-relative ones (an absolute path passes
            # the out-dir filter and always fails `git show`, so every event —
            # the build's own output included — would read as structural and the
            # watcher would rebuild in a loop).
            raw = Path(str(getattr(event, "src_path", "")))
            if not raw.is_absolute():
                raw = config.PROJECT_DIR / raw
            try:
                rel = raw.resolve().relative_to(config.PROJECT_DIR.resolve()).as_posix()
            except (ValueError, OSError):
                return  # outside the project — not ours
            with lock:
                pending.add(rel)
                old = timer.get("t")
                if old is not None:
                    old.cancel()
                t = threading.Timer(debounce, _flush)
                t.daemon = True
                timer["t"] = t
                t.start()

    observer = Observer()
    observer.schedule(_Handler(), str(config.PROJECT_DIR), recursive=True)
    observer.daemon = True
    observer.start()
    print(
        f"graphlore watch: watching {config.PROJECT_DIR} "
        f"(structural changes -> graphlore_build update; debounce {debounce}s)",
        file=sys.stderr,
        flush=True,
    )
    return observer


def main() -> None:
    """Console-script entry point.

    Transport is selected by GRAPHLORE_TRANSPORT (default ``stdio``); ``sse`` and
    ``streamable-http`` serve over HTTP on GRAPHLORE_HOST:GRAPHLORE_PORT. Any HTTP
    transport force-enables path containment (GRAPHLORE_RESTRICT_PATHS), since the
    build tool would otherwise let a network client extract arbitrary paths. Set
    GRAPHLORE_API_KEY to require bearer auth on HTTP; GRAPHLORE_TOOLSET=lean trims the
    surface to the core exploration tools and GRAPHLORE_TOOLSET=locate to a minimal
    locate-first set (falls back to lean without a semantic backend).
    GRAPHLORE_WATCH=1 starts a background watcher
    that re-syncs the graph on structural source changes (needs the [watch] extra).
    GRAPHLORE_ALLOWED_HOSTS tunes the SDK's DNS-rebinding Host allowlist ("*" disables
    it) — needed when a reverse proxy in front doesn't rewrite the Host header.
    """
    _apply_toolset()
    _start_watch()  # no-op unless GRAPHLORE_WATCH is set
    is_http = TRANSPORT in ("streamable-http", "http", "sse")
    transport = ("sse" if TRANSPORT == "sse" else "streamable-http") if is_http else "stdio"
    # Boot banner: name + version + transport + project dir, so it's clear from
    # the first stderr line which server and project a client connected to
    # (graphify's own embedded MCP server is a common neighbor in configs).
    where = f" {HTTP_HOST}:{HTTP_PORT}" if is_http else ""
    print(
        f"graphlore v{__version__} | transport={transport}{where} | "
        f"toolset={TOOLSET} | project={config.PROJECT_DIR}",
        file=sys.stderr,
        flush=True,
    )
    if is_http:
        global RESTRICT_PATHS
        RESTRICT_PATHS = True
        security = _transport_security()
        if API_KEY:
            import uvicorn

            # host= must match the uvicorn bind host: the SDK auto-enables DNS
            # rebinding protection (Host-header allowlist) for loopback hosts,
            # and a mismatched default would reject legitimate remote clients.
            base = (
                mcp.sse_app(host=HTTP_HOST, transport_security=security)
                if transport == "sse"
                else mcp.streamable_http_app(host=HTTP_HOST, transport_security=security)
            )
            app = _bearer_auth_asgi(base, API_KEY)
            uvicorn.run(
                app, host=HTTP_HOST, port=HTTP_PORT,
                log_level=mcp.settings.log_level.lower(),
            )
        else:
            if HTTP_HOST not in ("127.0.0.1", "localhost", "::1"):
                print(
                    f"WARNING: serving HTTP on {HTTP_HOST} without GRAPHLORE_API_KEY — "
                    "anyone who can reach this port can drive the server. Set "
                    "GRAPHLORE_API_KEY to require bearer auth.",
                    file=sys.stderr,
                )
            mcp.run(
                transport="sse" if TRANSPORT == "sse" else "streamable-http",
                host=HTTP_HOST,
                port=HTTP_PORT,
                transport_security=security,
            )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
