//! JS / TS routes, after `routes.py`'s `_routes_js`: Express-style verb calls
//! (`app.get('/x', h)`, `router.route('/x').get(h)`) in a file importing a server
//! framework, or on a receiver named like a router (`needs_server_import`);
//! NestJS `@Controller('p')` + `@Get(':id')`. Beyond it: routers
//! (`express.Router()`, `new Router({ prefix })`), mounts (`app.use('/api',
//! router)`, `app.use('/x', require('./x'))`, Fastify `register(plugin, {
//! prefix })`) and NestJS `app.setGlobalPrefix('api')`.

use std::collections::HashSet;

use tree_sitter::Node;

use super::{
    join_path, path_expr, single_call, CallFact, ClientBase, GlobalPrefix, Handler, HttpFacts,
    MountFact, Mounted, Part, PathExpr, RouteFact, RouterFact,
};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, path_of, string_value};

const SERVER_FRAMEWORKS: &[&str] =
    &["express", "fastify", "koa-router", "@koa/router", "restify", "polka"];
const ROUTER_NAMES: &[&str] = &["app", "router", "server", "api", "routes", "route", "r"];
const HANDLER_KINDS: &[&str] = &[
    "identifier",
    "member_expression",
    "arrow_function",
    "function_expression",
    "function",
    "call_expression",
];
const VERBS: &[&str] =
    &["get", "post", "put", "delete", "patch", "options", "head", "trace", "all"];
/// HTTP client modules: a call through one of them, or through an object one
/// of them made, is a request.
const CLIENT_MODULES: &[&str] = &[
    "axios",
    "ky",
    "ofetch",
    "superagent",
    "node-fetch",
    "cross-fetch",
    "isomorphic-fetch",
    "got",
    "@angular/common/http",
    "supertest",
];
/// Functions that make a request with the URL first: `fetch(url, { method })`.
const FETCH_FUNCTIONS: &[&str] = &["fetch", "$fetch", "ofetch"];
/// Client methods named for the request's verb.
const CLIENT_VERBS: &[&str] = &["get", "post", "put", "delete", "patch", "head", "options"];
const NEST_VERBS: &[(&str, &str)] = &[
    ("Get", "GET"),
    ("Post", "POST"),
    ("Put", "PUT"),
    ("Delete", "DELETE"),
    ("Patch", "PATCH"),
    ("Options", "OPTIONS"),
    ("Head", "HEAD"),
    ("All", "ANY"),
];

fn string_arg(args: Node, src: &[u8], index: usize) -> Option<String> {
    kids(args)
        .into_iter()
        .filter(|a| {
            a.kind() == "string" || (a.kind() == "template_string" && a.named_child_count() == 0)
        })
        .nth(index)
        .map(|a| string_value(a, src))
}

/// The first string argument as a path: a template string keeps its holes
/// (`` `/${hop}` ``).
fn path_arg(args: Node, src: &[u8]) -> Option<PathExpr> {
    let a = kids(args).into_iter().find(|a| matches!(a.kind(), "string" | "template_string"))?;
    path_expr(a, src)
}

fn decorator_call(dec: Node) -> Option<Node> {
    kids(dec).into_iter().find(|c| c.kind() == "call_expression")
}

fn call_name(call: Node, src: &[u8]) -> Option<String> {
    call.child_by_field_name("function").filter(|f| f.kind() == "identifier").map(|f| text(f, src))
}

/// A request's method, URL and client object.
type Request = (Option<String>, PathExpr, Option<Vec<String>>);

struct Ctx<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    framework: Option<&'static str>,
    label: &'static str,
    /// Names bound to a client module or to an object one made.
    clients: HashSet<String>,
    /// Inside a class body: requests only (`routes.py` reads no route there).
    in_class: bool,
    out: HttpFacts,
}

