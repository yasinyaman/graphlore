//! Python routes, after `routes.py`'s `_routes_python`: FastAPI / Flask /
//! Sanic / Quart / Litestar verb decorators (`@router.get("/x")`, Flask
//! `@app.route("/x", methods=[…])`), gated on the file importing the framework;
//! Django `path()` / `re_path()` / `url()` in a file importing django. Beyond
//! it: `APIRouter(prefix=…)` and `Blueprint(url_prefix=…)` routers, and
//! `include_router(…, prefix=…)` / `register_blueprint(…, url_prefix=…)` mounts,
//! which resolution joins across files.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use std::collections::HashSet;

use super::{
    path_expr, CallFact, ClientBase, Handler, HttpFacts, MountFact, Mounted, PathExpr, RouteFact,
    RouterFact,
};
use crate::facts::FileFacts;
use crate::generic::{kids, line, text};
use crate::refs::{innermost, path_of, string_value};

const VERB_FRAMEWORKS: &[&str] = &["fastapi", "flask", "sanic", "quart", "litestar"];
/// HTTP client modules; `requests.get(url)` and calls through the objects
/// they make (`httpx.Client(base_url=…)`, `TestClient(app)`) are requests.
const CLIENT_MODULES: &[&str] =
    &["requests", "httpx", "aiohttp", "urllib3", "fastapi.testclient", "starlette.testclient"];
/// What makes a client object.
const CLIENT_MAKERS: &[&str] =
    &["Session", "Client", "AsyncClient", "ClientSession", "TestClient", "PoolManager"];
const METHODS: &[&str] = &["get", "post", "put", "delete", "patch", "options", "head", "trace"];

/// The positional arguments of a call.
fn positional<'t>(call: Node<'t>) -> Vec<Node<'t>> {
    let Some(list) = call.by_field("arguments") else { return Vec::new() };
    kids(list)
        .into_iter()
        .filter(|a| {
            !matches!(
                a.kind_str(),
                "keyword_argument" | "comment" | "list_splat" | "dictionary_splat"
            )
        })
        .collect()
}

fn keyword<'t>(call: Node<'t>, src: &[u8], name: &str) -> Option<Node<'t>> {
    let list = call.by_field("arguments")?;
    kids(list).into_iter().find_map(|a| {
        (a.kind_str() == "keyword_argument"
            && a.by_field("name").is_some_and(|k| text(k, src) == name))
        .then(|| a.by_field("value"))
        .flatten()
    })
}

fn literal(n: Node, src: &[u8]) -> Option<String> {
    let interpolated = kids(n).iter().any(|c| c.kind_str() == "interpolation");
    (n.kind_str() == "string" && !interpolated).then(|| string_value(n, src))
}

