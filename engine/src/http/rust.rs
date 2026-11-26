//! Rust routes, after `routes.py`'s `_routes_rust`: actix-web `#[get("/x")]`
//! on a function and its builders (`web::scope("/api").service(web::resource
//! ("/x").route(web::get().to(f)))`), axum `Router::new().route("/x",
//! get(h).post(h2)).nest("/api", r)`, gated on the crate being used. Beyond
//! it: a router a function builds is the function's (`<self>`), so `.nest
//! ("/api", users::router())`, `.merge(other())` and actix `.configure(routes)`
//! mount the function they call, across files.
//!
//! The requests: `reqwest::get(url)`, a client's `get(url)` / `post(url)` in a
//! file using reqwest, `ureq::get(url)`.

use crate::parse::Fields;
use crate::parse::Kinds;
use std::sync::OnceLock;

use regex::Regex;
use tree_sitter::Node;

use super::{
    join_path, path_expr, CallFact, Handler, HttpFacts, MountFact, Mounted, Part, PathExpr,
    RouteFact, SELF_ROUTER,
};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, string_value};

const METHODS: &[&str] = &["get", "post", "put", "delete", "patch", "head", "options", "trace"];

fn verb_attr() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r#"(?s)^#\s*\[\s*(?:actix_web::)?(get|post|put|delete|patch|head|options|trace|route)\s*\(\s*"([^"]*)"(.*)\)\s*\]\s*$"#,
        )
        .unwrap()
    })
}

fn method_arg() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r#"method\s*=\s*"(\w+)""#).unwrap())
}

fn lit(n: Node, src: &[u8]) -> Option<String> {
    (n.kind_str() == "string_literal" || n.kind_str() == "raw_string_literal")
        .then(|| string_value(n, src))
}

/// `(name, receiver, arguments, name node)` of `recv.name(args)` or
/// `path::name(args)` (no receiver).
fn call_parts<'t>(
    call: Node<'t>,
    src: &[u8],
) -> (String, Option<Node<'t>>, Vec<Node<'t>>, Node<'t>) {
    let args = call.by_field("arguments").map(kids).unwrap_or_default();
    let Some(f) = call.by_field("function") else {
        return (String::new(), None, args, call);
    };
    if f.kind_str() == "field_expression" {
        let field = f.by_field("field");
        return (
            field.map(|x| text(x, src)).unwrap_or_default(),
            f.by_field("value"),
            args,
            field.unwrap_or(f),
        );
    }
    let t = text(f, src);
    (t.rsplit("::").next().unwrap_or("").to_string(), None, args, f)
}

/// A function named by path (`users::router`, `routes`): what a mount calls.
fn callee_path(n: Node, src: &[u8]) -> Option<Vec<String>> {
    let target = if n.kind_str() == "call_expression" { n.by_field("function")? } else { n };
    if !matches!(target.kind_str(), "identifier" | "scoped_identifier") {
        return None;
    }
    let path: Vec<String> = text(target, src).split("::").map(str::to_string).collect();
    // `Router::new()` builds a router in place: nothing to mount
    (path.last().map(String::as_str) != Some("new")).then_some(path)
}

struct Reader<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    label: &'static str,
    out: HttpFacts,
}

