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
  * **JS/TS** (tree-sitter): Express/Koa-router style ``app.get('/x', h)`` and
    ``router.route('/x').get(h)`` — in files that import/require a server
    framework (express/fastify/koa-router/…, which also picks the label), or,
    flagged ``needs_server_import``, on a receiver named like a router or
    passed in as a parameter (``module.exports = function (app) {…}``); the
    first argument must be a string literal starting with ``/`` (that filter is
    what keeps ``map.get('key')`` out); NestJS ``@Controller('prefix')`` +
    ``@Get(':id')`` method decorators.
  * **Go** (tree-sitter): verb methods (gin/echo ``r.GET``, chi ``r.Get``),
    ``Handle``/``HandleFunc`` (with the Go 1.22 ``"GET /items/{id}"`` pattern
    split into method + path, and the gin 3-arg ``r.Handle("GET", "/x", h)``
    form), and chi ``r.Route("/api", func(r) {...})`` nesting via
    prefix-carrying recursion. Framework labelled from the import paths
    (gin/echo/chi/gorilla, else net-http).
  * **Java** (tree-sitter): Spring ``@GetMapping``-family + ``@RequestMapping``
    (class-level prefix joined onto method-level paths); JAX-RS ``@Path`` +
    ``@GET`` in files that import ``javax.ws.rs`` / ``jakarta.ws.rs``.
  * **Kotlin**: Spring annotations and the Ktor ``routing { route("/x") {…} }``
    DSL, each gated on its import.
  * **C#**: ASP.NET Core ``[Route]`` / ``[HttpGet]`` controllers (``[controller]``
    and ``[action]`` filled in) and minimal APIs (``app.MapGet``, ``MapGroup``
    prefixes held in a variable).
  * **Rust**: actix-web ``#[get("/x")]`` macros and ``web::scope`` / ``resource``
    builders; axum ``Router::new().route("/x", get(h)).nest(...)``.
  * **Ruby**: Sinatra ``get '/x' do`` and the Rails ``routes.draw`` DSL
    (``resources`` with ``only:`` / ``except:`` / ``param:`` and nesting,
    ``namespace``, ``scope``, ``root``).
  * **PHP**: Laravel ``Route::get`` chains (``prefix`` / ``group`` /
    ``resource``) and Symfony ``#[Route]`` attributes.

Honest limits: the result is a LOWER BOUND. Non-literal patterns, dynamic
registration, gorilla ``.Methods("GET")``, Flask ``add_url_rule``, Django
``include()`` recursion, prefixes that cross files (NestJS global prefixes,
Laravel's ``/api`` for ``routes/api.php``, axum routers built in another
function, Rails ``draw(:admin)``), docblock ``@Route`` annotations and
Kotlin ``router {}`` DSLs are all invisible; gorilla chains surface as method
``ANY``, and gin's variable-bound groups (``api := r.Group("/api")``) are not
resolved.
Zero routes reads "no visibility", not "no routes".

