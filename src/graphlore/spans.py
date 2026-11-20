"""Code-analysis engine: per-file symbol spans + chunk→node join + structural diff.

Resolves a semble chunk ``(file, line[..end])`` to the graph node whose *real* line
range contains it — Python via the stdlib ``ast`` (decorator-aware), every other
language via an optional tree-sitter backend with automatic language detection. Also
provides the comment-stripped structural comparison behind cosmetic-vs-structural
freshness. Reads the project root from :mod:`graphlore.config`.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, engine
from .graph import _is_test_path, _node_file, _node_line

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


def _resolve_in_project(file_path: object) -> Path | None:
    """Resolve a (possibly hostile) path to an absolute path confined to PROJECT_DIR.

    This is the single security boundary for every per-file analyzer: an empty,
    absolute-outside, or ``..``-escaping path returns None, so no engine ever
    reads a file outside the project — even though most outputs are only
    line/name metadata.
    """
    rel = _norm_relpath(file_path)
    if not rel:
        return None
    try:
        full = (config.PROJECT_DIR / rel).resolve()
        full.relative_to(config.PROJECT_DIR.resolve())
    except (ValueError, OSError):
        return None
    return full


def _cached_file_analysis(
    cache: dict[str, tuple[float, Any]],
    file_path: object,
    compute: Callable[[bytes, str], Any],
    empty: Callable[[], Any],
    cache_max: int | None = None,
) -> Any:
    """Shared per-file pipeline behind the span / API / route indexes.

    Confine to PROJECT_DIR, stat the mtime, serve the ``(path, mtime)`` cache,
    else read bytes and call ``compute(src, rel)`` — caching the result (or
    ``empty()`` on a read failure) with bounded-FIFO eviction so a long-lived
    process with file churn doesn't retain entries for deleted files forever.
    """
    full = _resolve_in_project(file_path)
    if full is None:
        return empty()
    try:
        mtime = full.stat().st_mtime
    except OSError:
        return empty()
    key = str(full)
    cached = cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        src = full.read_bytes()
    except OSError:
        result = empty()
    else:
        result = compute(src, _norm_relpath(file_path))
    # Resolved at call time (not a frozen default) so tests can shrink the bound.
    limit = _SPAN_CACHE_MAX if cache_max is None else cache_max
    if key not in cache and len(cache) >= limit:
        cache.pop(next(iter(cache)), None)  # FIFO: drop the oldest entry
    cache[key] = (mtime, result)
    return result


def _norm_relpath(p: object) -> str:
    s = str(p or "").strip()
    return s[2:] if s.startswith("./") else s


# tree-sitter symbol node-type hints (generic across languages). A node counts as
# a definition only if its type matches one of these AND it names itself — through
# a "name" field, a declarator chain (C/C++) or, in grammars that have neither
# (Kotlin), an identifier child — which filters out look-alikes (function_type,
# class_body, ...).
_TS_SYMBOL_HINTS = (
    "function", "method", "constructor", "class", "struct", "interface",
    "enum", "trait", "module", "object", "type", "subroutine",
    "procedure", "package", "namespace", "record", "protocol",
)
# Hints that must be a whole word of the node type: as a substring "impl" also
# sits in PHP's `simple_parameter` and Kotlin's `simple_identifier`.
_TS_SYMBOL_WORD_HINTS = frozenset({"impl", "mod"})
# NB: no "_expression" (function_expression is a named def) and no "_specifier"
# (C++ class_specifier / struct_specifier / enum_specifier ARE definitions when
# they have a body). The excluded nodes match a hint substring but name a use of a
# symbol, never its definition: a type written in a signature (`React.FC`,
# `io::Result`, Swift `x: Foo`) or a parameter (Scala `class_parameter`).
_TS_NON_SYMBOL_SUFFIXES = (
    "_type", "_body", "_parameters", "_clause",
    "_identifier", "_annotation", "_parameter",
)
# Nodes that match a hint substring ("function"/"type") but are NOT definitions:
# C/C++ calls/sub-declarators (`holds_alternative<T>(x)` -> template_function, a real
# name under a nested function_declarator), and type-level constructs — generic
# parameters (`<T>`, Go `[T any]`), associated-type bindings (`impl Iterator<Item = X>`),
# a Swift tuple member, a TypeScript `x is T` predicate, a Rust struct literal
# (`Foo { a: 1 }`) and a C macro mistaken for a type — that carry a `name` field
# yet name no symbol.
_TS_CALL_TYPES = frozenset({
    "template_function", "template_method", "call_expression", "function_declarator",
    "type_binding", "type_parameter", "constrained_type_parameter",
    "optional_type_parameter", "type_argument", "type_arguments",
    "type_parameter_declaration", "type_predicate", "tuple_type_item",
    "struct_expression", "macro_type_specifier",
})
# Definitions whose grammar gives them a keyword, not a name.
_TS_KEYWORD_NAMES = {
    "init_declaration": "init", "deinit_declaration": "deinit",   # Swift
    "secondary_constructor": "constructor",                        # Kotlin
}
# The child that names a declaration in a grammar without a `name` field (Kotlin:
# `function_declaration > simple_identifier`, `class_declaration > type_identifier`).
_TS_NAME_CHILD_TYPES = frozenset({"simple_identifier", "type_identifier", "identifier"})
# C/C++ declarations with no body of their own: a prototype in a header, a method
# declared in its class. They are spans only when they declare a function.
_TS_PROTOTYPE_TYPES = frozenset({"declaration", "field_declaration"})
# A `.h` file is C to the language pack; with one of these at the start of a line
# it is a C++ header, and the C grammar reads its classes as errors.
_CPP_HEADER_MARK = re.compile(
    rb"^[ \t]*(?:class\s+\w|namespace\b|template\s*<|using\s+namespace\b"
    rb"|(?:public|private|protected)\s*:)", re.M)
# Parser cache keyed by language name; value is a parser or None (unavailable).
_TS_PARSERS: dict[str, Any] = {}
# The span walk visits every named node, so what it decides per node type is
# cached: whether the type can be a definition (`_ts_is_symbol_type`), and per
# language the ids of the fields it reads (None: the grammar has no such field,
# so the lookup can be skipped).
_TS_SYMBOL_CACHE: dict[str, bool] = {}
_TS_FIELD_NAMES = ("name", "key", "property", "value", "left", "right", "body",
                   "receiver", "type")
_TS_FIELDS: dict[str, dict[str, int | None]] = {}
# The only node types `_ts_anonymous_class` can name.
_TS_ANON_CLASS_TYPES = frozenset({"object_creation_expression", "object_literal", "assignment"})


def _ts_parser_for(rel: str, src: bytes | None = None) -> tuple[Any, str | None]:
    """(parser, language) for a path via the optional tree-sitter backend.

    Returns (None, None) when tree-sitter / the language pack is not installed or
    the language is unknown. tree-sitter ships with graphify, so it's usually
    present; declared as the ``[treesitter]`` extra otherwise. Cached per language.
    With ``src``, a ``.h`` file that reads as C++ gets the C++ grammar.
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
    if lang == "c" and src is not None and _CPP_HEADER_MARK.search(src):
        lang = "cpp"
    if lang not in _TS_PARSERS:
        if config.offline() and not _grammar_cached(lang):
            # get_language downloads an uncached grammar; offline, do without it
            # (not cached as None, so it is tried once the switch is off)
            return None, None
        try:
            # Some pack releases type get_language's argument as a Literal of
            # every grammar name, while detect_language_from_path returns str.
            _TS_PARSERS[lang] = Parser(get_language(lang))  # type: ignore[arg-type]
        except Exception:
            _TS_PARSERS[lang] = None
    return _TS_PARSERS[lang], lang


