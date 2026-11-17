//! Java and Kotlin routes, after `routes.py`'s `_routes_java` /
//! `_routes_kotlin`: Spring `@GetMapping`-family and `@RequestMapping` (the
//! class prefix joined), JAX-RS `@Path` + `@GET` in a file importing
//! `javax.ws.rs` / `jakarta.ws.rs`, and Kotlin's Ktor routing DSL
//! (`route("/api") { get("/x") { } }`) in a file importing it.
//!
//! The requests: Feign `@RequestLine("GET /x")`; the mappings of a Spring
//! `@FeignClient` or `@HttpExchange` interface, a MicroProfile
//! `@RegisterRestClient` interface and Retrofit's `@GET("x")`, which declare a
//! request rather than a route; `RestTemplate` / `TestRestTemplate`
//! (`getForObject(url)`, `exchange(url, HttpMethod.POST, …)`); `WebClient` /
//! `RestClient` / `WebTestClient` (`.get().uri("/x")`); MockMvc `get("/x")`;
//! RestAssured `.get("/x")`; the Ktor client (`client.get("/x")`).

use super::{
    join_path, path_expr, single_call, CallFact, Handler, HttpFacts, Part, PathExpr, RouteFact,
};
use crate::facts::FileFacts;
use crate::generic::{all_kids, kids, line, text};
use crate::refs::{innermost, string_value};
use tree_sitter::Node;

const SPRING_MAPPINGS: &[(&str, &str)] = &[
    ("GetMapping", "GET"),
    ("PostMapping", "POST"),
    ("PutMapping", "PUT"),
    ("DeleteMapping", "DELETE"),
    ("PatchMapping", "PATCH"),
];
const EXCHANGES: &[(&str, &str)] = &[
    ("GetExchange", "GET"),
    ("PostExchange", "POST"),
    ("PutExchange", "PUT"),
    ("DeleteExchange", "DELETE"),
    ("PatchExchange", "PATCH"),
];
const VERBS: &[&str] = &["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"];
const KTOR_VERBS: &[&str] = &["get", "post", "put", "delete", "patch", "head", "options"];
/// Interfaces whose mappings are requests made, not routes served.
const CLIENT_INTERFACES: &[&str] = &["FeignClient", "HttpExchange", "RegisterRestClient"];
/// `RestTemplate` methods and the method each sends.
const TEMPLATE_METHODS: &[(&str, &str)] = &[
    ("getForObject", "GET"),
    ("getForEntity", "GET"),
    ("postForObject", "POST"),
    ("postForEntity", "POST"),
    ("postForLocation", "POST"),
    ("patchForObject", "PATCH"),
    ("put", "PUT"),
    ("delete", "DELETE"),
    ("exchange", ""),
];

fn imports(facts: &FileFacts, prefix: &str) -> bool {
    facts.imports.iter().any(|i| {
        i.module == prefix
            || i.module.starts_with(&format!("{prefix}."))
            || i.names.iter().any(|(n, _)| format!("{}.{n}", i.module) == prefix)
    })
}

/// An annotation as `(name, named arguments, bare arguments)`, either grammar.
struct Ann<'t> {
    name: String,
    named: Vec<(String, Node<'t>)>,
    bare: Vec<Node<'t>>,
}

impl<'t> Ann<'t> {
    fn arg(&self, keys: &[&str]) -> Option<Node<'t>> {
        keys.iter().find_map(|k| self.named.iter().find(|(n, _)| n == k).map(|(_, v)| *v))
    }

    /// `routes.py`'s `annotation_path`: the first string of `value=` / `path=`,
    /// else of the bare arguments (never of an unrelated `produces=`).
    fn path(&self, src: &[u8]) -> String {
        let scopes: Vec<Node> = match self.arg(&["value", "path"]) {
            Some(v) => vec![v],
            None => self.bare.clone(),
        };
        scopes.into_iter().find_map(|s| first_literal(s, src)).unwrap_or_default()
    }

    /// `method = RequestMethod.GET` or an array of them; `ANY` without one.
    fn methods(&self, src: &[u8]) -> Vec<String> {
        let Some(v) = self.arg(&["method"]) else { return vec!["ANY".into()] };
        let elems = if matches!(v.kind(), "element_value_array_initializer" | "collection_literal")
        {
            kids(v)
        } else {
            vec![v]
        };
        let out: Vec<String> = elems
            .iter()
            .map(|e| text(*e, src).rsplit('.').next().unwrap_or("").to_uppercase())
            .filter(|m| !m.is_empty())
            .collect();
        if out.is_empty() {
            vec!["ANY".into()]
        } else {
            out
        }
    }
}