impl Reader<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    fn route(
        &mut self,
        at: Node,
        framework: &str,
        method: &str,
        path: String,
        handler: Handler,
        router: bool,
    ) {
        self.out.routes.push(RouteFact {
            line: line(at),
            framework: framework.to_string(),
            method: method.to_uppercase(),
            path: PathExpr::lit(path),
            router: router.then(|| vec![SELF_ROUTER.to_string()]),
            handler,
            from_def: self.home(at.start_byte()),
            needs_server_import: false,
        });
    }

    fn mount(&mut self, at: Node, child: Vec<String>, prefix: String) {
        self.out.mounts.push(MountFact {
            line: line(at),
            parent: vec![SELF_ROUTER.to_string()],
            child: Mounted::Path(child),
            prefix: PathExpr::lit(prefix),
            from_def: self.home(at.start_byte()),
        });
    }

    /// actix `#[get("/x")]` on the functions of a container.
    fn attributes(&mut self, n: Node) {
        let src = self.src;
        let mut pending: Vec<Node> = Vec::new();
        for c in kids(n) {
            match c.kind_str() {
                "attribute_item" => pending.push(c),
                k if k.contains("comment") => {}
                "function_item" => {
                    if let Some(name) = c.by_field("name") {
                        let handler =
                            self.home(c.start_byte()).map_or(Handler::Inline, Handler::Def);
                        for a in &pending {
                            let t = text(*a, src);
                            let Some(m) = verb_attr().captures(&t) else { continue };
                            let (verb, path, rest) = (&m[1], &m[2], &m[3]);
                            let methods: Vec<String> = if verb == "route" {
                                method_arg().captures_iter(rest).map(|c| c[1].to_string()).collect()
                            } else {
                                vec![verb.to_string()]
                            };
                            let methods =
                                if methods.is_empty() { vec!["ANY".to_string()] } else { methods };
                            for meth in methods {
                                self.route(
                                    name,
                                    "actix-web",
                                    &meth,
                                    join_path(&[path]),
                                    handler.clone(),
                                    false,
                                );
                            }
                        }
                    }
                    pending.clear();
                    self.attributes(c);
                }
                _ => {
                    pending.clear();
                    self.attributes(c);
                }
            }
        }
    }

    /// `get(h).post(h2)` / `web::get().to(f)`: (method, handler) pairs.
    fn methods_of(&self, expr: Option<Node>, pending: Option<Node>) -> Vec<(String, Handler)> {
        let src = self.src;
        let Some(expr) = expr.filter(|e| e.kind_str() == "call_expression") else {
            return Vec::new();
        };
        let (name, recv, args, _) = call_parts(expr, src);
        let first = args.first().copied();
        if name == "to" || name == "to_async" {
            return self.methods_of(recv, first);
        }
        if METHODS.contains(&name.as_str()) || name == "any" {
            let h = first
                .or(pending)
                .and_then(|h| callee_path(h, src))
                .map_or(Handler::Inline, Handler::Path);
            let mut out = vec![(if name == "any" { "ANY".into() } else { name.clone() }, h)];
            out.extend(self.methods_of(recv, pending));
            return out;
        }
        Vec::new()
    }

    /// The literal of the first `kind("/p")` call down a receiver chain.
    fn chain_path(&self, recv: Option<Node>, names: &[&str]) -> String {
        let src = self.src;
        let mut node = recv;
        while let Some(n) = node.filter(|n| n.kind_str() == "call_expression") {
            let (name, inner, args, _) = call_parts(n, src);
            if names.contains(&name.as_str()) && !args.is_empty() {
                return args.first().and_then(|a| lit(*a, src)).unwrap_or_default();
            }
            node = inner;
        }
        String::new()
    }

    fn walk(&mut self, n: Node, prefix: &str) {
        let src = self.src;
        if n.kind_str() == "call_expression" {
            let (name, recv, args, name_node) = call_parts(n, src);
            let scope = match recv {
                Some(_) => join_path(&[prefix, &self.chain_path(recv, &["scope"])]),
                None => prefix.to_string(),
            };
            let label = self.label;
            match name.as_str() {
                // axum: .route("/x", get(h))
                "route" if recv.is_some() && args.len() == 2 && lit(args[0], src).is_some() => {
                    let path = join_path(&[&scope, &lit(args[0], src).unwrap_or_default()]);
                    for (m, h) in self.methods_of(Some(args[1]), None) {
                        self.route(name_node, label, &m, path.clone(), h, true);
                    }
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                // actix: web::resource("/x").route(web::get().to(h))
                "route" if recv.is_some() && args.len() == 1 => {
                    let path = join_path(&[&scope, &self.chain_path(recv, &["resource"])]);
                    for (m, h) in self.methods_of(Some(args[0]), None) {
                        self.route(name_node, label, &m, path.clone(), h, true);
                    }
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                "to" | "to_async"
                    if recv.is_some()
                        && !args.is_empty()
                        && !self.chain_path(recv, &["resource"]).is_empty()
                        && self.methods_of(recv, None).is_empty() =>
                {
                    let path = join_path(&[&scope, &self.chain_path(recv, &["resource"])]);
                    let h = callee_path(args[0], src).map_or(Handler::Inline, Handler::Path);
                    self.route(name_node, label, "ANY", path, h, true);
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                "nest" if recv.is_some() && args.len() == 2 && lit(args[0], src).is_some() => {
                    let inner = join_path(&[prefix, &lit(args[0], src).unwrap_or_default()]);
                    match callee_path(args[1], src) {
                        Some(f) if args[1].kind_str() == "call_expression" => {
                            self.mount(n, f, inner)
                        }
                        _ => self.walk(args[1], &inner),
                    }
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                "merge" | "configure" if recv.is_some() && args.len() == 1 => {
                    let mounted = callee_path(args[0], src)
                        .filter(|_| args[0].kind_str() == "call_expression" || name == "configure");
                    match mounted {
                        Some(f) => self.mount(n, f, scope.clone()),
                        None => self.walk(args[0], prefix),
                    }
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                "service" if recv.is_some() && !args.is_empty() => {
                    for a in &args {
                        self.walk(*a, &scope);
                    }
                    self.walk(recv.unwrap(), prefix);
                    return;
                }
                _ => {}
            }
        }
        for c in kids(n) {
            self.walk(c, prefix);
        }
    }

    /// `reqwest::get(url)`, `client.get(url)`, `ureq::get(url)`.
    fn requests(&mut self, root: Node) {
        let src = self.src;
        let uses =
            |m: &str| self.facts.imports.iter().any(|i| i.module.split("::").next() == Some(m));
        let (reqwest, ureq) = (uses("reqwest"), uses("ureq"));
        let mut stack = vec![root];
        while let Some(n) = stack.pop() {
            stack.extend(kids(n));
            if n.kind_str() != "call_expression" {
                continue;
            }
            let (name, recv, args, _) = call_parts(n, src);
            if !METHODS.contains(&name.as_str()) {
                continue;
            }
            let f = n.by_field("function").map(|f| text(f, src)).unwrap_or_default();
            let ok = match recv {
                None => f.starts_with("reqwest::") || f.starts_with("ureq::"),
                Some(r) => {
                    let t = text(r, src).to_lowercase();
                    (reqwest || ureq) && (t.contains("client") || t.contains("agent"))
                }
            };
            if !ok {
                continue;
            }
            let Some(url) = args.first().and_then(|a| path_expr(*a, src)) else { continue };
            if !url.0.iter().any(|p| matches!(p, Part::Lit(_))) {
                continue;
            }
            self.out.calls.push(CallFact {
                line: line(n),
                method: Some(name.to_uppercase()),
                url,
                client: None,
                from_def: self.home(n.start_byte()),
            });
        }
    }
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let uses = |m: &str| facts.imports.iter().any(|i| i.module.contains(m));
    let label = if uses("axum") {
        Some("axum")
    } else if uses("actix_web") {
        Some("actix-web")
    } else {
        None
    };
    let mut r = Reader { src, facts, label: label.unwrap_or(""), out: HttpFacts::default() };
    if label.is_some() {
        r.attributes(root);
        r.walk(root, "");
    }
    r.requests(root);
    r.out
}
