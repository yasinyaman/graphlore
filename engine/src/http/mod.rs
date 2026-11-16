//! Web routes and the HTTP calls that reach them.
//!
//! Per file, a framework module reads what the source declares (`python`,
//! `ecma`, …, following graphlore's `routes.py`): routes (method, path, the
//! router they are registered on, the handler), routers made with a prefix
//! (`APIRouter(prefix="/users")`, `Blueprint(url_prefix=…)`, `express.Router()`),
//! mounts of one router under another (`include_router`, `register_blueprint`,
//! `app.use('/api', router)`) and global prefixes (`app.setGlobalPrefix('api')`).
//! Resolution (`resolve::http`) then joins them across files, so a route reads
//! `/api/v1/users/{user_id}` rather than the `/{user_id}` its decorator says,
//! and links each route to its handler.

pub mod ecma;
pub mod python;

use serde::{Deserialize, Serialize};
use tree_sitter::Node;

use crate::facts::FileFacts;
use crate::generic::{kids, text};
use crate::lang::Lang;
use crate::refs::{path_of, string_value};

/// A piece of a path as the source writes it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Part {
    /// Literal text: `/users`.
    Lit(String),
    /// A name whose value is the text: `settings.API_V1_STR`.
    Name(Vec<String>),
    /// Something computed the walk cannot know (`${id}` in a client's URL).
    Hole,
}

/// A path written as literal text, names and holes, in order.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PathExpr(pub Vec<Part>);

impl PathExpr {
    pub fn lit(s: impl Into<String>) -> Self {
        PathExpr(vec![Part::Lit(s.into())])
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

/// Who handles a route.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Handler {
    /// A definition of this file (a decorated function, a controller method).
    Def(usize),
    /// A name to resolve (`users.login`, `views.index`).
    Path(Vec<String>),
    /// A function written in the registration: the definition around it.
    Inline,
}

/// One route as a file declares it.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouteFact {
    pub line: u32,
    pub framework: String,
    /// Upper-case, or `ANY`.
    pub method: String,
    pub path: PathExpr,
    /// The object it is registered on (`router`, `app`); `None` when the path
    /// is complete where it is written (a NestJS controller, Django).
    pub router: Option<Vec<String>>,
    pub handler: Handler,
    /// The innermost definition around the registration.
    pub from_def: Option<usize>,
    /// Registered on a receiver named like a router in a file importing no
    /// server framework: a route only if some file of the project imports one.
    pub needs_server_import: bool,
}

/// A router made with a prefix of its own.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouterFact {
    /// The top-level name it is bound to.
    pub name: String,
    pub prefix: PathExpr,
}

/// What is mounted.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Mounted {
    /// A router named in the source (`users.router`, `apiRouter`).
    Path(Vec<String>),
    /// A module required in place (`require('./routes/users')`).
    Module(String),
}

/// One router mounted under another, with a prefix.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MountFact {
    pub line: u32,
    pub parent: Vec<String>,
    pub child: Mounted,
    pub prefix: PathExpr,
    pub from_def: Option<usize>,
}

/// A prefix applied to every route of a framework under the file's directory
/// (NestJS `app.setGlobalPrefix('api')`).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GlobalPrefix {
    pub framework: String,
    pub prefix: PathExpr,
}

/// An HTTP request the code makes: `fetch('/api/users')`,
/// `axios.post(url)`, `client.get({ url: '/api/v1/items/' })`, `requests.get(…)`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CallFact {
    pub line: u32,
    /// Upper-case; `None` when the source does not say.
    pub method: Option<String>,
    pub url: PathExpr,
    /// The top-level name of the client object it is made through, whose base
    /// URL (`axios.create({ baseURL })`, `httpx.Client(base_url=…)`) comes first.
    pub client: Option<Vec<String>>,
    pub from_def: Option<usize>,
}

/// A client object made with a base URL, bound to a top-level name.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientBase {
    pub name: String,
    pub base: PathExpr,
}

/// What a file declares about routes, and the requests it makes.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct HttpFacts {
    pub routes: Vec<RouteFact>,
    pub routers: Vec<RouterFact>,
    pub mounts: Vec<MountFact>,
    pub prefixes: Vec<GlobalPrefix>,
    pub calls: Vec<CallFact>,
    pub bases: Vec<ClientBase>,
    /// The file imports a JS server framework (see `needs_server_import`).
    pub server_import: bool,
}

/// Read a file's routes, after its definitions and references.
pub fn read(lang: Lang, root: Node, src: &[u8], facts: &mut FileFacts) {
    let http = match lang {
        Lang::Python => python::read(root, src, facts),
        Lang::JavaScript | Lang::TypeScript | Lang::Tsx => ecma::read(root, src, facts),
        _ => return,
    };
    facts.http = http;
}