impl Ctx<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    fn handle_class(&mut self, cls: Node, pending: &[Node]) {
        let src = self.src;
        let mut prefix = String::new();
        let mut decs: Vec<Node> = pending.to_vec();
        decs.extend(kids(cls).into_iter().filter(|c| c.kind() == "decorator"));
        for dec in decs {
            let Some(call) = decorator_call(dec) else { continue };
            if call_name(call, src).as_deref() == Some("Controller") {
                prefix = call
                    .child_by_field_name("arguments")
                    .and_then(|a| string_arg(a, src, 0))
                    .unwrap_or_default();
            }
        }
        let Some(body) = cls.child_by_field_name("body") else { return };
        let mut method_pending: Vec<Node> = Vec::new();
        for child in kids(body) {
            if child.kind() == "decorator" {
                method_pending.push(child);
                continue;
            }
            if child.kind() != "method_definition" {
                method_pending.clear();
                continue;
            }
            let mut decs = std::mem::take(&mut method_pending);
            decs.extend(kids(child).into_iter().filter(|c| c.kind() == "decorator"));
            for dec in decs {
                let Some(call) = decorator_call(dec) else { continue };
                let Some(name) = call_name(call, src) else { continue };
                let Some((_, method)) = NEST_VERBS.iter().find(|(n, _)| *n == name) else {
                    continue;
                };
                let sub = call
                    .child_by_field_name("arguments")
                    .and_then(|a| string_arg(a, src, 0))
                    .unwrap_or_default();
                let handler = self.home(child.start_byte()).map_or(Handler::Inline, Handler::Def);
                self.out.routes.push(RouteFact {
                    line: line(child),
                    framework: "nestjs".into(),
                    method: method.to_string(),
                    path: PathExpr::lit(join_path(&[&prefix, &sub])),
                    router: None,
                    handler,
                    from_def: self.home(child.start_byte()),
                    needs_server_import: false,
                });
            }
        }
    }

    /// `x.route('/p').get(h).post(h2)`: `(x, '/p')` from any verb call of it.
    fn chain_root<'t>(&self, call: Node<'t>) -> Option<(Node<'t>, String)> {
        let src = self.src;
        let mut f = call.child_by_field_name("function")?;
        while f.kind() == "member_expression" {
            let obj = f.child_by_field_name("object").filter(|o| o.kind() == "call_expression")?;
            let inner =
                obj.child_by_field_name("function").filter(|i| i.kind() == "member_expression")?;
            let args = obj.child_by_field_name("arguments")?;
            let prop = inner.child_by_field_name("property")?;
            let recv = inner.child_by_field_name("object")?;
            if text(prop, src) == "route" {
                let pattern = string_arg(args, src, 0)?;
                return (recv.kind() == "identifier").then_some((recv, pattern));
            }
            if !VERBS.contains(&text(prop, src).as_str()) {
                return None;
            }
            f = inner;
        }
        None
    }

    /// A route registration; `true` when the call is one.
    fn registration(&mut self, call: Node, params: &HashSet<String>) -> bool {
        let src = self.src;
        let (Some(f), Some(args)) =
            (call.child_by_field_name("function"), call.child_by_field_name("arguments"))
        else {
            return false;
        };
        if f.kind() != "member_expression" {
            return false;
        }
        let (Some(obj), Some(prop)) =
            (f.child_by_field_name("object"), f.child_by_field_name("property"))
        else {
            return false;
        };
        let verb = text(prop, src);
        self.mount_or_prefix(call, obj, &verb, args);
        if !VERBS.contains(&verb.as_str()) {
            return false;
        }
        let chained = if obj.kind() == "call_expression" { self.chain_root(call) } else { None };
        let was_chained = chained.is_some();
        let (recv, pattern) = match chained {
            Some((r, p)) => (r, PathExpr::lit(p)),
            None if obj.kind() == "identifier" => match path_arg(args, src) {
                Some(p) => (obj, p),
                None => return false,
            },
            None => return false,
        };
        if !matches!(pattern.0.first(), Some(Part::Lit(l)) if l.starts_with('/')) {
            return false;
        }
        let named = kids(args);
        let last = named.last().copied();
        let name = text(recv, src);
        // a request through a client object is not a route
        if self.clients.contains(&name) && !was_chained {
            return false;
        }
        if self.framework.is_none() {
            let handlers = if was_chained { &named[..] } else { named.get(1..).unwrap_or(&[]) };
            let ok_last = last.is_some_and(|l| HANDLER_KINDS.contains(&l.kind()));
            if handlers.is_empty() || !ok_last {
                return false;
            }
            if !ROUTER_NAMES.contains(&name.as_str()) && !params.contains(&name) {
                return false;
            }
        }
        let handler = match last {
            Some(l) if matches!(l.kind(), "identifier" | "member_expression") => {
                path_of(l, src).map(Handler::Path).unwrap_or(Handler::Inline)
            }
            // `(req, res) => ctrl.show(req, res)`: the call it makes
            Some(l)
                if matches!(l.kind(), "arrow_function" | "function_expression" | "function") =>
            {
                single_call(l, src).map_or(Handler::Inline, Handler::Path)
            }
            _ => Handler::Inline,
        };
        self.out.routes.push(RouteFact {
            line: line(prop),
            framework: self.label.to_string(),
            method: if verb == "all" { "ANY".into() } else { verb.to_uppercase() },
            path: pattern,
            router: Some(vec![text(recv, src)]),
            handler,
            from_def: self.home(call.start_byte()),
            needs_server_import: self.framework.is_none(),
        });
        true
    }

    /// A request: `fetch(url, { method })`, `axios.get(url)`, `axios({ url,
    /// method })`, `api.post(url)` through a client a client module made,
    /// `client.get({ url: '/x' })` (a generated SDK), `request(app).get('/x')`
    /// (supertest), `$.ajax({ url, type })`, `new EventSource(url)`.
    fn client_request(&self, call: Node) -> Option<Request> {
        let src = self.src;
        let (Some(f), Some(args)) = (
            call.child_by_field_name("function")
                .or_else(|| call.child_by_field_name("constructor")),
            call.child_by_field_name("arguments"),
        ) else {
            return None;
        };
        let named = kids(args);
        let first = named.first().copied();
        let options = |i: usize| named.get(i).copied().filter(|o| o.kind() == "object");
        let method_in = |o: Option<Node>, keys: &[&str]| {
            o.and_then(|o| keys.iter().find_map(|k| object_value(o, src, k)))
                .filter(|v| matches!(v.kind(), "string" | "template_string"))
                .map(|v| string_value(v, src).to_uppercase())
        };
        let url_of = |n: Node| match n.kind() {
            "object" => object_value(n, src, "url").and_then(|v| path_expr(v, src)),
            _ => path_expr(n, src),
        };
        if call.kind() == "new_expression" {
            if text(f, src) == "EventSource" {
                return Some((Some("GET".into()), first.and_then(url_of)?, None));
            }
            return None;
        }
        match f.kind() {
            // fetch(url, { method }), axios(url | { url, method })
            "identifier" => {
                let name = text(f, src);
                let a = first?;
                if FETCH_FUNCTIONS.contains(&name.as_str()) || self.clients.contains(&name) {
                    let opts = if a.kind() == "object" { Some(a) } else { options(1) };
                    let m = method_in(opts, &["method"]).or(Some("GET".into()));
                    return Some((m, url_of(a)?, None));
                }
            }
            "member_expression" => {
                let (Some(obj), Some(prop)) =
                    (f.child_by_field_name("object"), f.child_by_field_name("property"))
                else {
                    return None;
                };
                let verb = text(prop, src);
                let recv = path_of(obj, src).unwrap_or_default();
                let head = recv.first().map(String::as_str).unwrap_or("");
                let last = recv.last().map(String::as_str).unwrap_or("");
                let a = first?;
                // $.ajax({ url, type }), axios.request({ url, method })
                if matches!(verb.as_str(), "ajax" | "request") && a.kind() == "object" {
                    if (head == "$" || head == "jQuery") || self.clients.contains(head) {
                        let m = method_in(Some(a), &["method", "type"]).or(Some("GET".into()));
                        return Some((m, url_of(a)?, self.client_path(&recv)));
                    }
                    return None;
                }
                let verb_method = match verb.as_str() {
                    "getJSON" if head == "$" || head == "jQuery" => "GET".to_string(),
                    v if CLIENT_VERBS.contains(&v) => v.to_uppercase(),
                    _ => return None,
                };
                // a generated SDK: client.get({ url: '/api/v1/items/' })
                let sdk = a.kind() == "object"
                    && object_value(a, src, "url").is_some_and(|v| {
                        string_value(v, src).starts_with('/')
                            || matches!(path_expr(v, src), Some(PathExpr(ref ps)) if matches!(ps.first(), Some(Part::Lit(l)) if l.starts_with('/')))
                    });
                // supertest: request(app).get('/x')
                let supertest = obj.kind() == "call_expression"
                    && self.imports("supertest")
                    && obj
                        .child_by_field_name("function")
                        .is_some_and(|g| g.kind() == "identifier");
                let through_client = self.clients.contains(head)
                    || ((head == "$" || head == "jQuery") && matches!(verb.as_str(), "get" | "post"))
                    // Angular: this.http.get(url) in a file importing HttpClient
                    || (self.imports("@angular/common/http")
                        && matches!(last, "http" | "httpClient" | "_http"));
                if sdk || supertest || through_client {
                    let client = if supertest { None } else { self.client_path(&recv) };
                    return Some((Some(verb_method), url_of(a)?, client));
                }
            }
            _ => {}
        }
        None
    }

    fn client_call(&mut self, call: Node) {
        if let Some((method, url, client)) = self.client_request(call) {
            if !url.is_empty() {
                self.out.calls.push(CallFact {
                    line: line(call),
                    method,
                    url,
                    client,
                    from_def: self.home(call.start_byte()),
                });
            }
        }
    }

    fn imports(&self, module: &str) -> bool {
        self.facts
            .imports
            .iter()
            .any(|i| i.module == module || i.module.starts_with(&format!("{module}/")))
    }

    /// The client object a request is made through, when it is a name the file
    /// binds (whose base URL resolution looks up).
    fn client_path(&self, recv: &[String]) -> Option<Vec<String>> {
        (!recv.is_empty() && !recv.iter().any(|s| s == "()")).then(|| recv.to_vec())
    }

    /// `const api = axios.create({ baseURL: '/api' })`, `ky.create({ prefixUrl })`:
    /// a client, and its base URL.
    fn client_decl(&mut self, decl: Node) {
        let src = self.src;
        let (Some(name), Some(value)) =
            (decl.child_by_field_name("name"), decl.child_by_field_name("value"))
        else {
            return;
        };
        if name.kind() != "identifier" || value.kind() != "call_expression" {
            return;
        }
        let Some(path) = value.child_by_field_name("function").and_then(|c| path_of(c, src)) else {
            return;
        };
        let (Some(head), Some(last)) = (path.first(), path.last()) else { return };
        if path.len() != 2
            || !matches!(last.as_str(), "create" | "extend")
            || !self.clients.contains(head)
        {
            return;
        }
        let name = text(name, src);
        self.clients.insert(name.clone());
        let base = value
            .child_by_field_name("arguments")
            .and_then(|a| kids(a).into_iter().find(|o| o.kind() == "object"))
            .and_then(|o| {
                ["baseURL", "baseUrl", "prefixUrl"].iter().find_map(|k| object_value(o, src, k))
            })
            .and_then(|v| path_expr(v, src));
        if let Some(base) = base {
            self.out.bases.push(ClientBase { name, base });
        }
    }

    /// `app.use('/api', router)`, `app.use(router)`, `app.use('/x', require('./x'))`,
    /// `fastify.register(plugin, { prefix: '/v1' })`, `app.setGlobalPrefix('api')`.
    fn mount_or_prefix(&mut self, call: Node, obj: Node, verb: &str, args: Node) {
        let src = self.src;
        let Some(parent) = path_of(obj, src).filter(|p| !p.iter().any(|s| s == "()")) else {
            return;
        };
        let named = kids(args);
        match verb {
            "setGlobalPrefix" => {
                if let Some(p) = named.first().and_then(|a| path_expr(*a, src)) {
                    self.out.prefixes.push(GlobalPrefix {
                        framework: "nestjs".into(),
                        prefix: p,
                        file: None,
                    });
                }
            }
            "use" if self.framework.is_some() || ROUTER_NAMES.contains(&parent[0].as_str()) => {
                let (prefix, rest) = match named.first() {
                    Some(a) if a.kind() == "string" => {
                        (PathExpr::lit(string_value(*a, src)), &named[1..])
                    }
                    _ => (PathExpr::default(), &named[..]),
                };
                for c in rest {
                    if let Some(m) = mounted(*c, src) {
                        self.out.mounts.push(MountFact {
                            line: line(call),
                            parent: parent.clone(),
                            child: m,
                            prefix: prefix.clone(),
                            from_def: self.home(call.start_byte()),
                        });
                    }
                }
            }
            "register" if self.framework == Some("fastify") => {
                let Some(m) = named.first().and_then(|c| mounted(*c, src)) else { return };
                let prefix = named
                    .get(1)
                    .filter(|o| o.kind() == "object")
                    .and_then(|o| object_value(*o, src, "prefix"))
                    .and_then(|v| path_expr(v, src))
                    .unwrap_or_default();
                self.out.mounts.push(MountFact {
                    line: line(call),
                    parent,
                    child: m,
                    prefix,
                    from_def: self.home(call.start_byte()),
                });
            }
            _ => {}
        }
    }

    /// `const router = express.Router()`, `Router()`, `new Router({ prefix })`.
    fn router_decl(&mut self, decl: Node) {
        let src = self.src;
        let (Some(name), Some(value)) =
            (decl.child_by_field_name("name"), decl.child_by_field_name("value"))
        else {
            return;
        };
        if name.kind() != "identifier" {
            return;
        }
        let (callee, args) = match value.kind() {
            "call_expression" => {
                (value.child_by_field_name("function"), value.child_by_field_name("arguments"))
            }
            "new_expression" => {
                (value.child_by_field_name("constructor"), value.child_by_field_name("arguments"))
            }
            _ => return,
        };
        let Some(path) = callee.and_then(|c| path_of(c, src)) else { return };
        if path.last().map(String::as_str) != Some("Router") {
            return;
        }
        let prefix = args
            .and_then(|a| kids(a).into_iter().find(|o| o.kind() == "object"))
            .and_then(|o| object_value(o, src, "prefix"))
            .and_then(|v| path_expr(v, src))
            .unwrap_or_default();
        self.out.routers.push(RouterFact { name: text(name, src), prefix, scope: None });
    }

    fn class_body(&mut self, cls: Node, params: &HashSet<String>) {
        let was = std::mem::replace(&mut self.in_class, true);
        self.walk(cls, params);
        self.in_class = was;
    }

    fn walk(&mut self, node: Node, params: &HashSet<String>) {
        let mut pending: Vec<Node> = Vec::new();
        for child in kids(node) {
            let k = child.kind();
            if k == "decorator" {
                pending.push(child);
                continue;
            }
            if k == "class_declaration" || k == "class" {
                self.handle_class(child, &pending);
                pending.clear();
                self.class_body(child, params);
                continue;
            }
            if k == "export_statement" {
                // `@Controller() export class X` keeps its decorators on the export
                let mut decs = std::mem::take(&mut pending);
                decs.extend(kids(child).into_iter().filter(|c| c.kind() == "decorator"));
                if let Some(cls) = child
                    .child_by_field_name("declaration")
                    .filter(|d| d.kind() == "class_declaration")
                {
                    self.handle_class(cls, &decs);
                    self.class_body(cls, params);
                    continue;
                }
            }
            if k == "call_expression" && (self.in_class || !self.registration(child, params)) {
                self.client_call(child);
            }
            if k == "new_expression" {
                self.client_call(child);
            }
            if k == "variable_declarator" && !self.in_class {
                self.router_decl(child);
                self.client_decl(child);
            }
            let mut inner = params.clone();
            if matches!(
                k,
                "function_declaration"
                    | "function_expression"
                    | "function"
                    | "arrow_function"
                    | "method_definition"
                    | "generator_function"
            ) {
                inner.extend(parameters(child, self.src));
            }
            self.walk(child, &inner);
            pending.clear();
        }
    }
}

