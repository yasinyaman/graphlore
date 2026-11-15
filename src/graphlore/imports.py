"""Import statements read from the source: what each can mean, and when it runs.

graphify resolves an import to a graph node by name, which goes wrong when a
first-party module shares its name with an installed package: ``import yaml``
(PyYAML) becomes an import of ``pkg/adapters/export/yaml.py``. And an import edge
does not say whether the statement runs when the module is imported, sits under
``if TYPE_CHECKING:``, or inside a function — the difference between a cycle that
breaks start-up and two modules that merely know each other.

:class:`ImportChecker` answers both from the importing file's own statements, per
language, like the extractors in :mod:`graphlore.apis`:

  * **Python** — stdlib ``ast``: module-level, inside a function (deferred),
    under ``if TYPE_CHECKING:``.
  * **JS / TS** — tree-sitter: a top-level ``import`` / ``export ... from`` /
    ``require`` is module-level, an ``import()`` or ``require`` inside a function
    is deferred, ``import type`` is type-only. A relative specifier is resolved
    to its file; a bare one-word specifier is an npm package.
  * **Go, Java, Kotlin, Scala, C#, Rust, Swift** — imports are resolved at
    compile time: always module-level, nothing to read.

Anything else is kept and reported as unclassified, unless the graph says more:
a ``dynamic_import`` relation, or an import edge that leaves a function node.
"""
from __future__ import annotations

import ast
import posixpath
import re
from typing import Any, NamedTuple

from .apis import _js_internal_source, _strip_quotes
from .graph import (
    IMPORT_DEFERRED,
    IMPORT_MODULE_LEVEL,
    IMPORT_TYPE_CHECKING,
    IMPORT_UNCLASSIFIED,
    _rel,
)
from .spans import _cached_file_analysis, _norm_relpath, _resolve_in_project, _ts_parser_for

# When an import statement runs (the values live in graph.py, which tiers cycles
# by them): at import time; when its function is called; never — it sits under
# `if TYPE_CHECKING:`; or unknown, the source not being readable as Python.
MODULE_LEVEL, DEFERRED = IMPORT_MODULE_LEVEL, IMPORT_DEFERRED
TYPE_CHECKING, UNCLASSIFIED = IMPORT_TYPE_CHECKING, IMPORT_UNCLASSIFIED
_RUNTIME_RANK = {MODULE_LEVEL: 0, UNCLASSIFIED: 0, DEFERRED: 1, TYPE_CHECKING: 2}