fn is_literal(n: Node) -> bool {
    matches!(n.kind(), "string_literal" | "line_string_literal")
        && !kids(n).iter().any(|c| c.kind().contains("interpolat"))
}

/// The first string literal at or under `n`, breadth first.
fn first_literal(n: Node, src: &[u8]) -> Option<String> {
    let mut queue = std::collections::VecDeque::from([n]);
    while let Some(c) = queue.pop_front() {
        if is_literal(c) {
            return Some(string_value(c, src));
        }
        queue.extend(kids(c));
    }
    None
}

/// The annotations on a declaration (Java `modifiers`, Kotlin `modifiers`).
fn annotations<'t>(decl: Node<'t>, src: &[u8], kotlin: bool) -> Vec<Ann<'t>> {
    let Some(mods) = kids(decl).into_iter().find(|c| c.kind() == "modifiers") else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for a in kids(mods) {
        if kotlin {
            if a.kind() != "annotation" {
                continue;
            }
            let ci = kids(a).into_iter().find(|c| c.kind() == "constructor_invocation");
            let ut = kids(ci.unwrap_or(a)).into_iter().find(|c| c.kind() == "user_type");
            let Some(ut) = ut else { continue };
            let name = text(ut, src).rsplit('.').next().unwrap_or("").to_string();
            let (mut named, mut bare) = (Vec::new(), Vec::new());
            let va = ci.and_then(|c| kids(c).into_iter().find(|x| x.kind() == "value_arguments"));
            for arg in va.map(kids).unwrap_or_default() {
                let ks = kids(arg);
                match ks.as_slice() {
                    [k, .., v] if k.kind() == "simple_identifier" && ks.len() >= 2 => {
                        named.push((text(*k, src), *v));
                    }
                    [.., v] => bare.push(*v),
                    [] => {}
                }
            }
            out.push(Ann { name, named, bare });
        } else {
            if !matches!(a.kind(), "annotation" | "marker_annotation") {
                continue;
            }
            let Some(n) = a.child_by_field_name("name") else { continue };
            let name = text(n, src).rsplit('.').next().unwrap_or("").to_string();
            let (mut named, mut bare) = (Vec::new(), Vec::new());
            for p in a.child_by_field_name("arguments").map(kids).unwrap_or_default() {
                if p.kind() == "element_value_pair" {
                    if let (Some(k), Some(v)) =
                        (p.child_by_field_name("key"), p.child_by_field_name("value"))
                    {
                        named.push((text(k, src), v));
                    }
                } else {
                    bare.push(p);
                }
            }
            out.push(Ann { name, named, bare });
        }
    }
    out
}

/// Every class / interface / object declaration under `root`.
fn classes(root: Node) -> Vec<Node> {
    let mut out = Vec::new();
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        if matches!(n.kind(), "class_declaration" | "interface_declaration" | "object_declaration")
        {
            out.push(n);
        }
        stack.extend(kids(n));
    }
    out.sort_by_key(|n| n.start_byte());
    out
}

fn is_interface(cls: Node) -> bool {
    cls.kind() == "interface_declaration" || all_kids(cls).iter().any(|c| c.kind() == "interface")
}

struct Reader<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    kotlin: bool,
    out: HttpFacts,
}