def _grammar_cached(lang: str) -> bool:
    """Whether tree-sitter-language-pack has ``lang`` on disk already. A pack too
    old to say (no ``downloaded_languages``) is trusted, since it then bundles its
    grammars rather than downloading them."""
    try:
        from tree_sitter_language_pack import downloaded_languages
    except ImportError:
        return True
    try:
        return lang in set(downloaded_languages())
    except Exception:  # noqa: BLE001 - a cache probe that fails reads as "not cached"
        return False


# Definitions whose node type has no hint word: C++ `using AsyncResponse =
# AsyncWrapper<Response>;` (not every `alias`: Python's `aliased_import` names a
# module it imports).
_TS_SYMBOL_TYPES = frozenset({"alias_declaration"})


def _is_ts_symbol(node_type: str) -> bool:
    t = node_type.lower()
    if t in _TS_CALL_TYPES or t.endswith(_TS_NON_SYMBOL_SUFFIXES):
        return False
    if t in _TS_SYMBOL_TYPES:
        return True
    # Calls/invocations expose the callee under a `name` field but define nothing —
    # Java `method_invocation`, C# `invocation_expression`, Ruby `method_call`, PHP
    # `function_call_expression`, etc. Match by substring so we don't have to
    # enumerate every grammar's spelling (`call_expression` is also caught here).
    if "invocation" in t or "call" in t:
        return False
    if any(h in t for h in _TS_SYMBOL_HINTS):
        return True
    return not _TS_SYMBOL_WORD_HINTS.isdisjoint(t.split("_"))


