"""Edges the graph lacks and the source proves: calls through an imported module.

graphify's local extractor links ``from pkg import helper; helper()`` to the
function, but not ``from pkg import mod; mod.helper()`` — nor, often, the import
of ``mod`` itself. A test file that says ``from graphlore import server`` and
calls ``server.graphlore_unreferenced(...)`` four times then leaves that tool
with no caller at all, and every answer read from the edges (impact, neighbors,
subgraph, tests-for, references discounted in ``graphlore_unreferenced``) is
short by exactly those calls.

:func:`_resolved` adds them at read time, from the importing file's own AST:
``mod.name(...)`` where ``mod`` is bound by an import to a first-party module
that defines ``name`` (or re-exports it from one that does). Added edges are
marked ``resolved_by: "graphlore"`` and never written back to graph.json.

Per language, only what its extractor is seen to leave out:

  * **Python** — every call through a module object, and the module import.
  * **JS / TS** — calls through a default import (``import api from './api'``)
    and through a dynamically imported module (``const m = await import(..)``);
    graphify already links namespace imports and ``require``.
  * **Go, Java** — nothing: graphify links package-selector and static calls
    itself, so there is no gap to fill.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, NamedTuple

from . import config
from .graph import (
    _CALL_RELATIONS,
    _IMPORT_RELATIONS,
    _bare_name,
    _edge_ends,
    _is_file_hub_node,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _rel,
)
from .imports import _JS_EXTS, ImportChecker, _js_for_file, _module_parts
from .spans import _cached_file_analysis, _enclosing_spans, _norm_relpath

RESOLVED_BY = "graphlore"
# How far a name is followed through re-exports (`from .graph import x` in the
# module the caller went through).
_REEXPORT_DEPTH = 3


class _Binding(NamedTuple):
    level: int                # 0 = absolute import, n = leading dots
    module: tuple[str, ...]   # dotted path of the statement's module
    name: str | None          # the from-imported name, None for `import a.b [as m]`
    rooted: bool              # `import a.b.c` with no alias: the name is the root `a`


class _FileCalls(NamedTuple):
    bindings: dict[str, _Binding]
    # (lines of the enclosing defs, innermost first; line of the call; a.b.name chain)
    calls: list[tuple[tuple[int, ...], int, tuple[str, ...]]]


def _attribute_chain(node: ast.expr) -> tuple[str, ...] | None:
    """``a.b.c`` -> (a, b, c); None for anything not a plain dotted name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return tuple(reversed(parts))


