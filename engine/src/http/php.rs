//! PHP routes, after `routes.py`'s `_routes_php`: Laravel's `Route` facade
//! (`Route::get('/x', [C::class, 'm'])`, `Route::prefix('api')->group(…)`,
//! `Route::group(['prefix' => …], …)`, `match`, `resource` / `apiResource` with
//! `only` / `except`) and Symfony `#[Route]` on a controller class and its
//! methods (in a file using Symfony's `Route`). Beyond it: a group of a route
//! file (`->group(base_path('routes/api.php'))` in `RouteServiceProvider`,
//! Laravel 11's `withRouting(api: …)`) prefixes that file's routes; handlers
//! `[UserController::class, 'store']` and `'UserController@store'` are the
//! controller's method.
//!
//! The requests: Guzzle `$client->request('GET', '/x')` / `$client->get('/x')`
//! in a file using Guzzle, Laravel `Http::get(url)`, and in tests
//! `$this->getJson('/api/x')`, `$this->get('/x')`, `$this->json('POST', '/x')`.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use super::{
    join_path, path_expr, CallFact, GlobalPrefix, Handler, HttpFacts, Part, PathExpr, RouteFact,
};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, string_value};

const VERBS: &[&str] = &["get", "post", "put", "patch", "delete", "options", "any"];
const REST: &[(&str, &str, &str)] = &[
    ("GET", "", "index"),
    ("GET", "create", "create"),
    ("POST", "", "store"),
    ("GET", "{id}", "show"),
    ("GET", "{id}/edit", "edit"),
    ("PUT", "{id}", "update"),
    ("PATCH", "{id}", "update"),
    ("DELETE", "{id}", "destroy"),
];

fn lit(n: Option<Node>, src: &[u8]) -> Option<String> {
    let n = n?;
    let plain = n.kind_str() == "string"
        || (n.kind_str() == "encapsed_string"
            && kids(n).iter().all(|c| c.kind_str() == "string_content"));
    plain.then(|| string_value(n, src))
}

/// The first string literal at or under `n`, breadth first.
fn first_lit(n: Node, src: &[u8]) -> Option<String> {
    let mut queue = std::collections::VecDeque::from([n]);
    while let Some(c) = queue.pop_front() {
        if let Some(v) = lit(Some(c), src) {
            return Some(v);
        }
        queue.extend(kids(c));
    }
    None
}

/// Positional and named arguments.
fn args<'t>(a: Option<Node<'t>>, src: &[u8]) -> (Vec<Node<'t>>, Vec<(String, Node<'t>)>) {
    let (mut pos, mut named) = (Vec::new(), Vec::new());
    for arg in a.map(kids).unwrap_or_default().into_iter().filter(|c| c.kind_str() == "argument") {
        let key = arg.by_field("name");
        let Some(v) = kids(arg).into_iter().find(|c| Some(*c) != key) else { continue };
        match key {
            Some(k) => named.push((text(k, src), v)),
            None => pos.push(v),
        }
    }
    (pos, named)
}

/// `['GET', 'POST']` / `'GET'` → its strings.
fn strings(n: Option<Node>, src: &[u8]) -> Vec<String> {
    let Some(n) = n else { return Vec::new() };
    if n.kind_str() == "array_creation_expression" {
        return kids(n).into_iter().filter_map(|e| first_lit(e, src)).collect();
    }
    lit(Some(n), src).into_iter().collect()
}

/// The value under `'key'` in an array literal.
fn array_get<'t>(n: Option<Node<'t>>, key: &str, src: &[u8]) -> Option<Node<'t>> {
    let n = n.filter(|n| n.kind_str() == "array_creation_expression")?;
    kids(n).into_iter().find_map(|e| {
        let ks = kids(e);
        (ks.len() == 2 && lit(Some(ks[0]), src).as_deref() == Some(key)).then_some(ks[1])
    })
}

fn class_name(n: Node, src: &[u8]) -> Option<(Vec<String>, String)> {
    let t = text(n, src);
    let t = t.trim_end_matches("::class").trim_start_matches('\\');
    let mut parts: Vec<String> = t.split('\\').map(str::to_string).collect();
    let last = parts.pop().filter(|l| !l.is_empty())?;
    Some((parts, last))
}