Python via the stdlib ``ast``; JS/TS, Go and Java via the optional tree-sitter
backend. Results are cached per (path, mtime) like the span index.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from .apis import _strip_quotes
from .spans import _cached_file_analysis, _ts_parser_for

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
# The label a row gets for the framework the file imports.
_JS_FRAMEWORK_LABELS = {"koa-router": "koa", "@koa/router": "koa"}
# Without a framework import in the file, `x.get('/p', handler)` is taken for a
# registration only when `x` reads like a server object or is a parameter of the
# function around it (`module.exports = function (app) { app.get(...) }`), and
# only in a project where some file imports a server framework: the row carries
# `needs_server_import` and graphlore_routes drops it otherwise.
_JS_ROUTER_NAMES = frozenset({"app", "router", "server", "api", "routes", "route", "r"})
_JS_HANDLER_TYPES = frozenset({
    "identifier", "member_expression", "arrow_function", "function_expression",
    "function", "call_expression",
})


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
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
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
                        if kw.arg == "methods" and isinstance(kw.value, ast.List | ast.Tuple):
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
    framework = next(
        (fw for fw in _JS_SERVER_FRAMEWORKS for imp in sorted(imports)
         if imp == fw or imp.startswith(fw + "/")), None)
    label = _JS_FRAMEWORK_LABELS.get(framework or "", framework or "express")

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

    def parameters(fn: Any) -> set[str]:
        """Names a function binds as parameters (plain identifiers only)."""
        out: set[str] = set()
        one = fn.child_by_field_name("parameter")      # `app => …`
        if one is not None and one.type == "identifier":
            out.add(_text(one))
        params = fn.child_by_field_name("parameters")
        for p in params.named_children if params is not None else ():
            if p.type == "identifier":
                out.add(_text(p))
            elif p.type in ("required_parameter", "optional_parameter"):  # TypeScript
                pat = p.child_by_field_name("pattern")
                if pat is not None and pat.type == "identifier":
                    out.add(_text(pat))
        return out

    def chain_root(call: Any) -> tuple[Any, str] | None:
        """For `x.route('/p').get(h).post(h2)`: (`x`, '/p') from any verb call of it."""
        fn = call.child_by_field_name("function")
        while fn is not None and fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            if obj is None or obj.type != "call_expression":
                return None
            inner = obj.child_by_field_name("function")
            args = obj.child_by_field_name("arguments")
            if inner is None or inner.type != "member_expression" or args is None:
                return None
            prop = inner.child_by_field_name("property")
            recv = inner.child_by_field_name("object")
            if prop is not None and _text(prop) == "route":
                pattern = string_arg(args)
                if recv is not None and recv.type == "identifier" and pattern is not None:
                    return recv, pattern
                return None
            if prop is None or _text(prop) not in verbs:
                return None
            fn = inner
        return None

    def registration(call: Any, params: set[str]) -> None:
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if fn is None or args is None or fn.type != "member_expression":
            return
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is None or prop is None or _text(prop) not in verbs:
            return
        chained = chain_root(call) if obj.type == "call_expression" else None
        if chained is not None:
            recv, pattern = chained
        elif obj.type == "identifier":
            # a plain receiver only: `this.x.get(...)` and `a.b.get(...)` are not
            recv, found = obj, string_arg(args)
            if found is None:
                return
            pattern = found
        else:
            return
        if not pattern.startswith("/"):
            return
        last = args.named_children[-1] if args.named_children else None
        if framework is None:
            # no framework import here: ask more of the call (see _JS_ROUTER_NAMES)
            name = _text(recv)
            handlers = args.named_children[0 if chained else 1:]
            if not handlers or last is None or last.type not in _JS_HANDLER_TYPES:
                return
            if name not in _JS_ROUTER_NAMES and name not in params:
                return
        handler = "<inline>"
        if last is not None and last.type in ("identifier", "member_expression"):
            handler = _text(last)
        method = _text(prop)
        row = _row(label, "ANY" if method == "all" else method, pattern, handler,
                   prop.start_point[0] + 1)
        if framework is None:
            row["needs_server_import"] = True
        rows.append(row)

    def walk(node: Any, pending: list[Any], params: set[str]) -> None:
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
                registration(child, params)
            inner = params
            if t in ("function_declaration", "function_expression", "function",
                     "arrow_function", "method_definition", "generator_function"):
                inner = params | parameters(child)
            walk(child, pending, inner)
            pending = []

    walk(root, [], set())
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


_JAXRS_VERBS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})


def _routes_java(root: Any) -> list[RouteRow]:
    """Spring ``@GetMapping``-family and ``@RequestMapping`` (class prefix joined),
    and JAX-RS ``@Path`` + ``@GET`` — gated on a ``javax.ws.rs`` /
    ``jakarta.ws.rs`` import, since Retrofit's client interfaces say ``@GET`` too."""
    rows: list[RouteRow] = []
    jaxrs = any(
        "javax.ws.rs" in (t := _text(imp)) or "jakarta.ws.rs" in t
        for imp in _all(root, "import_declaration"))
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
            if child.type in ("class_declaration", "interface_declaration"):
                prefix = ""
                path_prefix: str | None = None
                for ann in annotations_of(child):
                    if annotation_name(ann) == "RequestMapping":
                        prefix = annotation_path(ann)
                    elif annotation_name(ann) == "Path":
                        path_prefix = annotation_path(ann)
                body = child.child_by_field_name("body")
                for member in (body.named_children if body is not None else []):
                    if member.type != "method_declaration":
                        continue
                    name = member.child_by_field_name("name")
                    handler = _text(name) if name is not None else "<unknown>"
                    line = member.start_point[0] + 1
                    if jaxrs:
                        anns = {annotation_name(a): a for a in annotations_of(member)}
                        sub = anns.get("Path")
                        if path_prefix is not None or sub is not None:
                            for verb in sorted(_JAXRS_VERBS & anns.keys()):
                                rows.append(_row(
                                    "jax-rs", verb, _join_path(
                                        path_prefix or "",
                                        annotation_path(sub) if sub is not None else ""),
                                    handler, line))
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


# ---------------------------------------------------------------------------
# Shared helpers for the tree-sitter route extractors below
# ---------------------------------------------------------------------------

_STRING_PARTS = frozenset({
    "string_content", "string_fragment", "string_literal_content", "string_value",
})
_STRING_TYPES = frozenset({
    "string_literal", "string", "encapsed_string", "raw_string_literal",
    "verbatim_string_literal",
})


def _lit(node: Any) -> str | None:
    """The value of a string literal with nothing interpolated; None otherwise."""
    if node is None or node.type not in _STRING_TYPES:
        return None
    parts: list[str] = []
    for c in node.named_children:
        if c.type in _STRING_PARTS:
            parts.append(_text(c))
        elif c.type != "escape_sequence":
            return None  # `"${x}/users"`, `"#{id}"`: not a literal pattern
    if parts or not node.named_children:
        return "".join(parts) if parts else _strip_quotes(_text(node))
    return None


def _kid(node: Any, *types: str) -> Any | None:
    return next((c for c in node.named_children if c.type in types), None)


