//! Routes joined across files: the full path of each route (the prefixes of
//! the routers it is mounted under, up to the application, and a global
//! prefix), its node and the `handles` edge to its handler.

use std::collections::{HashMap, HashSet};

use super::{modules, Confidence, DefRef, Family, ModRes, Project, Target};
use crate::facts::{FileFacts, ImportForm};
use crate::graph::ids::slug;
use crate::http::{join_path, Handler, Mounted, Part, PathExpr, PARAM_ROUTER, SELF_ROUTER};

/// A router: a definition the resolver found (`router = APIRouter(…)`), or an
/// object only a file names (an `app` parameter).
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Key {
    Def(DefRef),
    Obj(usize, String),
    /// A router bound in a definition: `v1 := router.Group("/v1")`.
    Local(usize, usize, String),
    /// A function's parameter that routes are registered on.
    Param(DefRef, usize),
}

/// A route node to write.
pub(super) struct RouteNode {
    pub id: String,
    pub label: String,
    pub method: String,
    pub pattern: String,
    pub key: String,
    pub framework: String,
    pub file: usize,
    pub line: u32,
    /// Every handler registered for it in its file, in order.
    pub handlers: Vec<String>,
    /// Part of a prefix named something whose value is unknown.
    pub unresolved: bool,
}

struct Mount {
    parent: Key,
    prefix: PathExpr,
    file: usize,
    from_def: Option<usize>,
}

const MAX_DEPTH: usize = 8;

/// The value of a path expression in file `f`: its text, and whether a part of
/// it could not be known.
fn value(p: &Project, f: usize, d: Option<usize>, e: &PathExpr) -> (String, bool) {
    let mut out = String::new();
    let mut unresolved = false;
    for part in &e.0 {
        match part {
            Part::Lit(s) => out.push_str(s),
            Part::Name(path) => match p.resolve_path(f, d, path, true) {
                Some((Target::Def(t), _)) if p.def(t).value.is_some() => {
                    out.push_str(p.def(t).value.as_deref().unwrap_or_default());
                }
                // unknown: written as a parameter named for it
                _ => {
                    unresolved = true;
                    out.push_str(&format!("{{{}}}", path.last().map_or("", String::as_str)));
                }
            },
            Part::Hole => {
                unresolved = true;
                out.push_str("{}");
            }
        }
    }
    (out, unresolved)
}

fn key_of(p: &Project, f: usize, d: Option<usize>, path: &[String]) -> Key {
    match p.resolve_path(f, d, path, true) {
        Some((Target::Def(t), _)) => Key::Def(t),
        _ => Key::Obj(f, path.join(".")),
    }
}

/// The router a registration's receiver names in file `f`, inside `d`: the
/// definition itself, a parameter of it, a router bound in it or around it,
/// or what resolution makes of the name.
fn router_key(
    p: &Project,
    files: &[FileFacts],
    f: usize,
    d: Option<usize>,
    path: &[String],
) -> Key {
    match path {
        [s] if s == SELF_ROUTER => {
            return d.map_or_else(|| Key::Obj(f, s.clone()), |d| Key::Def((f, d)));
        }
        [s, n] if s == PARAM_ROUTER => {
            if let (Some(d), Ok(i)) = (d, n.parse::<usize>()) {
                return Key::Param((f, d), i);
            }
        }
        [name] => {
            let mut cur = d;
            while let Some(x) = cur {
                if files[f].http.routers.iter().any(|r| r.scope == Some(x) && r.name == *name) {
                    return Key::Local(f, x, name.clone());
                }
                cur = files[f].defs[x].parent;
            }
        }
        _ => {}
    }
    key_of(p, f, d, path)
}

/// The routers a mounted module holds: those it declares and those its
/// routes are registered on.
fn module_keys(p: &Project, files: &[FileFacts], g: usize) -> Vec<Key> {
    let mut out: Vec<Key> = Vec::new();
    for r in &files[g].http.routers {
        if r.scope.is_none() {
            out.push(top_key(p, files, g, &r.name));
        }
    }
    for r in &files[g].http.routes {
        if let Some(path) = &r.router {
            out.push(router_key(p, files, g, r.from_def, path));
        }
    }
    out.sort_by_key(|k| format!("{k:?}"));
    out.dedup();
    out
}