def _python_module_calls(src: bytes) -> _FileCalls | None:
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001 - unparseable: nothing to resolve
        return None
    bindings: dict[str, _Binding] = {}
    raw: list[tuple[tuple[int, ...], int, tuple[str, ...]]] = []

    def walk(node: ast.AST, stack: tuple[int, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for a in child.names:
                    parts = tuple(a.name.split("."))
                    if a.asname:
                        bindings[a.asname] = _Binding(0, parts, None, False)
                    else:
                        bindings.setdefault(parts[0], _Binding(0, parts[:1], None, True))
            elif isinstance(child, ast.ImportFrom):
                module = tuple(child.module.split(".")) if child.module else ()
                for a in child.names:
                    if a.name != "*":
                        bindings[a.asname or a.name] = _Binding(
                            child.level or 0, module, a.name, False
                        )
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, (child.lineno, *stack))
                continue
            elif isinstance(child, ast.Call):
                chain = _attribute_chain(child.func)
                if chain is not None and len(chain) >= 2:
                    raw.append((stack, child.lineno, chain))
            walk(child, stack)

    try:
        walk(tree, ())
    except RecursionError:
        return None
    return _FileCalls(bindings, [c for c in raw if c[2][0] in bindings])


# rel path -> (mtime, _FileCalls | None); the (path, mtime) scheme of the span cache.
_CALLS_CACHE: dict[str, tuple[float, Any]] = {}


def _module_calls_for_file(rel: str) -> _FileCalls | None:
    if not rel.lower().endswith(".py"):
        return None
    return _cached_file_analysis(
        _CALLS_CACHE, rel, lambda src, _rel_path: _python_module_calls(src), lambda: None
    )


@dataclass
class Resolved:
    """The graph's edges plus the ones graphlore resolved from the source.

    ``edges`` IS the list passed in when nothing was added, so identity-keyed
    caches keep hitting; otherwise it is built once per graph and reused.
    """

    edges: list[dict]
    calls: int = 0
    imports: int = 0

    def payload(self) -> dict[str, int]:
        return {"calls": self.calls, "imports": self.imports}

    def note(self) -> str:
        """One line naming what was added; "" when nothing was."""
        if not (self.calls or self.imports):
            return ""
        bits = []
        if self.calls:
            bits.append(f"{self.calls} call(s) made through an imported module")
        if self.imports:
            bits.append(f"{self.imports} module import(s)")
        return (
            "Includes " + " and ".join(bits) + " that graph.json lacks, resolved by "
            "graphlore from the source."
        )


def _enabled() -> bool:
    return config.env("RESOLVE", "1").strip().lower() not in ("0", "false", "no", "off")


def _build(nodes: list[dict], edges: list[dict]) -> Resolved:
    # Indexes over the graph's Python files: module path -> file, the node that
    # stands for each file, and each file's definitions by name and by line.
    by_parts: dict[tuple[str, ...], str] = {}
    by_last: dict[str, list[tuple[str, ...]]] = {}
    hub: dict[str, str] = {}
    by_name: dict[str, dict[str, str]] = {}
    by_line: dict[str, dict[int, str]] = {}
    js_files: set[str] = set()
    for n in nodes:
        file = _norm_relpath(_node_file(n))
        low = file.lower()
        if low.endswith(_JS_EXTS):
            js_files.add(file)
        elif not low.endswith(".py"):
            continue
        parts = _module_parts(file) if low.endswith(".py") else None
        if parts is not None and parts not in by_parts:
            by_parts[parts] = file
            if parts:
                by_last.setdefault(parts[-1], []).append(parts)
        nid = _node_id(n)
        if _is_file_hub_node(n):
            hub.setdefault(file, nid)
            continue
        if str(n.get("file_type") or "code").lower() != "code":
            continue
        label = _node_label(n)
        if not label.startswith("."):  # a module's own names, not its classes' methods
            by_name.setdefault(file, {}).setdefault(_bare_name(label), nid)
        try:
            by_line.setdefault(file, {}).setdefault(int(_node_line(n)), nid)
        except (TypeError, ValueError):
            pass
    if not by_parts and not js_files:
        return Resolved(edges)

    known_calls: set[tuple[str, str]] = set()
    known_imports: set[tuple[str, str]] = set()
    byid = {_node_id(n): n for n in nodes}
    for e in edges:
        rel = _rel(e)
        s, t = _edge_ends(e)
        if rel in _CALL_RELATIONS:
            known_calls.add((s, t))
        elif rel in _IMPORT_RELATIONS:
            sn, tn = byid.get(s), byid.get(t)
            if sn is not None and tn is not None:
                known_imports.add((_norm_relpath(_node_file(sn)), _norm_relpath(_node_file(tn))))

    checker = ImportChecker()

    def module_file(importer: str, b: _Binding, extra: tuple[str, ...]) -> str | None:
        dotted = b.module + ((b.name,) if b.name else ()) + extra
        return checker.resolve(importer, b.level, dotted, by_parts, by_last)

    def definition(file: str, name: str, depth: int = 0) -> str | None:
        """Node of ``name`` in ``file``, following re-exports a few modules deep."""
        got = by_name.get(file, {}).get(name)
        if got is not None or depth >= _REEXPORT_DEPTH:
            return got
        analysis = _module_calls_for_file(file)
        b = analysis.bindings.get(name) if analysis else None
        if b is None or b.name is None:
            return None
        origin = checker.resolve(file, b.level, b.module, by_parts, by_last)
        return definition(origin, b.name, depth + 1) if origin else None

    added: list[dict] = []
    calls = imports = 0
    for file in sorted(f for f in by_line.keys() | hub.keys() if f not in js_files):
        analysis = _module_calls_for_file(file)
        if analysis is None:
            continue
        for b in analysis.bindings.values():
            target_file = module_file(file, b, ()) if not b.rooted else None
            if (target_file and target_file != file and file in hub and target_file in hub
                    and (file, target_file) not in known_imports):
                known_imports.add((file, target_file))
                imports += 1
                added.append({
                    "source": hub[file], "target": hub[target_file],
                    "relation": "imports_from", "confidence": "EXTRACTED",
                    "source_file": file, "context": "import", "resolved_by": RESOLVED_BY,
                })
        for stack, line, chain in analysis.calls:
            target_file = module_file(file, analysis.bindings[chain[0]], chain[1:-1])
            if not target_file:
                continue
            target = definition(target_file, chain[-1])
            lines = by_line.get(file, {})
            source = next((lines[ln] for ln in stack if ln in lines), hub.get(file))
            if not target or not source or source == target or (source, target) in known_calls:
                continue
            known_calls.add((source, target))
            calls += 1
            added.append({
                "source": source, "target": target, "relation": "calls",
                "confidence": "EXTRACTED", "source_file": file,
                "source_location": f"L{line}", "context": "call",
                "resolved_by": RESOLVED_BY,
            })
    # JS / TS: `name.prop(...)` where `name` stands for a whole module.
    for file in sorted(js_files):
        js = _js_for_file(file)
        if js is None:
            continue
        targets: dict[str, str | None] = {}
        for line, name, prop in js.calls:
            specifier = js.bindings[name]
            if specifier not in targets:
                targets[specifier] = checker.resolve_js(file, specifier, js_files)
            target_file = targets[specifier]
            target = by_name.get(target_file, {}).get(prop) if target_file else None
            if not target:
                continue
            # the innermost enclosing definition that has a node, else the file
            lines = by_line.get(file, {})
            source = next(
                (lines[span[2]] for span in _enclosing_spans(file, line, line)
                 if span[2] in lines),
                hub.get(file),
            )
            if not source or source == target or (source, target) in known_calls:
                continue
            known_calls.add((source, target))
            calls += 1
            added.append({
                "source": source, "target": target, "relation": "calls",
                "confidence": "EXTRACTED", "source_file": file,
                "source_location": f"L{line}", "context": "call",
                "resolved_by": RESOLVED_BY,
            })
    if not added:
        return Resolved(edges)
    return Resolved([*edges, *added], calls, imports)


# (id(nodes), id(edges)) -> (nodes, edges, project dir, Resolved), identity-guarded
# like graph._ADJ_CACHE: the extended list must be the same object across calls.
_RESOLVED_CACHE: dict[tuple[int, int], tuple[list[dict], list[dict], str, Resolved]] = {}
_RESOLVED_CACHE_MAX = 8


def _resolved(nodes: list[dict], edges: list[dict]) -> Resolved:
    """``edges`` plus the calls and imports graphlore can prove from the source.

    Off with ``GRAPHLORE_RESOLVE=0``. Costs one parse per Python and JS/TS file
    of the graph (cached per file by mtime), once per loaded graph.
    """
    if not _enabled():
        return Resolved(edges)
    ident = (id(nodes), id(edges))
    project = str(config.PROJECT_DIR)
    cached = _RESOLVED_CACHE.get(ident)
    if (cached is not None and cached[0] is nodes and cached[1] is edges
            and cached[2] == project):
        return cached[3]
    result = _build(nodes, edges)
    if ident not in _RESOLVED_CACHE and len(_RESOLVED_CACHE) >= _RESOLVED_CACHE_MAX:
        _RESOLVED_CACHE.pop(next(iter(_RESOLVED_CACHE)), None)  # FIFO
    _RESOLVED_CACHE[ident] = (nodes, edges, project, result)
    return result
