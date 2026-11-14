"""Import statements read from the source: what each can mean, and when it runs.

graphify resolves an import to a graph node by name, which goes wrong when a
first-party module shares its name with an installed package: ``import yaml``
(PyYAML) becomes an import of ``pkg/adapters/export/yaml.py``. And an import edge
does not say whether the statement runs when the module is imported, sits under
``if TYPE_CHECKING:``, or inside a function — the difference between a cycle that
breaks start-up and two modules that merely know each other.

:class:`ImportChecker` answers both from the importing file's own statements.
Python is read with the stdlib ``ast``; for other languages the edge is kept and
reported as unclassified, except where the graph or the import line itself says
more (a ``dynamic_import`` relation, TypeScript's ``import type``).
"""
from __future__ import annotations

import ast
import re
from typing import Any, NamedTuple

from .graph import (
    IMPORT_DEFERRED,
    IMPORT_MODULE_LEVEL,
    IMPORT_TYPE_CHECKING,
    IMPORT_UNCLASSIFIED,
    _rel,
)
from .spans import _cached_file_analysis, _norm_relpath, _resolve_in_project

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
        self._lines: dict[str, list[str]] = {}
        self.rejected: list[dict[str, str]] = []
        self.unclassified = 0

    def _exists(self, rel: str) -> bool:
        full = _resolve_in_project(rel)
        return full is not None and full.is_file()

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

    def kind(self, importer: str, imported: str, edge: dict) -> str | None:
        """When the import behind a file-level edge runs, or None for a false edge.

        None means the importing file's own statements cannot mean ``imported``:
        the resolver matched a name. Edges this cannot judge (non-Python, source
        missing or unparseable) are kept as ``UNCLASSIFIED``.
        """
        importer, imported = _norm_relpath(importer), _norm_relpath(imported)
        if _rel(edge) == "dynamic_import":
            return DEFERRED
        stmts = _imports_for_file(importer)
        if stmts is None:
            if _TS_TYPE_IMPORT.match(self._line(importer, edge)):
                return TYPE_CHECKING
            self.unclassified += 1
            return UNCLASSIFIED
        target = _module_parts(imported)
        kinds = (
            [s.kind for s in stmts if self._can_mean(s, importer, target)] if target else []
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