def _ts_is_symbol_type(node_type: str) -> bool:
    """`_is_ts_symbol`, or a keyword-named definition; cached per type."""
    hit = _TS_SYMBOL_CACHE.get(node_type)
    if hit is None:
        hit = _is_ts_symbol(node_type) or node_type in _TS_KEYWORD_NAMES
        _TS_SYMBOL_CACHE[node_type] = hit
    return hit


def _ts_fields(lang: str, language: Any) -> dict[str, int | None]:
    """Field ids of `_TS_FIELD_NAMES` in a grammar, cached per language."""
    fields = _TS_FIELDS.get(lang)
    if fields is None:
        fields = {}
        for name in _TS_FIELD_NAMES:
            try:
                fields[name] = language.field_id_for_name(name)
            except Exception:  # noqa: BLE001 - an unknown field reads as absent
                fields[name] = None
        _TS_FIELDS[lang] = fields
    return fields


def _ts_field(node: Any, name: str, fields: dict[str, int | None] | None) -> Any:
    """``node.child_by_field_name(name)``; with ``fields`` (from `_ts_fields`) by
    id, and None without a lookup when the grammar has no such field."""
    if fields is None:
        return node.child_by_field_name(name)
    fid = fields.get(name)
    return None if fid is None else node.child_by_field_id(fid)


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


def _ts_bound_function(node: Any,
                       fields: dict[str, int | None] | None = None) -> tuple[str, Any] | None:
    """An *anonymous* function bound to a name, so the binding name becomes the
    qualname (the arrow/closure carries no ``name`` field of its own). Covers
    ``const f = () => …`` / ``var h = func(){}`` (name+value), object properties
    ``{ foo: () => … }`` (key+value), class fields ``handler = (r) => …``
    (property+value) and assignments ``exports.send = function () {…}`` /
    ``res.send = …`` (left+right, named by the member assigned to). ``None`` for
    non-function bindings or a function that already has its own name (handled by
    the normal path). ``fields`` (from `_ts_fields`) only speeds up the lookups."""
    name = (_ts_field(node, "name", fields)
            or _ts_field(node, "key", fields)
            or _ts_field(node, "property", fields))
    value = _ts_field(node, "value", fields)
    if name is None and value is None:
        left = _ts_field(node, "left", fields)
        if left is not None and left.type == "member_expression":
            left = _ts_field(left, "property", fields)
        name, value = left, _ts_field(node, "right", fields)
    if name is None or value is None or not name.type.endswith("identifier"):
        return None
    if value.type == "call_expression":
        value = _ts_wrapped_function(value)
        if value is None:
            return None
    vt = value.type.lower()
    if not any(h in vt for h in ("function", "arrow", "lambda", "closure", "func")):
        return None
    if vt.endswith("_type"):
        return None  # a type written out (Go `map[string]func() int`), not a function
    if _ts_field(value, "name", fields) is not None:
        return None
    return name.text.decode("utf-8", "replace"), value


# Callees that take a function to run later, not to return a wrapped version of it.
_TS_SCHEDULERS = frozenset({"setTimeout", "setInterval", "setImmediate", "queueMicrotask"})
_TS_FUNCTION_VALUES = frozenset({
    "arrow_function", "function_expression", "function", "generator_function",
})


def _ts_wrapped_function(call: Any) -> Any | None:
    """The function in a one-call wrapper: ``async(function* () {…})``,
    ``wrap(async (req, res) => {…})`` — a plain-named callee given exactly one
    anonymous function. ``users.map(u => …)`` and ``setTimeout(() => …)`` are
    not wrappers."""
    fn = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if fn is None or args is None or fn.type != "identifier":
        return None
    if fn.text.decode("utf-8", "replace") in _TS_SCHEDULERS:
        return None
    found = [a for a in args.named_children if a.type in _TS_FUNCTION_VALUES]
    if len(found) != 1 or found[0].child_by_field_name("name") is not None:
        return None
    return found[0]


