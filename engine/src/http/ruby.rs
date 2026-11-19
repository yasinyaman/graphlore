//! Ruby routes, after `routes.py`'s `_routes_ruby`: Sinatra `get '/x' do … end`
//! (in a file that requires sinatra, or under a `Sinatra::Base` subclass) with
//! `namespace`, and the Rails `routes.draw` DSL: verbs, `match … via:`, `root`,
//! `resources` / `resource` (`only:` / `except:` / `param:`, nested, `on:
//! :collection`), `namespace`, `scope`. A Rails handler `"articles#index"` is
//! the `index` method of `ArticlesController` (in `Api::` under `namespace
//! :api`).
//!
//! The requests: `HTTParty` / `Faraday` / `RestClient` `get(url)`, and in a
//! test file a receiver-less `get '/x'` (rack-test, request specs), counted.

use tree_sitter::Node;

use super::{join_path, path_expr, CallFact, Handler, HttpFacts, Part, PathExpr, RouteFact};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, string_value};

const VERBS: &[&str] = &["get", "post", "put", "patch", "delete", "options", "head"];
/// What `resources :articles` declares: (method, path under it, action).
const REST: &[(&str, &str, &str)] = &[
    ("GET", "", "index"),
    ("GET", "new", "new"),
    ("POST", "", "create"),
    ("GET", ":id", "show"),
    ("GET", ":id/edit", "edit"),
    ("PATCH", ":id", "update"),
    ("PUT", ":id", "update"),
    ("DELETE", ":id", "destroy"),
];
const REST_ONE: &[(&str, &str, &str)] = &[
    ("GET", "new", "new"),
    ("POST", "", "create"),
    ("GET", "", "show"),
    ("GET", "edit", "edit"),
    ("PATCH", "", "update"),
    ("PUT", "", "update"),
    ("DELETE", "", "destroy"),
];
const CLIENTS: &[&str] = &["HTTParty", "Faraday", "RestClient", "Typhoeus"];

/// A string literal (no interpolation) or a symbol (`:feed` → `feed`).
fn value(n: Option<Node>, src: &[u8]) -> Option<String> {
    let n = n?;
    match n.kind() {
        "simple_symbol" | "hash_key_symbol" => {
            Some(text(n, src).trim_start_matches(':').to_string())
        }
        "string" if !kids(n).iter().any(|c| c.kind() == "interpolation") => {
            Some(string_value(n, src))
        }
        _ => None,
    }
}

struct Call<'t> {
    name: String,
    pos: Vec<Node<'t>>,
    kw: Vec<(String, Node<'t>)>,
    block: Option<Node<'t>>,
}

impl<'t> Call<'t> {
    fn kw(&self, k: &str) -> Option<Node<'t>> {
        self.kw.iter().find(|(n, _)| n == k).map(|(_, v)| *v)
    }
}

/// `routes.py`'s `_ruby_call`: method, positional and keyword arguments, block.
fn ruby_call<'t>(n: Node<'t>, src: &[u8]) -> Call<'t> {
    let name = n.child_by_field_name("method").map(|m| text(m, src)).unwrap_or_default();
    let (mut pos, mut kw) = (Vec::new(), Vec::new());
    for a in n.child_by_field_name("arguments").map(kids).unwrap_or_default() {
        if a.kind() == "pair" {
            let (k, v) = (a.child_by_field_name("key"), a.child_by_field_name("value"));
            if let (Some(k), Some(v)) = (k, v) {
                if k.kind() == "string" {
                    // `get 'x' => 'c#a'`
                    pos.push(k);
                    if !kw.iter().any(|(n, _): &(String, Node)| n == "to") {
                        kw.push(("to".into(), v));
                    }
                } else if let Some(key) = value(Some(k), src) {
                    kw.push((key, v));
                }
            }
        } else {
            pos.push(a);
        }
    }
    Call { name, pos, kw, block: n.child_by_field_name("block") }
}

/// The receiver-less calls a block holds, not looking inside other calls.
fn body_calls(block: Node) -> Vec<Node> {
    let mut out = Vec::new();
    let mut stack: Vec<Node> = kids(block).into_iter().rev().collect();
    while let Some(n) = stack.pop() {
        if n.kind() == "call" && n.child_by_field_name("receiver").is_none() {
            out.push(n);
        } else if n.kind() != "call" {
            stack.extend(kids(n).into_iter().rev());
        }
    }
    out
}

