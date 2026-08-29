"""External-package API-surface extraction: which names a file actually USES.

The difference between "this module imports fastapi" (package level) and "this
module uses Depends, APIRouter, CORSMiddleware from fastapi" (symbol level). A
version upgrade is audited at the symbol level: a breaking change in a release
note only matters if it touches a symbol you use.

Two capture paths per file:
  * **from-imports**: ``from fastapi import Depends, Query`` -> the symbol names
    directly (``fastapi: {Depends, Query}``). ``import *`` is skipped (what was
    taken can't be known) and relative imports are skipped (internal modules,
    not external surface).
  * **attribute access through an alias**: ``import numpy as np`` first builds an
    alias -> package map; then every attribute chain is inspected — ``np.array(...)``
    resolves the alias and records ``numpy: {array}``. The symbol comes from the
    real use site, not the import line.

Both paths also record the **qualified path** variant: ``np.linalg.norm([1])``
resolves the whole chain to ``numpy.linalg.norm``, and ``from
fastapi.middleware.cors import CORSMiddleware`` keeps the full module path
(``fastapi.middleware.cors.CORSMiddleware``). The short symbol is the "what is
used" summary; the qualified path is the precise input for a version-diff audit.

Honest limits: dynamic import (``importlib.import_module``), ``getattr(pkg,
name)``, ``import *``, indirect use through a wrapper function and names inside
strings are all invisible. The symbol list is therefore a LOWER BOUND — it says
"at least these must be audited", never "only these"; zero visible symbols
reads as "no visibility", not "no usage".

Python via the stdlib ``ast``; JS/TS, Go and Java via the optional tree-sitter
backend. Results are cached per (path, mtime) like the span index.
"""
from __future__ import annotations

import ast
from typing import Any

from . import config
from .spans import _norm_relpath, _ts_parser_for

# (packages, symbols, paths): every absolutely-imported package (even with no
# visible symbol), package -> short symbols used, package -> qualified use paths.
ApiUses = tuple[set[str], dict[str, set[str]], dict[str, set[str]]]

# Per-file API-use index, keyed by absolute path -> (mtime, ApiUses). Same
# bounded-FIFO scheme as spans._SPAN_CACHE (see there for the rationale).
_API_CACHE: dict[str, tuple[float, ApiUses]] = {}
_API_CACHE_MAX = 4096


def _api_cache_put(key: str, value: tuple[float, ApiUses]) -> None:
    if key not in _API_CACHE and len(_API_CACHE) >= _API_CACHE_MAX:
        _API_CACHE.pop(next(iter(_API_CACHE)), None)  # FIFO: drop the oldest entry
    _API_CACHE[key] = value


def _empty() -> ApiUses:
    return set(), {}, {}


def _record(uses: ApiUses, pkg: str, module: str, chain: list[str]) -> None:
    """Record one use site: ``chain`` is the attribute/symbol chain after ``module``."""
    packages, symbols, paths = uses
    packages.add(pkg)
    if not chain:
        return
    symbols.setdefault(pkg, set()).add(chain[0])
    paths.setdefault(pkg, set()).add(".".join([module, *chain]))


# ---------------------------------------------------------------------------
# Python (stdlib ast)
# ---------------------------------------------------------------------------


def _api_uses_python(src: bytes) -> ApiUses:
    uses = _empty()
    packages, _symbols, _paths = uses
    try:
        tree = ast.parse(src)
    except Exception:
        return uses

    # Pass 1 — imports. from-imports yield symbols directly; plain imports build
    # the alias -> package map for pass 2 (``import numpy as np`` -> np: numpy).
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    # `import a.b.c` binds only the top name `a`
                    top = a.name.split(".")[0]
                    aliases[top] = top
                packages.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # relative import: internal module, not external surface
            pkg = node.module.split(".")[0]
            packages.add(pkg)
            for a in node.names:
                if a.name == "*":
                    continue  # what was taken can't be known
                _record(uses, pkg, node.module, [a.name])

    # Pass 2 — attribute chains through an alias. ``np.linalg.norm([1])`` resolves
    # outermost-first so the full chain is recorded once (never also ``np.linalg``).
    class _AttrVisitor(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute) -> None:
            chain: list[str] = []
            cur: ast.expr = node
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in aliases:
                module = aliases[cur.id]
                chain.reverse()
                _record(uses, module.split(".")[0], module, chain)
                return  # chain consumed; don't re-visit its inner attributes
            self.generic_visit(node)

    try:
        _AttrVisitor().visit(tree)
    except RecursionError:
        pass
    return uses