def _ts_anonymous_class(node: Any) -> str | None:
    """The name a class with no name of its own goes by: Java
    ``new Callback<Void>() { … }`` and Kotlin ``object : Callback<T> { … }`` by
    the type they implement, Ruby ``Request = Struct.new(:a) do … end`` by the
    constant it is assigned to."""
    t = node.type
    if t == "object_creation_expression":
        if not any(c.type == "class_body" for c in node.named_children):
            return None
        typ = node.child_by_field_name("type")
        return typ.text.decode("utf-8", "replace") if typ is not None else None
    if t == "object_literal":
        spec = next((c for c in node.named_children if c.type == "delegation_specifier"), None)
        user = None if spec is None else next(
            (c for c in spec.named_children if c.type == "user_type"), None)
        if user is None or not any(c.type == "class_body" for c in node.named_children):
            return None
        return user.text.decode("utf-8", "replace").split("<", 1)[0]
    if t == "assignment":
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if (left is not None and left.type == "constant" and right is not None
                and right.type == "call" and right.child_by_field_name("block") is not None):
            return left.text.decode("utf-8", "replace")
    return None


def _ts_declarator_name(node: Any) -> str | None:
    """C/C++ function name, which lives in a declarator chain rather than a ``name``
    field: ``function_definition > function_declarator > identifier /
    qualified_identifier / field_identifier``. ``Session::Get`` -> ``Session.Get``.
    The chain is followed through the wrappers that carry no ``declarator`` field
    (``const T& f()`` is a reference_declarator around the function_declarator)."""
    decl = node.child_by_field_name("declarator")
    for _ in range(8):  # bounded unwrap
        if decl is None:
            return None
        if decl.type in ("identifier", "field_identifier", "destructor_name",
                         "type_identifier", "operator_name"):
            return decl.text.decode("utf-8", "replace")
        if decl.type == "qualified_identifier":
            return decl.text.decode("utf-8", "replace").replace("::", ".")
        decl = decl.child_by_field_name("declarator") or next(
            (c for c in decl.named_children
             if c.type.endswith("declarator") or c.type == "qualified_identifier"), None)
    return None


def _ts_declares_function(node: Any) -> bool:
    """Whether a C/C++ ``declaration`` / ``field_declaration`` declares a function
    (``int f(void);``) rather than a variable or a field."""
    decl = node.child_by_field_name("declarator")
    for _ in range(8):
        if decl is None:
            return False
        if decl.type == "function_declarator":
            return True
        decl = decl.child_by_field_name("declarator") or next(
            (c for c in decl.named_children if c.type.endswith("declarator")), None)
    return False


def _ts_child_name(node: Any) -> str | None:
    """The name of a declaration whose grammar has no ``name`` field: its keyword
    (Swift ``init``), or its first identifier child (Kotlin)."""
    fixed = _TS_KEYWORD_NAMES.get(node.type)
    if fixed is not None:
        return fixed
    t = node.type.lower()
    if not t.endswith(("_declaration", "_entry", "type_alias")) or "package" in t:
        return None
    for c in node.named_children:
        if c.type in _TS_NAME_CHILD_TYPES:
            return c.text.decode("utf-8", "replace")
    return None


_PP_SPACE = b" \t\n\x0c\r"  # what Rust's `is_ascii_whitespace` strips
_PP_ENDS = b";{}],)"  # what ends a statement, a block, an attribute or an item of a list


def _blank_expression_branches(src: bytes) -> bytes:
    """C#: an ``#if`` … ``#else`` … ``#endif`` that opens in the middle of an
    expression or a declaration (the code line before it ends none: ``? null``
    before ``#if NET`` / ``: new A(x)`` / ``#else`` / ``: new A()``) is one
    piece of code written once per configuration; the parser, seeing every
    branch, loses the type around. The source with the directive lines and
    every branch but the first blanked (each byte but a newline or carriage
    return a space), else ``src``. Of a group with one branch only the two
    directive lines go: its lines are part of the expression.

    The native engine's ``parse::blank_expression_branches`` is the same rule.
    """
    if b"#if" not in src:
        return src
    lines = src.split(b"\n")
    blank: list[tuple[int, int]] = []
    stack: list[tuple[bool, list[int]]] = []
    last = b""  # the last line of code, without its comment
    for li, line in enumerate(lines):
        t = line.strip(_PP_SPACE)
        if t.startswith(b"#"):
            word = t[1:].lstrip(_PP_SPACE)
            n = 0
            while n < len(word) and word[n:n + 1].isalpha():
                n += 1
            word = word[:n]
            if word == b"if":
                inside = bool(last) and last[-1:] not in _PP_ENDS
                stack.append((inside, [li]))
            elif word in (b"elif", b"else"):
                if stack:
                    stack[-1][1].append(li)
            elif word == b"endif":
                if stack:
                    inside, directives = stack.pop()
                    if inside:
                        blank.append((directives[0], directives[0]))
                        blank.append((directives[1] if len(directives) >= 2 else li, li))
            continue
        cut = t.find(b"//")
        code = (t if cut < 0 else t[:cut]).rstrip(_PP_SPACE)
        if code:
            last = code
    if not blank:
        return src
    for lo, hi in blank:
        for i in range(lo, hi + 1):
            lines[i] = bytes(c if c == 0x0D else 0x20 for c in lines[i])
    return b"\n".join(lines)