def _kids(node: Any, *types: str) -> list[Any]:
    return [c for c in node.named_children if c.type in types]


def _all(root: Any, *types: str) -> list[Any]:
    """Every node of these types under ``root``, in source order, iteratively."""
    out: list[Any] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in types:
            out.append(n)
        stack.extend(reversed(n.named_children))
    return out


def _first_lit(node: Any) -> str | None:
    """The first string literal at or under ``node``, breadth first."""
    queue = [node]
    while queue:
        n = queue.pop(0)
        value = _lit(n)
        if value is not None:
            return value
        queue.extend(n.named_children)
    return None


# ---------------------------------------------------------------------------
# Kotlin: Spring annotations and the Ktor routing DSL
# ---------------------------------------------------------------------------

_SPRING_MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                    "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}


def _routes_kotlin(root: Any) -> list[RouteRow]:
    """Spring ``@GetMapping`` / ``@RequestMapping`` (class prefix joined) and Ktor
    ``routing { route("/api") { get("/x") { } } }``. Each is gated on its import
    (``org.springframework.web…``, ``io.ktor…routing``), since the grammar has no
    fields to tell a `get("/x")` route from any other call."""
    imports = [_text(i) for h in _all(root, "import_header")
               if (i := _kid(h, "identifier")) is not None]
    rows: list[RouteRow] = []
    if any(i.startswith("org.springframework.web") for i in imports):
        rows += _kotlin_spring(root)
    if any(i.startswith("io.ktor") and "routing" in i for i in imports):
        rows += _kotlin_ktor(root)
    return rows


def _kotlin_annotations(decl: Any) -> list[tuple[str, Any | None]]:
    """(name, value_arguments) of each annotation on a Kotlin declaration."""
    mods = _kid(decl, "modifiers")
    out: list[tuple[str, Any | None]] = []
    for ann in _kids(mods, "annotation") if mods is not None else []:
        ci = _kid(ann, "constructor_invocation")
        ut = _kid(ci if ci is not None else ann, "user_type")
        if ut is not None:
            out.append((_text(ut).rsplit(".", 1)[-1],
                        _kid(ci, "value_arguments") if ci is not None else None))
    return out


def _kotlin_args(va: Any | None) -> tuple[dict[str, Any], list[Any]]:
    named: dict[str, Any] = {}
    bare: list[Any] = []
    for arg in _kids(va, "value_argument") if va is not None else []:
        kids = arg.named_children
        if len(kids) >= 2 and kids[0].type == "simple_identifier":
            named[_text(kids[0])] = kids[-1]
        elif kids:
            bare.append(kids[-1])
    return named, bare


def _kotlin_spring(root: Any) -> list[RouteRow]:
    rows: list[RouteRow] = []

    def path(va: Any | None) -> str:
        named, bare = _kotlin_args(va)
        scope = named.get("value") or named.get("path") or (bare[0] if bare else None)
        return (_first_lit(scope) or "") if scope is not None else ""

    def methods(va: Any | None) -> list[str]:
        named, _bare = _kotlin_args(va)
        value = named.get("method")
        if value is None:
            return ["ANY"]
        elems = _kids(value, *[c.type for c in value.named_children]) \
            if value.type == "collection_literal" else [value]
        return [_text(e).rsplit(".", 1)[-1].upper() for e in elems] or ["ANY"]

    for cls in _all(root, "class_declaration", "object_declaration"):
        prefix = next((path(va) for n, va in _kotlin_annotations(cls)
                       if n == "RequestMapping"), "")
        body = _kid(cls, "class_body")
        for fn in _kids(body, "function_declaration") if body is not None else []:
            name = _kid(fn, "simple_identifier")
            handler = _text(name) if name is not None else "<unknown>"
            line = (name or fn).start_point[0] + 1
            for ann, va in _kotlin_annotations(fn):
                if ann in _SPRING_MAPPINGS:
                    rows.append(_row("spring", _SPRING_MAPPINGS[ann],
                                     _join_path(prefix, path(va)), handler, line))
                elif ann == "RequestMapping":
                    rows += [_row("spring", m, _join_path(prefix, path(va)), handler, line)
                             for m in methods(va)]
    return rows


def _kotlin_ktor(root: Any) -> list[RouteRow]:
    rows: list[RouteRow] = []

    def walk(node: Any, prefix: str) -> None:
        for ch in node.named_children:
            ident = ch.named_children[0] if ch.type == "call_expression" and \
                ch.named_children else None
            suffix = _kid(ch, "call_suffix") if ident is not None else None
            if ident is None or ident.type != "simple_identifier" or suffix is None \
                    or _kid(suffix, "type_arguments") is not None:
                walk(ch, prefix)
                continue
            name = _text(ident)
            va = _kid(suffix, "value_arguments")
            lam = _kid(suffix, "annotated_lambda")
            args = [a.named_children[-1] for a in _kids(va, "value_argument")
                    if a.named_children] if va is not None else []
            pattern = _lit(args[0]) if args else None
            if name == "route" and lam is not None and pattern is not None:
                walk(lam, _join_path(prefix, pattern))
                continue
            if name in _HTTP_METHODS and (lam is not None or len(args) >= 2) and (
                    pattern is not None or (not args and prefix)):
                handler = "<inline>" if lam is not None else _text(args[-1]).lstrip(":")
                rows.append(_row("ktor", name, _join_path(prefix, pattern or ""), handler,
                                 ident.start_point[0] + 1))
                continue
            walk(ch, prefix)

    walk(root, "")
    return rows