/// The key of a router a file binds at its top level.
fn top_key(p: &Project, files: &[FileFacts], f: usize, name: &str) -> Key {
    files[f]
        .defs
        .iter()
        .position(|d| d.parent.is_none() && d.name == name && d.kind.is_state())
        .map(|i| Key::Def((f, i)))
        .unwrap_or_else(|| key_of(p, f, None, &[name.to_string()]))
}

pub(super) fn compose(p: &Project, files: &[FileFacts]) -> Vec<RouteNode> {
    // the framework's own repository: a file takes it by a relative path
    // (`require('../../')` in express's examples), which names the directory
    // whose package.json is the framework's
    let own: Vec<&str> = p
        .modsys
        .js_packages
        .iter()
        .filter(|(name, _)| crate::http::ecma::SERVER_FRAMEWORKS.contains(&name.as_str()))
        .map(|(_, dir)| dir.trim_end_matches('/'))
        .collect();
    let takes_own = |f: &FileFacts| {
        !own.is_empty()
            && f.imports.iter().any(|i| {
                i.module.starts_with('.')
                    && super::normalize(&format!("{}/{}", super::dir_of(&f.path), i.module))
                        .is_some_and(|t| own.contains(&t.as_str()))
            })
    };
    let any_server = files.iter().any(|f| f.http.server_import || takes_own(f));
    // own prefixes and mounts, by router
    let mut own: HashMap<Key, (usize, PathExpr)> = HashMap::new();
    let mut mounts: HashMap<Key, Vec<Mount>> = HashMap::new();
    for (f, facts) in files.iter().enumerate() {
        for r in &facts.http.routers {
            let k = match r.scope {
                Some(d) => Key::Local(f, d, r.name.clone()),
                None => top_key(p, files, f, &r.name),
            };
            own.insert(k, (f, r.prefix.clone()));
        }
        for m in &facts.http.mounts {
            let parent = router_key(p, files, f, m.from_def, &m.parent);
            let children: Vec<Key> = match &m.child {
                Mounted::Path(path)
                    if !matches!(
                        router_key(p, files, f, m.from_def, path),
                        Key::Def(_) | Key::Obj(..)
                    ) =>
                {
                    vec![router_key(p, files, f, m.from_def, path)]
                }
                Mounted::Param(callee, i) => match p.resolve_path(f, m.from_def, callee, true) {
                    Some((Target::Def(t), _)) => vec![Key::Param(t, *i)],
                    _ => Vec::new(),
                },
                Mounted::Path(path) => match p.resolve_path(f, m.from_def, path, true) {
                    Some((Target::Def(t), _)) => vec![Key::Def(t)],
                    Some((Target::Module(gs), _)) => {
                        gs.iter().flat_map(|g| module_keys(p, files, *g)).collect()
                    }
                    _ => vec![Key::Obj(f, path.join("."))],
                },
                Mounted::Module(spec) => {
                    let imp = crate::facts::ImportFact {
                        line: m.line,
                        form: ImportForm::Module,
                        module: spec.clone(),
                        names: Vec::new(),
                        alias: None,
                        runs: crate::facts::Runs::ModuleLevel,
                        is_static: false,
                        system: false,
                        public: false,
                        byte: 0,
                        scope: None,
                        within: Vec::new(),
                    };
                    let fam = p.family[f].unwrap_or(Family::Ecma);
                    match modules::resolve_import(p, fam, f, &imp) {
                        ModRes::Target(Target::Module(gs)) => {
                            gs.iter().flat_map(|g| module_keys(p, files, *g)).collect()
                        }
                        _ => Vec::new(),
                    }
                }
            };
            for c in children {
                mounts.entry(c).or_default().push(Mount {
                    parent: parent.clone(),
                    prefix: m.prefix.clone(),
                    file: f,
                    from_def: m.from_def,
                });
            }
        }
    }
    // global prefixes, by the directory of the file that sets them; a file
    // prefix, by the end of the route file's path
    let mut globals: Vec<(String, String, String, bool)> = Vec::new();
    let mut file_prefixes: Vec<(String, String, String, bool)> = Vec::new();
    for (f, facts) in files.iter().enumerate() {
        for g in &facts.http.prefixes {
            let (v, u) = value(p, f, None, &g.prefix);
            match &g.file {
                Some(tail) => file_prefixes.push((g.framework.clone(), tail.clone(), v, u)),
                None => {
                    let dir =
                        facts.path.rsplit_once('/').map_or(String::new(), |(d, _)| format!("{d}/"));
                    globals.push((g.framework.clone(), dir, v, u));
                }
            }
        }
    }

    let mut memo: HashMap<Key, Vec<(String, bool)>> = HashMap::new();
    let mut out: Vec<RouteNode> = Vec::new();
    // one node per file, method and full path; a path two files register gets
    // the file in its id
    let mut at: HashMap<(usize, String, String), usize> = HashMap::new();
    for (f, facts) in files.iter().enumerate() {
        for r in &facts.http.routes {
            if r.needs_server_import && !any_server {
                continue;
            }
            let bases = match &r.router {
                Some(path) => {
                    let k = router_key(p, files, f, r.from_def, path);
                    prefixes(p, &k, &own, &mounts, &mut memo, 0, &mut HashSet::new())
                }
                None => vec![(String::new(), false)],
            };
            let (sub, sub_unresolved) = value(p, f, r.from_def, &r.path);
            let global = globals
                .iter()
                .find(|(fw, dir, _, _)| *fw == r.framework && facts.path.starts_with(dir.as_str()))
                .or_else(|| {
                    file_prefixes.iter().find(|(fw, tail, _, _)| {
                        *fw == r.framework
                            && (facts.path == *tail || facts.path.ends_with(&format!("/{tail}")))
                    })
                });
            let handler = handler_id(p, files, f, r.from_def, &r.handler);
            for (base, unresolved) in bases {
                let pattern = match global {
                    Some((_, _, g, _)) => join_path(&[g, &base, &sub]),
                    None => join_path(&[&base, &sub]),
                };
                let unresolved =
                    unresolved || sub_unresolved || global.is_some_and(|(_, _, _, u)| *u);
                if let Some(&i) = at.get(&(f, r.method.clone(), pattern.clone())) {
                    let node = &mut out[i];
                    node.unresolved |= unresolved;
                    if !node.handlers.contains(&handler) {
                        node.handlers.push(handler.clone());
                    }
                    continue;
                }
                at.insert((f, r.method.clone(), pattern.clone()), out.len());
                out.push(RouteNode {
                    // in a workspace the repository leads the id, so that a
                    // route another repository adds never renames this one
                    id: if p.repo[f].is_empty() {
                        format!("route_{}_{}", r.method.to_ascii_lowercase(), slug(&pattern))
                    } else {
                        format!(
                            "route_{}_{}_{}",
                            slug(&p.repo[f]),
                            r.method.to_ascii_lowercase(),
                            slug(&pattern)
                        )
                    },
                    label: format!("{} {}", r.method, pattern),
                    key: format!("{} {}", r.method, crate::http::normalize(&pattern)),
                    method: r.method.clone(),
                    pattern,
                    framework: r.framework.clone(),
                    file: f,
                    line: r.line,
                    handlers: vec![handler.clone()],
                    unresolved,
                });
            }
        }
    }
    let mut count: HashMap<&str, usize> = HashMap::new();
    for r in &out {
        *count.entry(r.id.as_str()).or_default() += 1;
    }
    let shared: HashSet<String> =
        count.into_iter().filter(|(_, n)| *n > 1).map(|(id, _)| id.to_string()).collect();
    for r in &mut out {
        if shared.contains(&r.id) {
            r.id = format!("{}_{}", r.id, slug(&files[r.file].path));
        }
    }
    // paths that slug alike (`/` and `/*`) still need ids of their own: an
    // edge is rewritten by id, and two nodes on one id would leave one bare
    let mut taken: HashSet<String> = HashSet::new();
    for r in &mut out {
        let mut id = r.id.clone();
        let mut n = 2;
        while !taken.insert(id.clone()) {
            id = format!("{}_{n}", r.id);
            n += 1;
        }
        r.id = id;
    }
    out
}