/// `routes.py`'s `_join_path`: the parts joined by single slashes, rooted.
pub fn join_path(parts: &[&str]) -> String {
    let kept: Vec<&str> =
        parts.iter().map(|p| p.trim_matches('/')).filter(|p| !p.is_empty()).collect();
    format!("/{}", kept.join("/"))
}

/// The pieces of a path expression: a string literal (with interpolations
/// holding names or holes), a name, or a `+` concatenation of those.
pub fn path_expr(n: Node, src: &[u8]) -> Option<PathExpr> {
    let mut out = Vec::new();
    collect(n, src, &mut out, 0)?;
    Some(PathExpr(out))
}

fn collect(n: Node, src: &[u8], out: &mut Vec<Part>, depth: usize) -> Option<()> {
    if depth > 6 {
        return None;
    }
    let k = n.kind();
    if k == "binary_expression" || k == "binary_operator" {
        let op = n.child_by_field_name("operator").map(|o| text(o, src)).unwrap_or_default();
        if op != "+" {
            return None;
        }
        collect(n.child_by_field_name("left")?, src, out, depth + 1)?;
        return collect(n.child_by_field_name("right")?, src, out, depth + 1);
    }
    if k == "parenthesized_expression" {
        return collect(n.named_child(0)?, src, out, depth + 1);
    }
    if k.contains("string") || k == "template_string" {
        let pieces = kids(n);
        let interpolated = pieces.iter().any(|c| {
            let ck = c.kind();
            ck.contains("interpolation") || ck == "template_substitution"
        });
        if !interpolated {
            out.push(Part::Lit(string_value(n, src)));
            return Some(());
        }
        // `f"{settings.API_V1_STR}/users"`, `` `/users/${id}` ``: the text
        // between the holes, and each hole a name or unknown
        let body = text(n, src);
        let start = n.start_byte();
        let mut last = start;
        let open = body.find(['"', '\'', '`']).map_or(0, |i| i + 1);
        last += open;
        for c in pieces {
            let ck = c.kind();
            if !(ck.contains("interpolation") || ck == "template_substitution") {
                continue;
            }
            if c.start_byte() > last {
                out.push(Part::Lit(
                    String::from_utf8_lossy(&src[last..c.start_byte()]).to_string(),
                ));
            }
            let inner = c.named_child(0);
            match inner.and_then(|e| path_of(e, src)) {
                Some(p) if !p.iter().any(|s| s == "()") => out.push(Part::Name(p)),
                _ => out.push(Part::Hole),
            }
            last = c.end_byte();
        }
        let end = n.end_byte().saturating_sub(1).max(last);
        if end > last {
            let tail = String::from_utf8_lossy(&src[last..end]).to_string();
            let tail = tail.trim_end_matches(['"', '\'', '`']).to_string();
            if !tail.is_empty() {
                out.push(Part::Lit(tail));
            }
        }
        return Some(());
    }
    match path_of(n, src) {
        Some(p) if !p.iter().any(|s| s == "()") => {
            out.push(Part::Name(p));
            Some(())
        }
        _ => None,
    }
}

/// A route pattern or a URL with every parameter written `{}`: `{id}`,
/// `{id:int}`, `:id`, `<id>`, `<int:id>`, `[id]`, `*`, `{*path}`; the query and a
/// trailing slash dropped. `/users/:id/` and `/users/{user_id}` are both
/// `/users/{}`.
pub fn normalize(pattern: &str) -> String {
    let path = pattern.split(['?', '#']).next().unwrap_or("");
    let segs: Vec<String> = path
        .split('/')
        .filter(|s| !s.is_empty())
        .map(|s| {
            let param = (s.starts_with('{') && s.ends_with('}'))
                || s.starts_with(':')
                || (s.starts_with('<') && s.ends_with('>'))
                || (s.starts_with('[') && s.ends_with(']'))
                || s == "*"
                || s == "**";
            if param {
                "{}".to_string()
            } else {
                s.to_string()
            }
        })
        .collect();
    format!("/{}", segs.join("/"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_path_follows_routes_py() {
        assert_eq!(join_path(&["/api/v1", "/users", "/{id}"]), "/api/v1/users/{id}");
        assert_eq!(join_path(&["", "/"]), "/");
        assert_eq!(join_path(&["api", "users/"]), "/api/users");
    }

    #[test]
    fn parameters_normalize_to_one_form() {
        for p in
            ["/users/{user_id}", "/users/:id/", "/users/<int:id>", "/users/[id]", "/users/{id:int}"]
        {
            assert_eq!(normalize(p), "/users/{}", "{p}");
        }
        assert_eq!(normalize("/owners?page=1"), "/owners");
        assert_eq!(normalize("/"), "/");
    }
}
