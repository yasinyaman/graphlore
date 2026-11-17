"""graph.json loading + schema-tolerant node/edge/traversal helpers.

Pure graph-data utilities with no dependency on the MCP surface; reads the project
location from :mod:`graphlore.config`.
"""
from __future__ import annotations

import difflib
import json
import re
from collections import deque
from collections.abc import Callable, Iterable
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
# id(nodes list) -> (nodes, the graph's `graph` header): lets helpers that get
# only the node list (resolve.py) ask which engine wrote it. Identity-guarded
# like the other id-keyed caches.
_GRAPH_META: dict[int, tuple[list, dict[str, Any]]] = {}
_GRAPH_META_MAX = 8


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
        _remember_meta(data)
        return data
    except json.JSONDecodeError as e:
        return f"ERROR: failed to parse graph.json: {e}"
    except (OSError, UnicodeDecodeError) as e:
        # stat/read can fail mid-rebuild (file swapped between exists() and stat)
        # or on undecodable bytes; both are "graph unavailable", not a crash.
        return f"ERROR: failed to read graph.json: {e}"


def _remember_meta(data: dict[str, Any]) -> None:
    nodes = data.get("nodes") or data.get("vertices")
    meta = data.get("graph")
    if not isinstance(nodes, list) or not isinstance(meta, dict):
        return
    if id(nodes) not in _GRAPH_META and len(_GRAPH_META) >= _GRAPH_META_MAX:
        _GRAPH_META.pop(next(iter(_GRAPH_META)), None)  # FIFO
    _GRAPH_META[id(nodes)] = (nodes, meta)


def _graph_meta(nodes: list) -> dict[str, Any]:
    """The `graph` header of the loaded graph these nodes came from ({} if none)."""
    entry = _GRAPH_META.get(id(nodes))
    return entry[1] if entry is not None and entry[0] is nodes else {}


def _engine_resolved(nodes: list) -> bool:
    """Whether graphlore's native engine wrote this graph with its own import and
    call resolution (schema 2 on): its edges need no correcting at read time."""
    meta = _graph_meta(nodes)
    try:
        return meta.get("engine") == "graphlore" and int(meta.get("schema_version", 0)) >= 2
    except (TypeError, ValueError):
        return False


def _native_routes(nodes: list) -> bool:
    """Whether the native engine wrote this graph's routes (schema 5 on): route
    nodes with their full paths, `handles` and `http_calls` edges."""
    meta = _graph_meta(nodes)
    try:
        return meta.get("engine") == "graphlore" and int(meta.get("schema_version", 0)) >= 5
    except (TypeError, ValueError):
        return False


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


def _node_type(n: dict) -> str:
    """What kind of thing a node is: ``type`` where the schema has it, else
    graphify >= 0.9's ``file_type`` (code / document / concept / rationale)."""
    v = n.get("type") or n.get("file_type")
    return "" if v is None else str(v)


def _bare_name(label: str) -> str:
    """``.dump()`` -> ``dump``, ``helper()`` -> ``helper``, ``TableSchema`` -> itself.

    A scope written with ``::`` (Ruby ``Faraday::Utils::Headers``, C++ / PHP
    ``Session::SetOption()``) is dropped like a dotted one. Only the part before a
    ``<`` is cut, so type arguments (``Callback<a.B>``) and ``operator<<`` survive.
    """
    name = label.strip()
    if name.endswith("()"):
        name = name[:-2]
    head, lt, tail = name.lstrip(".").partition("<")
    if not head:  # `<anonymous>`, `<init>`: nothing to cut
        return name.lstrip(".")
    return head.replace("::", ".").rsplit(".", 1)[-1] + lt + tail


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


# --- shared state ------------------------------------------------------------


# How far a call path between two definitions sharing state is looked for.
_CALL_PATH_HOPS = 6
# Pairs listed per shared state row.
_SHARED_PAIRS_LIMIT = 10


