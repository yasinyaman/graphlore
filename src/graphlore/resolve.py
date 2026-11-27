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
    _CONTAINMENT_RELATIONS,
    _IMPORT_RELATIONS,
    _NON_DEPENDENCY_RELATIONS,
    _bare_name,
    _edge_ends,
    _engine_resolved,
    _is_file_hub_node,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _rel,
)
from .imports import _JS_EXTS, ImportChecker, _js_for_file, _module_parts
from .spans import (
    _cached_file_analysis,
    _enclosing_spans,
    _node_for_location,
    _nodes_by_file,
    _norm_relpath,
    _resolve_in_project,
)

RESOLVED_BY = "graphlore"
# How far a name is followed through re-exports (`from .graph import x` in the
# module the caller went through).
_REEXPORT_DEPTH = 3


class _Binding(NamedTuple):
    level: int                # 0 = absolute import, n = leading dots
    module: tuple[str, ...]   # dotted path of the statement's module
    name: str | None          # the from-imported name, None for `import a.b [as m]`
    rooted: bool              # `import a.b.c` with no alias: the name is the root `a`
    line: int = 0             # line of the import statement


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
                        bindings[a.asname] = _Binding(0, parts, None, False, child.lineno)
                    else:
                        bindings.setdefault(
                            parts[0], _Binding(0, parts[:1], None, True, child.lineno)
                        )
            elif isinstance(child, ast.ImportFrom):
                module = tuple(child.module.split(".")) if child.module else ()
                for a in child.names:
                    if a.name != "*":
                        bindings[a.asname or a.name] = _Binding(
                            child.level or 0, module, a.name, False, child.lineno
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
    # what a SCIP index said of the edges (``_precise``): None when none was read
    precision: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"calls": self.calls, "imports": self.imports}
        if self.precision is not None:
            out["precision"] = self.precision
        return out

    def note(self) -> str:
        """One line naming what was added; "" when nothing was."""
        lines = []
        if self.calls or self.imports:
            bits = []
            if self.calls:
                bits.append(f"{self.calls} call(s) made through an imported module")
            if self.imports:
                bits.append(f"{self.imports} module import(s)")
            lines.append(
                "Includes " + " and ".join(bits) + " that graph.json lacks, resolved by "
                "graphlore from the source."
            )
        p = self.precision
        if p is not None:
            lines.append(
                f"Checked against a SCIP index ({', '.join(p['tools']) or 'unknown indexer'}): "
                f"{p['confirmed']} edge(s) confirmed, {p['added']} added, "
                f"{p['contradicted']} contradicted (marked AMBIGUOUS, kept)."
            )
        return "\n".join(lines)


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
                    "source_file": file, "source_location": f"L{b.line}",
                    "context": "import", "resolved_by": RESOLVED_BY,
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
    """``edges`` plus the calls and imports graphlore can prove from the source,
    then checked against a SCIP index when the project has one (``_precise``).

    Off with ``GRAPHLORE_RESOLVE=0``. Costs one parse per Python and JS/TS file
    of the graph (cached per file by mtime), once per loaded graph.
    """
    return _precise(nodes, _from_source(nodes, edges))


def _from_source(nodes: list[dict], edges: list[dict]) -> Resolved:
    if not _enabled() or _engine_resolved(nodes):
        # graphlore's native engine resolved these calls and imports itself
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


# --- a compiler's answer: a SCIP index ------------------------------------------------

SCIP_BY = "scip"
# the nodes a use reads or writes (the native engine's `kind`)
_STATE_KINDS = frozenset({"variable", "property", "constant", "field"})
_WRITE_OPERATORS = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=",
                    "??=", "&^=", "++", "--")
# how far a SCIP definition's line may be from the node's (a decorator, a comment
# the extractor counted in) and still be that node
_DEF_LINE_SLACK = 3
# (id(nodes), id(base edges), id(index)) -> (nodes, edges, index, Resolved)
_PRECISE_CACHE: dict[tuple[int, int, int], tuple[list[dict], list[dict], Any, Resolved]] = {}
_PRECISE_CACHE_MAX = 4


def _precise(nodes: list[dict], base: Resolved) -> Resolved:
    """``base`` checked against the project's SCIP index (``scip.py``).

    Each use the index records, other than a definition or an import, is given
    to the node whose span holds it (the source) and the node its symbol's
    definition is (the target, by file, name and nearest line). Then:

    * an edge the graph has between the two is **confirmed**: a copy with
      ``confidence: EXTRACTED`` and ``scip: "confirmed"``;
    * a pair the graph lacks is **added**: ``calls`` when a ``(`` follows the
      name in the source, else ``references``, ``resolved_by: "scip"``;
    * a call or reference the graph has at a site where the index names the
      same name as something else (another node, or a library's symbol) is
      **contradicted**: a copy with ``confidence: AMBIGUOUS`` and
      ``scip: "contradicted"``. It is never removed.

    Nothing changes without an index; the result is cached on the identity of
    the lists and of the index (which ``scip.load`` caches on its files)."""
    from . import scip

    index = scip.load()
    if index is None or not index.documents:
        return base
    ident = (id(nodes), id(base.edges), id(index))
    hit = _PRECISE_CACHE.get(ident)
    if hit is not None and hit[0] is nodes and hit[1] is base.edges and hit[2] is index:
        return hit[3]
    result = _apply_scip(nodes, base, index)
    if ident not in _PRECISE_CACHE and len(_PRECISE_CACHE) >= _PRECISE_CACHE_MAX:
        _PRECISE_CACHE.pop(next(iter(_PRECISE_CACHE)), None)
    _PRECISE_CACHE[ident] = (nodes, base.edges, index, result)
    return result