pub fn read(root: Node, src: &[u8], facts: &FileFacts) -> HttpFacts {
    let mut out = HttpFacts::default();
    let imported: Vec<String> = facts
        .imports
        .iter()
        .map(|i| i.module.trim_start_matches('.').split('.').next().unwrap_or("").to_string())
        .collect();
    let has = |m: &str| imported.iter().any(|i| i == m);
    let verb_framework = VERB_FRAMEWORKS.iter().find(|m| has(m)).copied();
    let django = has("django");
    let mut calls: Vec<Node> = Vec::new();
    let mut decorated: Vec<Node> = Vec::new();
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        match n.kind_str() {
            "call" => calls.push(n),
            "decorated_definition" => decorated.push(n),
            _ => {}
        }
        stack.extend(kids(n));
    }
    let defs = &facts.defs;
    let home = |b: usize| innermost(defs, &[b])[0];
    clients(src, facts, &calls, &mut out);
    // decorators: @router.get("/x"), @app.route("/x", methods=[...])
    if let Some(fw) = verb_framework {
        for d in decorated {
            let Some(def) = d.by_field("definition") else { continue };
            if def.kind_str() != "function_definition" {
                continue;
            }
            let handler = home(def.start_byte()).map_or(Handler::Inline, Handler::Def);
            for dec in kids(d).into_iter().filter(|c| c.kind_str() == "decorator") {
                let Some(call) = dec.named_child(0).filter(|c| c.kind_str() == "call") else {
                    continue;
                };
                let Some(f) = call.by_field("function").filter(|f| f.kind_str() == "attribute")
                else {
                    continue;
                };
                let (Some(obj), Some(attr)) = (f.by_field("object"), f.by_field("attribute"))
                else {
                    continue;
                };
                let verb = text(attr, src);
                let pos = positional(call);
                let pattern = pos
                    .first()
                    .and_then(|a| literal(*a, src))
                    .or_else(|| keyword(call, src, "path").and_then(|v| literal(v, src)));
                let Some(pattern) = pattern else { continue };
                let router = path_of(obj, src);
                let methods: Vec<String> = if verb == "route" {
                    let declared: Vec<String> = keyword(call, src, "methods")
                        .map(|v| {
                            kids(v)
                                .into_iter()
                                .filter_map(|e| literal(e, src))
                                .map(|m| m.to_uppercase())
                                .collect()
                        })
                        .unwrap_or_default();
                    if declared.is_empty() {
                        vec!["GET".into()]
                    } else {
                        declared
                    }
                } else if METHODS.contains(&verb.as_str()) {
                    vec![verb.to_uppercase()]
                } else {
                    continue;
                };
                let label = if verb == "route" && has("flask") { "flask" } else { fw };
                for m in methods {
                    out.routes.push(RouteFact {
                        line: line(def),
                        framework: label.to_string(),
                        method: m,
                        path: PathExpr::lit(pattern.clone()),
                        router: router.clone(),
                        handler: handler.clone(),
                        from_def: home(dec.start_byte()),
                        needs_server_import: false,
                    });
                }
            }
        }
    }
    for call in calls {
        let Some(f) = call.by_field("function") else { continue };
        let fpath = path_of(f, src).unwrap_or_default();
        let last = fpath.last().map(String::as_str).unwrap_or("");
        let pos = positional(call);
        // Django: path("x/", view)
        if django && f.kind_str() == "identifier" && matches!(last, "path" | "re_path" | "url") {
            if let (Some(p), Some(h)) = (pos.first().and_then(|a| literal(*a, src)), pos.get(1)) {
                let handler = match path_of(*h, src) {
                    Some(hp) if !hp.iter().any(|s| s == "()") => Handler::Path(hp),
                    _ => Handler::Inline,
                };
                out.routes.push(RouteFact {
                    line: line(call),
                    framework: "django".into(),
                    method: "ANY".into(),
                    path: PathExpr::lit(p),
                    router: None,
                    handler,
                    from_def: home(call.start_byte()),
                    needs_server_import: false,
                });
            }
            continue;
        }
        if verb_framework.is_none() {
            continue;
        }
        // routers with a prefix: router = APIRouter(prefix="/users")
        if matches!(last, "APIRouter" | "Blueprint") {
            let key = if last == "APIRouter" { "prefix" } else { "url_prefix" };
            let bound = call
                .parent()
                .filter(|a| a.kind_str() == "assignment")
                .and_then(|a| a.by_field("left"))
                .filter(|l| l.kind_str() == "identifier");
            if let Some(name) = bound {
                let prefix =
                    keyword(call, src, key).and_then(|v| path_expr(v, src)).unwrap_or_default();
                out.routers.push(RouterFact { name: text(name, src), prefix, scope: None });
            }
            continue;
        }
        // mounts: parent.include_router(child, prefix=…)
        if matches!(last, "include_router" | "register_blueprint") && fpath.len() >= 2 {
            let parent = fpath[..fpath.len() - 1].to_vec();
            let Some(child) = pos.first().and_then(|c| path_of(*c, src)) else { continue };
            let key = if last == "include_router" { "prefix" } else { "url_prefix" };
            let prefix =
                keyword(call, src, key).and_then(|v| path_expr(v, src)).unwrap_or_default();
            out.mounts.push(MountFact {
                line: line(call),
                parent,
                child: Mounted::Path(child),
                prefix,
                from_def: home(call.start_byte()),
            });
        }
    }
    out
}