_CLASS_MACRO = re.compile(
    rb"\b(?:class|struct|union)[ \t]+([A-Z][A-Z0-9_]{2,})[ \t]+[A-Za-z_][A-Za-z0-9_]*"
    rb"[ \t\r\n]*(?:\{|:[^:]|final\b)")


def _blank_class_macros(src: bytes) -> bytes:
    """C, C++: `class LEVELDB_EXPORT Env {` with the macro blanked.

    A macro between the keyword and the name is nothing to the language once
    expanded, but the parser takes it for the type's name and the rest for a
    function, so the members belong to no class. Each byte of the macro becomes
    a space (bytes and lines stay where they were). The engine's
    `parse::blank_class_macros` is the same rule.
    """
    out: bytearray | None = None
    for m in _CLASS_MACRO.finditer(src):
        if out is None:
            out = bytearray(src)
        out[m.start(1):m.end(1)] = b" " * (m.end(1) - m.start(1))
    return bytes(out) if out is not None else src


def _blank_duplicate_branches(src: bytes) -> bytes:
    """C, C++: an ``#if`` … ``#elif``/``#else`` … ``#endif`` whose every branch
    leaves the same number of braces open (not none) is one piece of code
    written once per configuration (``if (error_code) {`` under ``#if`` and
    again under ``#else``, closed once after ``#endif``); the parser, seeing every
    branch, would swallow the rest of the file into the function around. The
    source with the directive lines and every branch but the first blanked
    (each byte but a newline or carriage return a space), else ``src``.

    The native engine's ``parse::blank_duplicate_branches`` is the same rule.
    """
    if b"#" not in src:
        return src
    lines: list[tuple[int, int]] = []
    start = 0
    for i, b in enumerate(src):
        if b == 0x0A:
            lines.append((start, i))
            start = i + 1
    if start < len(src):
        lines.append((start, len(src)))
    stack: list[tuple[list[int], list[list[int]]]] = []  # (directives, [[first, delta]])
    blank: list[tuple[int, int]] = []
    in_comment = False
    continued = False
    for li, (lo, hi) in enumerate(lines):
        line = src[lo:hi]
        was_continued = continued
        continued = line.endswith(b"\\") or line.endswith(b"\\\r")
        if was_continued:
            continue
        trimmed = line.lstrip(_PP_SPACE)
        if not in_comment and trimmed[:1] == b"#":
            rest = trimmed[1:].lstrip(_PP_SPACE)
            n = 0
            while n < len(rest) and (65 <= rest[n] <= 90 or 97 <= rest[n] <= 122):
                n += 1
            word = rest[:n]
            if word in (b"if", b"ifdef", b"ifndef"):
                stack.append(([li], [[li + 1, 0]]))
            elif word in (b"elif", b"else", b"elifdef", b"elifndef"):
                if stack:
                    stack[-1][0].append(li)
                    stack[-1][1].append([li + 1, 0])
            elif word == b"endif":
                if stack:
                    directives, branches = stack.pop()
                    first = branches[0][1]
                    if len(branches) >= 2 and first != 0 and all(b[1] == first for b in branches):
                        blank.extend((d, d) for d in directives)
                        blank.append((li, li))
                        for a, b in zip(directives[1:], directives[2:], strict=False):
                            blank.append((a + 1, b - 1))
                        if len(directives) >= 2:
                            blank.append((directives[-1] + 1, li - 1))
                    if stack:
                        stack[-1][1][-1][1] += first
            continue
        delta = 0
        i = 0
        while i < len(line):
            c = line[i]
            if in_comment:
                if c == 0x2A and line[i + 1:i + 2] == b"/":
                    in_comment = False
                    i += 1
            elif c == 0x2F and line[i + 1:i + 2] == b"/":
                break
            elif c == 0x2F and line[i + 1:i + 2] == b"*":
                in_comment = True
                i += 1
            elif c in (0x22, 0x27):
                i += 1
                while i < len(line) and line[i] != c:
                    if line[i] == 0x5C:
                        i += 1
                    i += 1
            elif c == 0x7B:
                delta += 1
            elif c == 0x7D:
                delta -= 1
            i += 1
        if stack:
            stack[-1][1][-1][1] += delta
    if not blank:
        return src
    out = bytearray(src)
    last = len(lines) - 1
    for a, b in blank:
        for lo, hi in lines[a:min(b, last) + 1]:
            for k in range(lo, hi):
                if out[k] != 0x0D:
                    out[k] = 0x20
    return bytes(out)


