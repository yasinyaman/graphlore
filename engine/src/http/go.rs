//! Go routes, after `routes.py`'s `_routes_go`: verb methods (gin / echo
//! `GET`, chi `Get`), `Handle` / `HandleFunc` (Go 1.22 `"GET /x"` patterns, gin's
//! `Handle("GET", "/x", h)`), chi `Route` / `Group` / `With` lambdas. Beyond it:
//! groups bound to a name (`v1 := router.Group("/v1")`, gorilla
//! `PathPrefix("/api").Subrouter()`), and a router passed to a function
//! (`addUserRoutes(v1)` with `func addUserRoutes(rg *gin.RouterGroup)`), which
//! resolution joins across files; a path without a leading `/` on a known
//! router (`apiV1.GET("users", …)`) is a route too.
//!
//! The requests: `http.Get` / `Post` / `Head` / `PostForm`, `http.NewRequest`
//! / `NewRequestWithContext` / `httptest.NewRequest` (the method a literal or
//! `http.MethodPost`), a client's `Get(url)`, resty's `R().Get(url)`.

use std::collections::{HashMap, HashSet};

use tree_sitter::Node;

use super::{
    join_path, path_expr, single_call, CallFact, Handler, HttpFacts, MountFact, Mounted, Part,
    PathExpr, RouteFact, RouterFact, PARAM_ROUTER,
};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, path_of, string_value};

const UPPER: &[&str] = &["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"];
const TITLE: &[&str] = &["Get", "Post", "Put", "Delete", "Patch", "Options", "Head"];
const FRAMEWORKS: &[(&str, &str)] = &[
    ("gin-gonic/gin", "gin"),
    ("labstack/echo", "echo"),
    ("go-chi/chi", "chi"),
    ("gorilla/mux", "gorilla"),
];
/// Calls that make a router.
const MAKERS: &[&str] = &["Default", "New", "NewRouter", "NewServeMux", "NewMux"];
/// Parameter types that are routers.
const ROUTER_TYPES: &[&str] =
    &["RouterGroup", "Engine", "IRouter", "IRoutes", "Echo", "Group", "Router", "Mux", "ServeMux"];

fn is_string(n: Node) -> bool {
    matches!(n.kind(), "interpreted_string_literal" | "raw_string_literal")
}

#[derive(Clone, Default)]
struct Ctx {
    /// The router parameters of the function around, by name.
    params: HashMap<String, usize>,
    /// Names bound to routers in the function around.
    routers: HashSet<String>,
    /// Inside a chi `Route("/p", func(r chi.Router) { … })`: the router it
    /// is called on, the lambda's parameter and the prefix so far.
    chi: Option<(Vec<String>, String, String)>,
}

struct Reader<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    framework: &'static str,
    /// Package-level names bound to routers.
    top: HashSet<String>,
    out: HttpFacts,
}

/// `(method name, receiver, arguments)` of `recv.Name(args)`.
fn selector_call<'t>(call: Node<'t>, src: &[u8]) -> Option<(String, Node<'t>, Vec<Node<'t>>)> {
    if call.kind() != "call_expression" {
        return None;
    }
    let f = call.child_by_field_name("function")?;
    if f.kind() != "selector_expression" {
        return None;
    }
    let name = text(f.child_by_field_name("field")?, src);
    let args = call.child_by_field_name("arguments").map(kids).unwrap_or_default();
    Some((name, f.child_by_field_name("operand")?, args))
}