fn object_value<'t>(obj: Node<'t>, src: &[u8], key: &str) -> Option<Node<'t>> {
    kids(obj).into_iter().find_map(|p| {
        (p.kind() == "pair"
            && p.child_by_field_name("key")
                .is_some_and(|k| string_value(k, src) == key || text(k, src) == key))
        .then(|| p.child_by_field_name("value"))
        .flatten()
    })
}

/// What `app.use(…)` mounts: a router by name, or a module required in place.
fn mounted(n: Node, src: &[u8]) -> Option<Mounted> {
    if n.kind() == "call_expression"
        && n.child_by_field_name("function").is_some_and(|f| text(f, src) == "require")
    {
        let spec = n.child_by_field_name("arguments").and_then(|a| string_arg(a, src, 0))?;
        return Some(Mounted::Module(spec));
    }
    if matches!(n.kind(), "identifier" | "member_expression") {
        return path_of(n, src).filter(|p| !p.iter().any(|s| s == "()")).map(Mounted::Path);
    }
    None
}

/// Names a function binds as plain parameters.
fn parameters(f: Node, src: &[u8]) -> HashSet<String> {
    let mut out = HashSet::new();
    if let Some(one) = f.child_by_field_name("parameter").filter(|p| p.kind() == "identifier") {
        out.insert(text(one, src));
    }
    if let Some(ps) = f.child_by_field_name("parameters") {
        for p in kids(ps) {
            match p.kind() {
                "identifier" => {
                    out.insert(text(p, src));
                }
                "required_parameter" | "optional_parameter" => {
                    if let Some(pat) =
                        p.child_by_field_name("pattern").filter(|x| x.kind() == "identifier")
                    {
                        out.insert(text(pat, src));
                    }
                }
                _ => {}
            }
        }
    }
    out
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let framework = SERVER_FRAMEWORKS.iter().copied().find(|fw| {
        facts.imports.iter().any(|i| i.module == *fw || i.module.starts_with(&format!("{fw}/")))
    });
    let label = match framework {
        Some("koa-router") | Some("@koa/router") => "koa",
        Some(fw) => fw,
        None => "express",
    };
    // names the file binds to a client module: `import axios from 'axios'`,
    // `const ky = require('ky')`
    let clients: HashSet<String> = facts
        .imports
        .iter()
        .filter(|i| CLIENT_MODULES.iter().any(|m| i.module == *m))
        .flat_map(|i| {
            let mut v: Vec<String> = i.alias.iter().cloned().collect();
            v.extend(i.names.iter().map(|(n, a)| a.clone().unwrap_or_else(|| n.clone())));
            v
        })
        .collect();
    let mut ctx =
        Ctx { src, facts, framework, label, clients, in_class: false, out: HttpFacts::default() };
    ctx.out.server_import = framework.is_some();
    ctx.walk(root, &HashSet::new());
    ctx.out
}