/// Requests through an HTTP client module or an object one made.
fn clients(src: &[u8], facts: &FileFacts, calls: &[Node], out: &mut HttpFacts) {
    let mut names: HashSet<String> = HashSet::new();
    let mut makers: HashSet<String> = HashSet::new();
    for i in &facts.imports {
        if !CLIENT_MODULES.contains(&i.module.as_str()) {
            continue;
        }
        if i.names.is_empty() {
            let bound = i.alias.clone().unwrap_or_else(|| i.module.clone());
            names.insert(bound.split('.').next().unwrap_or("").to_string());
        }
        for (n, a) in &i.names {
            let bound = a.clone().unwrap_or_else(|| n.clone());
            if CLIENT_MAKERS.contains(&n.as_str()) {
                makers.insert(bound);
            } else if matches!(n.as_str(), "get" | "post" | "put" | "delete" | "patch" | "request")
            {
                names.insert(format!("={bound}"));
            }
        }
    }
    if names.is_empty() && makers.is_empty() {
        return;
    }
    // objects a client maker makes: `s = requests.Session()`, `with
    // httpx.Client(base_url=…) as c:`, `self.client = TestClient(app)`
    let made = |call: Node| -> bool {
        let Some(path) = call.by_field("function").and_then(|f| path_of(f, src)) else {
            return false;
        };
        let last = path.last().map(String::as_str).unwrap_or("");
        match path.len() {
            1 => makers.contains(last),
            _ => CLIENT_MAKERS.contains(&last) && names.contains(&path[0]),
        }
    };
    let mut objects: HashSet<String> = HashSet::new();
    for &call in calls {
        if !made(call) {
            continue;
        }
        let Some(parent) = call.parent() else { continue };
        let target = match parent.kind_str() {
            "assignment" => parent.by_field("left"),
            "as_pattern" => parent.by_field("alias").and_then(|a| a.named_child(0).or(Some(a))),
            _ => None,
        };
        let Some(target) = target.and_then(|t| path_of(t, src)) else { continue };
        let Some(name) = target.last().cloned() else { continue };
        objects.insert(name.clone());
        let top = parent.kind_str() == "assignment"
            && target.len() == 1
            && parent
                .parent()
                .is_some_and(|e| e.parent().is_some_and(|m| m.kind_str() == "module"));
        if top {
            let base = keyword(call, src, "base_url").and_then(|v| path_expr(v, src));
            if let Some(base) = base {
                out.bases.push(ClientBase { name, base });
            }
        }
    }
    let defs = &facts.defs;
    for &call in calls {
        let Some(f) = call.by_field("function") else { continue };
        let pos = positional(call);
        // `get(url)` imported from requests
        if f.kind_str() == "identifier" {
            let name = text(f, src);
            if !names.contains(&format!("={name}")) {
                continue;
            }
            let (method, url) = if name == "request" {
                (pos.first().and_then(|m| literal(*m, src)).map(|m| m.to_uppercase()), pos.get(1))
            } else {
                (Some(name.to_uppercase()), pos.first())
            };
            if let Some(url) = url.and_then(|u| path_expr(*u, src)) {
                out.calls.push(CallFact {
                    line: line(call),
                    method,
                    url,
                    client: None,
                    from_def: innermost(defs, &[call.start_byte()])[0],
                });
            }
            continue;
        }
        if f.kind_str() != "attribute" {
            continue;
        }
        let (Some(obj), Some(attr)) = (f.by_field("object"), f.by_field("attribute")) else {
            continue;
        };
        let verb = text(attr, src);
        let Some(recv) = path_of(obj, src).filter(|r| !r.iter().any(|s| s == "()")) else {
            continue;
        };
        let head = recv[0].as_str();
        let last = recv.last().map(String::as_str).unwrap_or("");
        let module = recv.len() == 1 && names.contains(head);
        if !module && !objects.contains(last) {
            continue;
        }
        let (method, url) = match verb.as_str() {
            "request" => (
                pos.first().and_then(|m| literal(*m, src)).map(|m| m.to_uppercase()),
                pos.get(1).copied().or_else(|| keyword(call, src, "url")),
            ),
            v if METHODS.contains(&v) => {
                (Some(v.to_uppercase()), pos.first().copied().or_else(|| keyword(call, src, "url")))
            }
            _ => continue,
        };
        let Some(url) = url.and_then(|u| path_expr(u, src)) else { continue };
        out.calls.push(CallFact {
            line: line(call),
            method,
            url,
            client: (!module).then(|| recv.clone()),
            from_def: innermost(defs, &[call.start_byte()])[0],
        });
    }
}
