"""Analysis scope: which part of the graph the analysis tools look at.

One ``scope`` string is shared by the analysis tools (god nodes, validate,
cycles, surprises, duplication scan, unreferenced, same names). By default it
leaves out what is not the project's own code — vendored or minified files and
nodes standing for external packages — and says how much it left out, so a
bundled third-party script cannot dominate a degree ranking or a cycle list.

Depends on :mod:`graphlore.graph` for the node/edge accessors and on
:mod:`graphlore.spans` for the confined, cached per-file read.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

from . import config
from .graph import _edge_ends, _is_external_node, _is_test_path, _node_file, _node_id
from .spans import _cached_file_analysis, _norm_relpath

# Directory names that hold third-party code checked into the repository.
_VENDOR_DIR_PARTS = frozenset({
    "vendor", "vendors", "vendored", "_vendor", "node_modules", "bower_components",
    "third_party", "third-party", "thirdparty", "site-packages",
})
# Extensions whose files get shipped minified; only these are sniffed by content.
_MINIFIABLE_EXTS = frozenset({".js", ".mjs", ".cjs", ".css"})
# A build hash in a file name: ``decorate-BgofJUJ5.js``, ``main.3f2a1b9c.js``.
_HASHED_NAME = re.compile(r"[-.]([A-Za-z0-9_]{8,})\.(?:js|mjs|cjs|css)$")
# Mean line length above which a file is treated as minified. Hand-written
# source sits far below this even with the odd long line; a bundle sits far above.
_MINIFIED_MEAN_LINE = 150
_SNIFF_BYTES = 65536

# rel path -> (mtime, is-minified); same (path, mtime) scheme as the span cache.
_MINIFIED_CACHE: dict[str, tuple[float, bool]] = {}


def _looks_hashed(token: str) -> bool:
    """A random-looking build hash, not a word: mixes digits with both letter
    cases, or is hexadecimal with digits and letters."""
    has_digit = any(c.isdigit() for c in token)
    if not has_digit:
        return False
    if all(c in "0123456789abcdef" for c in token):
        return any(c.isalpha() for c in token)
    return any(c.isupper() for c in token) and any(c.islower() for c in token)


def _vendored_by_path(rel: str) -> bool:
    """Vendored or minified judging by the path alone (no disk access)."""
    parts = rel.split("/")
    if any(p.lower() in _VENDOR_DIR_PARTS for p in parts[:-1]):
        return True
    name = parts[-1]
    low = name.lower()
    if ".min." in low or ".bundle." in low:
        return True
    m = _HASHED_NAME.search(name)
    return bool(m and _looks_hashed(m.group(1)))


def _minified_by_content(rel: str) -> bool:
    """True when a JS/CSS file's lines are far too long to be hand-written."""
    dot = rel.rfind(".")
    if dot < 0 or rel[dot:].lower() not in _MINIFIABLE_EXTS:
        return False

    def compute(src: bytes, _rel: str) -> bool:
        head = src[:_SNIFF_BYTES]
        if not head.strip():
            return False
        return len(head) / (head.count(b"\n") + 1) > _MINIFIED_MEAN_LINE

    return bool(_cached_file_analysis(_MINIFIED_CACHE, rel, compute, lambda: False))


def _is_vendored_file(rel: str) -> bool:
    """Vendored or minified: by directory, by name, or by the look of its lines."""
    return _vendored_by_path(rel) or _minified_by_content(rel)


def _path_matches(pattern: str, path: str) -> bool:
    """Match a project-relative file path against a scope/layer pattern.

    A plain pattern is a path prefix at a directory boundary (``src/pkg`` covers
    ``src/pkg/a.py``), or the module file itself (``src/pkg/core`` covers
    ``src/pkg/core.py``). A pattern with ``*``/``?``/``[`` is a glob over the
    whole path, where ``*`` also crosses ``/``.
    """
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, pattern + "/*")
    if path == pattern or path.startswith(pattern + "/"):
        return True
    return path.rsplit(".", 1)[0] == pattern