def _spans_treesitter(src: bytes, rel: str) -> list[tuple[int, int, int, str]]:
    """def/class/etc. spans for any tree-sitter-supported language (optional dep).

    Generic: a named node whose type hints at a definition and that exposes a
    ``name`` field becomes a span ``(region_start, end, def_line, qualname)``,
    where ``region_start`` absorbs leading doc-comment/decorator/annotation lines
    and the qualname chains enclosing named symbols. Empty when the tree-sitter
    backend or the language is unavailable (caller then uses the point heuristic).
    """
    parser, lang = _ts_parser_for(rel, src)
    if parser is None:
        # No Python tree-sitter (or no grammar, offline): with the native engine
        # chosen, its compiled-in grammars answer with the same rule.
        if engine.requested() != "graphify":
            return engine.file_spans(src, rel) or []
        return []
    try:
        # (C, C++: an `#if` written once per configuration is parsed once)
        parsed = src
        if lang in ("c", "cpp"):
            # (and `class EXPORT_MACRO Name {` without the macro)
            parsed = _blank_duplicate_branches(_blank_class_macros(src))
        elif lang == "csharp":
            # (C#: an `#if` in the middle of an expression is parsed once)
            parsed = _blank_expression_branches(src)
        root = parser.parse(parsed).root_node
    except Exception:
        return []
    spans: list[tuple[int, int, int, str]] = []
    fields = _ts_fields(lang or "", parser.language)
    f_name, f_body, f_recv, f_type = (fields["name"], fields["body"],
                                      fields["receiver"], fields["type"])
    f_value, f_left = fields["value"], fields["left"]
    symbol_cache = _TS_SYMBOL_CACHE

    def add(child: Any, qual: str) -> None:
        def_line = child.start_point[0] + 1
        end = child.end_point[0] + 1
        spans.append((_ts_region_start(child, def_line), end, def_line, qual))

    # Every check below is the one the generic rules make; they are only ordered
    # so that a node pays for the lookups that can still change the answer.
    def walk(node: Any, prefix: str) -> None:
        for child in node.named_children:
            t = child.type
            if not child.child_count:
                # a leaf has no fields and no children: only a keyword-named
                # definition can come of it
                kname = _TS_KEYWORD_NAMES.get(t)
                if kname is not None:
                    add(child, f"{prefix}{kname}")
                continue
            is_sym = symbol_cache.get(t)
            if is_sym is None:
                is_sym = _ts_is_symbol_type(t)
            name_node = child.child_by_field_id(f_name) \
                if is_sym and f_name is not None else None
            if name_node is not None and t.endswith("_specifier") \
                    and (f_body is None or child.child_by_field_id(f_body) is None):
                # C/C++ `struct config cfg` in a signature, or a forward
                # declaration: the type is named here, not defined
                walk(child, prefix)
            elif name_node is not None and t == "mod_item" \
                    and (f_body is None or child.child_by_field_id(f_body) is None):
                pass  # Rust `mod foo;` names a file, it defines nothing here
            elif name_node is not None:
                base = name_node.text.decode("utf-8", "replace")
                # Go method: prefix the receiver's type
                recv = child.child_by_field_id(f_recv) if f_recv is not None else None
                if recv is not None and (rt := _ts_receiver_type(recv)):
                    base = f"{rt}.{base}"
                qual = f"{prefix}{base}"
                add(child, qual)
                walk(child, qual + ".")
            elif is_sym and f_type is not None and "impl" in t.lower() \
                    and (type_node := child.child_by_field_id(f_type)) is not None:
                # Rust `impl Pool { ... }`: no `name` field, but the `type` field
                # contributes to the qualname chain (so methods read `Pool.acquire`)
                # without a span. Restricted to impl-like nodes so a C++
                # function_definition's `type` (its return type) isn't mistaken for one.
                # Strip generics: `impl ConfigBuilder<X>` -> `ConfigBuilder.`, not the
                # full `ConfigBuilder<X>.`.
                base = type_node.child_by_field_id(f_type) or type_node
                walk(child, f"{prefix}{base.text.decode('utf-8', 'replace')}.")
            elif (is_sym or (t in _TS_PROTOTYPE_TYPES
                             and _ts_declares_function(child))) \
                    and (dname := _ts_declarator_name(child)) is not None:
                # C/C++ function/method: the name lives in the declarator chain
                qual = f"{prefix}{dname}"
                add(child, qual)
                walk(child, qual + ".")
            elif is_sym and (kname := _ts_child_name(child)) is not None:
                # no `name` field in this grammar (Kotlin), or a keyword-named
                # definition (Swift `init`)
                qual = f"{prefix}{kname}"
                add(child, qual)
                walk(child, qual + ".")
            elif t in _TS_ANON_CLASS_TYPES and (anon := _ts_anonymous_class(child)) is not None:
                # a class with no name of its own: named by what it implements
                qual = f"{prefix}{anon}"
                add(child, qual)
                walk(child, qual + ".")
            elif child.child_count >= 2 and (
                    (f_value is not None and child.child_by_field_id(f_value) is not None)
                    or (f_left is not None and child.child_by_field_id(f_left) is not None)) \
                    and (bound := _ts_bound_function(child, fields)) is not None:
                # anonymous function bound to a name: `const f = () => …`. It takes
                # two children, one of them a `value` or a `left`.
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


