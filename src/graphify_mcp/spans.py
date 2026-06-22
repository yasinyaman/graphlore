"""Code-analysis engine: per-file symbol spans + chunk→node join + structural diff.

Resolves a semble chunk ``(file, line[..end])`` to the graph node whose *real* line
range contains it — Python via the stdlib ``ast`` (decorator-aware), every other
language via an optional tree-sitter backend with automatic language detection. Also
provides the comment-stripped structural comparison behind cosmetic-vs-structural
freshness. Reads the project root from :mod:`graphify_mcp.config`.
"""
from __future__ import annotations

import ast
from typing import Any

from . import config
from .graph import _node_line

# Per-file span index, keyed by absolute path -> (mtime, spans). A span is
# ``(region_start, end_line, def_line, qualname)`` for each def/class:
# ``region_start`` absorbs decorator / doc-comment / attribute lines, ``def_line``
# is where the graph node's ``source_location`` points. Used to resolve a semble
# chunk to its *containing* symbol by a real line range instead of graphify's single
# point.
_SPAN_CACHE: dict[str, tuple[float, list[tuple[int, int, int, str]]]] = {}
# Bound _SPAN_CACHE: under a long-lived HTTP transport with file churn (branch
# switches, generated files, multiple projects) it would otherwise grow for the
# life of the process, retaining entries for deleted/renamed files.
_SPAN_CACHE_MAX = 4096


def _span_cache_put(key: str, value: tuple[float, list[tuple[int, int, int, str]]]) -> None:
    if key not in _SPAN_CACHE and len(_SPAN_CACHE) >= _SPAN_CACHE_MAX:
        _SPAN_CACHE.pop(next(iter(_SPAN_CACHE)), None)  # FIFO: drop the oldest entry
    _SPAN_CACHE[key] = value


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
# NB: no "_expression" (function_expression is a named def) and no "_specifier"
# (C++ class_specifier / struct_specifier / enum_specifier ARE definitions). The
# excluded nodes match a hint substring but are never name-bearing definitions.
_TS_NON_SYMBOL_SUFFIXES = ("_type", "_body", "_parameters", "_clause")
# Nodes that match a hint substring ("function"/"type") but are NOT definitions:
# C/C++ calls/sub-declarators (`holds_alternative<T>(x)` -> template_function, a real
# name under a nested function_declarator), and type-level constructs — generic
# parameters (`<T>`) and associated-type bindings (`impl Iterator<Item = X>`) that
# carry a `name` field yet name no symbol.
_TS_CALL_TYPES = frozenset({
    "template_function", "template_method", "call_expression", "function_declarator",
    "type_binding", "type_parameter", "constrained_type_parameter",
    "optional_type_parameter", "type_argument", "type_arguments",
})
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
    if t in _TS_CALL_TYPES or t.endswith(_TS_NON_SYMBOL_SUFFIXES):
        return False
    # Calls/invocations expose the callee under a `name` field but define nothing —
    # Java `method_invocation`, C# `invocation_expression`, Ruby `method_call`, PHP
    # `function_call_expression`, etc. Match by substring so we don't have to
    # enumerate every grammar's spelling (`call_expression` is also caught here).
    if "invocation" in t or "call" in t:
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
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
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
    annotation / attribute siblings (Go/Java/JS/Rust doc style), mirroring Python's
    decorator-aware region — so a chunk that starts on the doc comment still resolves
    to the symbol it documents (instead of falling outside the span)."""
    start = def_line
    prev = child.prev_named_sibling
    while prev is not None:
        ptype = prev.type.lower()
        if not any(h in ptype for h in ("comment", "decorator", "annotation", "attribute")):
            break
        prev_end = prev.end_point[0] + 1
        if prev_end < start - 1:  # a blank-line gap -> detached, not a doc comment
            break
        start = prev.start_point[0] + 1
        prev = prev.prev_named_sibling
    return start


def _ts_receiver_type(recv: Any) -> str | None:
    """The receiver's type name in a method receiver, e.g. Go ``(c *Client)`` -> ``Client``."""
    stack = list(recv.named_children)
    while stack:
        n = stack.pop(0)
        if n.type == "type_identifier":
            return n.text.decode("utf-8", "replace")
        stack.extend(n.named_children)
    return None