/// `[C::class, 'm']`, `'C@m'`, `C::class` (invokable) → the method.
fn handler(n: Option<Node>, action: Option<&str>, src: &[u8]) -> Handler {
    let Some(n) = n else { return Handler::Inline };
    match n.kind_str() {
        "array_creation_expression" => {
            let parts: Vec<Node> =
                kids(n).into_iter().filter_map(|e| kids(e).first().copied()).collect();
            if let [c, m] = parts.as_slice() {
                if let (Some((ns, class)), Some(method)) = (class_name(*c, src), lit(Some(*m), src))
                {
                    return Handler::Class(ns, class, method);
                }
            }
            Handler::Inline
        }
        "class_constant_access_expression" => match class_name(n, src) {
            Some((ns, class)) => {
                Handler::Class(ns, class, action.unwrap_or("__invoke").to_string())
            }
            None => Handler::Inline,
        },
        _ => match lit(Some(n), src)
            .and_then(|s| s.split_once('@').map(|(c, m)| (c.to_string(), m.to_string())))
        {
            Some((c, m)) => {
                let mut parts: Vec<String> = c.split('\\').map(str::to_string).collect();
                let class = parts.pop().unwrap_or_default();
                Handler::Class(parts, class, m)
            }
            None => Handler::Inline,
        },
    }
}

/// A route file a group names: `base_path('routes/api.php')`,
/// `__DIR__.'/../routes/api.php'` → `routes/api.php`.
fn route_file(n: Node, src: &[u8]) -> Option<String> {
    let mut found = None;
    let mut stack = vec![n];
    while let Some(c) = stack.pop() {
        if let Some(v) = lit(Some(c), src) {
            if v.ends_with(".php") {
                found = Some(v);
            }
        }
        stack.extend(kids(c));
    }
    let f = found?;
    let kept: Vec<&str> =
        f.split('/').filter(|s| !s.is_empty() && *s != "." && *s != "..").collect();
    Some(kept.join("/"))
}