# Directory names that are source roots by convention.
_ROOT_DIR_NAMES = frozenset({"src", "lib", "python", "source", "sources"})
# A directory holding one of these is the root of an installable distribution.
_ROOT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
_TS_TYPE_IMPORT = re.compile(r"^\s*(?:import|export)\s+type\b")
# The JS family, longest extension first so `.d.ts` is stripped whole.
_JS_EXTS = (".d.ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".ts", ".js")
# Languages whose imports are resolved at compile time: an import cannot be
# deferred or conditional, so every one of them is module-level by definition.
# A C-family `#include` / `#import` is resolved earlier still, by the preprocessor,
# and a PHP `use` only aliases a name (the file loads on first use, by autoload).
# Ruby needs nothing: graphify records no file-level import edge for `require`.
_COMPILED_EXTS = (
    ".go", ".java", ".kt", ".kts", ".scala", ".cs", ".rs", ".swift",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".m", ".mm", ".php",
)
# How many re-exporting entry files (`index.ts` barrels) a JS/TS import is
# followed through before the edge counts as contradicted by the source.
_BARREL_DEPTH = 3


class _Import(NamedTuple):
    line: int
    kind: str                 # MODULE_LEVEL / DEFERRED / TYPE_CHECKING
    level: int                # 0 = absolute, n = number of leading dots
    module: tuple[str, ...]   # dotted module path of the statement
    names: tuple[str, ...]    # names of a from-import (each may be a submodule)


def _is_type_checking_test(test: ast.expr) -> bool:
    """``TYPE_CHECKING`` or ``typing.TYPE_CHECKING`` (also ``t.TYPE_CHECKING``)."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _python_imports(src: bytes) -> list[_Import] | None:
    """Every import statement of a Python file, or None if it does not parse."""
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001 - SyntaxError, ValueError (NUL bytes), RecursionError
        return None
    out: list[_Import] = []

    def walk(node: ast.AST, kind: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for a in child.names:
                    out.append(_Import(child.lineno, kind, 0, tuple(a.name.split(".")), ()))
            elif isinstance(child, ast.ImportFrom):
                module = tuple(child.module.split(".")) if child.module else ()
                names = tuple(a.name for a in child.names if a.name != "*")
                out.append(_Import(child.lineno, kind, child.level or 0, module, names))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                # A function body runs when called, whatever it sits under.
                walk(child, DEFERRED if kind == MODULE_LEVEL else kind)
            elif isinstance(child, ast.If) and _is_type_checking_test(child.test):
                for stmt in child.body:
                    walk(ast.Module(body=[stmt], type_ignores=[]), TYPE_CHECKING)
                for stmt in child.orelse:
                    walk(ast.Module(body=[stmt], type_ignores=[]), kind)
            else:
                walk(child, kind)

    try:
        walk(tree, MODULE_LEVEL)
    except RecursionError:
        return None
    return out


# rel path -> (mtime, statements | None); the (path, mtime) scheme of the span cache.
_IMPORTS_CACHE: dict[str, tuple[float, Any]] = {}


def _imports_for_file(rel: str) -> list[_Import] | None:
    """Import statements of a Python file under PROJECT_DIR; None when unreadable."""
    if not rel.lower().endswith((".py", ".pyi")):
        return None
    return _cached_file_analysis(
        _IMPORTS_CACHE, rel, lambda src, _rel_path: _python_imports(src), lambda: None
    )


# ---------------------------------------------------------------------------
# JS / TS (optional tree-sitter backend)
# ---------------------------------------------------------------------------


class _JsImport(NamedTuple):
    line: int
    kind: str        # MODULE_LEVEL / DEFERRED / TYPE_CHECKING
    specifier: str   # './lib/api.js', 'yaml', '@/store'
    # `export … from './x'` / `module.exports = require('./x')`: the file passes
    # on what `./x` defines, so an import of this file reaches `./x` too
    reexport: bool = False


class JsFile(NamedTuple):
    """What one JS/TS file imports, binds and calls through a binding."""

    imports: list[_JsImport]
    # local name -> specifier, for names that stand for a whole module:
    # `import * as ns`, a default import, `const m = require(..)` / `await import(..)`
    bindings: dict[str, str]
    # (line, bound name, property) for every `name.prop(...)` call
    calls: list[tuple[int, str, str]]


def _js_function_like(node_type: str) -> bool:
    t = node_type.lower()
    return "function" in t or t == "method_definition"


def _js_file(root: Any) -> JsFile:
    imports: list[_JsImport] = []
    bindings: dict[str, str] = {}
    raw_calls: list[tuple[int, str, str]] = []

    def text(node: Any) -> str:
        return node.text.decode("utf-8", "replace")

    def dynamic_source(call: Any) -> str | None:
        """Specifier of `require('x')` / `import('x')`, else None."""
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if fn is None or args is None or not (fn.type == "import" or fn.text == b"require"):
            return None
        for a in args.named_children:
            if "string" in a.type:
                return _strip_quotes(text(a)) or None
        return None

    def bind_clause(stmt: Any, source: str) -> None:
        stack = [c for c in stmt.named_children if c.type == "import_clause"]
        while stack:
            n = stack.pop()
            if n.type == "namespace_import":  # import * as ns
                for c in n.named_children:
                    if c.type == "identifier":
                        bindings[text(c)] = source
            elif n.type == "identifier":      # default import
                bindings[text(n)] = source
            elif n.type != "named_imports":   # { a, b } bind symbols, not the module
                stack.extend(n.named_children)

    def walk(node: Any, in_function: bool) -> None:
        for child in node.named_children:
            t = child.type
            line = child.start_point[0] + 1
            if t in ("import_statement", "export_statement"):
                src = child.child_by_field_name("source")
                if src is not None:
                    source = _strip_quotes(text(src))
                    kind = TYPE_CHECKING if _TS_TYPE_IMPORT.match(text(child)) else MODULE_LEVEL
                    imports.append(_JsImport(line, kind, source, t == "export_statement"))
                    if t == "import_statement" and kind != TYPE_CHECKING:
                        bind_clause(child, source)
                    continue
            if t == "call_expression":
                dynamic = dynamic_source(child)
                if dynamic is not None:
                    left = (node.child_by_field_name("left")
                            if node.type == "assignment_expression" else None)
                    imports.append(_JsImport(
                        line, DEFERRED if in_function else MODULE_LEVEL, dynamic,
                        left is not None
                        and text(left).startswith(("module.exports", "exports")),
                    ))
                    continue
                fn = child.child_by_field_name("function")
                if fn is not None and fn.type == "member_expression":
                    obj = fn.child_by_field_name("object")
                    prop = fn.child_by_field_name("property")
                    if obj is not None and prop is not None and obj.type == "identifier":
                        raw_calls.append((line, text(obj), text(prop)))
            if t == "variable_declarator":  # const m = require('x') / await import('x')
                name = child.child_by_field_name("name")
                value = child.child_by_field_name("value")
                if value is not None and value.type == "await_expression":
                    value = next(iter(value.named_children), None)
                if (name is not None and name.type == "identifier" and value is not None
                        and value.type == "call_expression"
                        and (bound := dynamic_source(value)) is not None):
                    bindings[text(name)] = bound
            walk(child, in_function or _js_function_like(t))

    walk(root, False)
    return JsFile(imports, bindings, [c for c in raw_calls if c[1] in bindings])


# rel path -> (mtime, JsFile | None); the (path, mtime) scheme of the span cache.
_JS_CACHE: dict[str, tuple[float, Any]] = {}


def _js_for_file(rel: str) -> JsFile | None:
    """Imports, module bindings and member calls of a JS/TS file; None when the
    tree-sitter backend is absent or the file does not parse."""
    if not rel.lower().endswith(_JS_EXTS):
        return None

    def compute(src: bytes, path: str) -> JsFile | None:
        parser, _lang = _ts_parser_for(path)
        if parser is None:
            return None
        try:
            return _js_file(parser.parse(src).root_node)
        except Exception:  # noqa: BLE001 - grammar drift / recursion: no visibility
            return None

    return _cached_file_analysis(_JS_CACHE, rel, compute, lambda: None)


def _js_module_forms(path: str) -> set[str]:
    """The extension-less ways a specifier can name a file: ``lib/api`` for
    ``lib/api.ts``, and both ``lib/store/index`` and ``lib/store`` for an index file."""
    low = path.lower()
    base = next((path[: -len(ext)] for ext in _JS_EXTS if low.endswith(ext)), path)
    forms = {base}
    if base.endswith("/index"):
        forms.add(base[: -len("/index")])
    elif base == "index":
        forms.add("")  # the project root's own index: `require('../..')`
    return forms


def _js_relative_base(specifier: str, importer: str) -> str:
    """The project-relative path a relative specifier points at ("" for the root)."""
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
    return "" if base == "." else base


def _js_can_mean(specifier: str, importer: str, target: str) -> bool:
    """Can ``specifier``, written in ``importer``, name the file ``target``?"""
    forms = _js_module_forms(target)
    if specifier.startswith("."):
        base = _js_relative_base(specifier, importer)
        # `./x.js` in TypeScript source names `x.ts`: compare without extensions
        return base == target or bool(_js_module_forms(base) & forms)
    if _js_internal_source(specifier):  # '/abs', '@/alias', '~/alias', '#subpath'
        path = specifier.lstrip("/#~@").lstrip("/")
        wanted = _js_module_forms(path)
        return any(f == w or f.endswith("/" + w) for f in forms for w in wanted)
    segments = specifier.split("/")
    if len(segments) == 1:
        # One bare word is an npm package — unless the project root, or a source
        # root directly under it (a tsconfig `baseUrl`), holds a module of that name.
        return any(
            f == specifier or (
                f.endswith("/" + specifier)
                and f[: -len(specifier) - 1].lower() in _ROOT_DIR_NAMES
            )
            for f in forms
        )
    # `@app/utils`, `components/Button`: a scoped package or a tsconfig path
    # alias. Which one cannot be told without the config, so the edge is kept
    # whenever the last segment names the target.
    last = next(iter(_js_module_forms(segments[-1])))
    return any(f.rsplit("/", 1)[-1] == last for f in forms)


def _module_parts(path: str) -> tuple[str, ...] | None:
    """``a/b/c.py`` -> (a, b, c); ``a/b/__init__.py`` -> (a, b). None if not Python."""
    if not path.lower().endswith((".py", ".pyi")):
        return None
    parts = path.rsplit(".", 1)[0].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return tuple(parts)


class ImportChecker:
    """Classifies file-level import edges against the importing file's source.

    One instance per tool call: it memoizes the directory lookups (is this a
    package, is this a distribution root) that the edges of one graph repeat.
    """

    def __init__(self) -> None:
        self._is_package: dict[str, bool] = {}
        self._is_root: dict[str, bool] = {}
        self._is_file: dict[str, bool] = {}
        self._reexports: dict[tuple[str, str], bool] = {}
        self._lines: dict[str, list[str]] = {}
        self.rejected: list[dict[str, str]] = []
        self.unclassified = 0

    def _exists(self, rel: str) -> bool:
        got = self._is_file.get(rel)
        if got is None:
            full = _resolve_in_project(rel)
            got = self._is_file[rel] = full is not None and full.is_file()
        return got

    def _js_entries(self, specifier: str, importer: str, target: str) -> list[str]:
        """Files other than ``target`` that ``specifier`` names from ``importer``.

        A relative specifier names one module or a directory's index. An alias or
        a bare path (``@/client``, ``app/store``) cannot be resolved without the
        build config, so the index files of the directories above ``target`` are
        tried against it.
        """
        found: list[str] = []
        if specifier.startswith("."):
            for stem in _js_module_forms(_js_relative_base(specifier, importer)):
                for ext in _JS_EXTS:
                    found.append(stem + ext)
                    found.append(f"{stem}/index{ext}" if stem else f"index{ext}")
        else:
            d = posixpath.dirname(target)
            while True:
                found += [f"{d}/index{ext}" if d else f"index{ext}" for ext in _JS_EXTS]
                if not d:
                    break
                d = posixpath.dirname(d)
        return [
            f for f in dict.fromkeys(found)
            if f != target and self._exists(f)
            and (specifier.startswith(".") or _js_can_mean(specifier, importer, f))
        ]

    def _js_means(self, specifier: str, importer: str, target: str) -> bool:
        """Can ``specifier`` reach ``target``: by naming it, or by naming a module
        that re-exports from it.

        graphify links ``import { CreateDto } from './dto'`` to the file that
        defines ``CreateDto``, not to ``dto/index.ts`` which re-exports it. The
        statement still loads that file, so the edge holds. Only re-exports are
        followed (``export … from``, ``module.exports = require(…)``), through at
        most ``_BARREL_DEPTH`` files: a file the entry merely imports is not what
        the importer asked for.
        """
        if _js_can_mean(specifier, importer, target):
            return True
        return any(self._js_reexports(entry, target, _BARREL_DEPTH)
                   for entry in self._js_entries(specifier, importer, target))

    def _js_reexports(self, entry: str, target: str, depth: int) -> bool:
        key = (entry, target)
        got = self._reexports.get(key)
        if got is None:
            self._reexports[key] = False  # a re-export cycle ends here
            js = _js_for_file(entry)
            got = js is not None and any(
                i.reexport and (
                    _js_can_mean(i.specifier, entry, target)
                    or (depth > 1 and any(
                        self._js_reexports(nxt, target, depth - 1)
                        for nxt in self._js_entries(i.specifier, entry, target)))
                )
                for i in js.imports
            )
            self._reexports[key] = got
        return got

    def _package(self, d: str) -> bool:
        got = self._is_package.get(d)
        if got is None:
            prefix = f"{d}/" if d else ""
            got = self._is_package[d] = (
                self._exists(prefix + "__init__.py") or self._exists(prefix + "__init__.pyi")
            )
        return got

    def _root_like(self, d: str, importer: str) -> bool:
        """Could ``d`` be on ``sys.path`` when ``importer`` runs?

        The project root, a conventional source directory, a distribution root, or
        a directory the importing file lives under (a script's own directory and
        the working directories above it).
        """
        if not d:
            return True
        if importer.startswith(d + "/") or d.rsplit("/", 1)[-1].lower() in _ROOT_DIR_NAMES:
            return True
        got = self._is_root.get(d)
        if got is None:
            got = self._is_root[d] = any(self._exists(f"{d}/{m}") for m in _ROOT_MARKERS)
        return got

    def _can_mean(self, stmt: _Import, importer: str, target: tuple[str, ...]) -> bool:
        """Can this statement, written in ``importer``, import the module ``target``?"""
        # Importing a.b.c imports a and a.b on the way; a from-import may name a
        # submodule. So the statement reaches every prefix, and one level below.
        reach = [stmt.module[:i] for i in range(1, len(stmt.module) + 1)]
        reach += [(*stmt.module, n) for n in stmt.names]
        if stmt.level:
            base = importer.split("/")[:-1]
            if stmt.level - 1 > len(base):
                return False
            base = base[: len(base) - (stmt.level - 1)]
            reach.append(())  # `from . import x` also names the package itself
            return any(tuple(base) + r == target for r in reach)
        for dotted in reach:
            n = len(dotted)
            if not n or len(target) < n or target[-n:] != dotted:
                continue
            root = "/".join(target[:-n])
            # The directory the dotted path starts in must be importable from: a
            # package is not (its modules are reached as pkg.mod, never as mod),
            # and a bare one-word import needs more than that — it is what an
            # installed package looks like.
            if self._package(root):
                continue
            if n > 1 or self._root_like(root, importer):
                return True
        return False

    def resolve(
        self, importer: str, level: int, dotted: tuple[str, ...],
        by_parts: dict[tuple[str, ...], str], by_last: dict[str, list[tuple[str, ...]]],
    ) -> str | None:
        """The first-party file a dotted module path names from ``importer``.

        ``by_parts`` maps a file's module parts to the file and ``by_last`` indexes
        those parts by their last segment. None when no file, or more than one,
        can be meant — the same root rules as :meth:`kind`.
        """
        if not dotted:
            return None
        if level:
            base = importer.split("/")[:-1]
            if level - 1 > len(base):
                return None
            return by_parts.get(tuple(base[: len(base) - (level - 1)]) + dotted)
        n = len(dotted)
        found: list[str] = []
        for parts in by_last.get(dotted[-1], ()):
            if len(parts) < n or parts[-n:] != dotted:
                continue
            root = "/".join(parts[:-n])
            if not self._package(root) and (n > 1 or self._root_like(root, importer)):
                found.append(by_parts[parts])
        return found[0] if len(found) == 1 else None

    def kind(self, importer: str, imported: str, edge: dict) -> str | None:
        """When the import behind a file-level edge runs, or None for a false edge.

        None means the importing file's own statements cannot mean ``imported``:
        the resolver matched a name. Edges this cannot judge (a language it does
        not read, source missing or unparseable) are kept as ``UNCLASSIFIED``.
        """
        importer, imported = _norm_relpath(importer), _norm_relpath(imported)
        if _rel(edge) == "dynamic_import":
            return DEFERRED
        low = importer.lower()
        if low.endswith(_COMPILED_EXTS):
            return MODULE_LEVEL  # resolved at compile time: never deferred
        kinds: list[str]
        if low.endswith(_JS_EXTS):
            js = _js_for_file(importer)
            if js is None or not js.imports:
                # no tree-sitter backend (or nothing parsed): the line still says
                # whether a TypeScript import is type-only
                if _TS_TYPE_IMPORT.match(self._line(importer, edge)):
                    return TYPE_CHECKING
                self.unclassified += 1
                return UNCLASSIFIED
            kinds = [i.kind for i in js.imports
                     if self._js_means(i.specifier, importer, imported)]
        else:
            stmts = _imports_for_file(importer)
            if stmts is None:
                self.unclassified += 1
                return UNCLASSIFIED
            target = _module_parts(imported)
            kinds = (
                [s.kind for s in stmts if self._can_mean(s, importer, target)]
                if target else []
            )
        if not kinds:
            self.rejected.append({
                "from_file": importer, "to_file": imported,
                "site": f"{importer}:{edge.get('source_location')}"
                if edge.get("source_location") else importer,
            })
            return None
        # One edge may stand for several statements (the graph keeps one per pair
        # of nodes): the one that runs earliest decides.
        return min(kinds, key=lambda k: _RUNTIME_RANK[k])

    def reaches(self, importer: str, imported: str) -> bool | None:
        """Can any import statement in ``importer``'s own source mean ``imported``?

        For a file whose imports the graph did not record: False when the source
        was read and none of its statements can import that file (a script that
        imports only the standard library imports nothing of the project). None
        when it cannot tell — a language it does not read, a compiled language
        (imports resolved by the build), missing or unparseable source.
        """
        importer, imported = _norm_relpath(importer), _norm_relpath(imported)
        low = importer.lower()
        if low.endswith(_COMPILED_EXTS):
            return None
        if low.endswith(_JS_EXTS):
            js = _js_for_file(importer)
            if js is None:
                return None
            return any(self._js_means(i.specifier, importer, imported) for i in js.imports)
        stmts = _imports_for_file(importer)
        target = _module_parts(imported)
        if stmts is None or target is None:
            return None
        return any(self._can_mean(st, importer, target) for st in stmts)

    def resolve_js(self, importer: str, specifier: str, files: set[str]) -> str | None:
        """The one file of ``files`` a JS/TS specifier names from ``importer``."""
        found = [f for f in files if _js_can_mean(specifier, importer, f)]
        return found[0] if len(found) == 1 else None

    def _line(self, rel: str, edge: dict) -> str:
        """Source text of the line an edge was recorded at ("" when unavailable)."""
        loc = str(edge.get("source_location") or "").lstrip("Ll")
        digits = "".join(ch for ch in loc.split("-")[0] if ch.isdigit())
        if not digits:
            return ""
        lines = self._lines.get(rel)
        if lines is None:
            lines = []
            full = _resolve_in_project(rel)
            if full is not None:
                try:
                    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
            self._lines[rel] = lines
        n = int(digits)
        return lines[n - 1] if 0 < n <= len(lines) else ""