# --- test code that lives in a source file ------------------------------------
# Rust keeps unit tests next to the code: `#[cfg(test)] mod tests { … }` and
# `#[test] fn …`. No path rule can tell those from production code, so the span
# engine marks their line ranges. Per file, (path, mtime)-cached like the spans.
_TEST_REGION_CACHE: dict[str, tuple[float, list[tuple[int, int]]]] = {}


def _rust_attributes(node: Any) -> list[str]:
    """Texts of the `#[...]` attributes stacked right above ``node``."""
    out: list[str] = []
    prev = node.prev_named_sibling
    while prev is not None and (prev.type == "attribute_item" or "comment" in prev.type):
        if prev.type == "attribute_item":
            out.append(prev.text.decode("utf-8", "replace"))
        prev = prev.prev_named_sibling
    return out


_RUST_CFG_TEST = re.compile(r"\bcfg\s*\((?:[^()]|\([^()]*\))*\btest\b")
_RUST_CFG_NOT_TEST = re.compile(r"\bnot\s*\(\s*test\s*\)")
_RUST_TEST_ATTR = re.compile(r"^#\s*\[\s*(?:[\w:]+::)?test\s*(?:\(|\])")


def _is_cfg_test(attr: str) -> bool:
    return bool(_RUST_CFG_TEST.search(attr)) and not _RUST_CFG_NOT_TEST.search(attr)


def _test_regions_rust(src: bytes, rel: str) -> list[tuple[int, int]]:
    parser, _lang = _ts_parser_for(rel, src)
    if parser is None:
        return []
    try:
        root = parser.parse(src).root_node
    except Exception:
        return []
    if any(c.type == "inner_attribute_item" and _is_cfg_test(c.text.decode("utf-8", "replace"))
           for c in root.named_children):
        return [(1, root.end_point[0] + 1)]  # `#![cfg(test)]`: the whole file
    regions: list[tuple[int, int]] = []

    def walk(node: Any) -> None:
        for child in node.named_children:
            span = (child.start_point[0] + 1, child.end_point[0] + 1)
            if child.type == "mod_item":
                body = child.child_by_field_name("body")
                inner = body is not None and any(
                    c.type == "inner_attribute_item"
                    and _is_cfg_test(c.text.decode("utf-8", "replace"))
                    for c in body.named_children)
                if inner or any(_is_cfg_test(a) for a in _rust_attributes(child)):
                    regions.append(span)
                    continue
            elif child.type == "function_item" and any(
                    # (`#[cfg(test)] fn helper()`: compiled only with the tests)
                    _RUST_TEST_ATTR.match(a) or _is_cfg_test(a)
                    for a in _rust_attributes(child)):
                regions.append(span)
                continue
            walk(child)

    try:
        walk(root)
    except RecursionError:
        pass
    return regions


def _test_regions(file_path: str) -> list[tuple[int, int]]:
    """Line ranges of a source file that are test code although the file is not a
    test file. Rust only: other languages keep tests in test files, which
    `_is_test_path` recognises."""
    if not str(file_path).lower().endswith(".rs"):
        return []
    return _cached_file_analysis(_TEST_REGION_CACHE, file_path, _test_regions_rust, list)