impl Reader<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    fn call(&mut self, at: Node, method: Option<String>, url: PathExpr) {
        if url.is_empty() {
            return;
        }
        self.out.calls.push(CallFact {
            line: line(at),
            method,
            url,
            client: None,
            from_def: self.home(at.start_byte()),
        });
    }

    fn route(&mut self, at: Node, framework: &str, method: &str, path: String, handler: Handler) {
        self.out.routes.push(RouteFact {
            line: line(at),
            framework: framework.to_string(),
            method: method.to_string(),
            path: PathExpr::lit(path),
            router: None,
            handler,
            from_def: self.home(at.start_byte()),
            needs_server_import: false,
        });
    }

    /// Spring / JAX-RS / Feign / Retrofit on the methods of each class.
    fn annotated(&mut self, root: Node) {
        let src = self.src;
        let facts = self.facts;
        let spring = !self.kotlin || imports(facts, "org.springframework.web");
        let jaxrs = imports(facts, "javax.ws.rs") || imports(facts, "jakarta.ws.rs");
        let retrofit = imports(facts, "retrofit2");
        let feign = imports(facts, "feign");
        for cls in classes(root) {
            let anns = annotations(cls, src, self.kotlin);
            let client = CLIENT_INTERFACES.iter().any(|c| anns.iter().any(|a| a.name == *c))
                || (retrofit && is_interface(cls));
            let prefix = anns
                .iter()
                .find(|a| matches!(a.name.as_str(), "RequestMapping" | "HttpExchange"))
                .map(|a| a.path(src))
                .unwrap_or_default();
            let path_prefix = anns.iter().find(|a| a.name == "Path").map(|a| a.path(src));
            let Some(body) = cls
                .child_by_field_name("body")
                .or_else(|| kids(cls).into_iter().find(|c| c.kind() == "class_body"))
            else {
                continue;
            };
            for m in kids(body) {
                if !matches!(m.kind(), "method_declaration" | "function_declaration") {
                    continue;
                }
                let handler = self.home(m.start_byte()).map_or(Handler::Inline, Handler::Def);
                let manns = annotations(m, src, self.kotlin);
                let at = m
                    .child_by_field_name("name")
                    .or_else(|| kids(m).into_iter().find(|c| c.kind() == "simple_identifier"))
                    .unwrap_or(m);
                // Feign: @RequestLine("GET /api/x")
                if feign {
                    for a in manns.iter().filter(|a| a.name == "RequestLine") {
                        let line_ = a.path(src);
                        let (verb, path) = line_.trim().split_once(' ').unwrap_or(("", ""));
                        let path = path.split_whitespace().next().unwrap_or("");
                        if VERBS.contains(&verb) && !path.is_empty() {
                            self.call(at, Some(verb.into()), PathExpr::lit(path));
                        }
                    }
                }
                // Retrofit: @GET("users/{id}")
                if retrofit && is_interface(cls) {
                    for a in manns.iter().filter(|a| VERBS.contains(&a.name.as_str())) {
                        let p = a.path(src);
                        self.call(at, Some(a.name.clone()), PathExpr::lit(join_path(&[&p])));
                    }
                    continue;
                }
                if jaxrs {
                    let sub = manns.iter().find(|a| a.name == "Path").map(|a| a.path(src));
                    if path_prefix.is_some() || sub.is_some() {
                        let full = join_path(&[
                            path_prefix.as_deref().unwrap_or(""),
                            sub.as_deref().unwrap_or(""),
                        ]);
                        let mut verbs: Vec<&str> = manns
                            .iter()
                            .map(|a| a.name.as_str())
                            .filter(|n| VERBS.contains(n))
                            .collect();
                        verbs.sort();
                        verbs.dedup();
                        for v in verbs {
                            if client {
                                self.call(at, Some(v.into()), PathExpr::lit(full.clone()));
                            } else {
                                self.route(at, "jax-rs", v, full.clone(), handler.clone());
                            }
                        }
                    }
                }
                if !spring && !client {
                    continue;
                }
                for a in &manns {
                    let fixed = SPRING_MAPPINGS
                        .iter()
                        .chain(EXCHANGES)
                        .find(|(n, _)| *n == a.name)
                        .map(|(_, m)| vec![m.to_string()]);
                    let methods = match fixed {
                        Some(m) => m,
                        None if a.name == "RequestMapping" => a.methods(src),
                        None => continue,
                    };
                    let full = join_path(&[&prefix, &a.path(src)]);
                    for meth in methods {
                        if client {
                            let m = (meth != "ANY").then_some(meth);
                            self.call(at, m, PathExpr::lit(full.clone()));
                        } else if !EXCHANGES.iter().any(|(n, _)| *n == a.name) {
                            self.route(at, "spring", &meth, full.clone(), handler.clone());
                        }
                    }
                }
            }
        }
    }
}