/// Every full prefix of a router: its own, under each mount's, up to a router
/// mounted nowhere.
fn prefixes(
    p: &Project,
    k: &Key,
    own: &HashMap<Key, (usize, PathExpr)>,
    mounts: &HashMap<Key, Vec<Mount>>,
    memo: &mut HashMap<Key, Vec<(String, bool)>>,
    depth: usize,
    visiting: &mut HashSet<Key>,
) -> Vec<(String, bool)> {
    if let Some(v) = memo.get(k) {
        return v.clone();
    }
    let (mine, mine_u) = own.get(k).map(|(f, e)| value(p, *f, None, e)).unwrap_or_default();
    let mut out = Vec::new();
    match mounts.get(k) {
        Some(ms) if depth < MAX_DEPTH && visiting.insert(k.clone()) => {
            for m in ms {
                let (mp, mu) = value(p, m.file, m.from_def, &m.prefix);
                for (pp, pu) in prefixes(p, &m.parent, own, mounts, memo, depth + 1, visiting) {
                    out.push((join_path(&[&pp, &mp, &mine]), pu || mu || mine_u));
                }
            }
            visiting.remove(k);
        }
        _ => out.push((mine, mine_u)),
    }
    out.sort();
    out.dedup();
    memo.insert(k.clone(), out.clone());
    out
}