struct Link<'t> {
    name: String,
    args: Option<Node<'t>>,
    at: Node<'t>,
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

    fn route(&mut self, at: Node, fw: &str, method: &str, path: String, h: Handler) {
        self.out.routes.push(RouteFact {
            line: line(at),
            framework: fw.to_string(),
            method: method.to_uppercase(),
            path: PathExpr::lit(path),
            router: None,
            handler: h,
            from_def: self.home(at.start_byte()),
            needs_server_import: false,
        });
    }

    /// The links of a call chain that starts at `Route::…`, root first.
    fn chain<'t>(&self, mut n: Node<'t>) -> Option<Vec<Link<'t>>> {
        let src = self.src;
        let mut links = Vec::new();
        while n.kind_str() == "member_call_expression" {
            let name = n.by_field("name")?;
            links.push(Link { name: text(name, src), args: n.by_field("arguments"), at: name });
            n = n.by_field("object")?;
        }
        if n.kind_str() != "scoped_call_expression" {
            return None;
        }
        let scope = text(n.by_field("scope")?, src);
        if scope.trim_start_matches('\\').rsplit('\\').next() != Some("Route") {
            return None;
        }
        let name = n.by_field("name")?;
        links.push(Link { name: text(name, src), args: n.by_field("arguments"), at: name });
        links.reverse();
        Some(links)
    }

    fn laravel(&mut self, n: Node, prefix: &str) {
        if matches!(n.kind_str(), "member_call_expression" | "scoped_call_expression") {
            if let Some(links) = self.chain(n) {
                self.laravel_chain(&links, prefix);
                return;
            }
        }
        for c in kids(n) {
            self.laravel(c, prefix);
        }
    }

    fn laravel_chain(&mut self, links: &[Link], prefix: &str) {
        let src = self.src;
        let mut pre = prefix.to_string();
        let mut only = Vec::new();
        let mut except = Vec::new();
        for l in links {
            let first = args(l.args, src).0.first().copied();
            match l.name.as_str() {
                "only" => only.extend(strings(first, src)),
                "except" => except.extend(strings(first, src)),
                _ => {}
            }
        }
        for l in links {
            let (pos, _) = args(l.args, src);
            let first = lit(pos.first().copied(), src);
            match l.name.as_str() {
                "prefix" if first.is_some() => pre = join_path(&[&pre, &first.unwrap_or_default()]),
                "group" => {
                    let mut inner = pre.clone();
                    for a in &pos {
                        if let Some(p) = lit(array_get(Some(*a), "prefix", src), src) {
                            inner = join_path(&[&inner, &p]);
                        }
                    }
                    for a in &pos {
                        if a.kind_str() == "array_creation_expression" {
                            continue;
                        }
                        // a route file: its routes take the prefix
                        if let Some(file) = route_file(*a, src).filter(|_| {
                            !matches!(
                                a.kind_str(),
                                "anonymous_function"
                                    | "arrow_function"
                                    | "anonymous_function_creation_expression"
                            )
                        }) {
                            self.out.prefixes.push(GlobalPrefix {
                                framework: "laravel".into(),
                                prefix: PathExpr::lit(inner.clone()),
                                file: Some(file),
                            });
                            continue;
                        }
                        self.laravel(*a, &inner);
                    }
                }
                v if VERBS.contains(&v) && first.is_some() && pos.len() >= 2 => {
                    let method = if v == "any" { "ANY" } else { v };
                    let h = handler(pos.get(1).copied(), None, src);
                    self.route(
                        l.at,
                        "laravel",
                        method,
                        join_path(&[&pre, &first.unwrap_or_default()]),
                        h,
                    );
                }
                "match" if pos.len() >= 3 && lit(pos.get(1).copied(), src).is_some() => {
                    let path =
                        join_path(&[&pre, &lit(pos.get(1).copied(), src).unwrap_or_default()]);
                    for verb in strings(pos.first().copied(), src) {
                        let h = handler(pos.get(2).copied(), None, src);
                        self.route(l.at, "laravel", &verb, path.clone(), h);
                    }
                }
                "resource" | "apiResource" if first.is_some() && pos.len() >= 2 => {
                    let opts = pos.get(2).copied();
                    let mut keep = only.clone();
                    keep.extend(strings(array_get(opts, "only", src), src));
                    let mut drop = except.clone();
                    drop.extend(strings(array_get(opts, "except", src), src));
                    let base = join_path(&[&pre, &first.unwrap_or_default()]);
                    for (method, sub, action) in REST {
                        if l.name == "apiResource" && matches!(*action, "create" | "edit") {
                            continue;
                        }
                        if (!keep.is_empty() && !keep.iter().any(|k| k == action))
                            || drop.iter().any(|d| d == action)
                        {
                            continue;
                        }
                        let h = handler(pos.get(1).copied(), Some(action), src);
                        self.route(l.at, "laravel", method, join_path(&[&base, sub]), h);
                    }
                }
                _ => {}
            }
        }
    }

    /// Laravel 11: `->withRouting(api: __DIR__.'/../routes/api.php', apiPrefix: 'api')`.
    fn with_routing(&mut self, root: Node) {
        let src = self.src;
        let mut stack = vec![root];
        while let Some(n) = stack.pop() {
            stack.extend(kids(n));
            if n.kind_str() != "member_call_expression"
                || n.by_field("name").map(|x| text(x, src)).as_deref() != Some("withRouting")
            {
                continue;
            }
            let (_, named) = args(n.by_field("arguments"), src);
            let Some(api) = named.iter().find(|(k, _)| k == "api").map(|(_, v)| *v) else {
                continue;
            };
            let prefix = named
                .iter()
                .find(|(k, _)| k == "apiPrefix")
                .and_then(|(_, v)| lit(Some(*v), src))
                .unwrap_or_else(|| "api".into());
            if let Some(file) = route_file(api, src) {
                self.out.prefixes.push(GlobalPrefix {
                    framework: "laravel".into(),
                    prefix: PathExpr::lit(prefix),
                    file: Some(file),
                });
            }
        }
    }

    fn symfony(&mut self, root: Node) {
        let src = self.src;
        let route_attrs = |decl: Node| -> Vec<(String, Vec<String>)> {
            let mut out = Vec::new();
            for al in kids(decl).into_iter().filter(|c| c.kind_str() == "attribute_list") {
                for g in kids(al).into_iter().filter(|c| c.kind_str() == "attribute_group") {
                    for a in kids(g).into_iter().filter(|c| c.kind_str() == "attribute") {
                        let Some(name) = kids(a)
                            .into_iter()
                            .find(|c| matches!(c.kind_str(), "name" | "qualified_name"))
                        else {
                            continue;
                        };
                        if text(name, src).rsplit('\\').next() != Some("Route") {
                            continue;
                        }
                        let list = a
                            .by_field("parameters")
                            .or_else(|| kids(a).into_iter().find(|c| c.kind_str() == "arguments"));
                        let (pos, named) = args(list, src);
                        let path = match named.iter().find(|(k, _)| k == "path") {
                            Some((_, v)) => lit(Some(*v), src),
                            None => lit(pos.first().copied(), src),
                        };
                        let methods = named
                            .iter()
                            .find(|(k, _)| k == "methods")
                            .map(|(_, v)| strings(Some(*v), src))
                            .filter(|m| !m.is_empty())
                            .unwrap_or_else(|| vec!["ANY".into()]);
                        out.push((path.unwrap_or_default(), methods));
                    }
                }
            }
            out
        };
        let mut stack = vec![root];
        while let Some(cls) = stack.pop() {
            stack.extend(kids(cls));
            if cls.kind_str() != "class_declaration" {
                continue;
            }
            let prefix = route_attrs(cls).into_iter().next().map(|(p, _)| p).unwrap_or_default();
            let Some(body) = cls.by_field("body") else { continue };
            for m in kids(body).into_iter().filter(|c| c.kind_str() == "method_declaration") {
                let at = m.by_field("name").unwrap_or(m);
                let h = self.home(m.start_byte()).map_or(Handler::Inline, Handler::Def);
                for (path, methods) in route_attrs(m) {
                    for meth in methods {
                        self.route(at, "symfony", &meth, join_path(&[&prefix, &path]), h.clone());
                    }
                }
            }
        }
    }

    fn requests(&mut self, root: Node, test: bool) {
        let src = self.src;
        let guzzle = String::from_utf8_lossy(src).contains("GuzzleHttp");
        let mut stack = vec![root];
        while let Some(n) = stack.pop() {
            stack.extend(kids(n));
            let (name, recv) = match n.kind_str() {
                "member_call_expression" => (n.by_field("name"), n.by_field("object")),
                "scoped_call_expression" => (n.by_field("name"), n.by_field("scope")),
                _ => continue,
            };
            let (Some(name), Some(recv)) = (name, recv) else { continue };
            let name = text(name, src);
            let recv_text = text(recv, src);
            let (pos, _) = args(n.by_field("arguments"), src);
            let through = recv_text == "Http"
                || recv_text.starts_with("Http::")
                || (guzzle && recv_text.to_lowercase().contains("client"))
                || (test && (recv_text == "$this" || recv_text.to_lowercase().contains("client")));
            if !through {
                continue;
            }
            let lower = name.to_lowercase();
            let (method, url) = match lower.as_str() {
                "request" | "json" => {
                    (lit(pos.first().copied(), src).map(|m| m.to_uppercase()), pos.get(1))
                }
                v => match v.strip_suffix("json").unwrap_or(v) {
                    m @ ("get" | "post" | "put" | "patch" | "delete") => {
                        (Some(m.to_uppercase()), pos.first())
                    }
                    _ => continue,
                },
            };
            let Some(url) = url.and_then(|u| path_expr(*u, src)) else { continue };
            if !url.0.iter().any(|p| matches!(p, Part::Lit(_))) {
                continue;
            }
            self.out.calls.push(CallFact {
                line: line(n),
                method,
                url,
                client: None,
                from_def: self.home(n.start_byte()),
            });
        }
    }
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut r = Reader { src, facts, out: HttpFacts::default() };
    r.laravel(root, "");
    r.with_routing(root);
    let symfony = facts.imports.iter().any(|i| {
        let full = format!(
            "{}\\{}",
            i.module,
            i.names.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>().join(",")
        );
        full.contains("Symfony") && full.contains("Route")
    });
    if symfony {
        r.symfony(root);
    }
    r.requests(root, crate::community::is_test_path(&facts.path));
    r.out
}
