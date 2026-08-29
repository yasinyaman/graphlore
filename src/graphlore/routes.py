"""Framework route extraction: URL pattern -> handler, per source file.

Web-framework routing is a graph edge no AST extractor emits: the URL pattern
lives in a decorator or a registration call, the handler is an ordinary
function, and the "this URL hits that code" link exists only in the framework's
conventions. This module recognizes the common registration idioms so
``graphlore_routes`` can answer "where is this endpoint" in one hop.

Recognized idioms (v1):
  * **Python** (stdlib ``ast``): Flask ``@app.route("/x", methods=[...])``,
    verb decorators ``@app.get("/x")`` / ``@router.post(...)`` — only in files
    that import a known web framework (fastapi/flask/sanic/quart/litestar,
    which also picks the label), so a registry-style ``@hooks.post("event")``
    in a framework-free file never fabricates a route; Django ``path()`` /
    ``re_path()`` / ``url()`` calls — only in files that import django, which
    kills false positives from local functions named ``path``.
  * **JS/TS** (tree-sitter): Express/Koa-router style ``app.get('/x', h)`` —
    only in files that import/require a server framework (express/fastify/
    koa-router/…), which keeps HTTP-client call sites (``axios.get('/x')``)
    out; the receiver must be a plain identifier and the first argument a
    string literal starting with ``/`` (that filter is what keeps
    ``map.get('key')`` out); NestJS ``@Controller('prefix')`` + ``@Get(':id')``
    method decorators.
  * **Go** (tree-sitter): verb methods (gin/echo ``r.GET``, chi ``r.Get``),
    ``Handle``/``HandleFunc`` (with the Go 1.22 ``"GET /items/{id}"`` pattern
    split into method + path, and the gin 3-arg ``r.Handle("GET", "/x", h)``
    form), and chi ``r.Route("/api", func(r) {...})`` nesting via
    prefix-carrying recursion. Framework labelled from the import paths
    (gin/echo/chi/gorilla, else net-http).
  * **Java** (tree-sitter): Spring ``@GetMapping``-family + ``@RequestMapping``
    (class-level prefix joined onto method-level paths).

Honest limits: the result is a LOWER BOUND. Non-literal patterns, dynamic
registration, chained builders (``router.route('/x').get(h)``, gorilla
``.Methods("GET")``), Flask ``add_url_rule``, Django ``include()`` recursion,
NestJS global prefixes and JAX-RS are all invisible; gorilla chains surface as
method ``ANY``, and variable-bound group prefixes (gin ``api := r.Group("/api")``)
are not resolved — only closure-style nesting (chi ``Route``) carries its prefix.
Zero routes reads "no visibility", not "no routes".

Python via the stdlib ``ast``; JS/TS, Go and Java via the optional tree-sitter
backend. Results are cached per (path, mtime) like the span index.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from . import config
from .apis import _strip_quotes
from .spans import _norm_relpath, _ts_parser_for

# One recognized registration: framework label, upper-cased HTTP method (ANY
# when the idiom doesn't pin one), URL pattern, handler name ("<inline>" for
# anonymous handlers), and the 1-based line of the handler definition (or the
# registration call when the handler is inline/remote) for the graph join.
RouteRow = dict[str, Any]

# Per-file route index, keyed by absolute path -> (mtime, rows). Same bounded
# FIFO scheme as spans._SPAN_CACHE (see there for the rationale).
_ROUTES_CACHE: dict[str, tuple[float, list[RouteRow]]] = {}
_ROUTES_CACHE_MAX = 4096

_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head", "trace"})
# Go 1.22 net/http pattern syntax: "GET /items/{id}" -> method + path.
_GO_122_PATTERN = re.compile(r"^([A-Z]+) (/.*)$")

# Python web frameworks sharing the @app.get/@router.post verb-decorator idiom,
# in label priority order (fastapi first: with both flask and fastapi imported,
# the shortcut style is fastapi's). The import both GATES the match — a file
# importing none of these must not turn @hooks.post("event") into a route — and
# picks the framework label.
_PY_VERB_FRAMEWORKS = (
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("sanic", "sanic"),
    ("quart", "quart"),
    ("litestar", "litestar"),
)

# JS/TS server frameworks whose import/require gates Express-style verb-call
# detection; without the gate, HTTP-client request sites (axios.get('/api/x'),
# apiClient.post('/login', body)) are indistinguishable from registrations.
_JS_SERVER_FRAMEWORKS = ("express", "fastify", "koa-router", "@koa/router", "restify", "polka")


def _routes_cache_put(key: str, value: tuple[float, list[RouteRow]]) -> None:
    if key not in _ROUTES_CACHE and len(_ROUTES_CACHE) >= _ROUTES_CACHE_MAX:
        _ROUTES_CACHE.pop(next(iter(_ROUTES_CACHE)), None)  # FIFO: drop the oldest
    _ROUTES_CACHE[key] = value


def _row(framework: str, method: str, pattern: str, handler: str, line: int) -> RouteRow:
    return {
        "framework": framework,
        "method": method.upper(),
        "pattern": pattern,
        "handler": handler,
        "line": line,
    }


def _join_path(prefix: str, sub: str) -> str:
    combined = "/".join(p.strip("/") for p in (prefix, sub) if p and p.strip("/"))
    return "/" + combined if combined else "/"


# ---------------------------------------------------------------------------
# Python (stdlib ast)
# ---------------------------------------------------------------------------


def _routes_python(src: bytes) -> list[RouteRow]:
    rows: list[RouteRow] = []
    try:
        tree = ast.parse(src)
    except Exception:
        return rows

    # Which frameworks does this file import? Gates Django call matching (a local
    # `path()` must not register) and labels the shared verb-decorator shortcut.
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    # The import set gates AND labels verb-decorator matching (see
    # _PY_VERB_FRAMEWORKS); None = no web framework imported, no verb rows.
    verb_framework = next(
        (label for mod, label in _PY_VERB_FRAMEWORKS if mod in imported), None
    )

    def _first_pattern(call: ast.Call) -> str | None:
        if call.args and isinstance(call.args[0], ast.Constant) and \
                isinstance(call.args[0].value, str):
            return call.args[0].value
        for kw in call.keywords:
            if kw.arg == "path" and isinstance(kw.value, ast.Constant) and \
                    isinstance(kw.value.value, str):
                return kw.value.value
        return None  # non-literal pattern: invisible, per the lower-bound contract

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                attr = dec.func.attr
                pattern = _first_pattern(dec)
                if pattern is None or verb_framework is None:
                    continue
                if attr == "route":  # Flask style, one row per declared method
                    methods = ["GET"]
                    for kw in dec.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            declared = [
                                e.value for e in kw.value.elts
                                if isinstance(e, ast.Constant) and isinstance(e.value, str)
                            ]
                            methods = declared or methods
                    label = "flask" if "flask" in imported else verb_framework
                    rows += [_row(label, m, pattern, node.name, node.lineno)
                             for m in methods]
                elif attr in _HTTP_METHODS:
                    rows.append(_row(verb_framework, attr, pattern, node.name, node.lineno))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("path", "re_path", "url") and "django" in imported
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str) and len(node.args) > 1):
            try:
                handler = ast.unparse(node.args[1])
            except Exception:
                handler = "<unknown>"
            rows.append(_row("django", "ANY", node.args[0].value, handler, node.lineno))
    return rows


# ---------------------------------------------------------------------------
# JS/TS, Go, Java (optional tree-sitter backend)
# ---------------------------------------------------------------------------


def _text(node: Any) -> str:
    return node.text.decode("utf-8", "replace")


def _routes_js(root: Any) -> list[RouteRow]:
    """Express/Koa-router verb calls + NestJS controller decorators (JS/TS/TSX)."""
    rows: list[RouteRow] = []
    verbs = _HTTP_METHODS | {"all"}
    nest_verbs = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE",
                  "Patch": "PATCH", "Options": "OPTIONS", "Head": "HEAD", "All": "ANY"}

    imports: set[str] = set()

    def collect_imports(node: Any) -> None:
        for child in node.named_children:
            if child.type == "import_statement":
                src = child.child_by_field_name("source")
                if src is not None:
                    imports.add(_strip_quotes(_text(src)))
            elif child.type == "call_expression":
                fn = child.child_by_field_name("function")
                args = child.child_by_field_name("arguments")
                if (fn is not None and args is not None and fn.type == "identifier"
                        and _text(fn) == "require"):
                    strings = [a for a in args.named_children if "string" in a.type]
                    if strings:
                        imports.add(_strip_quotes(_text(strings[0])))
            collect_imports(child)

    collect_imports(root)
    # Same gate idea as the Python verb decorators: without a server-framework
    # import in the file, identifier.verb('/x', …) is far likelier an HTTP-client
    # request site than a route registration.
    server_framework = any(
        imp == fw or imp.startswith(fw + "/")
        for imp in imports for fw in _JS_SERVER_FRAMEWORKS
    )

    def string_arg(args: Any, index: int = 0) -> str | None:
        strings = [a for a in args.named_children if "string" in a.type]
        if index >= len(strings):
            return None
        return _strip_quotes(_text(strings[index]))

    def decorator_call(dec: Any) -> Any | None:
        return next((c for c in dec.named_children if c.type == "call_expression"), None)

    def handle_class(cls: Any, pending: list[Any]) -> None:
        prefix = ""
        decs = pending + [c for c in cls.named_children if c.type == "decorator"]
        for dec in decs:
            call = decorator_call(dec)
            if call is None:
                continue
            fn = call.child_by_field_name("function")
            args = call.child_by_field_name("arguments")
            if fn is not None and fn.type == "identifier" and _text(fn) == "Controller":
                prefix = (string_arg(args) or "") if args is not None else ""
        body = cls.child_by_field_name("body")
        if body is None:
            return
        # Grammar versions differ on whether a method's decorators are siblings in
        # the class body or children of the method_definition — handle both.
        method_pending: list[Any] = []
        for child in body.named_children:
            if child.type == "decorator":
                method_pending.append(child)
                continue
            if child.type != "method_definition":
                method_pending = []
                continue
            decs = method_pending + [c for c in child.named_children if c.type == "decorator"]
            method_pending = []
            for dec in decs:
                call = decorator_call(dec)
                if call is None:
                    continue
                fn = call.child_by_field_name("function")
                args = call.child_by_field_name("arguments")
                if fn is None or fn.type != "identifier" or _text(fn) not in nest_verbs:
                    continue
                sub = (string_arg(args) or "") if args is not None else ""
                name = child.child_by_field_name("name")
                rows.append(_row(
                    "nestjs", nest_verbs[_text(fn)], _join_path(prefix, sub),
                    _text(name) if name is not None else "<unknown>",
                    child.start_point[0] + 1,
                ))

    def walk(node: Any, pending: list[Any]) -> None:
        for child in node.named_children:
            t = child.type
            if t == "decorator":
                pending.append(child)
                continue
            if t == "class_declaration":
                handle_class(child, pending)
                pending = []
                continue
            if t == "call_expression":
                fn = child.child_by_field_name("function")
                args = child.child_by_field_name("arguments")
                if (fn is not None and args is not None and fn.type == "member_expression"):
                    obj = fn.child_by_field_name("object")
                    prop = fn.child_by_field_name("property")
                    # Plain-identifier receiver only: excludes router.route('/x')
                    # .get(h) chains (documented v1 cut) and this.x receivers.
                    # Gated on a server-framework import (see above).
                    if (server_framework and obj is not None and prop is not None
                            and obj.type == "identifier" and _text(prop) in verbs):
                        pattern = string_arg(args)
                        if pattern is not None and pattern.startswith("/"):
                            handler = "<inline>"
                            last = args.named_children[-1] if args.named_children else None
                            if last is not None and last.type in (
                                    "identifier", "member_expression"):
                                handler = _text(last)
                            method = _text(prop)
                            rows.append(_row(
                                "express", "ANY" if method == "all" else method,
                                pattern, handler, child.start_point[0] + 1,
                            ))
            walk(child, pending)
            pending = []

    walk(root, [])
    return rows


def _routes_go(root: Any) -> list[RouteRow]:
    """Verb methods (gin/echo/chi), Handle/HandleFunc (+ Go 1.22 method patterns),
    chi ``Route``/``Group``/``With`` nesting via prefix-carrying recursion."""
    rows: list[RouteRow] = []
    verb_upper = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
    verb_title = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head"}

    framework = "net-http"
    imports: list[str] = []

    def collect_imports(node: Any) -> None:
        for child in node.named_children:
            if child.type == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node is not None:
                    imports.append(_strip_quotes(_text(path_node)))
            else:
                collect_imports(child)

    collect_imports(root)
    for path, label in (("gin-gonic/gin", "gin"), ("labstack/echo", "echo"),
                        ("go-chi/chi", "chi"), ("gorilla/mux", "gorilla")):
        if any(path in imp for imp in imports):
            framework = label
            break

    def first_string(args: Any) -> tuple[str | None, int]:
        for i, a in enumerate(args.named_children):
            if a.type in ("interpreted_string_literal", "raw_string_literal"):
                return _strip_quotes(_text(a)), i
        return None, -1

    def handler_text(args: Any) -> str:
        last = args.named_children[-1] if args.named_children else None
        if last is None or last.type == "func_literal":
            return "<inline>"
        return _text(last) if last.type in ("identifier", "selector_expression") else "<inline>"

    def walk(node: Any, prefix: str) -> None:
        for child in node.named_children:
            if child.type == "call_expression":
                fn = child.child_by_field_name("function")
                args = child.child_by_field_name("arguments")
                if fn is not None and args is not None and fn.type == "selector_expression":
                    field = fn.child_by_field_name("field")
                    name = _text(field) if field is not None else ""
                    pattern, _idx = first_string(args)
                    n_args = len(args.named_children)
                    if name == "Route" and pattern is not None:
                        # chi: r.Route("/api", func(r chi.Router) {...}) — recurse
                        # with the joined prefix, no row for the mount itself.
                        inner = next((a for a in args.named_children
                                      if a.type == "func_literal"), None)
                        if inner is not None:
                            walk(inner, _join_path(prefix, pattern))
                            continue
                    if name in ("Group", "With"):
                        inner = next((a for a in args.named_children
                                      if a.type == "func_literal"), None)
                        if inner is not None:
                            walk(inner, prefix)
                            continue
                    if (name in verb_upper or name in verb_title) and n_args >= 2 \
                            and pattern is not None and pattern.startswith("/"):
                        rows.append(_row(framework, name, _join_path(prefix, pattern),
                                         handler_text(args), child.start_point[0] + 1))
                        # fall through: the receiver may itself be a call chain
                    elif name in ("Handle", "HandleFunc") and n_args >= 2 \
                            and pattern is not None:
                        m = _GO_122_PATTERN.match(pattern)
                        strings = [
                            _strip_quotes(_text(a)) for a in args.named_children
                            if a.type in ("interpreted_string_literal", "raw_string_literal")
                        ]
                        if m:
                            method, pat = m.group(1), m.group(2)
                        elif pattern.startswith("/"):
                            method, pat = "ANY", pattern
                        elif (len(strings) >= 2 and strings[0].upper() in verb_upper
                                and strings[1].startswith("/")):
                            # gin 3-arg form: r.Handle("GET", "/x", handler) — the
                            # first literal is the METHOD, the second the path.
                            method, pat = strings[0].upper(), strings[1]
                        else:
                            walk(child, prefix)
                            continue
                        rows.append(_row(framework, method, _join_path(prefix, pat),
                                         handler_text(args), child.start_point[0] + 1))
            walk(child, prefix)

    walk(root, "")
    return rows


def _routes_java(root: Any) -> list[RouteRow]:
    """Spring ``@GetMapping``-family and ``@RequestMapping`` (class prefix joined)."""
    rows: list[RouteRow] = []
    mappings = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}

    def annotations_of(decl: Any) -> list[Any]:
        mods = next((c for c in decl.named_children if c.type == "modifiers"), None)
        if mods is None:
            return []
        return [c for c in mods.named_children
                if c.type in ("annotation", "marker_annotation")]

    def annotation_name(ann: Any) -> str:
        name = ann.child_by_field_name("name")
        return _text(name).rsplit(".", 1)[-1] if name is not None else ""

    def annotation_path(ann: Any) -> str:
        """First string in the arguments — bare value, value=/path= pair, or the
        first element of an array initializer (v1 keeps only the first)."""
        args = ann.child_by_field_name("arguments")
        if args is None:
            return ""
        pairs: dict[str, Any] = {}
        bare: list[Any] = []
        for p in args.named_children:
            if p.type == "element_value_pair":
                key = p.child_by_field_name("key")
                if key is not None:
                    pairs[_text(key)] = p
            else:
                bare.append(p)
        # value=/path= pairs first; otherwise only BARE values, so a string in an
        # unrelated pair (produces="application/json") is never read as the path.
        scopes = [pairs[k] for k in ("value", "path") if k in pairs] or bare
        for scope in scopes:
            stack = [scope]
            while stack:
                n = stack.pop(0)
                if n.type == "string_literal":
                    return _strip_quotes(_text(n))
                stack.extend(n.named_children)
        return ""

    def request_methods(ann: Any) -> list[str]:
        args = ann.child_by_field_name("arguments")
        if args is None:
            return ["ANY"]
        for p in args.named_children:
            key = p.child_by_field_name("key")
            value = p.child_by_field_name("value")
            if key is not None and _text(key) == "method" and value is not None:
                # method = RequestMethod.GET, or an array
                # method = {RequestMethod.GET, RequestMethod.POST} — one row each,
                # never the raw array text (which would yield a garbage 'POST}').
                elems = (list(value.named_children)
                         if value.type == "element_value_array_initializer" else [value])
                methods = [_text(el).rsplit(".", 1)[-1].upper() for el in elems]
                return [m for m in methods if m] or ["ANY"]
        return ["ANY"]

    def walk(node: Any) -> None:
        for child in node.named_children:
            if child.type == "class_declaration":
                prefix = ""
                for ann in annotations_of(child):
                    if annotation_name(ann) == "RequestMapping":
                        prefix = annotation_path(ann)
                body = child.child_by_field_name("body")
                for member in (body.named_children if body is not None else []):
                    if member.type != "method_declaration":
                        continue
                    name = member.child_by_field_name("name")
                    handler = _text(name) if name is not None else "<unknown>"
                    line = member.start_point[0] + 1
                    for ann in annotations_of(member):
                        ann_name = annotation_name(ann)
                        if ann_name in mappings:
                            rows.append(_row(
                                "spring", mappings[ann_name],
                                _join_path(prefix, annotation_path(ann)), handler, line))
                        elif ann_name == "RequestMapping":
                            for meth in request_methods(ann):
                                rows.append(_row(
                                    "spring", meth,
                                    _join_path(prefix, annotation_path(ann)), handler, line))
                walk(child)  # nested classes
            else:
                walk(child)

    walk(root)
    return rows


_TS_ROUTE_EXTRACTORS = {
    "javascript": _routes_js,
    "typescript": _routes_js,
    "tsx": _routes_js,
    "jsx": _routes_js,
    "go": _routes_go,
    "java": _routes_java,
}


def _routes_treesitter(src: bytes, rel: str) -> list[RouteRow]:
    parser, lang = _ts_parser_for(rel)
    extractor = _TS_ROUTE_EXTRACTORS.get(str(lang or "").lower())
    if parser is None or extractor is None:
        return []
    try:
        root = parser.parse(src).root_node
        return extractor(root)
    except Exception:  # parse failure / grammar drift: degrade to no visibility
        return []


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def routes_for_source(src: bytes | str, rel: str) -> list[RouteRow]:
    """PUBLIC: framework routes for one source blob — the stable contract for
    external consumers (importable as ``from graphlore.routes import
    routes_for_source``); the underscore-prefixed extractors behind it are
    internal and may be renamed.

    ``rel`` is the file's (relative) path and only selects the parser — ``.py``
    goes through the stdlib ``ast``, anything else through the optional
    tree-sitter backend (empty result when that backend or the language is
    unavailable). No file IO, no caching, no project-dir confinement: the caller
    owns the source bytes.

    Returns rows ``{framework, method, pattern, handler, line}`` sorted by line;
    a lower bound — see the module docstring for what is invisible.
    """
    data = src if isinstance(src, bytes) else src.encode("utf-8", "replace")
    if str(rel).lower().endswith(".py"):
        rows = _routes_python(data)
    else:
        rows = _routes_treesitter(data, str(rel))
    return sorted(rows, key=lambda r: (r["line"], r["method"], r["pattern"]))


def _routes_for_file(file_path: str) -> list[RouteRow]:
    """Routes for one source file under PROJECT_DIR.

    Python via stdlib ``ast``; JS/TS, Go and Java via the optional tree-sitter
    backend when present; empty for other / missing / unparseable files (cached
    either way). Confined to PROJECT_DIR like the span index. Cached by
    (path, mtime).
    """
    rel = _norm_relpath(file_path)
    if not rel:
        return []
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
    cached = _ROUTES_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        src = full.read_bytes()
    except OSError:
        _routes_cache_put(key, (mtime, []))
        return []
    rows = routes_for_source(src, rel)
    _routes_cache_put(key, (mtime, rows))
    return rows