# ---------------------------------------------------------------------------
# C#: ASP.NET Core attribute routing and minimal APIs
# ---------------------------------------------------------------------------

_ASPNET_VERBS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT",
                 "HttpDelete": "DELETE", "HttpPatch": "PATCH", "HttpHead": "HEAD",
                 "HttpOptions": "OPTIONS"}
_MINIMAL_VERBS = {"MapGet": "GET", "MapPost": "POST", "MapPut": "PUT",
                  "MapDelete": "DELETE", "MapPatch": "PATCH", "Map": "ANY"}


def _routes_csharp(root: Any) -> list[RouteRow]:
    """``[Route("api/[controller]")]`` controllers with ``[HttpGet("{id}")]``
    actions, and minimal APIs (``app.MapGet("/x", h)``, prefixes through
    ``var g = app.MapGroup("/api")``). Gated on the names themselves: implicit
    usings leave many such files without a `using` of ASP.NET."""
    rows: list[RouteRow] = []

    def attributes(decl: Any) -> list[tuple[str, Any]]:
        out = []
        for al in _kids(decl, "attribute_list"):
            for a in _kids(al, "attribute"):
                name = a.child_by_field_name("name")
                if name is not None:
                    short = _text(name).rsplit(".", 1)[-1]
                    out.append((short[:-9] if short.endswith("Attribute") else short, a))
        return out

    def template(attr: Any) -> str:
        args = _kid(attr, "attribute_argument_list")
        for arg in _kids(args, "attribute_argument") if args is not None else []:
            if arg.child_by_field_name("name") is None:
                return _first_lit(arg) or ""
        return ""

    for cls in _all(root, "class_declaration"):
        name_node = cls.child_by_field_name("name")
        cname = _text(name_node) if name_node is not None else ""
        attrs = attributes(cls)
        if not (cname.endswith("Controller") or any(n in ("ApiController", "Route")
                                                    for n, _a in attrs)):
            continue
        ctrl = cname[:-len("Controller")] if cname.endswith("Controller") else cname
        prefix = next((template(a) for n, a in attrs if n == "Route"), "")
        body = cls.child_by_field_name("body")
        for m in _kids(body, "method_declaration") if body is not None else []:
            mname_node = m.child_by_field_name("name")
            mname = _text(mname_node) if mname_node is not None else "<unknown>"
            mattrs = attributes(m)
            routes = [template(a) for n, a in mattrs if n == "Route"] or [""]
            line = (mname_node or m).start_point[0] + 1
            for n, a in mattrs:
                if n not in _ASPNET_VERBS:
                    continue
                for route in routes:
                    t = template(a)
                    pat = t.lstrip("~") if t.startswith(("/", "~/")) else \
                        _join_path(_join_path(prefix, route), t)
                    pat = re.sub(r"\[controller\]", ctrl, pat, flags=re.I)
                    pat = re.sub(r"\[action\]", mname, pat, flags=re.I)
                    rows.append(_row("aspnet", _ASPNET_VERBS[n], _join_path("", pat), mname,
                                     line))

    groups: dict[str, str] = {}

    def map_call(inv: Any) -> tuple[str, Any, Any, Any] | None:
        """(method name, receiver, arguments, name node) of `recv.MapX(...)`."""
        fn = inv.child_by_field_name("function")
        args = inv.child_by_field_name("arguments")
        if fn is None or args is None or fn.type != "member_access_expression":
            return None
        name = fn.child_by_field_name("name")
        recv = fn.child_by_field_name("expression")
        if name is None or recv is None:
            return None
        return _text(name), recv, args, name

    def group_prefix(recv: Any) -> str:
        if recv.type == "identifier":
            return groups.get(_text(recv), "")
        if recv.type == "invocation_expression":
            got = map_call(recv)
            if got is not None:
                name, inner, args, _n = got
                base = group_prefix(inner)
                if name == "MapGroup":
                    first = next(iter(_kids(args, "argument")), None)
                    return _join_path(base, (_first_lit(first) or "") if first else "")
                return base
        return ""

    for node in _all(root, "variable_declarator", "invocation_expression"):
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name") or _kid(node, "identifier")
            value = next((c for c in node.named_children
                          if c.type == "invocation_expression"), None)
            if name is not None and value is not None:
                prefix = group_prefix(value)
                if prefix not in ("", "/") or "MapGroup" in _text(value):
                    groups[_text(name)] = prefix
            continue
        got = map_call(node)
        if got is None or got[0] not in _MINIMAL_VERBS:
            continue
        name, recv, args, name_node = got
        arglist = _kids(args, "argument")
        pattern = _lit(arglist[0].named_children[0]) if arglist and \
            arglist[0].named_children else None
        if pattern is None or len(arglist) < 2:
            continue
        last = arglist[-1].named_children[-1] if arglist[-1].named_children else None
        handler = _text(last) if last is not None and last.type in (
            "identifier", "member_access_expression") else "<inline>"
        rows.append(_row("aspnet", _MINIMAL_VERBS[name],
                         _join_path(group_prefix(recv), pattern), handler,
                         name_node.start_point[0] + 1))
    return rows