def _ts_bound_function(node: Any) -> tuple[str, Any] | None:
    """An *anonymous* function bound to a name, so the binding name becomes the
    qualname (the arrow/closure carries no ``name`` field of its own). Covers
    ``const f = () => …`` / ``var h = func(){}`` (name+value), object properties
    ``{ foo: () => … }`` (key+value), and class fields ``handler = (r) => …``
    (property+value). ``None`` for non-function bindings or a function that already
    has its own name (handled by the normal path)."""
    name = (node.child_by_field_name("name")
            or node.child_by_field_name("key")
            or node.child_by_field_name("property"))
    value = node.child_by_field_name("value")
    if name is None or value is None or not name.type.endswith("identifier"):
        return None
    vt = value.type.lower()
    if not any(h in vt for h in ("function", "arrow", "lambda", "closure", "func")):
        return None
    if value.child_by_field_name("name") is not None:
        return None
    return name.text.decode("utf-8", "replace"), value


def _ts_declarator_name(node: Any) -> str | None:
    """C/C++ function name, which lives in a declarator chain rather than a ``name``
    field: ``function_definition > function_declarator > identifier /
    qualified_identifier / field_identifier``. ``Session::Get`` -> ``Session.Get``."""
    decl = node.child_by_field_name("declarator")
    for _ in range(8):  # bounded unwrap
        if decl is None:
            return None
        if decl.type in ("identifier", "field_identifier", "destructor_name"):
            return decl.text.decode("utf-8", "replace")
        if decl.type == "qualified_identifier":
            return decl.text.decode("utf-8", "replace").replace("::", ".")
        decl = decl.child_by_field_name("declarator")
    return None


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
                base = name_node.text.decode("utf-8", "replace")
                recv = child.child_by_field_name("receiver")  # Go method: prefix type
                if recv is not None and (rt := _ts_receiver_type(recv)):
                    base = f"{rt}.{base}"
                qual = f"{prefix}{base}"
                def_line = child.start_point[0] + 1
                end = child.end_point[0] + 1
                spans.append((_ts_region_start(child, def_line), end, def_line, qual))
                walk(child, qual + ".")
            elif is_sym and "impl" in child.type.lower() \
                    and (type_node := child.child_by_field_name("type")) is not None:
                # Rust `impl Pool { ... }`: no `name` field, but the `type` field
                # contributes to the qualname chain (so methods read `Pool.acquire`)
                # without a span. Restricted to impl-like nodes so a C++
                # function_definition's `type` (its return type) isn't mistaken for one.
                # Strip generics: `impl ConfigBuilder<X>` -> `ConfigBuilder.`, not the
                # full `ConfigBuilder<X>.`.
                base = type_node.child_by_field_name("type") or type_node
                walk(child, f"{prefix}{base.text.decode('utf-8', 'replace')}.")
            elif is_sym and (dname := _ts_declarator_name(child)) is not None:
                # C/C++ function/method: the name lives in the declarator chain
                qual = f"{prefix}{dname}"
                def_line = child.start_point[0] + 1
                end = child.end_point[0] + 1
                spans.append((_ts_region_start(child, def_line), end, def_line, qual))
                walk(child, qual + ".")
            elif (bound := _ts_bound_function(child)) is not None:
                # anonymous function bound to a name: `const f = () => …`
                bname, fn = bound
                qual = f"{prefix}{bname}"
                def_line = fn.start_point[0] + 1
                end = fn.end_point[0] + 1
                spans.append((def_line, end, def_line, qual))
                walk(fn, qual + ".")
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
        full = (config.PROJECT_DIR / rel).resolve()
        full.relative_to(config.PROJECT_DIR.resolve())
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
      1. **Span containment** (source available): find the most specific def/class
         whose real line range encloses the chunk, then the code node that *owns*
         that symbol; walk outward to the next enclosing symbol that has a node (so
         a chunk inside a nested closure with no node resolves to the enclosing
         function that does). A node owns the span that most tightly encloses its
         *own* line, so a node whose ``source_location`` points into a body
         (LLM-origin nodes) still binds to its real symbol. Using real end-lines,
         this never misattributes a chunk to a previous function that already
         ended — graphify nodes carry only a single ``source_location`` point.
      2. **Point heuristic** (fallback: no source on disk / no enclosing span
         carried a node): prefer a ``file_type == "code"`` node, pick the enclosing
         definition (greatest line <= start), else a def starting inside the chunk,
         else the closest. ``None`` only if the file has no nodes.
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
        return bytes(s) if isinstance(s, bytes | bytearray) else str(s).encode("utf-8", "replace")

    try:
        a = _ts_skeleton(parser.parse(_b(old_src)).root_node)
        b = _ts_skeleton(parser.parse(_b(new_src)).root_node)
    except Exception:
        return None
    if a is None or b is None:
        return None
    return a == b
