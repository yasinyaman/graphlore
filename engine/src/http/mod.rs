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

pub mod csharp;
pub mod ecma;
pub mod go;
pub mod jvm;
pub mod php;
pub mod python;
pub mod ruby;
pub mod rust;

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
    /// A method of a class named as text (Rails `"articles#index"` →
    /// `ArticlesController#index`, Laravel `"UserController@store"`): the
    /// namespace segments, the class, the method.
    Class(Vec<String>, String, String),
}

/// A router path naming the definition the registration is in: routes chained
/// on `Router::new()` in a function that returns it (axum), which a `nest`
/// elsewhere mounts.
pub const SELF_ROUTER: &str = "<self>";
/// A router path `[PARAM_ROUTER, "N"]`: the function's `N`th parameter (Go
/// `func addUserRoutes(rg *gin.RouterGroup)`), which each call mounts.
pub const PARAM_ROUTER: &str = "<param>";

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
    /// The name it is bound to.
    pub name: String,
    pub prefix: PathExpr,
    /// The definition a local router is bound in (`v1 := router.Group("/v1")`);
    /// `None` for a top-level one.
    #[serde(default)]
    pub scope: Option<usize>,
}

/// What is mounted.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Mounted {
    /// A router named in the source (`users.router`, `apiRouter`).
    Path(Vec<String>),
    /// A module required in place (`require('./routes/users')`).
    Module(String),
    /// The `N`th parameter of the function called (`addUserRoutes(v1)`).
    Param(Vec<String>, usize),
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
    /// Only the routes of this file, by its path's end (Laravel
    /// `Route::prefix('api')->group(base_path('routes/api.php'))`).
    #[serde(default)]
    pub file: Option<String>,
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
        Lang::Java => jvm::read_java(root, src, facts),
        Lang::Kotlin => jvm::read_kotlin(root, src, facts),
        Lang::CSharp => csharp::read(root, src, facts),
        Lang::Go => go::read(root, src, facts),
        Lang::Rust => rust::read(root, src, facts),
        Lang::Ruby => ruby::read(root, src, facts),
        Lang::Php => php::read(root, src, facts),
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
        let interpolated = pieces.iter().any(|c| hole_kind(c.kind()) || delimiter_kind(c.kind()));
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
            let hole = hole_kind(ck);
            if !hole && !delimiter_kind(ck) {
                continue;
            }
            if c.start_byte() > last {
                out.push(Part::Lit(
                    String::from_utf8_lossy(&src[last..c.start_byte()]).to_string(),
                ));
            }
            if hole {
                // Kotlin `$name` is the name itself; elsewhere the expression inside
                let path = if ck == "interpolated_identifier" {
                    Some(vec![text(c, src)])
                } else {
                    // past the braces C# names (`interpolation_brace`)
                    kids(c)
                        .into_iter()
                        .find(|e| !e.kind().contains("brace") && !delimiter_kind(e.kind()))
                        .and_then(|e| path_of(e, src))
                };
                match path {
                    Some(p) if !p.iter().any(|s| s == "()") => out.push(Part::Name(p)),
                    _ => out.push(Part::Hole),
                }
            }
            // a mark before the opening quote (C# `$`) leaves the start where it is
            last = last.max(c.end_byte());
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

/// A piece of an interpolated string that holds a value: `${x}`, `{x}`, `$x`.
fn hole_kind(k: &str) -> bool {
    matches!(
        k,
        "interpolation"
            | "template_substitution"
            | "interpolated_expression"
            | "interpolated_identifier"
    )
}

/// The marks around a hole that some grammars name (Kotlin `${`, `}`, `$`).
fn delimiter_kind(k: &str) -> bool {
    k.starts_with("interpolation_") && (k.ends_with("_start") || k.ends_with("_end"))
}

/// The callee of a function written in a registration whose body is one call:
/// `post { controller.register(ctx) }`, `() => svc.List()`, `(req, res) =>
/// ctrl.show(req, res)`. That call's target handles the route.
pub fn single_call(f: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut n = f.child_by_field_name("body").unwrap_or(f);
    for _ in 0..6 {
        let k = n.kind();
        if crate::refs::is_call_kind(k) {
            let (_, path) = crate::refs::callee(n, src)?;
            let ok = !path.is_empty() && path.len() <= 3 && !path.iter().any(|s| s == "()");
            return ok.then_some(path);
        }
        let inner: Vec<Node> =
            kids(n).into_iter().filter(|c| !c.kind().contains("comment")).collect();
        n = match k {
            "annotated_lambda"
            | "lambda_literal"
            | "statements"
            | "statement_list"
            | "statement_block"
            | "block"
            | "function_body"
            | "expression_statement"
            | "return_statement"
            | "await_expression"
            | "arrow_expression_clause"
            | "parenthesized_expression"
                if inner.len() == 1 =>
            {
                inner[0]
            }
            // a lambda's parameters before its body
            "lambda_literal" | "arrow_function" | "lambda_expression" => *inner.last()?,
            _ => return None,
        };
    }
    None
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

    /// The first string node of a parsed snippet, as a path expression.
    fn first_string(lang: Lang, code: &str) -> PathExpr {
        let tree = crate::parse::parse(lang, code.as_bytes(), None).unwrap();
        let mut stack = vec![tree.root_node()];
        while let Some(n) = stack.pop() {
            if n.kind().contains("string") {
                return path_expr(n, code.as_bytes()).unwrap();
            }
            stack.extend(kids(n).into_iter().rev());
        }
        panic!("no string in {code}");
    }

    #[test]
    fn interpolated_strings_keep_their_names() {
        let name = |s: &str| Part::Name(vec![s.to_string()]);
        let lit = |s: &str| Part::Lit(s.to_string());
        assert_eq!(
            first_string(Lang::Kotlin, "val u = \"$base/users/${id}\"\n").0,
            vec![name("base"), lit("/users/"), name("id")]
        );
        assert_eq!(
            first_string(Lang::CSharp, "class A { string U = $\"todos/{id}\"; }\n").0,
            vec![lit("todos/"), name("id")]
        );
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