# ---------------------------------------------------------------------------
# Rust: axum and actix-web
# ---------------------------------------------------------------------------

_RUST_VERB_ATTR = re.compile(
    r"^#\s*\[\s*(?:actix_web::)?(get|post|put|delete|patch|head|options|trace|route)"
    r"\s*\(\s*\"([^\"]*)\"(.*)\)\s*\]\s*$", re.S)
_RUST_METHOD_ARG = re.compile(r"method\s*=\s*\"(\w+)\"")


def _routes_rust(root: Any) -> list[RouteRow]:
    """actix-web ``#[get("/x")] async fn f`` and builders
    (``web::scope("/api").service(web::resource("/x").route(web::get().to(f)))``),
    axum ``Router::new().route("/x", get(h).post(h2)).nest("/api", r)``. Gated on
    the crate being used in the file; the crate also picks the label."""
    used = " ".join(_text(u) for u in _all(root, "use_declaration"))
    label = "axum" if "axum" in used else "actix-web" if "actix_web" in used else None
    if label is None:
        return []
    rows: list[RouteRow] = []

    for fn in _all(root, "function_item"):
        name = fn.child_by_field_name("name")
        prev = fn.prev_named_sibling
        while prev is not None and (prev.type == "attribute_item" or "comment" in prev.type):
            m = _RUST_VERB_ATTR.match(_text(prev)) if prev.type == "attribute_item" else None
            if m is not None and name is not None:
                verb, path, rest = m.groups()
                methods = _RUST_METHOD_ARG.findall(rest) if verb == "route" else [verb]
                rows += [_row("actix-web", meth, _join_path("", path), _text(name),
                              name.start_point[0] + 1) for meth in methods or ["ANY"]]
            prev = prev.prev_named_sibling

    def last_segment(fn: Any) -> str:
        return _text(fn).rsplit("::", 1)[-1]

    def call_parts(call: Any) -> tuple[str, Any | None, list[Any], Any | None]:
        """(method name, receiver, arguments, name node) of `recv.name(args)` or
        `path::name(args)` (no receiver)."""
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        arglist = list(args.named_children) if args is not None else []
        if fn is None:
            return "", None, arglist, None
        if fn.type == "field_expression":
            field = fn.child_by_field_name("field")
            return (_text(field) if field is not None else "", fn.child_by_field_name("value"),
                    arglist, field)
        return last_segment(fn), None, arglist, fn

    def methods_of(expr: Any, pending: str = "") -> list[tuple[str, str]]:
        """`get(h).post(h2)` / `web::get().to(f)` -> [(method, handler), ...]."""
        if expr is None or expr.type != "call_expression":
            return []
        name, recv, args, _n = call_parts(expr)
        first = _text(args[0]) if args else ""
        if name in ("to", "to_async"):
            return methods_of(recv, first)
        if name in _HTTP_METHODS or name == "any":
            here = [("ANY" if name == "any" else name, first or pending or "<unknown>")]
            return here + methods_of(recv, pending)
        return []

    def chain_path(recv: Any, kinds: tuple[str, ...]) -> str:
        """The literal of the first `kind("/p")` call down a receiver chain."""
        node = recv
        while node is not None and node.type == "call_expression":
            name, inner, args, _n = call_parts(node)
            if name in kinds and args:
                return _lit(args[0]) or ""
            node = inner
        return ""

    def walk(node: Any, prefix: str) -> None:
        if node.type == "call_expression":
            name, recv, args, name_node = call_parts(node)
            line = (name_node or node).start_point[0] + 1
            scope = _join_path(prefix, chain_path(recv, ("scope",))) if recv is not None \
                else prefix
            if name == "route" and recv is not None and len(args) == 2 \
                    and _lit(args[0]) is not None:
                path = _join_path(scope, _lit(args[0]) or "")           # axum
                rows.extend(_row(label, m, path, h, line) for m, h in methods_of(args[1]))
                walk(recv, prefix)
                return
            if name == "route" and recv is not None and len(args) == 1:
                path = _join_path(scope, chain_path(recv, ("resource",)))  # actix
                rows.extend(_row(label, m, path, h, line) for m, h in methods_of(args[0]))
                walk(recv, prefix)
                return
            if name in ("to", "to_async") and recv is not None and args \
                    and chain_path(recv, ("resource",)) and not methods_of(recv):
                path = _join_path(scope, chain_path(recv, ("resource",)))
                rows.append(_row(label, "ANY", path, _text(args[0]), line))
                walk(recv, prefix)
                return
            if name == "nest" and recv is not None and len(args) == 2 \
                    and _lit(args[0]) is not None:
                walk(args[1], _join_path(prefix, _lit(args[0]) or ""))
                walk(recv, prefix)
                return
            if name == "service" and recv is not None and args:
                for a in args:
                    walk(a, scope)
                walk(recv, prefix)
                return
        for ch in node.named_children:
            walk(ch, prefix)

    try:
        walk(root, "")
    except RecursionError:
        pass
    return rows