def _clean_pattern(pattern: str) -> str:
    p = _norm_relpath(pattern).strip()
    for tail in ("/**", "/*", "/"):
        if p.endswith(tail):
            p = p[: -len(tail)]
    return p


_FLAGS = {
    "tests": "tests", "test": "tests",
    "external": "external", "externals": "external",
    "vendored": "vendored", "vendor": "vendored", "minified": "vendored",
}


@dataclass(frozen=True)
class Scope:
    """A parsed ``scope`` argument. ``tests`` is None when the caller did not say,
    so each tool keeps its own default for test code."""

    spec: str = ""
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    tests: bool | None = None
    external: bool = False
    vendored: bool = False


def _parse_scope(spec: str) -> Scope | str:
    """Parse a scope string; an error message (str) names the token it rejects.

    Tokens are separated by commas or whitespace:
      ``src/pkg``, ``**/*.py``   keep only files under this prefix / matching this glob
      ``!docs``                  leave these files out
      ``+tests`` / ``-tests``    include / leave out test code
      ``+external``              include nodes standing for external packages
      ``+vendored``              include vendored and minified files
      ``all``                    everything: +tests +external +vendored
    """
    include: list[str] = []
    exclude: list[str] = []
    flags: dict[str, bool] = {}
    for tok in re.split(r"[,\s]+", (spec or "").strip()):
        if not tok:
            continue
        if tok.lower() == "all":
            flags.update(tests=True, external=True, vendored=True)
        elif tok[0] in "+-":
            name = _FLAGS.get(tok[1:].lower())
            if name is None:
                return (
                    f"ERROR: unknown scope flag '{tok}'. Flags: +tests / -tests, "
                    "+external, +vendored, or 'all'. Anything else is a path prefix or "
                    "glob to keep, or '!pattern' to leave out."
                )
            flags[name] = tok[0] == "+"
        elif tok[0] == "!":
            if _clean_pattern(tok[1:]):
                exclude.append(_clean_pattern(tok[1:]))
        elif _clean_pattern(tok):
            include.append(_clean_pattern(tok))
    return Scope(
        spec=" ".join((spec or "").split()),
        include=tuple(include),
        exclude=tuple(exclude),
        tests=flags.get("tests"),
        external=flags.get("external", False),
        vendored=flags.get("vendored", False),
    )


@dataclass
class ScopeView:
    """The nodes and edges a scope keeps, and a count of what it left out.

    When nothing is left out, ``nodes`` and ``edges`` ARE the lists passed in, so
    the identity-keyed adjacency / label caches keep hitting. Otherwise the
    filtered lists are built once per graph and reused (see ``_scoped``).
    """

    nodes: list[dict]
    edges: list[dict]
    scope: Scope
    vendored_nodes: int = 0
    vendored_files: list[str] = field(default_factory=list)
    external_nodes: int = 0
    test_nodes: int = 0
    filtered_nodes: int = 0

    @property
    def left_out(self) -> int:
        return (
            self.vendored_nodes + self.external_nodes + self.test_nodes + self.filtered_nodes
        )

    def payload(self) -> dict[str, Any]:
        """The ``scope`` object every scoped tool returns."""
        return {
            "spec": self.scope.spec or "default",
            "left_out": {
                "vendored_nodes": self.vendored_nodes,
                "vendored_files": self.vendored_files[:20],
                "vendored_file_count": len(self.vendored_files),
                "external_nodes": self.external_nodes,
                "test_nodes": self.test_nodes,
                "filtered_nodes": self.filtered_nodes,
            },
        }

    def note(self) -> str:
        """One line saying what was left out and how to get it back; "" if nothing."""
        bits: list[str] = []
        hints: list[str] = []
        if self.vendored_nodes:
            n = len(self.vendored_files)
            bits.append(
                f"{self.vendored_nodes} node(s) from {n} vendored/minified file(s)"
            )
            hints.append("+vendored")
        if self.external_nodes:
            bits.append(f"{self.external_nodes} external node(s)")
            hints.append("+external")
        if self.test_nodes:
            bits.append(f"{self.test_nodes} test node(s)")
            hints.append("+tests")
        if self.filtered_nodes:
            bits.append(f"{self.filtered_nodes} node(s) outside the include/exclude patterns")
        if not bits:
            return ""
        how = f' (scope="{" ".join(hints)}" or "all" to include)' if hints else ""
        return "Left out of scope: " + ", ".join(bits) + how + "."