/// A call's receiver, name, arguments and trailing lambda.
type CallParts<'t> = (Option<Node<'t>>, Node<'t>, Vec<Node<'t>>, Option<Node<'t>>);

/// A call as `(receiver, name, arguments, lambda)`, either grammar.
fn call_parts<'t>(n: Node<'t>, kotlin: bool) -> Option<CallParts<'t>> {
    if !kotlin {
        if n.kind() != "method_invocation" {
            return None;
        }
        let name = n.child_by_field_name("name")?;
        let args = n.child_by_field_name("arguments").map(kids).unwrap_or_default();
        return Some((n.child_by_field_name("object"), name, args, None));
    }
    if n.kind() != "call_expression" {
        return None;
    }
    let ks = kids(n);
    let (head, suffix) = (ks.first().copied()?, ks.iter().find(|c| c.kind() == "call_suffix")?);
    let (recv, name) = match head.kind() {
        "simple_identifier" => (None, head),
        "navigation_expression" => {
            let hk = kids(head);
            let nav = hk.iter().rev().find(|c| c.kind() == "navigation_suffix")?;
            let name = kids(*nav).into_iter().find(|c| c.kind() == "simple_identifier")?;
            (hk.first().copied(), name)
        }
        _ => return None,
    };
    let sk = kids(*suffix);
    let args = sk
        .iter()
        .find(|c| c.kind() == "value_arguments")
        .map(|va| kids(*va).into_iter().filter_map(|a| kids(a).last().copied()).collect())
        .unwrap_or_default();
    let lambda = sk.iter().find(|c| c.kind() == "annotated_lambda").copied();
    Some((recv, name, args, lambda))
}

impl Reader<'_> {
    /// Requests made in code: RestTemplate, WebClient / RestClient, MockMvc,
    /// RestAssured, the Ktor client.
    fn requests(&mut self, root: Node) {
        let src = self.src;
        let facts = self.facts;
        let mockmvc = imports(facts, "org.springframework.test.web.servlet");
        let restassured = imports(facts, "io.restassured");
        let ktor_client = imports(facts, "io.ktor.client");
        let mut stack = vec![root];
        while let Some(n) = stack.pop() {
            stack.extend(kids(n));
            let Some((recv, name, args, _)) = call_parts(n, self.kotlin) else { continue };
            let name = text(name, src);
            let first = args.first().copied();
            let recv_text = recv.map(|r| text(r, src).to_lowercase()).unwrap_or_default();
            // RestTemplate: getForObject(url, …), exchange(url, HttpMethod.POST, …)
            if let Some((_, m)) = TEMPLATE_METHODS.iter().find(|(t, _)| *t == name) {
                let named_like = recv_text.contains("rest") || recv_text.contains("template");
                if recv.is_some() && (named_like || !matches!(name.as_str(), "put" | "delete")) {
                    if let Some(url) = first.and_then(|a| url_arg(a, src)) {
                        let method = if m.is_empty() {
                            args.get(1).map(|a| last_segment(text(*a, src)))
                        } else {
                            Some(m.to_string())
                        };
                        if named_like || !m.is_empty() {
                            self.call(n, method.filter(|m| VERBS.contains(&m.as_str())), url);
                        }
                    }
                }
                continue;
            }
            // WebClient / RestClient: client.get().uri("/x"), .method(HttpMethod.X).uri(…)
            if name == "uri" {
                let verb =
                    recv.and_then(|r| call_parts(r, self.kotlin)).and_then(|(_, vn, va, _)| {
                        let vn = text(vn, src);
                        if KTOR_VERBS.contains(&vn.as_str()) && va.is_empty() {
                            Some(vn.to_uppercase())
                        } else if vn == "method" {
                            va.first().map(|a| last_segment(text(*a, src)))
                        } else {
                            None
                        }
                    });
                if let (Some(v), Some(url)) = (verb, first.and_then(|a| url_arg(a, src))) {
                    self.call(n, Some(v), url);
                }
                continue;
            }
            let lower = name.to_lowercase();
            if !KTOR_VERBS.contains(&lower.as_str()) || lower != name {
                continue;
            }
            let Some(url) = first.and_then(|a| url_arg(a, src)) else { continue };
            // MockMvc: perform(get("/owners/new")); RestAssured: given().get("/x");
            // the Ktor client: client.get("/x")
            let ok = match recv {
                None => mockmvc,
                Some(_) => restassured || ktor_client,
            };
            if ok {
                self.call(n, Some(name.to_uppercase()), url);
            }
        }
    }
}