# ---------------------------------------------------------------------------
# JS/TS, Go, Java (optional tree-sitter backend)
# ---------------------------------------------------------------------------


def _strip_quotes(text: str) -> str:
    return text.strip().strip("'\"`")


def _js_package(source: str) -> str:
    """npm package name from an import source: ``got/dist/x`` -> ``got``,
    ``@scope/pkg/sub`` -> ``@scope/pkg``, ``node:fs`` kept whole."""
    parts = source.split("/")
    return "/".join(parts[:2]) if source.startswith("@") else parts[0]


def _api_uses_js(root: Any) -> ApiUses:
    """import statements + member-expression chains through an alias (JS/TS/TSX).

    ``import got from 'got'`` / ``* as ns`` / ``const x = require('pkg')`` feed the
    alias map; ``import {HTTPError} from 'got'`` yields the symbol directly.
    Relative sources (``./x``) are internal, not external surface.
    """
    uses = _empty()
    aliases: dict[str, str] = {}

    def handle_import(stmt: Any) -> None:
        src_node = stmt.child_by_field_name("source")
        if src_node is None:
            return
        source = _strip_quotes(src_node.text.decode("utf-8", "replace"))
        if not source or source.startswith("."):
            return
        pkg = _js_package(source)
        uses[0].add(pkg)
        stack = [c for c in stmt.named_children if c.type == "import_clause"]
        while stack:
            n = stack.pop()
            if n.type == "import_specifier":  # import { HTTPError as E }
                name = n.child_by_field_name("name")
                if name is not None:
                    _record(uses, pkg, source, [name.text.decode("utf-8", "replace")])
            elif n.type == "namespace_import":  # import * as ns
                for c in n.named_children:
                    if c.type == "identifier":
                        aliases[c.text.decode("utf-8", "replace")] = source
            elif n.type == "identifier":  # default import
                aliases[n.text.decode("utf-8", "replace")] = source
            else:
                stack.extend(n.named_children)

    def require_source(call: Any) -> str | None:
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if fn is None or args is None or fn.text != b"require":
            return None
        for a in args.named_children:
            if "string" in a.type:
                source = _strip_quotes(a.text.decode("utf-8", "replace"))
                return source if source and not source.startswith(".") else None
        return None

    def walk(node: Any) -> None:
        for child in node.named_children:
            t = child.type
            if t == "import_statement":
                handle_import(child)
                continue
            if t == "variable_declarator":  # const x = require('pkg')
                value = child.child_by_field_name("value")
                name = child.child_by_field_name("name")
                if (value is not None and value.type == "call_expression"
                        and name is not None
                        and (source := require_source(value)) is not None):
                    pkg = _js_package(source)
                    uses[0].add(pkg)
                    if name.type == "identifier":
                        aliases[name.text.decode("utf-8", "replace")] = source
                    elif name.type == "object_pattern":
                        # const { HTTPError } = require('got') -> symbol-level
                        for p in name.named_children:
                            if "identifier" in p.type:
                                _record(uses, pkg, source,
                                        [p.text.decode("utf-8", "replace")])
                    continue
            if t == "call_expression" and (source := require_source(child)) is not None:
                uses[0].add(_js_package(source))  # bare require('pkg')
                continue
            if t == "member_expression":
                chain: list[str] = []
                cur = child
                while cur.type == "member_expression":
                    prop = cur.child_by_field_name("property")
                    obj = cur.child_by_field_name("object")
                    if prop is None or obj is None:
                        break
                    chain.append(prop.text.decode("utf-8", "replace"))
                    cur = obj
                if cur.type == "identifier":
                    name = cur.text.decode("utf-8", "replace")
                    if name in aliases:
                        source = aliases[name]
                        chain.reverse()
                        _record(uses, _js_package(source), source, chain)
                        continue  # chain consumed
            walk(child)

    walk(root)
    return uses