# ---------------------------------------------------------------------------
# Ruby: Sinatra and the Rails routes DSL
# ---------------------------------------------------------------------------

_RUBY_VERBS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
# What `resources :articles` declares: (method, path under the resource, action).
_RAILS_REST = (
    ("GET", "", "index"), ("GET", "new", "new"), ("POST", "", "create"),
    ("GET", ":id", "show"), ("GET", ":id/edit", "edit"), ("PATCH", ":id", "update"),
    ("PUT", ":id", "update"), ("DELETE", ":id", "destroy"),
)
_RAILS_REST_ONE = (
    ("GET", "new", "new"), ("POST", "", "create"), ("GET", "", "show"),
    ("GET", "edit", "edit"), ("PATCH", "", "update"), ("PUT", "", "update"),
    ("DELETE", "", "destroy"),
)


def _ruby_value(node: Any) -> str | None:
    """A string literal or a symbol (`:feed` -> feed)."""
    if node is None:
        return None
    if node.type in ("simple_symbol", "hash_key_symbol"):
        return _text(node).lstrip(":")
    return _lit(node)


def _ruby_call(node: Any) -> tuple[str, list[Any], dict[str, Any], Any | None]:
    """(method, positional args, keyword args, block) of a receiver-less call."""
    method = node.child_by_field_name("method")
    args = node.child_by_field_name("arguments")
    positional: list[Any] = []
    keywords: dict[str, Any] = {}
    for a in args.named_children if args is not None else []:
        if a.type == "pair":
            key, value = a.child_by_field_name("key"), a.child_by_field_name("value")
            k = _ruby_value(key)
            if key is not None and key.type in ("string",) and value is not None:
                positional.append(key)             # `get 'x' => 'c#a'`
                keywords.setdefault("to", value)
            elif k is not None and value is not None:
                keywords[k] = value
        else:
            positional.append(a)
    return (_text(method) if method is not None else "", positional, keywords,
            node.child_by_field_name("block"))


def _routes_ruby(root: Any) -> list[RouteRow]:
    """Sinatra ``get '/x' do … end`` (in a file that requires sinatra, or under a
    ``Sinatra::Base`` subclass) and the Rails ``routes.draw`` DSL: verbs,
    ``root``, ``resources`` / ``resource`` (``only:`` / ``except:``, nested),
    ``namespace`` and ``scope``."""
    rows: list[RouteRow] = []
    calls = _all(root, "call")
    sinatra = any(
        c.child_by_field_name("receiver") is None
        and _text(m) == "require" and "sinatra" in _text(c)
        for c in calls if (m := c.child_by_field_name("method")) is not None
    ) or any("Sinatra" in _text(s) for s in _all(root, "superclass"))

    def body_calls(block: Any) -> list[Any]:
        out: list[Any] = []
        stack = list(block.named_children)
        while stack:
            n = stack.pop(0)
            if n.type == "call" and n.child_by_field_name("receiver") is None:
                out.append(n)
            elif n.type != "call":
                stack = list(n.named_children) + stack
        return out

    def sinatra_walk(block: Any, prefix: str) -> None:
        for call in body_calls(block):
            name, pos, _kw, blk = _ruby_call(call)
            first = _lit(pos[0]) if pos else None
            line = call.start_point[0] + 1
            if name in _RUBY_VERBS and blk is not None and first is not None:
                rows.append(_row("sinatra", name, _join_path(prefix, first), "<inline>", line))
            elif name == "namespace" and blk is not None and first is not None:
                sinatra_walk(blk, _join_path(prefix, first))
            elif blk is not None and name not in _RUBY_VERBS:
                sinatra_walk(blk, prefix)

    if sinatra:
        sinatra_walk(root, "")

    def handler(kw: dict[str, Any], default: str, ns: str) -> str:
        to = _ruby_value(kw.get("to"))
        return ns + (to if to else default)

    def resources(call: Any, name: str, pos: list[Any], kw: dict[str, Any], blk: Any,
                  prefix: str, ns: str) -> None:
        line = call.start_point[0] + 1
        plural = name == "resources"
        table = _RAILS_REST if plural else _RAILS_REST_ONE
        only = [v for e in (kw["only"].named_children if "only" in kw else [])
                if (v := _ruby_value(e))]
        if not only and "only" in kw and (one := _ruby_value(kw["only"])):
            only = [one]
        except_ = [v for e in (kw["except"].named_children if "except" in kw else [])
                   if (v := _ruby_value(e))]
        param = _ruby_value(kw.get("param")) or "id"
        for arg in pos:
            res = _ruby_value(arg)
            if not res:
                continue
            base = _join_path(prefix, res)
            for method, sub, action in table:
                if (only and action not in only) or action in except_:
                    continue
                rows.append(_row("rails", method, _join_path(base, sub.replace(":id", f":{param}")),
                                 f"{ns}{res}#{action}", line))
            if blk is not None:
                member = _join_path(base, f":{res.rstrip('s')}_{param}") if plural else base
                rails_walk(blk, member, ns, collection=base, controller=res)

    def rails_walk(block: Any, prefix: str, ns: str, collection: str = "",
                   controller: str = "") -> None:
        for call in body_calls(block):
            name, pos, kw, blk = _ruby_call(call)
            line = call.start_point[0] + 1
            if name in _RUBY_VERBS or name == "match":
                first = _ruby_value(pos[0]) if pos else None
                if first is None:
                    continue
                base = collection if collection and _ruby_value(kw.get("on")) == "collection" \
                    else prefix
                methods = [v.upper() for e in kw["via"].named_children
                           if (v := _ruby_value(e))] if "via" in kw else []
                default = f"{controller}#{first}" if controller else first
                for method in methods or ([name] if name != "match" else ["ANY"]):
                    rows.append(_row("rails", method, _join_path(base, first),
                                     handler(kw, default, ns), line))
            elif name == "root":
                to = _ruby_value(pos[0]) if pos else None
                rows.append(_row("rails", "GET", prefix or "/",
                                 ns + (to or _ruby_value(kw.get("to")) or "<unknown>"), line))
            elif name in ("resources", "resource"):
                resources(call, name, pos, kw, blk, prefix, ns)
            elif name in ("namespace", "scope") and blk is not None:
                first = _ruby_value(pos[0]) if pos else None
                path = _ruby_value(kw.get("path")) or first or ""
                module = _ruby_value(kw.get("module")) or (first if name == "namespace" else "")
                rails_walk(blk, _join_path(prefix, path), ns + (f"{module}/" if module else ""))
            elif blk is not None:  # constraints, concerns, ...: routes inside count
                rails_walk(blk, prefix, ns, collection, controller)

    for call in calls:
        method = call.child_by_field_name("method")
        recv = call.child_by_field_name("receiver")
        blk = call.child_by_field_name("block")
        if method is not None and _text(method) == "draw" and recv is not None \
                and "routes" in _text(recv) and blk is not None:
            rails_walk(blk, "", "")
    return rows


