//! C# routes, after `routes.py`'s `_routes_csharp`: ASP.NET Core controllers
//! (`[Route("api/[controller]")]` + `[HttpGet("{id}")]`) and minimal APIs
//! (`app.MapGet("/x", h)`, prefixes through `var g = app.MapGroup("/api")` and
//! `app.MapGroup("/api").MapGet(…)`); group names are kept per definition, so
//! two extension methods that each say `var group = …` do not mix.
//!
//! The requests: `HttpClient`'s `GetAsync` / `PostAsJsonAsync` / … with a URL
//! written as a string, `$"todos/{id}"` or a concatenation, and
//! `SendAsync(new HttpRequestMessage(HttpMethod.Get, url))`, in a file that
//! names `HttpClient` (a cache's `GetAsync("key")` elsewhere is not one).

use std::collections::HashMap;

use tree_sitter::Node;

use super::{join_path, path_expr, single_call, CallFact, Handler, HttpFacts, PathExpr, RouteFact};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, path_of, string_value};

const ATTRIBUTE_VERBS: &[(&str, &str)] = &[
    ("HttpGet", "GET"),
    ("HttpPost", "POST"),
    ("HttpPut", "PUT"),
    ("HttpDelete", "DELETE"),
    ("HttpPatch", "PATCH"),
    ("HttpHead", "HEAD"),
    ("HttpOptions", "OPTIONS"),
];
const MINIMAL_VERBS: &[(&str, &str)] = &[
    ("MapGet", "GET"),
    ("MapPost", "POST"),
    ("MapPut", "PUT"),
    ("MapDelete", "DELETE"),
    ("MapPatch", "PATCH"),
    ("Map", "ANY"),
];
const CLIENT_METHODS: &[(&str, &str)] = &[
    ("GetAsync", "GET"),
    ("GetFromJsonAsync", "GET"),
    ("GetStringAsync", "GET"),
    ("GetStreamAsync", "GET"),
    ("GetByteArrayAsync", "GET"),
    ("PostAsync", "POST"),
    ("PostAsJsonAsync", "POST"),
    ("PutAsync", "PUT"),
    ("PutAsJsonAsync", "PUT"),
    ("PatchAsync", "PATCH"),
    ("PatchAsJsonAsync", "PATCH"),
    ("DeleteAsync", "DELETE"),
    ("DeleteFromJsonAsync", "DELETE"),
];

fn is_literal(n: Node) -> bool {
    matches!(n.kind(), "string_literal" | "verbatim_string_literal" | "raw_string_literal")
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

/// `(short name, attribute)` of the attributes on a declaration.
fn attributes<'t>(decl: Node<'t>, src: &[u8]) -> Vec<(String, Node<'t>)> {
    let mut out = Vec::new();
    for al in kids(decl).into_iter().filter(|c| c.kind() == "attribute_list") {
        for a in kids(al).into_iter().filter(|c| c.kind() == "attribute") {
            let Some(name) = a.child_by_field_name("name") else { continue };
            let t = text(name, src);
            let short = t.rsplit('.').next().unwrap_or("");
            let short = short.strip_suffix("Attribute").unwrap_or(short);
            out.push((short.to_string(), a));
        }
    }
    out
}

/// The route template of an attribute: its first unnamed argument's string.
fn template(attr: Node, src: &[u8]) -> String {
    let Some(args) = kids(attr).into_iter().find(|c| c.kind() == "attribute_argument_list") else {
        return String::new();
    };
    for arg in kids(args).into_iter().filter(|c| c.kind() == "attribute_argument") {
        let named = arg.child_by_field_name("name").is_some()
            || kids(arg).iter().any(|c| matches!(c.kind(), "name_equals" | "name_colon"));
        if !named {
            return first_literal(arg, src).unwrap_or_default();
        }
    }
    String::new()
}

fn replace_ci(s: &str, token: &str, with: &str) -> String {
    let lower = s.to_lowercase();
    let mut out = String::new();
    let mut i = 0;
    while let Some(j) = lower[i..].find(token) {
        out.push_str(&s[i..i + j]);
        out.push_str(with);
        i += j + token.len();
    }
    out.push_str(&s[i..]);
    out
}