/// The node a route's handler is.
fn handler_id(
    p: &Project,
    files: &[FileFacts],
    f: usize,
    from_def: Option<usize>,
    h: &Handler,
) -> String {
    let probe = |d: Option<usize>| {
        let r = crate::facts::RefFact {
            line: 0,
            kind: crate::facts::RefKind::Read,
            path: Vec::new(),
            context: "",
            from_def: d,
            from_owner: None,
            args: None,
            self_arg: false,
            hints: Vec::new(),
            implicit: None,
            targs: 0,
            arity: None,
            this_of: None,
            recv: None,
            byte: 0,
        };
        p.source_of(f, &r)
    };
    match h {
        Handler::Def(i) => match p.node_id((f, *i)) {
            Some(id) => id.to_string(),
            None => probe(Some(*i)),
        },
        // only what the scopes resolve: a name-only guess names no handler
        Handler::Path(path) => match p.resolve_path(f, from_def, path, false) {
            Some((Target::Def(t), Confidence::Extracted)) if p.node_id(t).is_some() => {
                p.node_id(t).unwrap_or_default().to_string()
            }
            _ => probe(from_def),
        },
        Handler::Inline => probe(from_def),
        Handler::Class(ns, class, method) => match class_method(p, files, ns, class, method) {
            Some(id) => id,
            None => probe(from_def),
        },
    }
}

/// A request linked to the route it reaches.
pub(super) struct CallLink {
    pub source: String,
    pub target: String,
    pub confidence: Confidence,
    pub file: usize,
    pub line: u32,
}

/// What became of the requests the code makes.
#[derive(Default)]
pub(super) struct CallStats {
    /// Requests in production code.
    pub calls: usize,
    pub linked: usize,
    /// To a path no route of the project has.
    pub unlinked: usize,
    /// In test code: counted, never linked.
    pub test_calls: usize,
    /// To another host.
    pub external: usize,
    /// To a URL the source does not spell out (`fetch(url)`).
    pub dynamic: usize,
}

const LOCAL_HOSTS: &[&str] = &["localhost", "127.0.0.1", "0.0.0.0", "[::1]"];

/// The hosts a workspace's `.graphlore-workspace.json` gives its
/// repositories (`{"repos": {"svc-b": {"hosts": ["svc-b.internal"]}}}`), by
/// host, lowercased, without a port.
fn workspace_hosts(p: &Project) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let Ok(text) = std::fs::read_to_string(p.root.join(crate::walk::WORKSPACE_FILE)) else {
        return out;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else { return out };
    let Some(repos) = v["repos"].as_object() else { return out };
    for (repo, conf) in repos {
        let repo = repo.trim_matches('/');
        for h in conf["hosts"].as_array().into_iter().flatten().filter_map(|h| h.as_str()) {
            let h = h.trim().trim_end_matches('/').to_ascii_lowercase();
            let h = h.split_once("://").map_or(h.as_str(), |(_, x)| x);
            let h = h.rsplit_once(':').map_or(h, |(x, port)| {
                if port.chars().all(|c| c.is_ascii_digit()) {
                    x
                } else {
                    h
                }
            });
            if !h.is_empty() {
                out.insert(h.to_string(), repo.to_string());
            }
        }
    }
    out
}