def _is_test_node(n: dict) -> bool:
    """A node that is test code: in a test file, or inside a test region of a
    source file (a Rust `#[cfg(test)]` module or `#[test]` function)."""
    file = _node_file(n)
    if not file:
        return False
    if _is_test_path(file):
        return True
    regions = _test_regions(file)
    if not regions:
        return False
    try:
        line = int(_node_line(n))
    except (TypeError, ValueError):
        return False
    return any(lo <= line <= hi for lo, hi in regions)


def _spans_for_file(file_path: str) -> list[tuple[int, int, int, str]]:
    """def/class/etc. spans for a source file under PROJECT_DIR, across languages.

    Returns ``[(region_start, end_line, def_line, qualname), ...]`` sorted by
    ``region_start``. Python uses stdlib ``ast`` (decorator-aware, zero deps); any
    other language uses the optional tree-sitter backend when present. Empty for
    unsupported / missing / unparseable files (cached either way so a broken file
    isn't re-parsed). Cached by (path, mtime).
    """
    # Read bytes so a BOM / coding cookie is honored. Span extraction is
    # best-effort: any failure degrades to [] so the caller falls back to the
    # point heuristic rather than breaking the whole locate.
    def compute(src: bytes, rel: str) -> list[tuple[int, int, int, str]]:
        spans = _spans_python(src) if rel.lower().endswith(".py") else _spans_treesitter(src, rel)
        spans.sort(key=lambda s: s[0])
        return spans

    return _cached_file_analysis(_SPAN_CACHE, file_path, compute, list)


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


def _span_of_definition(
    file_path: str, line: int, name: str
) -> tuple[int, int, int, str] | None:
    """The span of the definition a graph node stands for.

    Several spans can start on one line (``enum Color { RED, GREEN }``, a class and
    its one-line member), and the innermost is then not the node's own: the span
    that starts on the node's line under the node's name wins, else the innermost
    enclosing one.
    """
    spans = _enclosing_spans(file_path, line, line)
    if not spans:
        return None
    for s in spans:
        if s[2] == line and s[3].rsplit(".", 1)[-1] == name:
            return s
    return spans[0]


def _span_qualname(file_path: str, line: int, end_line: int | None = None) -> str | None:
    """Fully-qualified name of the most specific symbol enclosing a chunk, if any."""
    hi = end_line if end_line is not None else line
    spans = _enclosing_spans(file_path, line, hi)
    return spans[0][3] if spans else None


# file (normalized relpath) -> nodes index, keyed by id(nodes) with an identity
# guard — the same scheme as graph._ADJ_CACHE. _node_for_location is called in
# loops (per semantic hit in locate, per seed/related pair in duplication_scan,
# per route row), so the full-node scan it replaced was O(calls x N).
_FILE_NODES_CACHE: dict[int, tuple[list[dict], dict[str, list[dict]]]] = {}
_FILE_NODES_CACHE_MAX = 8


def _nodes_by_file(nodes: list[dict]) -> dict[str, list[dict]]:
    """Normalized-relpath -> nodes map for `nodes`, cached on list identity."""
    cached = _FILE_NODES_CACHE.get(id(nodes))
    if cached is not None and cached[0] is nodes:
        return cached[1]
    by_file: dict[str, list[dict]] = {}
    for n in nodes:
        rel = _norm_relpath(_node_file(n))
        if rel:
            by_file.setdefault(rel, []).append(n)
    if id(nodes) not in _FILE_NODES_CACHE and len(_FILE_NODES_CACHE) >= _FILE_NODES_CACHE_MAX:
        _FILE_NODES_CACHE.pop(next(iter(_FILE_NODES_CACHE)), None)  # FIFO
    _FILE_NODES_CACHE[id(nodes)] = (nodes, by_file)
    return by_file


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
    same_file = _nodes_by_file(nodes).get(target, [])
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
    def _b(s: Any) -> bytes:
        return bytes(s) if isinstance(s, bytes | bytearray) else str(s).encode("utf-8", "replace")

    parser, _lang = _ts_parser_for(rel, _b(new_src))
    if parser is None:
        return None

    try:
        a = _ts_skeleton(parser.parse(_b(old_src)).root_node)
        b = _ts_skeleton(parser.parse(_b(new_src)).root_node)
    except Exception:
        return None
    if a is None or b is None:
        return None
    return a == b