impl Reader<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    /// What a receiver names as a router: the router a chi lambda's parameter
    /// stands for (with the lambda's prefix), a router parameter, or the path.
    fn router(&self, recv: Node, ctx: &Ctx) -> Option<(Vec<String>, String, bool)> {
        // past middleware and chained groups: r.With(mw).Get(…),
        // router.Group("/api").GET(…), r.PathPrefix("/x").Subrouter()
        let mut recv = recv;
        let mut chained = String::new();
        while let Some((m, inner, args)) = selector_call(recv, self.src) {
            let lit = args.first().filter(|a| is_string(**a)).map(|a| string_value(*a, self.src));
            match m.as_str() {
                "With" | "Use" | "Subrouter" | "Methods" => {}
                "Group" | "PathPrefix" if !args.iter().any(|a| a.kind() == "func_literal") => {
                    chained = join_path(&[lit.as_deref().unwrap_or(""), &chained]);
                }
                _ => break,
            }
            recv = inner;
        }
        let (path, prefix, known) = self.named_router(recv, ctx)?;
        let prefix = if chained.is_empty() { prefix } else { join_path(&[&prefix, &chained]) };
        Some((path, prefix, known || !chained.is_empty()))
    }

    fn named_router(&self, recv: Node, ctx: &Ctx) -> Option<(Vec<String>, String, bool)> {
        let path = path_of(recv, self.src).filter(|p| !p.iter().any(|s| s == "()"))?;
        if let (Some((outer, param, prefix)), [one]) = (&ctx.chi, path.as_slice()) {
            if one == param {
                return Some((outer.clone(), prefix.clone(), true));
            }
        }
        if let [one] = path.as_slice() {
            if let Some(i) = ctx.params.get(one) {
                return Some((vec![PARAM_ROUTER.to_string(), i.to_string()], String::new(), true));
            }
            let known = ctx.routers.contains(one) || self.top.contains(one);
            return Some((path, String::new(), known));
        }
        Some((path, String::new(), false))
    }

    fn handler(&self, args: &[Node]) -> Handler {
        match args.last() {
            Some(l) if matches!(l.kind(), "identifier" | "selector_expression") => {
                path_of(*l, self.src).map_or(Handler::Inline, Handler::Path)
            }
            Some(l) if l.kind() == "func_literal" => {
                single_call(*l, self.src).map_or(Handler::Inline, Handler::Path)
            }
            _ => Handler::Inline,
        }
    }

    fn route(&mut self, at: Node, method: &str, router: Vec<String>, path: String, h: Handler) {
        self.out.routes.push(RouteFact {
            line: line(at),
            framework: self.framework.to_string(),
            method: method.to_string(),
            path: PathExpr::lit(path),
            router: Some(router),
            handler: h,
            from_def: self.home(at.start_byte()),
            needs_server_import: false,
        });
    }

    /// `name := recv.Group("/p")` and friends: a router bound to a name.
    fn binding(&mut self, names: Node, values: Node, ctx: &mut Ctx) {
        let src = self.src;
        let (names, values) = (kids(names), kids(values));
        for (n, v) in names.iter().zip(values.iter()) {
            if n.kind() != "identifier" {
                continue;
            }
            let name = text(*n, src);
            let Some((m, recv, args)) = selector_call(*v, src) else { continue };
            if MAKERS.contains(&m.as_str()) {
                ctx.routers.insert(name);
                continue;
            }
            // gorilla: r.PathPrefix("/api").Subrouter()
            let (m, recv, args) = if m == "Subrouter" {
                match selector_call(recv, src) {
                    Some((pm, inner, pa)) if pm == "PathPrefix" => (pm, inner, pa),
                    _ => continue,
                }
            } else {
                (m, recv, args)
            };
            if !matches!(m.as_str(), "Group" | "PathPrefix") {
                continue;
            }
            let Some(prefix) =
                args.first().filter(|a| is_string(**a)).map(|a| string_value(*a, src))
            else {
                continue;
            };
            let Some((parent, outer, _)) = self.router(recv, ctx) else { continue };
            let scope = self.home(v.start_byte());
            self.out.routers.push(RouterFact {
                name: name.clone(),
                prefix: PathExpr::lit(join_path(&[&outer, &prefix])),
                scope,
            });
            self.out.mounts.push(MountFact {
                line: line(*v),
                parent,
                child: Mounted::Path(vec![name.clone()]),
                prefix: PathExpr::default(),
                from_def: scope,
            });
            ctx.routers.insert(name);
        }
    }

    fn call(&mut self, call: Node, ctx: &Ctx) -> bool {
        let src = self.src;
        let Some(f) = call.child_by_field_name("function") else { return false };
        let args = call.child_by_field_name("arguments").map(kids).unwrap_or_default();
        // a router handed to a function: addUserRoutes(v1)
        if matches!(f.kind(), "identifier" | "selector_expression") {
            for (i, a) in args.iter().enumerate() {
                if a.kind() != "identifier" {
                    continue;
                }
                let Some((parent, _, known)) = self.router(*a, ctx) else { continue };
                if !known {
                    continue;
                }
                if let Some(callee) = path_of(f, src) {
                    self.out.mounts.push(MountFact {
                        line: line(call),
                        parent,
                        child: Mounted::Param(callee, i),
                        prefix: PathExpr::default(),
                        from_def: self.home(call.start_byte()),
                    });
                }
            }
        }
        let Some((name, recv, _)) = selector_call(call, src) else { return false };
        let first = args.iter().position(|a| is_string(*a));
        let pattern = first.map(|i| string_value(args[i], src));
        let Some((router, prefix, known)) = self.router(recv, ctx) else { return false };
        let verb = if UPPER.contains(&name.as_str()) || TITLE.contains(&name.as_str()) {
            Some(name.to_uppercase())
        } else if name == "Any" {
            Some("ANY".to_string())
        } else {
            None
        };
        if let (Some(v), Some(p)) = (&verb, &pattern) {
            if args.len() >= 2 && (p.starts_with('/') || known) {
                let h = self.handler(&args);
                self.route(call, v, router, join_path(&[&prefix, p]), h);
                return false;
            }
        }
        if matches!(name.as_str(), "Handle" | "HandleFunc") && args.len() >= 2 {
            let Some(p) = pattern else { return false };
            let strings: Vec<String> =
                args.iter().filter(|a| is_string(**a)).map(|a| string_value(*a, src)).collect();
            let (method, pat) = match p.split_once(' ') {
                Some((m, rest))
                    if m.chars().all(|c| c.is_ascii_uppercase()) && rest.starts_with('/') =>
                {
                    (m.to_string(), rest.to_string())
                }
                _ if p.starts_with('/') => ("ANY".to_string(), p.clone()),
                _ if strings.len() >= 2
                    && UPPER.contains(&strings[0].to_uppercase().as_str())
                    && strings[1].starts_with('/') =>
                {
                    (strings[0].to_uppercase(), strings[1].clone())
                }
                _ => return false,
            };
            let h = self.handler(&args);
            self.route(call, &method, router, join_path(&[&prefix, &pat]), h);
        }
        false
    }

    /// A chi `Route` / `Group` / `With` lambda: walked with the router it
    /// stands for and the prefix it adds. `true` when the call was one.
    fn chi_lambda(&mut self, call: Node, ctx: &Ctx) -> bool {
        let src = self.src;
        let Some((name, recv, args)) = selector_call(call, src) else { return false };
        if !matches!(name.as_str(), "Route" | "Group" | "With") {
            return false;
        }
        let Some(lam) = args.iter().find(|a| a.kind() == "func_literal") else { return false };
        let sub = if name == "Route" {
            match args.first().filter(|a| is_string(**a)) {
                Some(a) => string_value(*a, src),
                None => return false,
            }
        } else {
            String::new()
        };
        let Some((outer, prefix, _)) = self.router(recv, ctx) else { return false };
        let param = lam
            .child_by_field_name("parameters")
            .and_then(|ps| kids(ps).into_iter().find(|p| p.kind() == "parameter_declaration"))
            .and_then(|p| p.child_by_field_name("name"))
            .map(|n| text(n, src))
            .unwrap_or_default();
        let mut inner = ctx.clone();
        inner.chi = Some((outer, param, join_path(&[&prefix, &sub])));
        self.walk(*lam, &mut inner);
        true
    }

    fn function_ctx(&self, f: Node) -> Ctx {
        let src = self.src;
        let mut ctx = Ctx::default();
        let Some(ps) = f.child_by_field_name("parameters") else { return ctx };
        let mut i = 0;
        for p in kids(ps).into_iter().filter(|p| p.kind() == "parameter_declaration") {
            let ty = p.child_by_field_name("type").map(|t| text(t, src)).unwrap_or_default();
            let last = ty.rsplit(['.', '*']).next().unwrap_or("");
            let names: Vec<Node> =
                kids(p).into_iter().filter(|c| c.kind() == "identifier").collect();
            for n in &names {
                if ROUTER_TYPES.contains(&last) {
                    ctx.params.insert(text(*n, src), i);
                }
                i += 1;
            }
            if names.is_empty() {
                i += 1;
            }
        }
        ctx
    }

    fn walk(&mut self, n: Node, ctx: &mut Ctx) {
        for c in kids(n) {
            match c.kind() {
                "function_declaration" | "method_declaration" => {
                    let mut inner = self.function_ctx(c);
                    self.walk(c, &mut inner);
                    continue;
                }
                "short_var_declaration" | "assignment_statement" => {
                    if let (Some(l), Some(r)) =
                        (c.child_by_field_name("left"), c.child_by_field_name("right"))
                    {
                        self.binding(l, r, ctx);
                    }
                }
                "var_spec" => {
                    let names = c.child_by_field_name("name").map(|_| c);
                    let value = c.child_by_field_name("value");
                    if let (Some(_), Some(v)) = (names, value) {
                        // `var x = …`: the identifiers before the values
                        let ids: Vec<Node> =
                            kids(c).into_iter().filter(|k| k.kind() == "identifier").collect();
                        if let (Some(id), Some(first)) = (ids.first(), kids(v).first()) {
                            if let Some((m, _, _)) = selector_call(*first, self.src) {
                                if MAKERS.contains(&m.as_str()) {
                                    ctx.routers.insert(text(*id, self.src));
                                }
                            }
                        }
                        self.binding(c, v, ctx);
                    }
                }
                "call_expression" => {
                    if self.chi_lambda(c, ctx) {
                        continue;
                    }
                    self.call(c, ctx);
                    self.request(c);
                }
                _ => {}
            }
            self.walk(c, ctx);
        }
    }

    /// A request: `http.Get(url)`, `http.NewRequest(http.MethodPost, url, nil)`,
    /// `client.Get(url)`, resty's `client.R().Get(url)`.
    fn request(&mut self, call: Node) {
        let src = self.src;
        let Some((name, recv, args)) = selector_call(call, src) else { return };
        let recv_text = text(recv, src);
        let method_of = |a: Option<&Node>| {
            a.map(|m| {
                if is_string(*m) {
                    string_value(*m, src).to_uppercase()
                } else {
                    text(*m, src)
                        .rsplit('.')
                        .next()
                        .unwrap_or("")
                        .trim_start_matches("Method")
                        .to_uppercase()
                }
            })
            .filter(|m| UPPER.contains(&m.as_str()))
        };
        let (method, url) = match name.as_str() {
            "NewRequest" if matches!(recv_text.as_str(), "http" | "httptest") => {
                (method_of(args.first()), args.get(1))
            }
            "NewRequestWithContext" if recv_text == "http" => (method_of(args.get(1)), args.get(2)),
            "Get" | "Head" | "Post" | "PostForm" | "Put" | "Delete" | "Patch" => {
                let client = recv_text == "http"
                    || recv_text.ends_with("Client")
                    || recv_text.ends_with("client")
                    || (recv.kind() == "call_expression" && recv_text.ends_with(".R()"));
                if !client {
                    return;
                }
                let m = if name == "PostForm" { "POST".to_string() } else { name.to_uppercase() };
                (Some(m), args.first())
            }
            _ => return,
        };
        let Some(url) = url.and_then(|u| path_expr(*u, src)) else { return };
        if !url.0.iter().any(|p| matches!(p, Part::Lit(_))) {
            return;
        }
        self.out.calls.push(CallFact {
            line: line(call),
            method,
            url,
            client: None,
            from_def: self.home(call.start_byte()),
        });
    }
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let framework = FRAMEWORKS
        .iter()
        .find(|(p, _)| facts.imports.iter().any(|i| i.module.contains(p)))
        .map_or("net-http", |(_, l)| l);
    // package-level routers, known in every function of the file
    let mut top = HashSet::new();
    for decl in kids(root).into_iter().filter(|d| d.kind() == "var_declaration") {
        for spec in kids(decl).into_iter().filter(|c| c.kind() == "var_spec") {
            let ids: Vec<Node> =
                kids(spec).into_iter().filter(|k| k.kind() == "identifier").collect();
            let first = spec.child_by_field_name("value").and_then(|v| kids(v).first().copied());
            if let (Some(id), Some(first)) = (ids.first(), first) {
                if selector_call(first, src).is_some_and(|(m, _, _)| MAKERS.contains(&m.as_str())) {
                    top.insert(text(*id, src));
                }
            }
        }
    }
    let mut r = Reader { src, facts, framework, top, out: HttpFacts::default() };
    r.walk(root, &mut Ctx::default());
    r.out
}