def _shared_state(
    nodes: list[dict], edges: list[dict], labels: Any = None,
    call_edges: list[dict] | None = None,
    coupled: Callable[[str, str], tuple[int, float] | None] | None = None,
) -> list[dict]:
    """Variables, fields and resources that code in one file writes and code in
    another file reads or writes: implicit coupling, strongest first.

    Each pair of a writer and another definition touching the state says
    whether a call path joins them (`call_path`, either way, over calls,
    dispatch and HTTP, within 6 hops, on `call_edges` — the graph's edges by
    default) and whether their files change together (`co_change`, from
    `coupled(file_a, file_b)`, the git history). A score per state node: +2 when
    some pair has no call path between them (a hidden pair), +2 when the
    touching files sit in different top-level directories, +1 when they are in
    different communities, +2 when the files of a hidden pair change together.
    Test code is left out (a test setting a global is how tests work) unless
    the state itself is test code.
    """
    byid = {_node_id(n): n for n in nodes}
    name = (lambda i: labels.get(i, i)) if labels is not None else (
        lambda i: _node_label(byid[i]) if i in byid else i)
    readers: dict[str, set[str]] = {}
    writers: dict[str, set[str]] = {}
    for e in edges:
        rel = _rel(e)
        if rel not in _STATE_RELATIONS:
            continue
        s, t = _edge_ends(e)
        (writers if rel == "writes" else readers).setdefault(t, set()).add(s)
    call_rels = _CALL_RELATIONS | {"dispatches_to"} | _HTTP_RELATIONS
    adj = _relation_adjacency(call_edges if call_edges is not None else edges, call_rels)
    reach: dict[str, dict[str, int]] = {}

    def hops(a: str, b: str) -> int | None:
        if a not in reach:
            reach[a] = _hop_distances(adj, a, _CALL_PATH_HOPS)
        return reach[a].get(b)

    out: list[dict] = []
    for sid, ws in writers.items():
        state = byid.get(sid)
        if state is None:
            continue
        test_state = bool(_node_file(state)) and _is_test_path(_node_file(state))

        def keep(i: str, test_state: bool = test_state) -> bool:
            n = byid.get(i)
            f = _node_file(n) if n else ""
            return bool(f) and (test_state or not _is_test_path(f))

        ws_kept = sorted(i for i in ws if keep(i))
        rs_kept = sorted(i for i in readers.get(sid, set()) - ws if keep(i))
        touch = ws_kept + rs_kept
        if not ws_kept or len(touch) < 2:
            continue
        file_of = {i: _node_file(byid[i]) for i in touch}
        files = sorted(set(file_of.values()))
        if len(files) < 2:
            continue
        why: list[str] = []
        score = 1
        file_pairs = [(file_of[w], file_of[o]) for w in ws_kept for o in touch
                      if file_of[o] != file_of[w]]
        pairs: list[dict] = []
        seen: set[frozenset[str]] = set()
        for w in ws_kept:
            for o in touch:
                if o == w or frozenset((w, o)) in seen:
                    continue
                seen.add(frozenset((w, o)))
                ab, ba = hops(w, o), hops(o, w)
                path = ({"direction": "a→b", "hops": ab} if ab is not None
                        else {"direction": "b→a", "hops": ba} if ba is not None else None)
                cc = (coupled(file_of[w], file_of[o])
                      if coupled is not None and file_of[w] != file_of[o] else None)
                pairs.append({
                    "writer": name(w), "other": name(o),
                    "effect": "both write it" if o in ws else "the other reads what it writes",
                    "call_path": path,
                    "co_change": {"commits": cc[0], "degree": round(cc[1], 2)} if cc else None,
                })
        hidden = [p for p in pairs if p["call_path"] is None]
        co_hidden = [p for p in hidden if p["co_change"]]
        if hidden:
            score += 2
            why.append(f"{len(hidden)} of {len(pairs)} pair(s) share it with no call "
                       "path between them")
        elif pairs:
            why.append("every pair is joined by a call path as well")
        if any(_top_dir(a) != _top_dir(b) for a, b in file_pairs):
            score += 2
            why.append("crosses top-level directories")
        comm = {i: byid[i].get("community", byid[i].get("cluster")) for i in touch}
        if len({c for c in comm.values() if c is not None}) > 1:
            score += 1
            why.append("touched from more than one community")
        if co_hidden:
            score += 2
            why.append(f"{len(co_hidden)} hidden pair(s) whose files change together")
        pairs.sort(key=lambda p: (p["call_path"] is not None, p["co_change"] is None))
        row_pairs = pairs[:_SHARED_PAIRS_LIMIT]
        extra: dict[str, Any] = {}
        if len(pairs) > len(row_pairs):
            extra["pairs_left_out"] = len(pairs) - len(row_pairs)
        out.append({
            "state": name(sid),
            "kind": str(state.get("kind") or state.get("resource") or _node_type(state) or ""),
            "file": _node_file(state) or None,
            "writers": [name(i) for i in ws_kept],
            "readers": [name(i) for i in rs_kept],
            "files": len(files),
            "score": score,
            "strength": "strong" if score >= 5 else "medium" if score >= 3 else "weak",
            "why": why,
            "pairs": row_pairs,
            "hidden_pairs": len(hidden),
            "co_changing_pairs": len(co_hidden),
            **extra,
        })
    out.sort(key=lambda r: (-r["score"], -r["files"], -len(r["writers"]), r["state"]))
    return out


# --- computed surprises -----------------------------------------------------
# A graph built by local AST extraction carries no surprise flag and often no
# community info, so the flag check above returns nothing on it. Rather than
# reporting an empty list (which reads as "this codebase has no surprising
# couplings" when it means "this tool computed nothing"), score the edges.

# Relation vocabularies shared by the analysis tools (cycles, layers, unreferenced,
# same-names, surprises). Compared against ``_edge_rel(e).strip().lower()``.
_IMPORT_RELATIONS = frozenset({
    "imports", "imports_from", "dynamic_import", "re_exports", "requires",
})
_CALL_RELATIONS = frozenset({"calls", "indirect_call"})
# Parent -> member. graphify >= 0.9 also writes `defines` (a class's field, a
# script's function) and `case_of` (an enum's case).
_CONTAINMENT_RELATIONS = frozenset({"contains", "method", "defines", "case_of"})
# Edges that say nothing about one piece of code depending on another: structural
# containment, and a docstring/comment node pointing at the code it describes.
_NON_DEPENDENCY_RELATIONS = _CONTAINMENT_RELATIONS | {"rationale_for"}
# A definition reading or writing a variable, a field, a constant or a resource
# (the native engine). Two definitions touching one such node affect each other
# without either calling the other: shared state.
_STATE_RELATIONS = frozenset({"reads", "writes"})
# A web route and the code on either side of it (the native engine): `handles`
# from a route node to its handler, `http_calls` from a client's definition to
# the route its request reaches. Each is a real dependency across the wire.
_HTTP_RELATIONS = frozenset({"handles", "http_calls"})
# Node kinds that hold state (the native engine's `kind`).
_STATE_KINDS = frozenset({"variable", "property", "constant"})