def _site_line(e: dict) -> int | None:
    raw = str(e.get("source_location") or "")
    digits = raw.lstrip("Ll").split("-")[0].split(":")[0]
    return int(digits) if digits.isdigit() else None


def _apply_scip(nodes: list[dict], base: Resolved, index: Any) -> Resolved:
    from . import scip

    by_file = _nodes_by_file(nodes)
    def_node: dict[str, frozenset[str]] = {}

    def node_of(symbol: str) -> frozenset[str]:
        """The nodes a symbol's definitions are: several for a partial type
        or for namesakes the indexer gives one symbol."""
        if symbol in def_node:
            return def_node[symbol]
        got: set[str] = set()
        name = scip.bare_name(symbol)
        for file, line in (index.defs_of(symbol) if name else []):
            best = None
            for n in by_file.get(file, []):
                if _is_file_hub_node(n) or _bare_name(_node_label(n)) != name:
                    continue
                try:
                    d = abs(int(_node_line(n)) - line)
                except (TypeError, ValueError):
                    continue
                if d <= _DEF_LINE_SLACK and (best is None or d < best[0]):
                    best = (d, _node_id(n))
            if best:
                got.add(best[1])
        def_node[symbol] = frozenset(got)
        return def_node[symbol]

    owners: dict[tuple[str, int], str | None] = {}
    # a native graph's nodes carry their spans: (start, end, id) per file, and
    # the file's own node for what no definition holds
    regions: dict[str, tuple[list[tuple[int, int, str]], str | None] | None] = {}

    def spans_of(file: str) -> tuple[list[tuple[int, int, str]], str | None] | None:
        if file not in regions:
            rows, hub = [], None
            for n in by_file.get(file, []):
                if _is_file_hub_node(n):
                    hub = _node_id(n)
                    continue
                try:
                    end = int(n.get("end_line") or 0)
                    start = int(n.get("region_start") or _node_line(n))
                except (TypeError, ValueError):
                    continue
                if end >= start > 0:
                    rows.append((start, end, _node_id(n)))
            regions[file] = (rows, hub) if rows else None
        return regions[file]

    def owner(file: str, line: int) -> str | None:
        key = (file, line)
        if key not in owners:
            known = spans_of(file)
            if known is not None:
                rows, hub = known
                inside = [r for r in rows if r[0] <= line <= r[1]]
                owners[key] = min(inside, key=lambda r: (r[1] - r[0], -r[0]))[2] if inside else hub
            else:
                n = _node_for_location(nodes, file, line, line) if by_file.get(file) else None
                owners[key] = _node_id(n) if n is not None else None
        return owners[key]

    lines_of: dict[str, list[str] | None] = {}

    def after(occ: Any) -> str:
        """The source after the use's name, on its line."""
        if occ.file not in lines_of:
            path = _resolve_in_project(occ.file)
            try:
                lines_of[occ.file] = (path.read_text(encoding="utf-8-sig", errors="replace")
                                      .split("\n") if path else None)
            except OSError:
                lines_of[occ.file] = None
        text = lines_of[occ.file]
        if not text or occ.line > len(text):
            return ""
        return text[occ.line - 1][occ.end_char:].lstrip()

    state = {_node_id(n) for n in nodes if n.get("kind") in _STATE_KINDS}

    def relation(occ: Any, tgt: str) -> str:
        """What a use is, from the source after its name (the indexers mark
        no access): `(` a call; of state, an assignment (or a Go composite
        literal's key, `Command{Use: "c"}`) a write, else a read; else a
        reference."""
        rest = after(occ)
        if rest.startswith(("(", "!(", "::<")):
            return "calls"
        if tgt not in state:
            return "references"
        if rest.startswith(_WRITE_OPERATORS) and not rest.startswith(("==", "=>")):
            return "writes"
        if occ.file.endswith(".go") and rest.startswith(":") and not rest.startswith(":="):
            return "writes"
        return "reads"

    dependency = [
        i for i, e in enumerate(base.edges)
        if _rel(e) not in _NON_DEPENDENCY_RELATIONS and _rel(e) not in _CONTAINMENT_RELATIONS
    ]
    pairs: dict[tuple[str, str], list[int]] = {}
    for i in dependency:
        s, t = _edge_ends(base.edges[i])
        pairs.setdefault((s, t), []).append(i)
    # what holds each node, and the line it is defined on: a definition's own
    # signature naming what holds it (a Go method's receiver, `func (m *Mux)
    # Route(`) says what the containment edge says, and adds nothing
    parent: dict[str, str] = {}
    for e in base.edges:
        if _rel(e) in _CONTAINMENT_RELATIONS:
            s, t = _edge_ends(e)
            parent.setdefault(t, s)
    def_line: dict[str, int] = {}
    for n in nodes:
        try:
            def_line[_node_id(n)] = int(_node_line(n))
        except (TypeError, ValueError):
            pass

    label_of = {_node_id(n): _bare_name(_node_label(n)) for n in nodes}
    # a constructor -> its type (a definition named as what holds it)
    ctor_of = {c: p for c, p in parent.items()
               if label_of.get(c) and label_of.get(c) == label_of.get(p)}

    def holds(tgt: str, src: str) -> bool:
        seen = {src}
        cur = parent.get(src)
        while cur is not None and cur not in seen:
            if cur == tgt:
                return True
            seen.add(cur)
            cur = parent.get(cur)
        return False

    # what the index names at each site: (file, line) -> [(name, nodes, outside)]
    sites: dict[tuple[str, int], list[tuple[str, frozenset[str], bool]]] = {}
    confirmed: set[int] = set()
    added: dict[tuple[str, str], dict] = {}
    names: dict[str, str] = {}
    for occ in index.refs:
        if occ.roles & (scip.DEFINITION | scip.IMPORT):
            continue
        name = names.get(occ.symbol)
        if name is None:
            name = names[occ.symbol] = scip.bare_name(occ.symbol)
        if not name:
            continue
        tgts = node_of(occ.symbol)
        sites.setdefault((occ.file, occ.line), []).append(
            (name, tgts, not index.defs_of(occ.symbol)))
        if not tgts:
            continue
        src = owner(occ.file, occ.line)
        if src is None or src in tgts:
            continue
        have = [i for t in tgts for i in pairs.get((src, t), ())]
        if not have:
            # a constructor's use is a use of its type (`[Option(…)]`, an
            # edge the graph writes to `OptionAttribute`)
            have = [i for t in tgts if (p := ctor_of.get(t)) is not None
                    for i in pairs.get((src, p), ())]
        if have:
            confirmed.update(have)
            continue
        # a use of one of several namesakes adds no edge: which one is not said
        tgt = next(iter(tgts))
        if len(tgts) == 1 and (src, tgt) not in added and not (
                def_line.get(src) == occ.line and holds(tgt, src)):
            added[(src, tgt)] = {
                "source": src, "target": tgt,
                "relation": relation(occ, tgt),
                "confidence": "EXTRACTED", "source_file": occ.file,
                "source_location": f"L{occ.line}", "resolved_by": SCIP_BY, "scip": "added",
            }

    labels = label_of
    # only an edge to the project's own code can be contradicted: one to an
    # external node already says "the library's", as the index does
    own = {_node_id(n) for n in nodes if _node_file(n) and n.get("type") != "external"}
    contradicted: set[int] = set()
    for i in dependency:
        if i in confirmed:
            continue
        e = base.edges[i]
        if _rel(e) not in _CALL_RELATIONS and _rel(e) != "references":
            continue
        line = _site_line(e)
        file = _norm_relpath(str(e.get("source_file") or ""))
        said = sites.get((file, line)) if line is not None else None
        if not said:
            continue
        s, t = _edge_ends(e)
        if t not in own:
            continue
        name = labels.get(t, "")
        same = [x for x in said if x[0] == name]
        # the index names this name here as something else: another node of the
        # graph, or a symbol defined outside the project
        # (a constructor is its type where the index names only the type:
        # scip-dotnet's `new RestRequest()`)
        held_by = ctor_of.get(t)
        if same and all((x[1] and t not in x[1] and held_by not in x[1]) or (not x[1] and x[2])
                        for x in same):
            contradicted.add(i)

    precision = {
        "source": index.sources, "tools": index.tools, "documents": index.documents,
        "confirmed": len(confirmed), "added": len(added), "contradicted": len(contradicted),
        # uses whose source text is not their symbol's name: not read
        "mismatched": index.mismatched,
    }
    if index.errors:
        precision["errors"] = index.errors
    if not (confirmed or added or contradicted):
        return Resolved(base.edges, base.calls, base.imports, precision)
    out: list[dict] = []
    for i, e in enumerate(base.edges):
        if i in confirmed:
            out.append({**e, "confidence": "EXTRACTED", "scip": "confirmed"})
        elif i in contradicted:
            out.append({**e, "confidence": "AMBIGUOUS", "scip": "contradicted"})
        else:
            out.append(e)
    out += added.values()
    return Resolved(out, base.calls, base.imports, precision)