def _api_uses_go(root: Any) -> ApiUses:
    """import specs + selector expressions / qualified types through the package name."""
    uses = _empty()
    aliases: dict[str, str] = {}

    def walk(node: Any) -> None:
        for child in node.named_children:
            t = child.type
            if t == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node is None:
                    continue
                path = _strip_quotes(path_node.text.decode("utf-8", "replace"))
                if not path:
                    continue
                uses[0].add(path)
                name_node = child.child_by_field_name("name")
                local = (name_node.text.decode("utf-8", "replace")
                         if name_node is not None else path.split("/")[-1])
                if local not in ("_", "."):  # blank/dot imports bind no usable name
                    aliases[local] = path
            elif t in ("selector_expression", "qualified_type"):
                left = (child.child_by_field_name("operand")
                        or child.child_by_field_name("package"))
                right = (child.child_by_field_name("field")
                         or child.child_by_field_name("name"))
                if (left is not None and right is not None
                        and left.type in ("identifier", "package_identifier")):
                    name = left.text.decode("utf-8", "replace")
                    if name in aliases:
                        path = aliases[name]
                        _record(uses, path, path,
                                [right.text.decode("utf-8", "replace")])
                        continue
            walk(child)

    walk(root)
    return uses


def _api_uses_java(root: Any) -> ApiUses:
    """Java imports are already symbol-level: ``import a.b.Bar`` -> a.b: {Bar}.
    Wildcard imports (``a.b.*``) record the package only (surface unknown)."""
    uses = _empty()

    def walk(node: Any) -> None:
        for child in node.named_children:
            if child.type == "import_declaration":
                wildcard = any(c.type == "asterisk" for c in child.children)
                dotted = next(
                    (c for c in child.named_children
                     if c.type in ("scoped_identifier", "identifier")), None)
                if dotted is None:
                    continue
                full = dotted.text.decode("utf-8", "replace")
                if wildcard or "." not in full:
                    uses[0].add(full)
                    continue
                module, _, symbol = full.rpartition(".")
                _record(uses, module, module, [symbol])
            else:
                walk(child)

    walk(root)
    return uses


_TS_EXTRACTORS = {
    "javascript": _api_uses_js,
    "typescript": _api_uses_js,
    "tsx": _api_uses_js,
    "jsx": _api_uses_js,
    "go": _api_uses_go,
    "java": _api_uses_java,
}


def _api_uses_treesitter(src: bytes, rel: str) -> ApiUses:
    parser, lang = _ts_parser_for(rel)
    extractor = _TS_EXTRACTORS.get(str(lang or "").lower())
    if parser is None or extractor is None:
        return _empty()
    try:
        root = parser.parse(src).root_node
        return extractor(root)
    except Exception:  # parse failure / grammar drift: degrade to no visibility
        return _empty()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def api_uses_for_source(src: bytes | str, rel: str) -> ApiUses:
    """PUBLIC: external-package API uses for one source blob — the stable contract
    for external consumers (importable as ``from graphlore.apis import
    api_uses_for_source``); the underscore-prefixed extractors behind it are
    internal and may be renamed.

    ``rel`` is the file's (relative) path and only selects the parser — ``.py``
    goes through the stdlib ``ast``, anything else through the optional
    tree-sitter backend (empty result when that backend or the language is
    unavailable). No file IO, no caching, no project-dir confinement: the caller
    owns the source bytes.

    Returns ``(packages, symbols, paths)``:
      * ``packages``: every absolutely-imported package, even with no visible symbol
      * ``symbols``:  package -> short symbol names used (``numpy: {array}``)
      * ``paths``:    package -> qualified use paths (``numpy: {numpy.linalg.norm}``)
    All three are lower bounds — see the module docstring for what is invisible.
    """
    data = src if isinstance(src, bytes) else src.encode("utf-8", "replace")
    if str(rel).lower().endswith(".py"):
        return _api_uses_python(data)
    return _api_uses_treesitter(data, str(rel))


# ---------------------------------------------------------------------------
# Per-file entry point
# ---------------------------------------------------------------------------


def _api_uses_for_file(file_path: str) -> ApiUses:
    """(packages, symbols, paths) for one source file under PROJECT_DIR.

    Python via stdlib ``ast``; JS/TS, Go and Java via the optional tree-sitter
    backend when present; empty for other / missing / unparseable files (cached
    either way). Confined to PROJECT_DIR like the span index. Cached by
    (path, mtime).
    """
    rel = _norm_relpath(file_path)
    if not rel:
        return _empty()
    try:
        full = (config.PROJECT_DIR / rel).resolve()
        full.relative_to(config.PROJECT_DIR.resolve())
    except (ValueError, OSError):
        return _empty()
    try:
        mtime = full.stat().st_mtime
    except OSError:
        return _empty()
    key = str(full)
    cached = _API_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        src = full.read_bytes()
    except OSError:
        _api_cache_put(key, (mtime, _empty()))
        return _empty()
    uses = api_uses_for_source(src, rel)
    _api_cache_put(key, (mtime, uses))
    return uses