def _is_state_node(n: dict) -> bool:
    """A variable, field or constant, or a resource node (env, file, table, …)."""
    return str(n.get("kind", "")).lower() in _STATE_KINDS or _is_resource_node(n)


def _is_resource_node(n: dict) -> bool:
    """Something outside the program's memory that code reads or writes: an
    environment variable, a file, a table, an event (`type: resource`)."""
    return str(n.get("type", "")).lower() == "resource"


def _is_route_node(n: dict) -> bool:
    """A web route the native engine found (`type: route`): its method, full
    pattern and framework, at the line that registers it."""
    return str(n.get("type", "")).lower() == "route"


_SHARED_STATE_CACHE: dict[int, tuple[list[dict], list[dict]]] = {}


def _with_written_by(edges: list[dict]) -> list[dict]:
    """``edges`` plus, for each ``writes`` edge W → R, a ``written_by`` edge
    R → W, so a walk can go from a writer through what it writes to its
    readers. Cached on the identity of ``edges``, like the adjacency."""
    cached = _SHARED_STATE_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    out = list(edges)
    for e in edges:
        if _rel(e) == "writes":
            s, t = _edge_ends(e)
            v = {k: e[k] for k in ("confidence", "source_file", "source_location") if k in e}
            out.append({"source": t, "target": s, "relation": "written_by",
                        "context": "shared_state", **v})
    if len(_SHARED_STATE_CACHE) >= 4:
        _SHARED_STATE_CACHE.pop(next(iter(_SHARED_STATE_CACHE)), None)
    _SHARED_STATE_CACHE[id(edges)] = (edges, out)
    return out
_FILE_KEYS = ("file", "path", "source_file")


def _rel(e: dict) -> str:
    """Normalized relation name of an edge (lower-case, stripped)."""
    return _edge_rel(e).strip().lower()


def _is_external_node(n: dict) -> bool:
    """A node standing for code outside the repository.

    Either explicitly marked (graphify >= 0.9: ``type: "external"`` on imported
    packages such as ``json``/``pytest``), or carrying a source-file key that is
    present but EMPTY — how graphify records a name it could not resolve to a
    file (``Protocol``, a decorator's ``get``, gettext's ``_``). A node with no
    file key at all is not treated as external: that is a schema without file
    info, not an unresolved reference.
    """
    if n.get("external") is True or str(n.get("type", "")).lower() == "external":
        return True
    if _node_file(n):
        return False
    return any(k in n for k in _FILE_KEYS)


# Containment/import scaffolding: structurally expected, never surprising.
_SURPRISE_STRUCTURAL_RELS = _CONTAINMENT_RELATIONS | {"imports", "imports_from"}
# Inferred edges of these kinds claim "this code refers to that code"; a resolver
# that matched on the bare name leaves one with no import to back it.
_SURPRISE_NAME_RESOLVED_RELS = frozenset({
    "calls", "indirect_call", "uses", "references", "inherits", "extends", "implements",
})
# A package's entry file: importing it reaches the modules next to it.
_PACKAGE_ENTRY_STEMS = frozenset({"__init__", "index", "mod"})
_LANG_FAMILY = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".go": "go", ".rs": "rust",
    ".java": "jvm", ".kt": "jvm", ".kts": "jvm", ".scala": "jvm",
    ".rb": "ruby", ".php": "php", ".cs": "dotnet", ".swift": "swift",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cxx": "c", ".hxx": "c",
    ".m": "objc", ".mm": "objc", ".lua": "lua", ".zig": "zig",
    ".ex": "elixir", ".exs": "elixir",
}
_TEST_DIR_PARTS = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing", "e2e"})
# A camelCase or dotted name that ends in Test(s): Gradle source sets
# (`jvmTest`, `androidTest`), .NET projects (`Foo.Tests`, `Foo.Tests.Xml`) and the
# file convention of the JVM, .NET, PHP and Swift (`OwnerControllerTests`).
# `Spec` is left out: it is a production suffix too (`TypeSpec`, `FunSpec`).
_TEST_NAME = re.compile(r"[a-z0-9.](?:Tests?|TestCase)(?:$|\.)")


def _lang_family(path: str) -> str | None:
    dot = path.rfind(".")
    return _LANG_FAMILY.get(path[dot:].lower()) if dot > 0 else None


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    if any(p.lower() in _TEST_DIR_PARTS or _TEST_NAME.search(p) for p in parts[:-1]):
        return True
    stem = parts[-1].rsplit(".", 1)[0]
    low = stem.lower()
    return (
        low.startswith("test_") or low == "conftest"
        or low.endswith(("_test", "_tests", ".test", ".spec", "_spec"))
        or bool(_TEST_NAME.search(stem))
    )


def _top_dir(path: str) -> str:
    """First path component of a project-relative file ("." for a root-level file)."""
    head, sep, _ = path.partition("/")
    return head if sep else "."