# ---------------------------------------------------------------------------
# PHP: Laravel's Route facade and Symfony #[Route] attributes
# ---------------------------------------------------------------------------

_LARAVEL_VERBS = frozenset({"get", "post", "put", "patch", "delete", "options", "any"})
_LARAVEL_REST = (
    ("GET", "", "index"), ("GET", "create", "create"), ("POST", "", "store"),
    ("GET", "{id}", "show"), ("GET", "{id}/edit", "edit"), ("PUT", "{id}", "update"),
    ("PATCH", "{id}", "update"), ("DELETE", "{id}", "destroy"),
)


def _php_args(args: Any | None) -> tuple[list[Any], dict[str, Any]]:
    positional: list[Any] = []
    named: dict[str, Any] = {}
    for a in _kids(args, "argument") if args is not None else []:
        key = a.child_by_field_name("name")
        # Node wrappers are new objects per access: compare by value, not identity
        value = next((c for c in a.named_children if key is None or c != key), None)
        if value is None:
            continue
        if key is not None:
            named[_text(key)] = value
        else:
            positional.append(value)
    return positional, named


def _php_strings(node: Any | None) -> list[str]:
    """`['GET', 'POST']` / `'GET'` -> its strings."""
    if node is None:
        return []
    if node.type == "array_creation_expression":
        return [v for e in node.named_children if (v := _first_lit(e)) is not None]
    one = _lit(node)
    return [one] if one is not None else []


def _php_array_get(node: Any | None, key: str) -> Any | None:
    """The value under 'key' in a PHP array literal."""
    if node is None or node.type != "array_creation_expression":
        return None
    for e in node.named_children:
        kids = e.named_children
        if len(kids) == 2 and _lit(kids[0]) == key:
            return kids[1]
    return None


def _php_handler(node: Any | None) -> str:
    if node is None:
        return "<unknown>"
    if node.type in ("anonymous_function", "arrow_function",
                     "anonymous_function_creation_expression"):
        return "<inline>"
    if node.type == "array_creation_expression":
        parts = [e.named_children[0] for e in node.named_children if e.named_children]
        if len(parts) == 2:
            cls = _text(parts[0]).removesuffix("::class")
            meth = _lit(parts[1]) or _text(parts[1])
            return f"{cls}::{meth}"
    value = _lit(node)
    return value if value is not None else _text(node).removesuffix("::class")