/// `(method name, receiver, argument expressions, name node)` of `recv.M(…)`.
fn member_call<'t>(
    inv: Node<'t>,
    src: &[u8],
) -> Option<(String, Node<'t>, Vec<Node<'t>>, Node<'t>)> {
    if inv.kind() != "invocation_expression" {
        return None;
    }
    let f = inv.child_by_field_name("function")?;
    if f.kind() != "member_access_expression" {
        return None;
    }
    let name = f.child_by_field_name("name")?;
    let recv = f.child_by_field_name("expression")?;
    // `GetFromJsonAsync<List<T>>`: the name without its type arguments
    let short = text(name, src).split('<').next().unwrap_or("").trim().to_string();
    let args = inv
        .child_by_field_name("arguments")
        .map(kids)
        .unwrap_or_default()
        .into_iter()
        .filter(|a| a.kind() == "argument")
        .filter_map(|a| kids(a).into_iter().rfind(|c| c.kind() != "name_colon"))
        .collect();
    Some((short, recv, args, name))
}

struct Reader<'a> {
    src: &'a [u8],
    facts: &'a FileFacts,
    /// Group prefixes by the definition they are declared in and their name.
    groups: HashMap<(Option<usize>, String), String>,
    out: HttpFacts,
}

impl Reader<'_> {
    fn home(&self, byte: usize) -> Option<usize> {
        innermost(&self.facts.defs, &[byte])[0]
    }

    fn controllers(&mut self, root: Node) {
        let src = self.src;
        let mut stack = vec![root];
        while let Some(cls) = stack.pop() {
            stack.extend(kids(cls));
            if cls.kind() != "class_declaration" {
                continue;
            }
            let cname = cls.child_by_field_name("name").map(|n| text(n, src)).unwrap_or_default();
            let attrs = attributes(cls, src);
            let marked = attrs.iter().any(|(n, _)| n == "ApiController" || n == "Route");
            if !cname.ends_with("Controller") && !marked {
                continue;
            }
            let ctrl = cname.strip_suffix("Controller").unwrap_or(&cname).to_string();
            let prefix = attrs
                .iter()
                .find(|(n, _)| n == "Route")
                .map(|(_, a)| template(*a, src))
                .unwrap_or_default();
            let Some(body) = cls.child_by_field_name("body") else { continue };
            for m in kids(body).into_iter().filter(|c| c.kind() == "method_declaration") {
                let mname_node = m.child_by_field_name("name");
                let mname = mname_node.map(|n| text(n, src)).unwrap_or_default();
                let mattrs = attributes(m, src);
                let mut routes: Vec<String> = mattrs
                    .iter()
                    .filter(|(n, _)| n == "Route")
                    .map(|(_, a)| template(*a, src))
                    .collect();
                if routes.is_empty() {
                    routes.push(String::new());
                }
                let handler = self.home(m.start_byte()).map_or(Handler::Inline, Handler::Def);
                for (n, a) in &mattrs {
                    let Some((_, verb)) = ATTRIBUTE_VERBS.iter().find(|(v, _)| v == n) else {
                        continue;
                    };
                    for route in &routes {
                        let t = template(*a, src);
                        let pat = if t.starts_with('/') || t.starts_with("~/") {
                            t.trim_start_matches('~').to_string()
                        } else {
                            join_path(&[&join_path(&[&prefix, route]), &t])
                        };
                        let pat = replace_ci(&pat, "[controller]", &ctrl);
                        let pat = replace_ci(&pat, "[action]", &mname);
                        self.out.routes.push(RouteFact {
                            line: line(mname_node.unwrap_or(m)),
                            framework: "aspnet".into(),
                            method: verb.to_string(),
                            path: PathExpr::lit(join_path(&[&pat])),
                            router: None,
                            handler: handler.clone(),
                            from_def: self.home(m.start_byte()),
                            needs_server_import: false,
                        });
                    }
                }
            }
        }
    }

    /// The prefix the groups a receiver goes through add.
    fn group_prefix(&self, recv: Node, scope: Option<usize>) -> String {
        let src = self.src;
        match recv.kind() {
            "identifier" => {
                let name = text(recv, src);
                // the group declared in this definition, else one around it
                let mut cur = scope;
                loop {
                    if let Some(p) = self.groups.get(&(cur, name.clone())) {
                        return p.clone();
                    }
                    match cur {
                        Some(d) => cur = self.facts.defs[d].parent,
                        None => return String::new(),
                    }
                }
            }
            "invocation_expression" => match member_call(recv, src) {
                Some((name, inner, args, _)) => {
                    let base = self.group_prefix(inner, scope);
                    if name == "MapGroup" {
                        let p =
                            args.first().and_then(|a| first_literal(*a, src)).unwrap_or_default();
                        join_path(&[&base, &p])
                    } else {
                        base
                    }
                }
                None => String::new(),
            },
            _ => String::new(),
        }
    }

    /// Minimal APIs and requests, in document order (a group is declared
    /// before its routes).
    fn walk(&mut self, n: Node, http_client: bool) {
        let src = self.src;
        for c in kids(n) {
            if c.kind() == "variable_declarator" {
                let name = c
                    .child_by_field_name("name")
                    .or_else(|| kids(c).into_iter().find(|x| x.kind() == "identifier"));
                let value = kids(c)
                    .into_iter()
                    .find(|x| x.kind() == "invocation_expression")
                    .or_else(|| {
                        kids(c).into_iter().find(|x| x.kind() == "equals_value_clause").and_then(
                            |e| kids(e).into_iter().find(|x| x.kind() == "invocation_expression"),
                        )
                    });
                if let (Some(name), Some(value)) = (name, value) {
                    let scope = self.home(c.start_byte());
                    let prefix = self.group_prefix(value, scope);
                    if !matches!(prefix.as_str(), "" | "/") || text(value, src).contains("MapGroup")
                    {
                        self.groups.insert((scope, text(name, src)), prefix);
                    }
                }
            }
            if c.kind() == "invocation_expression" {
                self.minimal(c);
                if http_client {
                    self.request(c);
                }
            }
            self.walk(c, http_client);
        }
    }

    fn minimal(&mut self, inv: Node) {
        let src = self.src;
        let Some((name, recv, args, name_node)) = member_call(inv, src) else { return };
        let Some((_, verb)) = MINIMAL_VERBS.iter().find(|(v, _)| *v == name) else { return };
        if args.len() < 2 {
            return;
        }
        let Some(pattern) = args.first().filter(|a| is_literal(**a)).map(|a| string_value(*a, src))
        else {
            return;
        };
        let scope = self.home(inv.start_byte());
        let last = args.last().copied();
        let handler = match last {
            Some(l) if matches!(l.kind(), "identifier" | "member_access_expression") => {
                path_of(l, src).map(Handler::Path).unwrap_or(Handler::Inline)
            }
            Some(l) if l.kind() == "lambda_expression" => {
                single_call(l, src).map_or(Handler::Inline, Handler::Path)
            }
            _ => Handler::Inline,
        };
        let full = join_path(&[&self.group_prefix(recv, scope), &pattern]);
        self.out.routes.push(RouteFact {
            line: line(name_node),
            framework: "aspnet".into(),
            method: verb.to_string(),
            path: PathExpr::lit(full),
            router: None,
            handler,
            from_def: scope,
            needs_server_import: false,
        });
    }

    fn request(&mut self, inv: Node) {
        let src = self.src;
        let Some((name, _, args, _)) = member_call(inv, src) else { return };
        let (method, url) = if name == "SendAsync" {
            // SendAsync(new HttpRequestMessage(HttpMethod.Get, url))
            let Some(msg) = args.first().filter(|a| a.kind() == "object_creation_expression")
            else {
                return;
            };
            let margs: Vec<Node> = msg
                .child_by_field_name("arguments")
                .map(kids)
                .unwrap_or_default()
                .into_iter()
                .filter_map(|a| kids(a).last().copied())
                .collect();
            let m = margs
                .first()
                .map(|m| text(*m, src).rsplit('.').next().unwrap_or("").to_uppercase());
            (m, margs.get(1).copied())
        } else {
            match CLIENT_METHODS.iter().find(|(c, _)| *c == name) {
                Some((_, m)) => (Some(m.to_string()), args.first().copied()),
                None => return,
            }
        };
        let Some(url) = url.filter(|u| {
            is_literal(*u)
                || matches!(u.kind(), "interpolated_string_expression" | "binary_expression")
        }) else {
            return;
        };
        let Some(url) = path_expr(url, src) else { return };
        if url.is_empty() {
            return;
        }
        self.out.calls.push(CallFact {
            line: line(inv),
            method,
            url,
            client: None,
            from_def: self.home(inv.start_byte()),
        });
    }
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut r = Reader { src, facts, groups: HashMap::new(), out: HttpFacts::default() };
    r.controllers(root);
    let http_client = String::from_utf8_lossy(src).contains("HttpClient");
    r.walk(root, http_client);
    r.out
}