# From this many edges on, two directories are joined by a dependency, not by a
# surprise: an inbound adapter using the application layer in dozens of places is
# the architecture, not a finding.
_ESTABLISHED_PAIR_EDGES = 10


def _diverging(a: str, b: str) -> tuple[str, str]:
    """Where two file paths part ways: ``pkg/adapters/cli/x.py`` and
    ``pkg/application/svc/y.py`` -> (``pkg/adapters``, ``pkg/application``).
    Two files in one directory diverge at the files themselves."""
    pa, pb = a.split("/"), b.split("/")
    common = 0
    for x, y in zip(pa[:-1], pb[:-1], strict=False):
        if x != y:
            break
        common += 1
    return "/".join(pa[: common + 1]), "/".join(pb[: common + 1])


def _is_package_entry_of(entry: str, member: str) -> bool:
    """True if ``entry`` is the entry file (``__init__.py``, ``index.ts``, ``mod.rs``)
    of a package directory that contains ``member``."""
    d, _, name = entry.rpartition("/")
    if name.rsplit(".", 1)[0] not in _PACKAGE_ENTRY_STEMS:
        return False
    return not d or member.startswith(d + "/")


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
    """A node standing for a whole file.

    Its label is the file's basename, or a trailing part of its path: graphify
    >= 0.9 labels package entry files ``pkg/__init__.py`` so they can be told apart.
    """
    f = _node_file(n)
    if not f:
        return False
    label = _node_label(n)
    return label == f.rsplit("/", 1)[-1] or f == label or f.endswith("/" + label)


# When the import behind an edge runs (see graphlore.imports, which reads it from
# the source). Unclassified = the source could not be read as Python; such an
# import is assumed to run at import time.
IMPORT_MODULE_LEVEL = "module-level"
IMPORT_DEFERRED = "deferred"            # inside a function body
IMPORT_TYPE_CHECKING = "type-checking"  # under `if TYPE_CHECKING:`
IMPORT_UNCLASSIFIED = "unclassified"
# The values an edge's `runs` field may carry (native engine): every kind but
# unclassified.
_IMPORT_RUNS = frozenset({IMPORT_MODULE_LEVEL, IMPORT_DEFERRED, IMPORT_TYPE_CHECKING})
_IMPORT_RUNS_AT_IMPORT = frozenset({IMPORT_MODULE_LEVEL, IMPORT_UNCLASSIFIED})
# (importing file, imported file, edge) -> one of the kinds above, or None when
# the import statement cannot mean that file at all (the resolver matched a name).
ImportKind = Callable[[str, str, dict], "str | None"]


def _file_import_edges(
    nodes: list[dict], edges: list[dict], kind_of: ImportKind | None = None
) -> list[tuple[str, str, dict, dict, str]]:
    """Import edges lifted to file level:
    ``(source file, target file, edge, target node, import kind)``.

    graphify records an import as file -> file (``imports_from``) or as file ->
    imported symbol (``imports``); both say "this module imports that module".
    Edges with an endpoint outside the repository (no source file) and imports
    within one file are dropped. With ``kind_of`` (an
    :class:`graphlore.imports.ImportChecker`'s ``kind``), an edge the source
    contradicts is dropped too and the rest carry when their import runs; without
    it every edge is ``IMPORT_UNCLASSIFIED``.
    """
    byid = {_node_id(n): n for n in nodes}
    out: list[tuple[str, str, dict, dict, str]] = []
    for e in edges:
        if _rel(e) not in _IMPORT_RELATIONS:
            continue
        sid, tid = _edge_ends(e)
        src, dst = byid.get(sid), byid.get(tid)
        if src is None or dst is None:
            continue
        sf, tf = _node_file(src), _node_file(dst)
        if not (sf and tf) or sf == tf:
            continue
        if kind_of is not None:
            kind = kind_of(sf, tf, e)
        else:
            # an extractor that read the statement says when it runs
            carried = e.get("runs")
            kind = carried if carried in _IMPORT_RUNS else IMPORT_UNCLASSIFIED
        if kind is None:
            continue
        if kind == IMPORT_UNCLASSIFIED and kind_of is not None and not _is_file_hub_node(src) \
                and _node_label(src).endswith("()"):
            # The source could not be read, but the graph recorded this import
            # on a function rather than on its file: it runs when that is called.
            kind = IMPORT_DEFERRED
        out.append((sf, tf, e, dst, kind))
    return out


