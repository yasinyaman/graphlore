"""Review analyses over the graph: unreferenced code, same-named symbols, layer rules.

Each is exact and needs no embedding model. They read the graph's own edges and,
where a claim needs checking against the source (a decorator that registers a
function, a name mentioned in a template or a config file), the files under
``config.PROJECT_DIR`` through the confined readers of :mod:`graphlore.spans`.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from . import config
from .graph import (
    _IMPORT_RELATIONS,
    _NON_DEPENDENCY_RELATIONS,
    _edge_ends,
    _file_import_edges,
    _is_file_hub_node,
    _is_test_path,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _rel,
)
from .scope import _clean_pattern, _path_matches, _vendored_by_path
from .spans import _norm_relpath, _resolve_in_project, _spans_for_file

# Extensions whose nodes are source definitions (not config keys or doc headings).
_CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".java", ".kt", ".kts", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cxx", ".hxx", ".cs", ".rb", ".php", ".swift", ".scala", ".m", ".mm",
    ".lua", ".zig", ".ex", ".exs",
})
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_DECORATOR_LINE = re.compile(r"^\s*@\s*([A-Za-z_][\w.]*)")


def _bare_name(label: str) -> str:
    """``.dump()`` -> ``dump``, ``helper()`` -> ``helper``, ``TableSchema`` -> itself."""
    name = label.strip()
    if name.endswith("()"):
        name = name[:-2]
    return name.lstrip(".").rsplit(".", 1)[-1]


def _int_line(n: dict) -> int:
    try:
        return int(_node_line(n))
    except (TypeError, ValueError):
        return 0


class _Definitions:
    """Which nodes are definitions, of what kind, and how they nest.

    ``kind`` is "function", "method" or "class". graphify >= 0.9 marks callables
    (``_callable`` / ``_callable_class``); older graphs are read from the label
    convention (``name()``, ``.method()``) and from containment — a class is an
    identifier-labelled code node that a file contains or that has methods.
    """

    def __init__(self, nodes: list[dict], edges: list[dict]) -> None:
        self.byid: dict[str, dict] = {_node_id(n): n for n in nodes}
        self.parent: dict[str, str] = {}
        self.members: dict[str, list[str]] = {}
        self.incoming: dict[str, list[tuple[str, dict]]] = {}
        has_methods: set[str] = set()
        contained: set[str] = set()
        for e in edges:
            s, t = _edge_ends(e)
            rel = _rel(e)
            if rel == "method":
                has_methods.add(s)
                self.parent.setdefault(t, s)
                self.members.setdefault(s, []).append(t)
            elif rel == "contains":
                contained.add(t)
                self.parent.setdefault(t, s)
            elif s != t and rel not in _NON_DEPENDENCY_RELATIONS:
                self.incoming.setdefault(t, []).append((s, e))
        self.kind: dict[str, str] = {}
        for nid, n in self.byid.items():
            k = self._kind(n, nid in has_methods, nid in contained)
            if k:
                self.kind[nid] = k

    @staticmethod
    def _kind(n: dict, has_methods: bool, contained: bool) -> str:
        f = _node_file(n)
        dot = f.rfind(".")
        if not f or dot < 0 or f[dot:].lower() not in _CODE_EXTS or _is_file_hub_node(n):
            return ""
        if str(n.get("file_type") or "code").lower() != "code":
            return ""
        label = _node_label(n)
        if n.get("_callable_class"):
            return "class"
        if label.endswith("()"):
            return "method" if label.startswith(".") else "function"
        if n.get("_callable"):
            return "function"
        if (has_methods or contained) and _IDENT.fullmatch(label):
            return "class"
        return ""

    def is_top_level(self, nid: str) -> bool:
        """Defined directly in a file (not a method, not nested in a function)."""
        parent = self.byid.get(self.parent.get(nid, ""))
        return parent is None or _is_file_hub_node(parent)

    def referencing_files(self, nid: str, relations: frozenset[str] | None = None) -> set[str]:
        """Other files with an edge into ``nid`` (or, for a class, into its members)."""
        own = _node_file(self.byid[nid])
        family = {nid, *self.members.get(nid, ())}
        out: set[str] = set()
        for target in family:
            for src, e in self.incoming.get(target, ()):
                if src in family or (relations is not None and _rel(e) not in relations):
                    continue
                n = self.byid.get(src)
                f = (_node_file(n) if n else "") or str(e.get("source_file") or "")
                if f and f != own:
                    out.add(f)
        return out


# ---------------------------------------------------------------------------
# Unreferenced code
# ---------------------------------------------------------------------------

# Decorators that change how a function behaves but hand it to no one: a function
# carrying only these is still dead when nothing references it. Compared against
# the last dotted segment, lower-cased. Any OTHER decorator is taken to register
# the function somewhere the graph cannot see (a route table, a CLI group, a
# validator list, a pytest fixture registry).
_NEUTRAL_DECORATORS = frozenset({
    "staticmethod", "classmethod", "wraps", "lru_cache", "cache", "cached",
    "contextmanager", "asynccontextmanager", "dataclass", "total_ordering",
    "overload", "final", "override", "abstractmethod", "deprecated",
    "no_type_check", "suppresswarnings", "safevarargs", "functionalinterface",
    "inline", "noinline", "nonnull", "nullable", "notnull",
})
_MENTION_SKIP_DIRS = frozenset({
    "node_modules", "venv", "env", "__pycache__", "dist", "build", "target",
    "site-packages",
})
_MENTION_MAX_BYTES = 1_000_000
_MENTION_MAX_FILES = 20_000


# The line opening a class that names a base: `class V(ast.NodeVisitor):`,
# `class Handler extends Base {`, `class Repo : IRepo`. Empty parentheses don't count.
_CLASS_WITH_BASE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|abstract|final|default|static|open|"
    r"sealed|data)\s+)*class\s+[\w$]+\s*(?:<[^>]*>)?\s*"
    r"(?:\((?!\s*\))|extends\b|implements\b|:\s*[\w$])"
)
_INHERITANCE_RELATIONS = frozenset({"inherits", "extends", "implements", "mixes_in"})


def _in_subclass(file: str, line: int, lines: _SourceLines) -> bool:
    """Is the definition at ``line`` written directly inside a class that has a base?

    Read from the source, because graphify gives no node to a class nested in a
    function — exactly where visitor and event-handler subclasses tend to live.
    """
    spans = _spans_for_file(file)
    own = next((s for s in spans if s[2] == line), None)
    if own is None:
        return False
    outer = [s for s in spans if s[0] <= own[0] and s[1] >= own[1] and s is not own]
    if not outer:
        return False
    parent = min(outer, key=lambda s: s[1] - s[0])
    text = lines.get(file)
    return 0 < parent[2] <= len(text) and bool(_CLASS_WITH_BASE.match(text[parent[2] - 1]))


def _span_for(file: str, line: int) -> tuple[int, int] | None:
    """(region_start, end) of the definition at ``line``, from the span engine."""
    if line <= 0:
        return None
    spans = _spans_for_file(file)
    exact = [s for s in spans if s[2] == line]
    here = exact or [s for s in spans if s[0] <= line <= s[1]]
    if not here:
        return None
    rs, end, _dl, _q = min(here, key=lambda s: s[1] - s[0])
    return rs, end


class _SourceLines:
    """Per-call cache of file lines, read through the project confinement."""

    def __init__(self) -> None:
        self._files: dict[str, list[str]] = {}

    def get(self, file: str) -> list[str]:
        got = self._files.get(file)
        if got is None:
            got = []
            full = _resolve_in_project(file)
            if full is not None:
                try:
                    got = full.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    got = []
            self._files[file] = got
        return got


def _decorators(
    n: dict, nid: str, edges_out: dict[str, list[tuple[str, dict]]], byid: dict[str, dict],
    lines: _SourceLines,
) -> list[str]:
    """Decorator / annotation names on a definition.

    From the graph where it recorded them (a ``references`` edge whose context is
    ``decorator``), and from the source lines above the definition, which works in
    any language the span engine reads.
    """
    found: list[str] = []
    for target, e in edges_out.get(nid, ()):
        if str(e.get("context") or "").lower() == "decorator":
            t = byid.get(target)
            found.append(_node_label(t) if t else target)
    file, line = _node_file(n), _int_line(n)
    span = _span_for(file, line)
    if span is not None and span[0] < line:
        for text in lines.get(file)[span[0] - 1:line - 1]:
            m = _DECORATOR_LINE.match(text)
            if m:
                found.append(m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for d in found:
        short = d.rsplit(".", 1)[-1].rstrip("()")
        if short and short.lower() not in seen:
            seen.add(short.lower())
            out.append(short)
    return out


def _count_mentions(names: set[str]) -> tuple[Counter[str], int, bool]:
    """How often each name occurs as a whole identifier in the project's text files.

    Walks ``config.PROJECT_DIR`` once: templates, configs and docs count too, since
    a function named in ``pyproject.toml`` or called from an HTML attribute is not
    dead. Hidden directories, dependency and build trees, vendored or oversized
    files and binaries are skipped. Returns (counts, files read, hit the file cap).
    """
    counts: Counter[str] = Counter()
    if not names:
        return counts, 0, False
    root = config.PROJECT_DIR.resolve()
    scanned = 0
    capped = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _MENTION_SKIP_DIRS
            and d != config.OUT_DIR_NAME
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if _vendored_by_path(rel) or os.path.islink(full):
                continue  # a link may point outside the project: never follow it
            try:
                if os.path.getsize(full) > _MENTION_MAX_BYTES:
                    continue
                with open(full, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            if b"\0" in raw[:2048]:
                continue  # binary
            if scanned >= _MENTION_MAX_FILES:
                capped = True
                break
            scanned += 1
            for tok in _IDENT.findall(raw.decode("utf-8", "replace")):
                if tok in names:
                    counts[tok] += 1
        if capped:
            break
    return counts, scanned, capped


def _is_test_definition(name: str, file: str) -> bool:
    """A test the runner collects by naming convention, never by reference."""
    return _is_test_path(file) and (name.startswith(("test", "Test")) or name.endswith("Test"))


def _unreferenced(
    scoped_nodes: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
    count_test_references: bool = True,
) -> dict[str, Any]:
    """Definitions in ``scoped_nodes`` that nothing in the graph or the text refers to.

    References are counted over the WHOLE graph, whatever the scope: the scope
    picks which definitions to examine, not who may refer to them.
    """
    defs = _Definitions(all_nodes, all_edges)
    edges_out: dict[str, list[tuple[str, dict]]] = {}
    for e in all_edges:
        s, t = _edge_ends(e)
        edges_out.setdefault(s, []).append((t, e))

    examined = 0
    no_graph_ref: list[tuple[str, dict, str, str]] = []  # (id, node, kind, bare name)
    for n in scoped_nodes:
        nid = _node_id(n)
        kind = defs.kind.get(nid)
        if not kind:
            continue
        name = _bare_name(_node_label(n))
        file = _node_file(n)
        if not name or (name.startswith("__") and name.endswith("__")):
            continue  # dunder methods are called by the language, not by name
        if _is_test_definition(name, file):
            continue
        examined += 1
        family = {nid, *defs.members.get(nid, ())}
        referenced = False
        for target in family:
            for src, _e in defs.incoming.get(target, ()):
                if src in family:
                    continue
                if not count_test_references:
                    sn = defs.byid.get(src)
                    if sn is not None and _is_test_path(_node_file(sn)):
                        continue
                referenced = True
                break
            if referenced:
                break
        if not referenced:
            no_graph_ref.append((nid, n, kind, name))

    lines = _SourceLines()
    registered: Counter[str] = Counter()
    remaining: list[tuple[str, dict, str, str]] = []
    for nid, n, kind, name in no_graph_ref:
        decos = [
            d for d in _decorators(n, nid, edges_out, defs.byid, lines)
            if d.lower() not in _NEUTRAL_DECORATORS
        ]
        if decos:
            registered[decos[0]] += 1
        else:
            remaining.append((nid, n, kind, name))

    names = {name for _nid, _n, _kind, name in remaining}
    totals, files_scanned, capped = _count_mentions(names)
    subclasses = {
        _edge_ends(e)[0] for e in all_edges if _rel(e) in _INHERITANCE_RELATIONS
    }
    # every definition of a name accounts for one occurrence of it: its own
    definitions_of: Counter[str] = Counter(
        _bare_name(_node_label(defs.byid[nid])) for nid in defs.kind
    )
    items: list[dict[str, Any]] = []
    for nid, n, kind, name in remaining:
        file, line = _node_file(n), _int_line(n)
        own = 1
        span = _span_for(file, line)
        if span is not None:
            body = "\n".join(lines.get(file)[span[0] - 1:span[1]])
            own = max(1, sum(1 for tok in _IDENT.findall(body) if tok == name))
        others = max(0, definitions_of[name] - 1)
        item: dict[str, Any] = {
            "id": nid, "name": name, "kind": kind, "file": file, "line": line or None,
            "mentions": max(0, totals[name] - own - others),
        }
        parent = defs.parent.get(nid, "")
        if parent in subclasses or _in_subclass(file, line, lines):
            # called through the base class, which the graph cannot follow
            item["note"] = "method of a subclass: may override a hook its base class calls"
        elif kind == "method" and not name.startswith("_") and parent in defs.byid:
            if defs.referencing_files(parent):
                item["note"] = "public method of a class used elsewhere: unused here, may be API"
        items.append(item)
    items.sort(key=lambda it: (it["mentions"], "note" in it, it["file"], it["line"] or 0))
    return {
        "examined": examined,
        "no_graph_reference": len(no_graph_ref),
        "registered_by_decorator": dict(registered.most_common()),
        "items": items,
        "files_scanned_for_mentions": files_scanned,
        "mention_scan_capped": capped,
    }


# ---------------------------------------------------------------------------
# Same-named symbols
# ---------------------------------------------------------------------------

# Names every script or module is expected to repeat.
_CONVENTIONAL_NAMES = frozenset({"main", "setup", "teardown"})


def _same_named(
    scoped_nodes: list[dict], all_nodes: list[dict], all_edges: list[dict],
    kinds: frozenset[str] = frozenset({"class", "function"}),
) -> list[dict[str, Any]]:
    """Top-level classes / functions that share a name across different files."""
    defs = _Definitions(all_nodes, all_edges)
    groups: dict[str, list[tuple[str, dict, str]]] = {}
    for n in scoped_nodes:
        nid = _node_id(n)
        kind = defs.kind.get(nid)
        if kind not in kinds or not defs.is_top_level(nid):
            continue
        name = _bare_name(_node_label(n))
        if not name or name in _CONVENTIONAL_NAMES or (
            name.startswith("__") and name.endswith("__")
        ):
            continue
        groups.setdefault(name, []).append((nid, n, kind))
    out: list[dict[str, Any]] = []
    for name, members in groups.items():
        if len({_node_file(n) for _nid, n, _k in members}) < 2:
            continue
        definitions: list[dict[str, Any]] = []
        for nid, n, kind in members:
            definitions.append({
                "id": nid, "kind": kind, "file": _node_file(n), "line": _int_line(n) or None,
                "imported_by": len(defs.referencing_files(nid, _IMPORT_RELATIONS)),
                "referenced_by": len(defs.referencing_files(nid)),
            })
        definitions.sort(key=lambda d: (-d["imported_by"], -d["referenced_by"], d["file"]))
        out.append({
            "name": name,
            "kinds": sorted({d["kind"] for d in definitions}),
            "modules": len({d["file"] for d in definitions}),
            "definitions": definitions,
        })
    out.sort(key=lambda g: (
        "class" not in g["kinds"],
        -sum(d["imported_by"] for d in g["definitions"]),
        -g["modules"],
        g["name"],
    ))
    return out


# ---------------------------------------------------------------------------
# Layer rules
# ---------------------------------------------------------------------------


def _normalize_rules(raw: object) -> dict[str, list[str]] | str:
    """Validate ``{layer prefix: [prefixes it may import]}``; str = error message."""
    if isinstance(raw, dict) and isinstance(raw.get("layers"), dict):
        raw = raw["layers"]  # the file form may wrap the mapping
    if not isinstance(raw, dict) or not raw:
        return (
            "ERROR: layer rules must be a non-empty object mapping a path prefix to the "
            'prefixes it may import, e.g. {"src/app/api": ["src/app/core"], '
            '"src/app/core": []}.'
        )
    rules: dict[str, list[str]] = {}
    for key, allowed in raw.items():
        layer = _clean_pattern(str(key))
        if not layer:
            return f"ERROR: empty layer prefix in the rules ({key!r})."
        if isinstance(allowed, str):
            allowed = [allowed]
        if not isinstance(allowed, list | tuple):
            return f"ERROR: the rule for '{key}' must be a list of path prefixes."
        rules[layer] = [
            a for a in ("*" if str(x).strip() == "*" else _clean_pattern(str(x)) for x in allowed)
            if a
        ]
    return rules


def _layer_of(path: str, rules: dict[str, list[str]]) -> str | None:
    """The most specific declared layer holding ``path`` (longest matching prefix)."""
    best: str | None = None
    for layer in rules:
        if _path_matches(layer, path) and (best is None or len(layer) > len(best)):
            best = layer
    return best


def _layer_violations(
    nodes: list[dict], edges: list[dict], rules: dict[str, list[str]], strict: bool = False
) -> dict[str, Any]:
    """Import edges that break ``rules``, grouped by the importing layer.

    An import is allowed inside one layer, into any prefix the layer lists, and —
    unless ``strict`` — into files that belong to no declared layer. Imports of
    external packages are never checked.
    """
    files = sorted({_norm_relpath(_node_file(n)) for n in nodes} - {""})
    layers: dict[str, dict[str, Any]] = {
        layer: {"layer": layer, "may_import": allowed, "files": 0,
                "imports_checked": 0, "violations": []}
        for layer, allowed in rules.items()
    }
    for f in files:
        layer = _layer_of(f, rules)
        if layer is not None:
            layers[layer]["files"] += 1
    seen: set[tuple[str, str, str]] = set()
    for sf, tf, e, dst in _file_import_edges(nodes, edges):
        src_layer = _layer_of(sf, rules)
        if src_layer is None:
            continue
        entry = layers[src_layer]
        loc = str(e.get("source_location") or "")
        key = (sf, tf, loc)
        if key in seen:
            continue
        seen.add(key)
        entry["imports_checked"] += 1
        dst_layer = _layer_of(tf, rules)
        if dst_layer == src_layer:
            continue
        allowed = rules[src_layer]
        if "*" in allowed or any(_path_matches(a, tf) for a in allowed):
            continue
        if dst_layer is None and not strict:
            continue
        violation: dict[str, Any] = {
            "from_file": sf, "to_file": tf, "to_layer": dst_layer,
            "site": f"{sf}:{loc}" if loc else sf,
        }
        if not _is_file_hub_node(dst):
            violation["imported"] = _node_label(dst)
        entry["violations"].append(violation)
    for entry in layers.values():
        entry["violations"].sort(key=lambda v: (v["from_file"], v["site"], v["to_file"]))
    unmatched = sorted(
        {layer for layer, entry in layers.items() if entry["files"] == 0}
        | {a for allowed in rules.values() for a in allowed
           if a != "*" and not any(_path_matches(a, f) for f in files)}
    )
    return {
        "layers": list(layers.values()),
        "violation_count": sum(len(entry["violations"]) for entry in layers.values()),
        "imports_checked": sum(entry["imports_checked"] for entry in layers.values()),
        "unmatched_prefixes": unmatched,
    }