# (id(nodes), id(edges)) -> {cache key: view}, with an identity guard on both
# lists — the scheme of graph._ADJ_CACHE. The filtered lists a view holds must
# stay the same objects across calls, or every identity-keyed cache downstream
# (adjacency, display labels) would miss on each call.
_SCOPE_CACHE: dict[tuple[int, int], tuple[list[dict], list[dict], dict[str, ScopeView]]] = {}
_SCOPE_CACHE_MAX = 8


def _build_view(nodes: list[dict], edges: list[dict], scope: Scope, tests: bool) -> ScopeView:
    view = ScopeView(nodes=nodes, edges=edges, scope=scope)
    vendored_cache: dict[str, bool] = {}
    vendored_files: set[str] = set()
    dropped: set[str] = set()
    kept: list[dict] = []
    for n in nodes:
        rel = _norm_relpath(_node_file(n))
        reason = ""
        if rel and not scope.vendored:
            is_v = vendored_cache.get(rel)
            if is_v is None:
                is_v = vendored_cache[rel] = _is_vendored_file(rel)
            if is_v:
                reason = "vendored"
                vendored_files.add(rel)
        if not reason and not scope.external and _is_external_node(n):
            reason = "external"
        if not reason and not tests and rel and _is_test_path(rel):
            reason = "tests"
        if not reason and (scope.include or scope.exclude):
            if scope.include and not (rel and any(_path_matches(p, rel) for p in scope.include)):
                # an explicitly included external node has no path to match
                if not (scope.external and _is_external_node(n)):
                    reason = "filtered"
            if not reason and rel and any(_path_matches(p, rel) for p in scope.exclude):
                reason = "filtered"
        if not reason:
            kept.append(n)
            continue
        dropped.add(_node_id(n))
        if reason == "vendored":
            view.vendored_nodes += 1
        elif reason == "external":
            view.external_nodes += 1
        elif reason == "tests":
            view.test_nodes += 1
        else:
            view.filtered_nodes += 1
    if not dropped:
        return view  # nothing left out: hand back the very lists we were given
    view.vendored_files = sorted(vendored_files)
    view.nodes = kept
    # An edge survives when neither endpoint was left out. An endpoint that is no
    # node at all (a dangling edge) is kept: that is graphlore_validate's finding.
    view.edges = [
        e for e in edges
        if not any(end in dropped for end in _edge_ends(e))
    ]
    return view


def _scoped(
    nodes: list[dict], edges: list[dict], spec: str = "", default_tests: bool = True
) -> ScopeView | str:
    """The view of ``nodes``/``edges`` that ``spec`` keeps, or an error string.

    ``default_tests`` is what a tool does about test code when the spec does not
    say (``+tests`` / ``-tests`` always win).
    """
    scope = _parse_scope(spec)
    if isinstance(scope, str):
        return scope
    tests = default_tests if scope.tests is None else scope.tests
    key = f"{config.PROJECT_DIR}\0{scope.spec}\0{tests}"
    ident = (id(nodes), id(edges))
    cached = _SCOPE_CACHE.get(ident)
    if cached is not None and cached[0] is nodes and cached[1] is edges:
        views = cached[2]
    else:
        if ident not in _SCOPE_CACHE and len(_SCOPE_CACHE) >= _SCOPE_CACHE_MAX:
            _SCOPE_CACHE.pop(next(iter(_SCOPE_CACHE)), None)  # FIFO
        views = {}
        _SCOPE_CACHE[ident] = (nodes, edges, views)
    view = views.get(key)
    if view is None:
        if len(views) >= 16:
            views.pop(next(iter(views)), None)
        view = views[key] = _build_view(nodes, edges, scope, tests)
    return view