def _computed_surprises(
    nodes: list[dict], edges: list[dict], import_kind: ImportKind | None = None,
    reaches: Callable[[str, str], bool | None] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Score cross-file edges for "surprisingness" when the graph carries no flag.

    Returns (ranked candidates, diagnostics). Diagnostics name *why* edges were
    skipped and which signals the graph lacks, so a caller can distinguish
    "nothing surprising here" from "nothing computable here" — the distinction
    the flag-only check silently collapsed.

    One O(N+E) pass plus a sort of the survivors; no betweenness, so it stays
    usable on large graphs. ``reaches(importer, imported)`` (``ImportChecker.
    reaches``) answers from the source for a file whose imports the graph did not
    record: False means none of its import statements can mean that file.
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

    # Which files import which, and which top-level directories other directories
    # depend on. Both come from the graph's own edges, so a graph without import
    # edges (or with a single directory) simply leaves the two checks off.
    file_imports: dict[str, set[str]] = {}
    has_imports: set[str] = set()
    for e in edges:
        if _rel(e) in _IMPORT_RELATIONS:
            src = byid.get(_edge_ends(e)[0])
            if src is not None and _node_file(src):
                has_imports.add(_node_file(src))
    for sf, tf, _e, _dst, _kind in _file_import_edges(nodes, edges, import_kind):
        file_imports.setdefault(sf, set()).add(tf)

    def _imported(fu: str, fv: str) -> bool:
        got = file_imports.get(fu, ())
        return fv in got or any(_is_package_entry_of(g, fv) for g in got)

    source_says: dict[tuple[str, str], bool | None] = {}

    def _name_guess(e: dict, rel: str, fu: str, fv: str) -> bool:
        """The resolver guessed this target, and the source file imports neither
        the target's module nor its package: a same-named function in an
        unrelated directory, not a coupling. Judged from the graph's import edges
        when it recorded any for the file; otherwise from the file's own import
        statements (``reaches``), so a file importing only the standard library —
        which leaves no import edge at all — is judged too, not exempted."""
        conf = str(e.get("confidence") or "EXTRACTED").upper()
        if conf not in ("INFERRED", "AMBIGUOUS") or rel not in _SURPRISE_NAME_RESOLVED_RELS:
            return False
        if fu in has_imports:
            return not _imported(fu, fv)
        if reaches is None:
            return False
        key = (fu, fv)
        if key not in source_says:
            source_says[key] = reaches(fu, fv)
        return source_says[key] is False

    # Who depends on what, by top-level directory and by file — counted over the
    # edges that can be trusted: a resolver guess from the package into a script
    # must not make that script look depended upon. Test code is nobody's
    # dependent here; it uses everything.
    dependents_of: dict[str, set[str]] = {}
    file_dependents: dict[str, set[str]] = {}
    # ...and how many edges join each pair of directories. One edge between two
    # parts of a codebase is a lead; dozens are a dependency someone designed.
    pair_edges: dict[tuple[str, str], int] = {}
    for e in edges:
        rel = _rel(e)
        if rel in _NON_DEPENDENCY_RELATIONS:
            continue
        sid, tid = _edge_ends(e)
        nu, nv = byid.get(sid), byid.get(tid)
        if nu is None or nv is None:
            continue
        fu, fv = _node_file(nu), _node_file(nv)
        if not (fu and fv) or fu == fv or _is_test_path(fu) or _name_guess(e, rel, fu, fv):
            continue
        file_dependents.setdefault(fv, set()).add(fu)
        pair_edges[_diverging(fu, fv)] = pair_edges.get(_diverging(fu, fv), 0) + 1
        if _top_dir(fu) != _top_dir(fv):
            dependents_of.setdefault(_top_dir(fv), set()).add(_top_dir(fu))

    skipped: dict[str, int] = {}
    confidences: set[str] = set()
    scored: list[dict] = []

    for e in edges:
        rel = _rel(e)
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
        if _name_guess(e, rel, fu, fv):
            skipped["inferred_unimported"] = skipped.get("inferred_unimported", 0) + 1
            continue
        cat_u = str(nu.get("file_type") or "code").lower()
        cat_v = str(nv.get("file_type") or "code").lower()
        lang_u, lang_v = _lang_family(fu), _lang_family(fv)
        cross_lang = bool(lang_u and lang_v and lang_u != lang_v)
        # An inferred call/uses edge that also crosses a language or code<->doc
        # boundary is usually the resolver guessing, not a real coupling. Both
        # this and the two expected couplings below withhold the structural bonuses.
        resolver_noise = conf == "INFERRED" and rel in ("calls", "uses", "indirect_call") and (
            cross_lang or {cat_u, cat_v} == {"code", "document"}
        )
        # Test code using anything — the source under test, or shared fakes and
        # fixtures in another test directory — is routine. (Non-test code that
        # depends on test code is not, and keeps its bonuses.)
        from_test = _is_test_path(fu)
        # A directory nothing depends on (bench/, demo/, examples/), or a script
        # nothing depends on (docker/smoke/make_catalog.py), using a directory
        # that several others depend on is what it is there for.
        top_u, top_v = _top_dir(fu), _top_dir(fv)
        consumer_edge = (
            top_u != top_v and len(dependents_of.get(top_v, ())) >= 2
            and (not dependents_of.get(top_u) or not file_dependents.get(fu))
        )
        pair = _diverging(fu, fv)
        established = pair_edges.get(pair, 0) >= _ESTABLISHED_PAIR_EDGES
        suppress = resolver_noise or from_test or consumer_edge or established
        # An inferred edge into a module the source file imports is resolved by
        # that import, not guessed: it scores like an extracted one.
        guessed = (
            conf in ("AMBIGUOUS", "INFERRED")
            and not (rel in _SURPRISE_NAME_RESOLVED_RELS and _imported(fu, fv))
        )

        score = 0
        why: list[str] = []
        if not resolver_noise:
            score += {"AMBIGUOUS": 3, "INFERRED": 2}.get(conf, 1) if guessed else 1
            if guessed:
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
        if from_test:
            why.append("(from test code: routine coupling, structural bonuses withheld)")
        elif consumer_edge:
            what = f"{top_u}/" if not dependents_of.get(top_u) else fu
            why.append(
                f"({what} only consumes {top_v}/: expected coupling, "
                "structural bonuses withheld)"
            )
        elif established:
            why.append(
                f"({pair_edges[pair]} edges join {pair[0]} and {pair[1]}: an established "
                "dependency, structural bonuses withheld)"
            )

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


class _NameIndex:
    """Label -> nodes and bare name -> nodes, each list in graph order.

    ``bare`` leaves file hub nodes out (a file is named by its path, not by a
    symbol name). ``pool`` maps each lowercased bare name to its first node, the
    candidate set for "did you mean" suggestions.
    """

    __slots__ = ("labels", "bare", "pos", "pool")

    def __init__(self, nodes: list[dict]) -> None:
        self.labels: dict[str, list[dict]] = {}
        self.bare: dict[str, list[dict]] = {}
        self.pos: dict[int, int] = {}
        self.pool: dict[str, dict] = {}
        for i, n in enumerate(nodes):
            self.pos[id(n)] = i
            label = _node_label(n)
            self.labels.setdefault(label, []).append(n)
            if not _is_file_hub_node(n):
                name = _bare_name(label)
                self.bare.setdefault(name, []).append(n)
                if name:
                    self.pool.setdefault(name.lower(), n)

    def by_name(self, label: str, bare: str) -> list[dict]:
        """Nodes labelled ``label`` or (non-hub) with bare name ``bare``, in graph order."""
        hits = {id(n): n for n in self.labels.get(label, ())}
        for n in self.bare.get(bare, ()):
            hits.setdefault(id(n), n)
        return sorted(hits.values(), key=lambda n: self.pos[id(n)])


# Same identity-guarded FIFO scheme as _RESOLVE_CACHE.
_NAME_CACHE: dict[int, tuple[list[dict], _NameIndex]] = {}


def _name_index(nodes: list[dict]) -> _NameIndex:
    cached = _NAME_CACHE.get(id(nodes))
    if cached is not None and cached[0] is nodes:
        return cached[1]
    index = _NameIndex(nodes)
    if id(nodes) not in _NAME_CACHE and len(_NAME_CACHE) >= _RESOLVE_CACHE_MAX:
        _NAME_CACHE.pop(next(iter(_NAME_CACHE)), None)  # FIFO
    _NAME_CACHE[id(nodes)] = (nodes, index)
    return index


def _substring_matches(nodes: list[dict], key: str, cap: int = 6) -> tuple[list[dict], int]:
    """Nodes whose label or id contains ``key`` (case-insensitive), first ``cap`` in
    graph order, and how many match in all. The first is what ``_resolve_node``
    picks."""
    k = key.lower()
    hits: list[dict] = []
    total = 0
    for n in nodes:
        if k in _node_label(n).lower() or k in _node_id(n).lower():
            total += 1
            if len(hits) < cap:
                hits.append(n)
    return hits, total


# Above this many distinct names, the "did you mean" pool is narrowed before
# difflib (whose cost is per candidate) runs.
_CLOSE_MATCH_POOL = 5000


def _close_matches(nodes: list[dict], key: str, n: int = 5) -> list[dict]:
    """Nodes whose bare name is close to ``key`` (difflib ratio >= 0.6), best first.

    For a name that matched nothing. The candidate pool is bounded on big graphs:
    names of a comparable length first, then names sharing the first letter, then
    graph order.
    """
    want = _bare_name(key).lower()
    if not want:
        return []
    pool = _name_index(nodes).pool
    names = list(pool)
    if len(names) > _CLOSE_MATCH_POOL:
        lo, hi = len(want) * 0.6, len(want) / 0.6
        names = [s for s in names if lo <= len(s) <= hi]
    if len(names) > _CLOSE_MATCH_POOL:
        names = [s for s in names if s[:1] == want[:1]]
    names = names[:_CLOSE_MATCH_POOL]
    return [pool[s] for s in difflib.get_close_matches(want, names, n=n, cutoff=0.6)]


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
    if _TIKTOKEN_ENC is None and config.offline():
        # tiktoken fetches its encoding file on first use unless it is cached;
        # offline, count with the heuristic (and probe again once back online)
        return None
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


_EdgeFlags = dict[tuple[str, str, str], dict[str, str]]
_EDGE_FLAGS_CACHE: dict[int, tuple[list[dict], _EdgeFlags]] = {}


def _edge_flags(edges: list[dict]) -> _EdgeFlags:
    """``(source, target, relation) -> {"confidence"?, "resolved_by"?}`` for the
    edges whose provenance is worth showing next to them: inferred or ambiguous
    ones, and ones graphlore resolved from the source. An extracted graph edge is
    the default and has no entry. Cached on list identity like _edge_set."""
    cached = _EDGE_FLAGS_CACHE.get(id(edges))
    if cached is not None and cached[0] is edges:
        return cached[1]
    flags: _EdgeFlags = {}
    for e in edges:
        f: dict[str, str] = {}
        conf = str(e.get("confidence") or "EXTRACTED").upper()
        if conf != "EXTRACTED":
            f["confidence"] = conf
        if e.get("resolved_by"):
            f["resolved_by"] = str(e["resolved_by"])
        if f:
            flags.setdefault((*_edge_ends(e), _edge_rel(e)), f)
    if id(edges) not in _EDGE_FLAGS_CACHE and len(_EDGE_FLAGS_CACHE) >= _ADJ_CACHE_MAX:
        _EDGE_FLAGS_CACHE.pop(next(iter(_EDGE_FLAGS_CACHE)), None)  # FIFO
    _EDGE_FLAGS_CACHE[id(edges)] = (edges, flags)
    return flags


def _flag_suffix(flags: dict[str, str] | None) -> str:
    """``, inferred`` / ``, resolved`` for a text row, as graphlore_impact prints."""
    if not flags:
        return ""
    out = ""
    if flags.get("confidence"):
        out += ", " + flags["confidence"].lower()
    if flags.get("resolved_by"):
        out += ", resolved"
    return out


def _bfs_subgraph(
    adj: dict[str, list[tuple[str, str]]],
    labels: Any,  # id -> display label; dict or server._DisplayLabels (duck-typed .get)
    start_id: str,
    hops: int,
    budget_tokens: int,
    edge_set: set[tuple[str, str, str]] | None = None,
    order: Callable[[tuple[str, str]], Any] | None = None,
    *,
    edge_flags: _EdgeFlags | None = None,
) -> tuple[set[str], list[dict], bool, int]:
    """BFS around start_id collecting edges until a token budget is hit.

    Returns (visited_ids, edges, truncated, approx_tokens). Shared by
    graphlore_subgraph and graphlore_locate. ``edge_set`` (from :func:`_edge_set`)
    restores the true orientation of each collected edge: the undirected
    adjacency flattens direction, and an edge reached via its reverse entry must
    not be reported as ``B —calls→ A`` when the graph says ``A —calls→ B``.
    ``order`` is a sort key over a node's ``(neighbor, relation)`` entries: what
    comes first is what the budget keeps. ``edge_flags`` (from :func:`_edge_flags`)
    adds ``confidence`` / ``resolved_by`` to the edges that carry them.
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
        neighbours = adj.get(cur, [])
        for nb, rel in (sorted(neighbours, key=order) if order else neighbours):
            src, dst = cur, nb
            if (edge_set is not None and (src, dst, rel) not in edge_set
                    and (dst, src, rel) in edge_set):
                src, dst = dst, src  # traversed backwards: emit the true direction
            key = tuple(sorted((cur, nb)) + [rel])  # type: ignore
            if key not in seen_pairs:
                seen_pairs.add(key)
                edge = {"from": labels.get(src, src), "to": labels.get(dst, dst), "relation": rel}
                if edge_flags:
                    edge.update(edge_flags.get((src, dst, rel), {}))
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


def _cycle_path(forward: _Adj, comp: list[str]) -> list[str]:
    """One shortest concrete route through a cycle group: ``[a, b, ..., a]``.

    An SCC proves its members are mutually reachable; this names one actual loop
    so a reader can follow it. Starts at the smallest member for a stable result.
    """
    members = set(comp)
    start = min(comp)
    prev: dict[str, str] = {}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nb, _rel_name in forward.get(cur, []):
            if nb == start:
                path = [cur]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                path.reverse()
                return [*path, start]
            if nb in members and nb not in prev:
                prev[nb] = cur
                queue.append(nb)
    return []


def _relation_adjacency(edges: list[dict], relations: frozenset[str]) -> _Adj:
    """Directed adjacency over the edges of the given relations, self-edges dropped."""
    forward: _Adj = {}
    for e in edges:
        rel = _rel(e)
        if rel not in relations:
            continue
        s, t = _edge_ends(e)
        if s != t:
            forward.setdefault(s, []).append((t, rel))
    return forward


def _module_import_adjacency(
    nodes: list[dict], edges: list[dict], kind_of: ImportKind | None = None
) -> tuple[_Adj, int]:
    """(file -> [(imported file, import kind)], implicit-package edges ignored).

    The second item of each adjacency entry is WHEN the import runs (see
    ``IMPORT_*``), not a relation name; several edges between two files collapse
    to the one that runs earliest.

    A module's import of its own package's entry file is left out when the edge
    targets the file as a whole: ``from . import x`` makes graphify link the
    module to ``__init__.py``, but the parent package is always imported before
    its submodules, so that edge can never be the reason an import fails. An
    import of a *symbol* defined in ``__init__.py`` is kept.
    """
    rank = {IMPORT_MODULE_LEVEL: 0, IMPORT_UNCLASSIFIED: 1, IMPORT_DEFERRED: 2,
            IMPORT_TYPE_CHECKING: 3}
    best: dict[tuple[str, str], str] = {}
    implicit = 0
    for sf, tf, _e, dst, kind in _file_import_edges(nodes, edges, kind_of):
        if _is_file_hub_node(dst) and _is_package_entry_of(tf, sf):
            implicit += 1
            continue
        known = best.get((sf, tf))
        if known is None or rank[kind] < rank[known]:
            best[(sf, tf)] = kind
    forward: _Adj = {}
    for (sf, tf), kind in best.items():
        forward.setdefault(sf, []).append((tf, kind))
    return forward, implicit


def _shortest_path(forward: _Adj, members: set[str], src: str, dst: str) -> list[str]:
    """Shortest ``src -> ... -> dst`` route staying inside ``members`` ([] if none)."""
    if src == dst:
        return [src]
    prev: dict[str, str] = {}
    queue = deque([src])
    while queue:
        cur = queue.popleft()
        for nb, _kind in forward.get(cur, []):
            if nb not in members or nb in prev or nb == src:
                continue
            prev[nb] = cur
            if nb == dst:
                path = [dst]
                while path[-1] != src:
                    path.append(prev[path[-1]])
                return path[::-1]
            queue.append(nb)
    return []


# The three kinds of import cycle, most serious first, with the import kinds
# each one's loops may use.
_IMPORT_CYCLE_TIERS: tuple[tuple[str, frozenset[str]], ...] = (
    ("runtime", _IMPORT_RUNS_AT_IMPORT),
    ("deferred", _IMPORT_RUNS_AT_IMPORT | {IMPORT_DEFERRED}),
    ("type-only", _IMPORT_RUNS_AT_IMPORT | {IMPORT_DEFERRED, IMPORT_TYPE_CHECKING}),
)


def _tiered_import_cycles(forward: _Adj) -> list[dict[str, Any]]:
    """Import cycles between modules, classified by when their imports run.

    ``runtime``   — some loop consists of module-level imports only: it can fail
                    at start-up.
    ``deferred``  — the group only closes through an import inside a function.
    ``type-only`` — the group only closes through an import under
                    ``if TYPE_CHECKING:``; it exists for the type checker alone.
    A group is reported once, in the first tier where it is a cycle, with one
    loop through an import that tier needed (``path``) and the kind of each hop
    (``hops``).
    """
    out: list[dict[str, Any]] = []
    seen: set[frozenset[str]] = set()
    lower_of: dict[str, int] = {}  # node -> its cycle group in the previous tier

    def _apart(u: str, v: str) -> bool:
        gu, gv = lower_of.get(u), lower_of.get(v)
        return gu is None or gv is None or gu != gv

    for tier, allowed in _IMPORT_CYCLE_TIERS:
        sub: _Adj = {
            s: [(t, k) for t, k in lst if k in allowed] for s, lst in forward.items()
        }
        groups = _find_cycles(sub)[0]
        for comp in groups:
            key = frozenset(comp)
            if key in seen:
                continue
            seen.add(key)
            members = set(comp)
            path: list[str] = []
            if tier != "runtime":
                # Lead with an import this tier added that joins two parts the
                # earlier tiers kept apart — the edge the cycle depends on.
                newest = IMPORT_DEFERRED if tier == "deferred" else IMPORT_TYPE_CHECKING
                for u in sorted(members):
                    for v, k in sorted(sub.get(u, [])):
                        if k == newest and v in members and _apart(u, v):
                            back = _shortest_path(sub, members, v, u)
                            if back:
                                path = [u, *back]
                                break
                    if path:
                        break
            if not path:
                path = _cycle_path(sub, comp)
            kinds = {(u, v): k for u in members for v, k in sub.get(u, [])}
            out.append({
                "tier": tier, "nodes": comp, "path": path,
                "hops": [kinds.get((a, b), IMPORT_UNCLASSIFIED)
                         for a, b in zip(path, path[1:], strict=False)],
            })
        for i, comp in enumerate(groups):
            for n in comp:
                lower_of[n] = i
    return out


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
    exclude: frozenset[str] = frozenset(),
    edge_ok: Callable[[dict], bool] | None = None,
) -> dict[str, tuple[int, str, dict | None]]:
    """Like :func:`_hop_distances`, but records HOW each node was reached.

    Returns ``{node_id: (distance, reached_via_id, discovery_edge)}`` — the edge
    that first reached the node on a shortest path. ``relations=None`` walks
    every edge type, so the distances match _hop_distances exactly. ``seeds`` are
    extra depth-0 entries (used to keep a class's own members reachable when
    containment edges are filtered out); they carry ``None`` as their edge so
    callers can tell them from real hits. ``exclude`` names relations never
    walked, whatever ``relations`` says; ``edge_ok`` rejects single edges (an
    inferred one, under a strict walk).
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
            rel = _rel(e)
            if rel in exclude or (relations is not None and rel not in relations):
                continue
            if edge_ok is not None and not edge_ok(e):
                continue
            if nb in out:
                continue
            out[nb] = (d + 1, cur, e)
            frontier.append((nb, d + 1))
    return out


def _beyond_hops(
    adj: _EAdj,
    recs: dict[str, tuple[int, str, dict | None]],
    max_hops: int,
    relations: frozenset[str] | None = None,
    exclude: frozenset[str] = frozenset(),
    edge_ok: Callable[[dict], bool] | None = None,
) -> int:
    """How many nodes one more hop past ``max_hops`` would add to ``recs`` (a
    :func:`_hop_records` result walked with the same filters). Zero means the
    walk reached everything it could: the hop limit cut nothing off."""
    more: set[str] = set()
    for nid, (d, _via, _edge) in recs.items():
        if d != max_hops:
            continue
        for nb, e in adj.get(nid, []):
            rel = _rel(e)
            if rel in exclude or (relations is not None and rel not in relations):
                continue
            if edge_ok is not None and not edge_ok(e):
                continue
            if nb not in recs:
                more.add(nb)
    return len(more)