/// The path a request reaches on this project's own server: `None` for
/// another host or a URL with no path the source spells out. The flag says a
/// leading part was guessed away (an unknown base: `${API_URL}/users`); the
/// repository is the one a workspace's host map gives the URL's host.
fn request_path(
    url: &str,
    hosts: &HashMap<String, String>,
) -> Result<(String, bool, Option<String>), &'static str> {
    let mut guessed = false;
    let mut repo = None;
    let mut rest = url.trim();
    for scheme in ["http://", "https://", "ws://", "wss://", "//"] {
        if let Some(r) = rest.strip_prefix(scheme) {
            let (host, path) = r.split_once('/').unwrap_or((r, ""));
            let bare = host.rsplit_once(':').map_or(host, |(h, port)| {
                if port.chars().all(|c| c.is_ascii_digit() || c == '{' || c == '}') {
                    h
                } else {
                    host
                }
            });
            if LOCAL_HOSTS.contains(&bare) {
                rest = path;
                break;
            }
            if let Some(r) = hosts.get(&bare.to_ascii_lowercase()) {
                repo = Some(r.clone());
                rest = path;
                break;
            }
            if bare.starts_with('{') {
                guessed = true;
                rest = path;
                break;
            }
            return Err("external");
        }
    }
    // an unknown base first: `{API_URL}/users`, `{}/users`
    if rest.starts_with('{') {
        let (head, tail) = rest.split_once('/').unwrap_or((rest, ""));
        if head.ends_with('}') {
            guessed = true;
            rest = tail;
        }
    }
    let path = rest.split(['?', '#']).next().unwrap_or("");
    let ok = path.chars().all(|c| c.is_ascii_alphanumeric() || "/_-.{}:~%@+$".contains(c));
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty() && *s != ".").collect();
    if !ok || segs.is_empty() || segs.iter().all(|s| s.starts_with('{')) {
        return Err("dynamic");
    }
    Ok((format!("/{}", segs.join("/")), guessed, repo))
}

fn common_dir(a: &str, b: &str) -> usize {
    a.split('/').zip(b.split('/')).take_while(|(x, y)| x == y).count()
}