def _routes_php(root: Any) -> list[RouteRow]:
    """Laravel ``Route::get('/x', [C::class, 'm'])``, ``Route::prefix('api')
    ->group(...)``, ``Route::group(['prefix' => ...], ...)``, ``resource`` /
    ``apiResource``; Symfony ``#[Route('/x', methods: ['GET'])]`` on a controller
    class and its methods (gated on a Symfony ``Route`` import)."""
    rows: list[RouteRow] = []

    def chain(node: Any) -> list[tuple[str, Any, Any]] | None:
        """[(name, arguments, name node), ...] root first, for a call chain that
        starts at `Route::…`; None otherwise."""
        links: list[tuple[str, Any, Any]] = []
        while node is not None and node.type == "member_call_expression":
            name = node.child_by_field_name("name")
            links.append((_text(name) if name is not None else "",
                          node.child_by_field_name("arguments"), name))
            node = node.child_by_field_name("object")
        if node is None or node.type != "scoped_call_expression":
            return None
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        if scope is None or name is None:
            return None
        if _text(scope).lstrip("\\").rsplit("\\", 1)[-1] != "Route":
            return None
        links.append((_text(name), node.child_by_field_name("arguments"), name))
        return list(reversed(links))

    def laravel(node: Any, prefix: str) -> None:
        if node.type in ("member_call_expression", "scoped_call_expression"):
            links = chain(node)
            if links is not None:
                laravel_chain(links, prefix)
                return
        for ch in node.named_children:
            laravel(ch, prefix)

    def laravel_chain(links: list[tuple[str, Any, Any]], prefix: str) -> None:
        pre = prefix
        only = [v for n, a, _ in links if n == "only" for v in _php_strings(
            next(iter(_php_args(a)[0]), None))]
        except_ = [v for n, a, _ in links if n == "except" for v in _php_strings(
            next(iter(_php_args(a)[0]), None))]
        for name, args, name_node in links:
            pos, _named = _php_args(args)
            line = (name_node or args).start_point[0] + 1
            first = _lit(pos[0]) if pos else None
            if name == "prefix" and first is not None:
                pre = _join_path(pre, first)
            elif name == "group":
                inner = pre
                for a in pos:
                    p = _lit(_php_array_get(a, "prefix")) if a.type == \
                        "array_creation_expression" else None
                    if p is not None:
                        inner = _join_path(inner, p)
                for a in pos:
                    if a.type != "array_creation_expression":
                        laravel(a, inner)
            elif name in _LARAVEL_VERBS and first is not None and len(pos) >= 2:
                rows.append(_row("laravel", "ANY" if name == "any" else name,
                                 _join_path(pre, first), _php_handler(pos[1]), line))
            elif name == "match" and len(pos) >= 3 and _lit(pos[1]) is not None:
                for verb in _php_strings(pos[0]):
                    rows.append(_row("laravel", verb, _join_path(pre, _lit(pos[1]) or ""),
                                     _php_handler(pos[2]), line))
            elif name in ("resource", "apiResource") and first is not None and len(pos) >= 2:
                opts = pos[2] if len(pos) > 2 else None
                keep = only + _php_strings(_php_array_get(opts, "only"))
                drop = except_ + _php_strings(_php_array_get(opts, "except"))
                base = _join_path(pre, first)
                ctrl = _php_handler(pos[1])
                for method, sub, action in _LARAVEL_REST:
                    if name == "apiResource" and action in ("create", "edit"):
                        continue
                    if (keep and action not in keep) or action in drop:
                        continue
                    rows.append(_row("laravel", method, _join_path(base, sub),
                                     f"{ctrl}@{action}", line))

    laravel(root, "")

    if not any("Symfony" in (t := _text(u)) and "Route" in t
               for u in _all(root, "namespace_use_declaration")):
        return rows

    def route_attrs(decl: Any) -> list[tuple[str, list[str]]]:
        found = []
        for al in _kids(decl, "attribute_list"):
            for group in _kids(al, "attribute_group"):
                for attr in _kids(group, "attribute"):
                    name = _kid(attr, "name", "qualified_name")
                    if name is None or _text(name).rsplit("\\", 1)[-1] != "Route":
                        continue
                    pos, named = _php_args(attr.child_by_field_name("parameters"))
                    path = _lit(named.get("path")) if "path" in named else (
                        _lit(pos[0]) if pos else None)
                    found.append((path or "", _php_strings(named.get("methods")) or ["ANY"]))
        return found

    for cls in _all(root, "class_declaration"):
        prefix = next((p for p, _m in route_attrs(cls)), "")
        body = cls.child_by_field_name("body")
        for m in _kids(body, "method_declaration") if body is not None else []:
            name = m.child_by_field_name("name")
            for path, methods in route_attrs(m):
                rows += [_row("symfony", meth, _join_path(prefix, path),
                              _text(name) if name is not None else "<unknown>",
                              (name or m).start_point[0] + 1) for meth in methods]
    return rows


_TS_ROUTE_EXTRACTORS = {
    "javascript": _routes_js,
    "typescript": _routes_js,
    "tsx": _routes_js,
    "jsx": _routes_js,
    "go": _routes_go,
    "java": _routes_java,
    "kotlin": _routes_kotlin,
    "csharp": _routes_csharp,
    "rust": _routes_rust,
    "ruby": _routes_ruby,
    "php": _routes_php,
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
    return _cached_file_analysis(
        _ROUTES_CACHE, file_path, routes_for_source, list, _ROUTES_CACHE_MAX
    )