fn last_segment(t: String) -> String {
    t.rsplit('.').next().unwrap_or("").trim().to_uppercase()
}

/// A URL argument: a string, a template, a concatenation with names.
fn url_arg(a: Node, src: &[u8]) -> Option<PathExpr> {
    let e = path_expr(a, src)?;
    matches!(e.0.first(), Some(Part::Lit(_)) | Some(Part::Name(_)) | Some(Part::Hole))
        .then_some(e)
        .filter(|e| e.0.iter().any(|p| matches!(p, Part::Lit(_))))
}

impl Reader<'_> {
    /// Ktor: `route("/api") { get("/x") { … } }`, the prefixes stacked in the file.
    fn ktor(&mut self, n: Node, prefix: &str) {
        let src = self.src;
        for ch in kids(n) {
            let Some((None, name, args, lambda)) = call_parts(ch, true) else {
                self.ktor(ch, prefix);
                continue;
            };
            // a type argument (`get<T>()`) is not a route
            let typed = kids(ch).iter().any(|c| {
                c.kind() == "call_suffix" && kids(*c).iter().any(|x| x.kind() == "type_arguments")
            });
            if typed {
                self.ktor(ch, prefix);
                continue;
            }
            let name = text(name, src);
            let pattern = args.first().filter(|a| is_literal(**a)).map(|a| string_value(*a, src));
            if name == "route" {
                if let (Some(l), Some(p)) = (lambda, &pattern) {
                    let inner = join_path(&[prefix, p]);
                    self.ktor(l, &inner);
                    continue;
                }
            }
            let has_body = lambda.is_some() || args.len() >= 2;
            if KTOR_VERBS.contains(&name.as_str())
                && has_body
                && (pattern.is_some() || (args.is_empty() && !prefix.is_empty()))
            {
                let handler = match lambda {
                    Some(l) => single_call(l, src).map_or(Handler::Inline, Handler::Path),
                    None => {
                        let last = args.last().map(|a| text(*a, src)).unwrap_or_default();
                        let path: Vec<String> = last
                            .trim_start_matches(':')
                            .split(['.', ':'])
                            .filter(|s| !s.is_empty())
                            .map(str::to_string)
                            .collect();
                        if path.is_empty() {
                            Handler::Inline
                        } else {
                            Handler::Path(path)
                        }
                    }
                };
                let full = join_path(&[prefix, pattern.as_deref().unwrap_or("")]);
                self.route(ch, "ktor", &name.to_uppercase(), full, handler);
                continue;
            }
            self.ktor(ch, prefix);
        }
    }
}

pub fn read_java(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut r = Reader { src, facts, kotlin: false, out: HttpFacts::default() };
    r.annotated(root);
    r.requests(root);
    r.out
}

pub fn read_kotlin(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut r = Reader { src, facts, kotlin: true, out: HttpFacts::default() };
    r.annotated(root);
    let ktor_server = facts
        .imports
        .iter()
        .any(|i| i.module.starts_with("io.ktor") && i.module.contains("routing"));
    if ktor_server {
        r.ktor(root, "");
    }
    r.requests(root);
    r.out
}