/// Rails' plural of a resource name, for its controller: `user` → `users`,
/// `category` → `categories`; a name in `s` is left alone.
fn pluralize(s: &str) -> String {
    if s.ends_with('s') {
        s.to_string()
    } else if let Some(stem) =
        s.strip_suffix('y').filter(|t| !t.ends_with(['a', 'e', 'i', 'o', 'u']))
    {
        format!("{stem}ies")
    } else {
        format!("{s}s")
    }
}

fn camel(s: &str) -> String {
    s.split('_')
        .map(|w| {
            let mut c = w.chars();
            c.next().map_or(String::new(), |f| f.to_uppercase().chain(c).collect())
        })
        .collect()
}

/// `ns` + `"articles#index"` → the method of a controller class.
fn controller(ns: &str, to: &str) -> Handler {
    let Some((ctrl, action)) = to.split_once('#') else { return Handler::Inline };
    let full = format!("{ns}{ctrl}");
    let mut parts: Vec<String> = full.split('/').filter(|s| !s.is_empty()).map(camel).collect();
    let Some(last) = parts.pop() else { return Handler::Inline };
    Handler::Class(parts, format!("{last}Controller"), action.to_string())
}

struct Reader<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    out: HttpFacts,
}

impl Reader<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    fn route(&mut self, at: Node, fw: &str, method: &str, path: String, handler: Handler) {
        self.out.routes.push(RouteFact {
            line: line(at),
            framework: fw.to_string(),
            method: method.to_uppercase(),
            path: PathExpr::lit(path),
            router: None,
            handler,
            from_def: self.home(at.start_byte()),
            needs_server_import: false,
        });
    }

    fn sinatra(&mut self, block: Node, prefix: &str) {
        let src = self.src;
        for call in body_calls(block) {
            let c = ruby_call(call, src);
            let first = value(c.pos.first().copied(), src)
                .filter(|_| c.pos.first().is_some_and(|p| p.kind() == "string"));
            match (c.block, first) {
                (Some(_), Some(p)) if VERBS.contains(&c.name.as_str()) => {
                    self.route(call, "sinatra", &c.name, join_path(&[prefix, &p]), Handler::Inline);
                }
                (Some(b), Some(p)) if c.name == "namespace" => {
                    self.sinatra(b, &join_path(&[prefix, &p]));
                }
                (Some(b), _) if !VERBS.contains(&c.name.as_str()) => self.sinatra(b, prefix),
                _ => {}
            }
        }
    }

    fn strings(&self, n: Option<Node>) -> Vec<String> {
        let Some(n) = n else { return Vec::new() };
        if n.kind() == "array" {
            return kids(n).into_iter().filter_map(|e| value(Some(e), self.src)).collect();
        }
        value(Some(n), self.src).into_iter().collect()
    }

    fn resources(&mut self, call: Node, c: &Call, prefix: &str, ns: &str) {
        let src = self.src;
        let plural = c.name == "resources";
        let table = if plural { REST } else { REST_ONE };
        let only = self.strings(c.kw("only"));
        let except = self.strings(c.kw("except"));
        let param = value(c.kw("param"), src).unwrap_or_else(|| "id".into());
        for arg in &c.pos {
            let Some(res) = value(Some(*arg), src) else { continue };
            let base = join_path(&[prefix, &res]);
            for (method, sub, action) in table {
                if (!only.is_empty() && !only.iter().any(|o| o == action))
                    || except.iter().any(|e| e == action)
                {
                    continue;
                }
                let sub = sub.replace(":id", &format!(":{param}"));
                // a singular `resource :user` is still served by UsersController
                let ctrl = if plural { res.clone() } else { pluralize(&res) };
                let h = controller(ns, &format!("{ctrl}#{action}"));
                self.route(call, "rails", method, join_path(&[&base, &sub]), h);
            }
            if let Some(b) = c.block {
                let member = if plural {
                    join_path(&[&base, &format!(":{}_{param}", res.trim_end_matches('s'))])
                } else {
                    base.clone()
                };
                self.rails(b, &member, ns, &base, &res);
            }
        }
    }

    fn rails(&mut self, block: Node, prefix: &str, ns: &str, collection: &str, ctrl: &str) {
        let src = self.src;
        for call in body_calls(block) {
            let c = ruby_call(call, src);
            if VERBS.contains(&c.name.as_str()) || c.name == "match" {
                let Some(first) = value(c.pos.first().copied(), src) else { continue };
                let on_collection = value(c.kw("on"), src).as_deref() == Some("collection");
                let base =
                    if !collection.is_empty() && on_collection { collection } else { prefix };
                let mut methods: Vec<String> =
                    self.strings(c.kw("via")).into_iter().map(|m| m.to_uppercase()).collect();
                if methods.is_empty() {
                    methods.push(if c.name == "match" { "ANY".into() } else { c.name.clone() });
                }
                let to = value(c.kw("to"), src).unwrap_or_else(|| {
                    if ctrl.is_empty() {
                        first.clone()
                    } else {
                        format!("{ctrl}#{first}")
                    }
                });
                for m in methods {
                    let h = controller(ns, &to);
                    self.route(call, "rails", &m, join_path(&[base, &first]), h);
                }
            } else if c.name == "root" {
                let to = value(c.pos.first().copied(), src).or_else(|| value(c.kw("to"), src));
                let path = if prefix.is_empty() { "/".to_string() } else { prefix.to_string() };
                let h = to.map_or(Handler::Inline, |t| controller(ns, &t));
                self.route(call, "rails", "GET", path, h);
            } else if c.name == "resources" || c.name == "resource" {
                self.resources(call, &c, prefix, ns);
            } else if (c.name == "namespace" || c.name == "scope") && c.block.is_some() {
                let first = value(c.pos.first().copied(), src);
                let path = value(c.kw("path"), src).or(first.clone()).unwrap_or_default();
                let module = value(c.kw("module"), src)
                    .or(if c.name == "namespace" { first } else { None })
                    .unwrap_or_default();
                let ns = if module.is_empty() { ns.to_string() } else { format!("{ns}{module}/") };
                self.rails(c.block.unwrap(), &join_path(&[prefix, &path]), &ns, "", "");
            } else if let Some(b) = c.block {
                // constraints, concerns, …: the routes inside count
                self.rails(b, prefix, ns, collection, ctrl);
            }
        }
    }

    fn requests(&mut self, root: Node, test: bool) {
        let src = self.src;
        let mut stack = vec![root];
        while let Some(n) = stack.pop() {
            stack.extend(kids(n));
            if n.kind() != "call" {
                continue;
            }
            let c = ruby_call(n, src);
            if !VERBS.contains(&c.name.as_str()) || c.block.is_some() {
                continue;
            }
            let recv = n.child_by_field_name("receiver").map(|r| text(r, src));
            let ok = match &recv {
                Some(r) => CLIENTS.contains(&r.as_str()),
                None => test,
            };
            let Some(url) = c.pos.first().filter(|_| ok).and_then(|a| path_expr(*a, src)) else {
                continue;
            };
            if !url.0.iter().any(|p| matches!(p, Part::Lit(_))) {
                continue;
            }
            self.out.calls.push(CallFact {
                line: line(n),
                method: Some(c.name.to_uppercase()),
                url,
                client: None,
                from_def: self.home(n.start_byte()),
            });
        }
    }
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut r = Reader { src, facts, out: HttpFacts::default() };
    let mut calls = Vec::new();
    let mut supers = Vec::new();
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        match n.kind() {
            "call" => calls.push(n),
            "superclass" => supers.push(n),
            _ => {}
        }
        stack.extend(kids(n));
    }
    let sinatra = calls.iter().any(|c| {
        c.child_by_field_name("receiver").is_none()
            && c.child_by_field_name("method").is_some_and(|m| text(m, src) == "require")
            && text(*c, src).contains("sinatra")
    }) || supers.iter().any(|s| text(*s, src).contains("Sinatra"));
    if sinatra {
        r.sinatra(root, "");
    }
    for c in &calls {
        let draws = c.child_by_field_name("method").is_some_and(|m| text(m, src) == "draw")
            && c.child_by_field_name("receiver").is_some_and(|r| text(r, src).contains("routes"));
        if let (true, Some(b)) = (draws, c.child_by_field_name("block")) {
            r.rails(b, "", "", "", "");
        }
    }
    let test = crate::community::is_test_path(&facts.path);
    if !sinatra || test {
        r.requests(root, test);
    }
    r.out
}