pub(super) fn link_calls(
    p: &Project,
    files: &[FileFacts],
    routes: &[RouteNode],
) -> (Vec<CallLink>, CallStats) {
    let mut stats = CallStats::default();
    let mut out = Vec::new();
    let any_call = files.iter().any(|f| !f.http.calls.is_empty());
    if !any_call {
        return (out, stats);
    }
    let hosts = workspace_hosts(p);
    let mut bases: HashMap<Key, (usize, PathExpr)> = HashMap::new();
    for (f, facts) in files.iter().enumerate() {
        for b in &facts.http.bases {
            bases.insert(top_key(p, files, f, &b.name), (f, b.base.clone()));
        }
    }
    // routes by number of segments
    let mut by_len: HashMap<usize, Vec<(usize, Vec<&str>)>> = HashMap::new();
    for (i, r) in routes.iter().enumerate() {
        let path = r.key.split_once(' ').map_or("", |(_, p)| p);
        let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
        by_len.entry(segs.len()).or_default().push((i, segs));
    }
    for (f, facts) in files.iter().enumerate() {
        let test_file = crate::community::is_test_path(&facts.path);
        for c in &facts.http.calls {
            let test = test_file || c.from_def.is_some_and(|d| facts.defs[d].is_test);
            if test {
                stats.test_calls += 1;
                continue;
            }
            stats.calls += 1;
            let (mut url, _) = value(p, f, c.from_def, &c.url);
            if let Some(client) = &c.client {
                if let Some((bf, b)) = bases.get(&key_of(p, f, c.from_def, client)) {
                    let (base, _) = value(p, *bf, None, b);
                    url = format!("{}/{}", base.trim_end_matches('/'), url.trim_start_matches('/'));
                }
            }
            let (path, guessed, to_repo) = match request_path(&url, &hosts) {
                Ok(v) => v,
                Err("external") => {
                    stats.external += 1;
                    continue;
                }
                Err(_) => {
                    stats.dynamic += 1;
                    continue;
                }
            };
            let segs: Vec<String> = crate::http::normalize(&path)
                .split('/')
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect();
            let mut best: Vec<(usize, bool)> = Vec::new();
            let mut best_score = 0usize;
            for (i, rsegs) in by_len.get(&segs.len()).map(Vec::as_slice).unwrap_or(&[]) {
                let r = &routes[*i];
                if to_repo.as_ref().is_some_and(|repo| p.repo[r.file] != *repo) {
                    continue;
                }
                let method_ok = match &c.method {
                    Some(m) => r.method == *m || r.method == "ANY",
                    None => true,
                };
                if !method_ok {
                    continue;
                }
                let mut score = 0;
                let mut loose = false;
                let fits = segs.iter().zip(rsegs).all(|(cs, rs)| {
                    // the same text ranks above parameter ↔ parameter, which
                    // ranks above a value or a hole matched loosely
                    if cs == rs {
                        score += if *rs == "{}" { 1 } else { 2 };
                        true
                    } else if *rs == "{}" {
                        // a value where the route takes a parameter
                        loose = true;
                        true
                    } else if cs == "{}" {
                        loose = true;
                        true
                    } else {
                        false
                    }
                });
                if !fits {
                    continue;
                }
                if score > best_score || best.is_empty() {
                    if score > best_score {
                        best.clear();
                    }
                    best_score = best_score.max(score);
                }
                if score == best_score {
                    best.push((*i, loose));
                }
            }
            if best.is_empty() {
                stats.unlinked += 1;
                continue;
            }
            if best.len() > 1 {
                let near = |i: usize| common_dir(&files[routes[i].file].path, &facts.path);
                let top = best.iter().map(|(i, _)| near(*i)).max().unwrap_or(0);
                best.retain(|(i, _)| near(*i) == top);
            }
            stats.linked += 1;
            let single = best.len() == 1;
            let source = {
                let r = crate::facts::RefFact {
                    line: c.line,
                    kind: crate::facts::RefKind::Read,
                    path: Vec::new(),
                    context: "",
                    from_def: c.from_def,
                    from_owner: None,
                    args: None,
                    self_arg: false,
                    hints: Vec::new(),
                    implicit: None,
                    targs: 0,
                    arity: None,
                    this_of: None,
                    recv: None,
                    byte: 0,
                };
                p.source_of(f, &r)
            };
            for (i, loose) in best {
                let exact = single && !loose && !guessed && c.method.is_some();
                out.push(CallLink {
                    source: source.clone(),
                    target: routes[i].id.clone(),
                    confidence: if exact { Confidence::Extracted } else { Confidence::Inferred },
                    file: f,
                    line: c.line,
                });
            }
        }
    }
    (out, stats)
}

/// The node of `method` of the one class named `class` whose enclosing names
/// or path hold the namespace's segments (`Api::ArticlesController#index`,
/// `App\Http\Controllers\UserController@store`); `None` when no single class
/// fits.
fn class_method(
    p: &Project,
    files: &[FileFacts],
    ns: &[String],
    class: &str,
    method: &str,
) -> Option<String> {
    let fits = |f: usize, i: usize| {
        let d = &files[f].defs[i];
        let mut names: Vec<String> = d.qualname.split('.').map(str::to_lowercase).collect();
        names.extend(files[f].path.split('/').map(|s| s.to_lowercase()));
        ns.iter().all(|s| {
            names.iter().any(|n| n == &s.to_lowercase() || n.starts_with(&s.to_lowercase()))
        })
    };
    let mut found = None;
    for (f, facts) in files.iter().enumerate() {
        for (i, d) in facts.defs.iter().enumerate() {
            if d.name == class && d.kind.is_class_like() && fits(f, i) {
                if found.is_some() {
                    return None;
                }
                found = Some((f, i));
            }
        }
    }
    let m = p.class_member(found?, method, 4)?;
    p.node_id(m).map(str::to_string)
}
