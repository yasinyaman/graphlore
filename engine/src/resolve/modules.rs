//! How each language's module paths name files, and which names an import
//! binds. Everything else about resolution is shared (`mod.rs`).

use std::collections::{HashMap, HashSet};

use super::{dir_of, normalize, Bind, Family, ModRes, Project, Target};
use crate::facts::{ImportFact, ImportForm};
use crate::lang::Lang;

/// Directories Python puts on `sys.path` by convention (graphlore's
/// `imports._ROOT_DIR_NAMES`).
const PY_ROOT_DIRS: &[&str] = &["src", "lib", "python", "source", "sources"];
const PY_ROOT_MARKERS: &[&str] = &["pyproject.toml", "setup.py", "setup.cfg"];
const JS_EXTS: &[&str] = &[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts"];

struct TsConfig {
    dir: String,
    base_url: Option<String>,
    paths: Vec<(String, Vec<String>)>,
}

#[derive(Default)]
pub struct ModuleSystems {
    // Python
    py_parts: HashMap<Vec<String>, usize>,
    py_by_last: HashMap<String, Vec<Vec<String>>>,
    py_dirs: HashSet<String>,
    py_roots: HashSet<String>,
    // JS / TS
    tsconfigs: Vec<TsConfig>,
    pub(super) js_packages: Vec<(String, String)>,
    // Go
    go_modules: Vec<(String, String)>,
    go_dirs: HashMap<String, Vec<usize>>,
    // Rust: file -> (root key, module path); (root key, module path) -> files
    rs_mod: HashMap<usize, (String, Vec<String>)>,
    rs_files: HashMap<(String, Vec<String>), Vec<usize>>,
    rs_crates: HashMap<String, String>,
    // Swift: file -> module; module -> files
    swift_module: HashMap<usize, String>,
    swift_files: HashMap<String, Vec<usize>>,
}

fn read_manifest(p: &Project, rel: &str) -> Option<String> {
    let abs = p.manifests.iter().find(|(r, _)| r == rel).map(|(_, a)| a.clone())?;
    std::fs::read_to_string(abs).ok()
}

/// JSON with comments and trailing commas (tsconfig.json) as plain JSON.
fn jsonc(src: &str) -> Option<serde_json::Value> {
    let b = src.as_bytes();
    let mut out = String::with_capacity(src.len());
    let mut i = 0;
    let mut in_str = false;
    while i < b.len() {
        let c = b[i] as char;
        if in_str {
            out.push(c);
            if c == '\\' && i + 1 < b.len() {
                out.push(b[i + 1] as char);
                i += 2;
                continue;
            }
            if c == '"' {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if c == '"' {
            in_str = true;
            out.push(c);
            i += 1;
        } else if c == '/' && i + 1 < b.len() && b[i + 1] == b'/' {
            while i < b.len() && b[i] != b'\n' {
                i += 1;
            }
        } else if c == '/' && i + 1 < b.len() && b[i + 1] == b'*' {
            i += 2;
            while i + 1 < b.len() && !(b[i] == b'*' && b[i + 1] == b'/') {
                i += 1;
            }
            i += 2;
        } else {
            out.push(c);
            i += 1;
        }
    }
    // trailing commas
    let mut cleaned = String::with_capacity(out.len());
    let chars: Vec<char> = out.chars().collect();
    for (k, &c) in chars.iter().enumerate() {
        if c == ',' {
            let next = chars[k + 1..].iter().find(|x| !x.is_whitespace());
            if matches!(next, Some('}') | Some(']')) {
                continue;
            }
        }
        cleaned.push(c);
    }
    serde_json::from_str(&cleaned).ok()
}

/// The first `"…"` after `key` in `src` (Package.swift arguments).
fn quoted_after(src: &str, key: &str) -> Option<String> {
    let at = src.find(key)? + key.len();
    let rest = &src[at..];
    let open = rest.find('"')?;
    if rest[..open].trim() != "" {
        return None;
    }
    let close = rest[open + 1..].find('"')?;
    Some(rest[open + 1..open + 1 + close].to_string())
}

/// `key = "value"` of a TOML section, crudely (Cargo.toml `[package] name`).
fn toml_value(src: &str, section: &str, key: &str) -> Option<String> {
    let mut current = String::new();
    for line in src.lines() {
        let t = line.trim();
        if t.starts_with('[') {
            current = t.trim_matches(|c| c == '[' || c == ']').trim().to_string();
            continue;
        }
        if current != section {
            continue;
        }
        if let Some((k, v)) = t.split_once('=') {
            if k.trim() == key {
                return Some(v.trim().trim_matches('"').trim_matches('\'').to_string());
            }
        }
    }
    None
}

impl ModuleSystems {
    pub fn build(p: &Project) -> ModuleSystems {
        let mut m = ModuleSystems::default();
        for (rel, _) in &p.manifests {
            let base = rel.rsplit('/').next().unwrap_or(rel);
            let dir = dir_of(rel).to_string();
            if PY_ROOT_MARKERS.contains(&base) {
                m.py_roots.insert(dir.clone());
            }
            match base {
                "go.mod" => {
                    if let Some(src) = read_manifest(p, rel) {
                        if let Some(line) =
                            src.lines().find(|l| l.trim_start().starts_with("module "))
                        {
                            let module =
                                line.trim_start()[7..].trim().trim_matches('"').to_string();
                            m.go_modules.push((module, dir.clone()));
                        }
                    }
                }
                "tsconfig.json" | "jsconfig.json" => {
                    if let Some(v) = read_manifest(p, rel).and_then(|s| jsonc(&s)) {
                        let opts = &v["compilerOptions"];
                        let base_url = opts["baseUrl"].as_str().map(String::from);
                        let mut paths = Vec::new();
                        if let Some(obj) = opts["paths"].as_object() {
                            for (k, targets) in obj {
                                let ts = targets
                                    .as_array()
                                    .map(|a| {
                                        a.iter()
                                            .filter_map(|x| x.as_str().map(String::from))
                                            .collect()
                                    })
                                    .unwrap_or_default();
                                paths.push((k.clone(), ts));
                            }
                        }
                        m.tsconfigs.push(TsConfig { dir: dir.clone(), base_url, paths });
                    }
                }
                "package.json" => {
                    if let Some(v) = read_manifest(p, rel)
                        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
                    {
                        if let Some(name) = v["name"].as_str() {
                            m.js_packages.push((name.to_string(), dir.clone()));
                        }
                    }
                }
                "Cargo.toml" => {
                    if let Some(src) = read_manifest(p, rel) {
                        let name = toml_value(&src, "lib", "name")
                            .or_else(|| toml_value(&src, "package", "name"));
                        if let Some(name) = name {
                            let key = if dir.is_empty() {
                                "src".to_string()
                            } else {
                                format!("{dir}/src")
                            };
                            m.rs_crates.insert(name.replace('-', "_"), key);
                        }
                    }
                }
                _ => {}
            }
        }
        m.go_modules.sort_by_key(|(path, _)| std::cmp::Reverse(path.len()));
        m.tsconfigs.sort_by_key(|t| std::cmp::Reverse(t.dir.len()));
        let swift_pkgs: Vec<String> = p
            .manifests
            .iter()
            .filter(|(r, _)| r.ends_with("Package.swift"))
            .map(|(r, _)| dir_of(r).to_string())
            .collect();
        // targets with their own `path:` (`.target(name: "X", path: "Source")`)
        let mut swift_targets: Vec<(String, String)> = Vec::new();
        for (rel, _) in p.manifests.iter().filter(|(r, _)| r.ends_with("Package.swift")) {
            let Some(src) = read_manifest(p, rel) else { continue };
            let dir = dir_of(rel);
            for chunk in src.split(".target(").chain(src.split(".testTarget(")).skip(1) {
                let name = quoted_after(chunk, "name:");
                let path = quoted_after(chunk.split(".target(").next().unwrap_or(chunk), "path:");
                if let Some(name) = name {
                    let path = path.unwrap_or_else(|| format!("Sources/{name}"));
                    swift_targets.push((join(dir, &path), name));
                }
            }
        }
        swift_targets.sort_by_key(|(d, _)| std::cmp::Reverse(d.len()));
        swift_targets.dedup();
        for (fi, f) in p.files.iter().enumerate() {
            let Some(lang) = f.lang else { continue };
            let path = f.path.as_str();
            match lang {
                Lang::Python => {
                    let stem = path.rsplit_once('.').map(|x| x.0).unwrap_or(path);
                    let mut parts: Vec<String> = stem.split('/').map(String::from).collect();
                    if parts.last().is_some_and(|x| x == "__init__") {
                        parts.pop();
                    }
                    if let Some(last) = parts.last() {
                        m.py_by_last.entry(last.clone()).or_default().push(parts.clone());
                    }
                    // a .py next to a .pyi: the module is the .py
                    m.py_parts.entry(parts).or_insert(fi);
                    let mut d = dir_of(path);
                    while !d.is_empty() {
                        m.py_dirs.insert(d.to_string());
                        d = dir_of(d);
                    }
                }
                Lang::Go => m.go_dirs.entry(dir_of(path).to_string()).or_default().push(fi),
                Lang::Rust => {
                    let (key, modpath) = rust_module_path(path);
                    m.rs_files.entry((key.clone(), modpath.clone())).or_default().push(fi);
                    m.rs_mod.insert(fi, (key, modpath));
                }
                Lang::Swift => {
                    let by_target = swift_targets
                        .iter()
                        .find(|(d, _)| path.starts_with(&format!("{d}/")))
                        .map(|(_, n)| n.clone());
                    let module = by_target
                        .or_else(|| {
                            swift_pkgs.iter().find_map(|pkg| {
                                let pre =
                                    if pkg.is_empty() { String::new() } else { format!("{pkg}/") };
                                let rest = path.strip_prefix(&pre)?;
                                let mut it = rest.split('/');
                                match (it.next(), it.next()) {
                                    (Some("Sources" | "Tests"), Some(m)) => Some(m.to_string()),
                                    _ => None,
                                }
                            })
                        })
                        .unwrap_or_default();
                    m.swift_files.entry(module.clone()).or_default().push(fi);
                    m.swift_module.insert(fi, module);
                }
                _ => {}
            }
        }
        m
    }

    pub fn crate_of(&self, f: usize) -> Option<&str> {
        self.rs_mod.get(&f).map(|(k, _)| k.as_str())
    }
}

/// A Rust file's root key and module path: `crate/src/a/b.rs` -> (`crate/src`,
/// [a, b]); `src/lib.rs`, `src/main.rs` and `a/mod.rs` stand for their
/// directory's module.
fn rust_module_path(path: &str) -> (String, Vec<String>) {
    let parts: Vec<&str> = path.split('/').collect();
    let root_at = parts
        .iter()
        .rposition(|p| matches!(*p, "src" | "tests" | "examples" | "benches"))
        .unwrap_or(0);
    let (key, rest) =
        if parts.len() > 1 && matches!(parts[root_at], "src" | "tests" | "examples" | "benches") {
            (parts[..=root_at].join("/"), &parts[root_at + 1..])
        } else {
            (dir_of(path).to_string(), &parts[parts.len() - 1..])
        };
    let mut module: Vec<String> =
        rest.iter().map(|s| s.trim_end_matches(".rs").to_string()).collect();
    if module.first().is_some_and(|m| m == "bin") && module.len() == 2 {
        module.clear();
    }
    if module.last().is_some_and(|m| matches!(m.as_str(), "mod" | "lib" | "main")) {
        module.pop();
    }
    (key, module)
}

// ---- Python ---------------------------------------------------------------

fn py_package(p: &Project, dir: &str) -> bool {
    let pre = if dir.is_empty() { String::new() } else { format!("{dir}/") };
    p.by_path.contains_key(format!("{pre}__init__.py").as_str())
        || p.by_path.contains_key(format!("{pre}__init__.pyi").as_str())
}

fn py_root_like(p: &Project, dir: &str, importer: &str) -> bool {
    dir.is_empty()
        || importer.starts_with(&format!("{dir}/"))
        || PY_ROOT_DIRS.contains(&dir.rsplit('/').next().unwrap_or(dir).to_lowercase().as_str())
        || p.modsys.py_roots.contains(dir)
}

fn py_resolve(p: &Project, f: usize, module: &str) -> ModRes {
    let level = module.chars().take_while(|c| *c == '.').count();
    let dotted: Vec<String> =
        module[level..].split('.').filter(|s| !s.is_empty()).map(String::from).collect();
    let importer = p.files[f].path.as_str();
    let m = &p.modsys;
    if level > 0 {
        let mut base: Vec<String> =
            dir_of(importer).split('/').filter(|s| !s.is_empty()).map(String::from).collect();
        if level - 1 > base.len() {
            return ModRes::Missing;
        }
        base.truncate(base.len() - (level - 1));
        base.extend(dotted);
        if let Some(&fi) = m.py_parts.get(&base) {
            return ModRes::Target(Target::Module(vec![fi]));
        }
        let d = base.join("/");
        return if m.py_dirs.contains(&d) {
            ModRes::Target(Target::Dir(d))
        } else {
            ModRes::Missing
        };
    }
    let Some(last) = dotted.last() else { return ModRes::Missing };
    let n = dotted.len();
    let mut found = Vec::new();
    for parts in m.py_by_last.get(last).into_iter().flatten() {
        if parts.len() < n || parts[parts.len() - n..] != dotted[..] {
            continue;
        }
        let root = parts[..parts.len() - n].join("/");
        if !py_package(p, &root) && (n > 1 || py_root_like(p, &root, importer)) {
            found.push(m.py_parts[parts]);
        }
    }
    found.sort();
    found.dedup();
    match found.len() {
        1 => ModRes::Target(Target::Module(found)),
        0 => {
            // a namespace package: a directory of modules with no __init__.py
            let tail = dotted.join("/");
            let dirs: Vec<&String> = m
                .py_dirs
                .iter()
                .filter(|d| {
                    (*d == &tail || d.ends_with(&format!("/{tail}")))
                        && py_root_like(
                            p,
                            d[..d.len() - tail.len()].trim_end_matches('/'),
                            importer,
                        )
                        && !py_package(p, d.as_str())
                })
                .collect();
            if dirs.len() == 1 {
                ModRes::Target(Target::Dir(dirs[0].clone()))
            } else {
                ModRes::Ext(dotted.join("."))
            }
        }
        _ => ModRes::Missing,
    }
}

fn py_file_parts(p: &Project, f: usize) -> Vec<String> {
    let path = &p.files[f].path;
    let stem = path.rsplit_once('.').map(|x| x.0).unwrap_or(path);
    let mut parts: Vec<String> = stem.split('/').map(String::from).collect();
    if parts.last().is_some_and(|x| x == "__init__") {
        parts.pop();
    }
    parts
}

/// A Python module's child module, or a Rust module's child module.
pub fn submodule(p: &Project, files: &[usize], name: &str) -> Option<Target> {
    let &f = files.first()?;
    match p.family[f]? {
        Family::Python => {
            let path = &p.files[f].path;
            if !path.ends_with("__init__.py") && !path.ends_with("__init__.pyi") {
                return None;
            }
            let mut parts = py_file_parts(p, f);
            parts.push(name.to_string());
            if let Some(&g) = p.modsys.py_parts.get(&parts) {
                return Some(Target::Module(vec![g]));
            }
            let d = parts.join("/");
            p.modsys.py_dirs.contains(&d).then_some(Target::Dir(d))
        }
        Family::Rust => {
            let (key, path) = p.modsys.rs_mod.get(&f)?;
            let mut child = path.clone();
            child.push(name.to_string());
            p.modsys.rs_files.get(&(key.clone(), child)).map(|fs| Target::Module(fs.clone()))
        }
        _ => None,
    }
}

pub fn dir_member(p: &Project, dir: &str, name: &str) -> Option<Target> {
    let mut parts: Vec<String> = dir.split('/').map(String::from).collect();
    parts.push(name.to_string());
    if let Some(&g) = p.modsys.py_parts.get(&parts) {
        return Some(Target::Module(vec![g]));
    }
    let d = parts.join("/");
    p.modsys.py_dirs.contains(&d).then_some(Target::Dir(d))
}

// ---- JS / TS --------------------------------------------------------------

fn js_file(p: &Project, base: &str) -> Option<usize> {
    let base = normalize(base)?;
    let is_js = |i: usize| p.family[i] == Some(Family::Ecma);
    if let Some(&i) = p.by_path.get(base.as_str()) {
        if is_js(i) {
            return Some(i);
        }
    }
    // `./x.js` written in TypeScript names `x.ts`
    let stripped = [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"]
        .iter()
        .find_map(|e| base.strip_suffix(e))
        .unwrap_or(&base)
        .to_string();
    for cand in [&base, &stripped] {
        for ext in JS_EXTS {
            if let Some(&i) = p.by_path.get(format!("{cand}{ext}").as_str()) {
                if is_js(i) {
                    return Some(i);
                }
            }
        }
    }
    for ext in JS_EXTS {
        let idx =
            if base.is_empty() { format!("index{ext}") } else { format!("{base}/index{ext}") };
        if let Some(&i) = p.by_path.get(idx.as_str()) {
            if is_js(i) {
                return Some(i);
            }
        }
    }
    None
}

fn js_package_root(spec: &str) -> String {
    let spec = spec.strip_prefix("node:").unwrap_or(spec);
    let mut it = spec.split('/');
    match it.next() {
        Some(scope) if scope.starts_with('@') => match it.next() {
            Some(name) => format!("{scope}/{name}"),
            None => scope.to_string(),
        },
        Some(name) => name.to_string(),
        None => spec.to_string(),
    }
}

fn join(dir: &str, rel: &str) -> String {
    if dir.is_empty() {
        rel.to_string()
    } else {
        format!("{dir}/{rel}")
    }
}

fn js_resolve(p: &Project, f: usize, spec: &str) -> ModRes {
    let importer = p.files[f].path.as_str();
    if spec.starts_with('.') {
        return match js_file(p, &join(dir_of(importer), spec)) {
            Some(i) => ModRes::Target(Target::Module(vec![i])),
            None => ModRes::Missing,
        };
    }
    // the nearest tsconfig / jsconfig above the importer
    for ts in &p.modsys.tsconfigs {
        if !(ts.dir.is_empty() || importer.starts_with(&format!("{}/", ts.dir))) {
            continue;
        }
        let base = join(&ts.dir, ts.base_url.as_deref().unwrap_or("."));
        for (pattern, targets) in &ts.paths {
            let rest = match pattern.strip_suffix('*') {
                Some(prefix) => match spec.strip_prefix(prefix) {
                    Some(r) => r,
                    None => continue,
                },
                None if pattern == spec => "",
                None => continue,
            };
            for t in targets {
                if let Some(i) = js_file(p, &join(&base, &t.replace('*', rest))) {
                    return ModRes::Target(Target::Module(vec![i]));
                }
            }
        }
        if ts.base_url.is_some() {
            if let Some(i) = js_file(p, &join(&base, spec)) {
                return ModRes::Target(Target::Module(vec![i]));
            }
        }
        break;
    }
    // a workspace package of the repository
    let root = js_package_root(spec);
    for (name, dir) in &p.modsys.js_packages {
        if *name == root {
            let rest = spec[root.len()..].trim_start_matches('/');
            let cands: Vec<String> = if rest.is_empty() {
                ["src/index", "index", "lib/index", "src/main", "main"]
                    .iter()
                    .map(|x| join(dir, x))
                    .collect()
            } else {
                vec![join(dir, rest), join(dir, &format!("src/{rest}"))]
            };
            if let Some(i) = cands.iter().find_map(|c| js_file(p, c)) {
                return ModRes::Target(Target::Module(vec![i]));
            }
        }
    }
    // `@/x`, `~/x`: the source root
    if let Some(rest) = spec.strip_prefix("@/").or_else(|| spec.strip_prefix("~/")) {
        for base in ["src", ""] {
            if let Some(i) = js_file(p, &join(base, rest)) {
                return ModRes::Target(Target::Module(vec![i]));
            }
        }
        return ModRes::Missing;
    }
    if spec.starts_with('/') || spec.starts_with('#') {
        return ModRes::Missing;
    }
    ModRes::Ext(root)
}

// ---- Go -------------------------------------------------------------------

fn go_resolve(p: &Project, path: &str) -> ModRes {
    for (module, dir) in &p.modsys.go_modules {
        let rest = if path == module {
            ""
        } else if let Some(r) = path.strip_prefix(&format!("{module}/")) {
            r
        } else {
            continue;
        };
        let d = join(dir, rest);
        let files: Vec<usize> = p
            .modsys
            .go_dirs
            .get(&d)
            .into_iter()
            .flatten()
            .copied()
            .filter(|&i| !p.files[i].path.ends_with("_test.go"))
            .collect();
        return if files.is_empty() {
            ModRes::Missing
        } else {
            ModRes::Target(Target::Module(files))
        };
    }
    ModRes::Ext(path.to_string())
}

// ---- namespaces (JVM, C#, PHP) ---------------------------------------------

fn ns_target(p: &Project, fam: Family, name: &str) -> Option<Target> {
    let dotted = name.replace('\\', ".").trim_start_matches('.').to_string();
    if let Some(d) = p.best(
        p.fqn.get(&dotted).into_iter().flatten().copied().filter(|r| p.family[r.0] == Some(fam)),
    ) {
        return Some(Target::Def(d));
    }
    p.namespaces.contains(&(fam, dotted.clone())).then_some(Target::Ns(dotted))
}

// ---- Rust -----------------------------------------------------------------

/// The module above a Rust file's module.
pub fn rust_parent(p: &Project, f: usize) -> Option<Target> {
    let (key, path) = p.modsys.rs_mod.get(&f)?;
    let mut up = path.clone();
    up.pop()?;
    p.modsys.rs_files.get(&(key.clone(), up)).map(|fs| Target::Module(fs.clone()))
}

pub fn crate_root(p: &Project, f: usize) -> Option<Target> {
    let (key, _) = p.modsys.rs_mod.get(&f)?;
    p.modsys.rs_files.get(&(key.clone(), Vec::new())).map(|fs| Target::Module(fs.clone()))
}

fn rust_path(p: &Project, f: usize, segs: &[String]) -> ModRes {
    let Some((first, rest)) = segs.split_first() else {
        return ModRes::Target(Target::Module(vec![f]));
    };
    let (key, modpath) = match p.modsys.rs_mod.get(&f) {
        Some(x) => x.clone(),
        None => return ModRes::Missing,
    };
    let mut t = match first.as_str() {
        "crate" => match crate_root(p, f) {
            Some(t) => t,
            None => return ModRes::Missing,
        },
        "self" => Target::Module(vec![f]),
        "super" => {
            let mut up = modpath.clone();
            let mut r = rest;
            up.pop();
            while r.first().is_some_and(|s| s == "super") {
                up.pop();
                r = &r[1..];
            }
            let files = p.modsys.rs_files.get(&(key.clone(), up)).cloned().unwrap_or_default();
            if files.is_empty() {
                return ModRes::Missing;
            }
            return walk(p, Target::Module(files), r);
        }
        name => {
            if let Some(t) = p.member(&Target::Module(vec![f]), name, 0) {
                t
            } else if let Some(k) = p.modsys.rs_crates.get(name) {
                match p.modsys.rs_files.get(&(k.clone(), Vec::new())) {
                    Some(fs) => Target::Module(fs.clone()),
                    None => return ModRes::Missing,
                }
            } else {
                return ModRes::Ext(name.to_string());
            }
        }
    };
    for s in rest {
        match p.member(&t, s, 2) {
            Some(x) => t = x,
            None => return ModRes::Missing,
        }
    }
    ModRes::Target(t)
}

fn walk(p: &Project, mut t: Target, segs: &[String]) -> ModRes {
    for s in segs {
        match p.member(&t, s, 2) {
            Some(x) => t = x,
            None => return ModRes::Missing,
        }
    }
    ModRes::Target(t)
}

// ---- C / C++, Ruby, PHP files ------------------------------------------------

/// A file named by a path suffix, unique (or the one sharing the longest
/// directory prefix with the importer).
fn by_suffix(p: &Project, f: usize, rel: &str, fam: Family) -> Option<usize> {
    let want = format!("/{rel}");
    let cands: Vec<usize> = p
        .files
        .iter()
        .enumerate()
        .filter(|(i, x)| p.family[*i] == Some(fam) && (x.path == rel || x.path.ends_with(&want)))
        .map(|(i, _)| i)
        .collect();
    match cands.len() {
        0 => None,
        1 => Some(cands[0]),
        _ => {
            let here: Vec<&str> = p.files[f].path.split('/').collect();
            let score = |i: &usize| {
                p.files[*i].path.split('/').zip(&here).take_while(|(a, b)| a == *b).count()
            };
            let best = cands.iter().map(score).max()?;
            let top: Vec<usize> = cands.iter().copied().filter(|i| score(i) == best).collect();
            (top.len() == 1).then(|| top[0])
        }
    }
}

fn file_target(i: Option<usize>) -> ModRes {
    i.map(|i| ModRes::Target(Target::Module(vec![i]))).unwrap_or(ModRes::Missing)
}

/// What an import's module names, per language.
pub fn resolve_import(p: &Project, fam: Family, f: usize, imp: &ImportFact) -> ModRes {
    let importer = p.files[f].path.as_str();
    match fam {
        Family::Python => py_resolve(p, f, &imp.module),
        Family::Ecma => js_resolve(p, f, &imp.module),
        Family::Go => go_resolve(p, &imp.module),
        Family::Jvm | Family::CSharp => match ns_target(p, fam, &imp.module) {
            Some(t) => ModRes::Target(t),
            None if imp.module.is_empty() => ModRes::Missing,
            None => ModRes::Ext(imp.module.clone()),
        },
        Family::Php => {
            if imp.form == ImportForm::Module {
                let rel = imp.module.trim_start_matches('/');
                let cand = normalize(&join(dir_of(importer), rel))
                    .and_then(|c| p.by_path.get(c.as_str()).copied())
                    .or_else(|| p.by_path.get(rel).copied())
                    .or_else(|| by_suffix(p, f, rel, fam));
                return file_target(cand);
            }
            match ns_target(p, fam, &imp.module) {
                Some(t) => ModRes::Target(t),
                None if imp.module.is_empty() => ModRes::Target(Target::Ns(String::new())),
                None => ModRes::Ext(imp.module.clone()),
            }
        }
        Family::Rust => {
            let segs: Vec<String> =
                imp.module.split("::").filter(|s| !s.is_empty()).map(String::from).collect();
            if segs.is_empty() && imp.form == ImportForm::From {
                // `use foo;`: foo is the thing imported, a module or a crate
                let name = imp.names.first().map(|n| n.0.clone()).unwrap_or_default();
                return match rust_path(p, f, std::slice::from_ref(&name)) {
                    ModRes::Target(t) => ModRes::Target(Target::Module(match t {
                        Target::Module(fs) => fs,
                        _ => vec![f],
                    })),
                    other => other,
                };
            }
            rust_path(p, f, &segs)
        }
        Family::Ruby => {
            let m = imp.module.trim_end_matches(".rb");
            if !imp.system {
                return file_target(
                    normalize(&join(dir_of(importer), &format!("{m}.rb")))
                        .and_then(|c| p.by_path.get(c.as_str()).copied()),
                );
            }
            let cand = p
                .by_path
                .get(format!("lib/{m}.rb").as_str())
                .copied()
                .or_else(|| p.by_path.get(format!("{m}.rb").as_str()).copied())
                .or_else(|| by_suffix(p, f, &format!("lib/{m}.rb"), fam));
            match cand {
                Some(i) => ModRes::Target(Target::Module(vec![i])),
                None => ModRes::Ext(m.split('/').next().unwrap_or(m).to_string()),
            }
        }
        Family::Swift => match p.modsys.swift_files.get(&imp.module) {
            Some(fs) if !imp.module.is_empty() => ModRes::Target(Target::Module(fs.clone())),
            _ => ModRes::Ext(imp.module.clone()),
        },
        Family::C => {
            let local = (!imp.system)
                .then(|| normalize(&join(dir_of(importer), &imp.module)))
                .flatten()
                .and_then(|c| p.by_path.get(c.as_str()).copied());
            match local.or_else(|| by_suffix(p, f, &imp.module, fam)) {
                Some(i) => ModRes::Target(Target::Module(vec![i])),
                None if imp.system => ModRes::Ext(imp.module.clone()),
                None => ModRes::Missing,
            }
        }
    }
}

/// Whether an import makes all of its module's names visible.
pub fn is_star(fam: Family, imp: &ImportFact) -> bool {
    match fam {
        Family::C | Family::Swift => imp.form == ImportForm::Module,
        _ => false,
    }
}

/// The local names an import binds.
pub fn bindings(
    p: &Project,
    fam: Family,
    f: usize,
    imp: &ImportFact,
    t: &Target,
) -> Vec<(String, Bind)> {
    let mut out = Vec::new();
    match imp.form {
        ImportForm::Module => match fam {
            Family::Python => match &imp.alias {
                Some(a) => out.push((a.clone(), Bind::T(t.clone()))),
                None => {
                    // `import a.b.c` binds `a`
                    let first = imp.module.split('.').next().unwrap_or("").to_string();
                    let head = if first == imp.module {
                        t.clone()
                    } else {
                        match py_resolve(p, f, &first) {
                            ModRes::Target(x) => x,
                            ModRes::Ext(l) => Target::Ext(p.ext(&l, fam.ecosystem()), Vec::new()),
                            ModRes::Missing => return out,
                        }
                    };
                    out.push((first, Bind::T(head)));
                }
            },
            Family::Go => {
                let name = imp.alias.clone().unwrap_or_else(|| match t {
                    Target::Module(fs) => fs
                        .first()
                        .and_then(|&i| p.files[i].package.clone())
                        .unwrap_or_else(|| go_last(&imp.module)),
                    _ => go_last(&imp.module),
                });
                if name != "_" {
                    out.push((name, Bind::T(t.clone())));
                }
            }
            Family::Rust | Family::CSharp | Family::Ecma => {
                if let Some(a) = &imp.alias {
                    out.push((a.clone(), Bind::T(t.clone())));
                } else if fam == Family::Rust {
                    let last = imp.module.rsplit("::").next().unwrap_or("").to_string();
                    if !last.is_empty() {
                        out.push((last, Bind::T(t.clone())));
                    }
                }
            }
            _ => {}
        },
        ImportForm::From => {
            for (name, alias) in &imp.names {
                let local = alias.clone().unwrap_or_else(|| name.clone());
                if fam == Family::Rust && imp.module.is_empty() {
                    out.push((local, Bind::T(t.clone())));
                } else {
                    out.push((local, Bind::Member(t.clone(), name.clone())));
                }
            }
        }
        _ => {}
    }
    out
}

fn go_last(path: &str) -> String {
    let mut segs: Vec<&str> = path.split('/').collect();
    if segs.len() > 1
        && segs
            .last()
            .is_some_and(|s| s.starts_with('v') && s[1..].chars().all(|c| c.is_ascii_digit()))
    {
        segs.pop();
    }
    segs.last().unwrap_or(&path).replace('-', "_")
}

/// A name a JS/TS module exports without defining it: `export { a as b }`,
/// `export default X`, `export … from`.
pub fn reexport(p: &Project, f: usize, name: &str, depth: usize) -> Option<Target> {
    if p.family[f] != Some(Family::Ecma) {
        return None;
    }
    let facts = &p.files[f];
    for (local, exported) in &facts.exports {
        if exported == name && local != name {
            if let Some(t) = p.member(&Target::Module(vec![f]), local, depth) {
                return Some(t);
            }
        }
    }
    for (i, imp) in facts.imports.iter().enumerate() {
        if imp.form != ImportForm::ReExport {
            continue;
        }
        let ModRes::Target(m) = &p.scopes[f].modules[i] else { continue };
        if imp.names.is_empty() && imp.alias.is_none() {
            if let Some(t) = p.member(m, name, depth) {
                return Some(t);
            }
        }
        for (orig, alias) in &imp.names {
            if alias.as_deref().unwrap_or(orig) == name {
                return p.member(m, orig, depth);
            }
        }
        if imp.alias.as_deref() == Some(name) {
            return Some(m.clone());
        }
    }
    None
}

/// Names visible without an import: the package, namespace or module a file
/// shares with others; Ruby's constants.
pub fn scope_lookup(
    p: &Project,
    fam: Family,
    f: usize,
    d: Option<usize>,
    name: &str,
) -> Option<Target> {
    match fam {
        Family::Go => {
            let pkg = p.files[f].package.as_deref();
            let dir = dir_of(&p.files[f].path);
            let files: Vec<usize> = p
                .modsys
                .go_dirs
                .get(dir)
                .into_iter()
                .flatten()
                .copied()
                .filter(|&g| g != f && p.files[g].package.as_deref() == pkg)
                .collect();
            p.top_in(&files, name).map(Target::Def)
        }
        Family::Jvm | Family::CSharp | Family::Php => {
            let mut spaces: Vec<String> = Vec::new();
            let defs = &p.files[f].defs;
            let mut cur = d;
            while let Some(x) = cur {
                if defs[x].kind == crate::facts::DefKind::Namespace {
                    spaces.push(defs[x].qualname.clone());
                    // C# `namespace Shop.Core { … }`: `Shop`'s names too, as
                    // for a file-scoped `namespace Shop.Core;` below
                    if fam == Family::CSharp {
                        let q = defs[x].qualname.replace("::", ".");
                        let mut parts: Vec<&str> = q.split('.').collect();
                        parts.pop();
                        while !parts.is_empty() {
                            spaces.push(parts.join("."));
                            parts.pop();
                        }
                    }
                }
                cur = defs[x].parent;
            }
            if let Some(pkg) = &p.files[f].package {
                let pkg = pkg.replace('\\', ".");
                let mut parts: Vec<&str> = pkg.split('.').collect();
                while !parts.is_empty() {
                    spaces.push(parts.join("."));
                    if fam != Family::CSharp {
                        break;
                    }
                    parts.pop();
                }
            }
            spaces.push(String::new());
            for ns in spaces {
                let key = if ns.is_empty() { name.to_string() } else { format!("{ns}.{name}") };
                let found = p
                    .fqn
                    .get(&key)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|r| p.family[r.0] == Some(fam));
                if let Some(r) = p.best(found) {
                    return Some(Target::Def(r));
                }
            }
            None
        }
        Family::Ruby => {
            // lexical nesting, then the top level
            let defs = &p.files[f].defs;
            let mut prefixes = Vec::new();
            let mut cur = d;
            while let Some(x) = cur {
                if defs[x].kind.is_class_like() {
                    prefixes.push(format!("{}.", defs[x].qualname.replace("::", ".")));
                }
                cur = defs[x].parent;
            }
            prefixes.push(String::new());
            for pre in prefixes {
                let key = format!("{pre}{name}");
                let found = p
                    .by_qual
                    .get(&key)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|r| p.family[r.0] == Some(fam))
                    // a constant is found wherever it is written (autoload);
                    // a method at another file's top level only when this
                    // file requires that one
                    .filter(|r| {
                        !pre.is_empty()
                            || r.0 == f
                            || !p.def(*r).kind.is_callable()
                            || p.scopes[f].imported.contains(&r.0)
                    });
                if let Some(r) = p.best(found) {
                    return Some(Target::Def(r));
                }
            }
            None
        }
        Family::Swift => {
            let module = p.modsys.swift_module.get(&f)?;
            let files: Vec<usize> =
                p.modsys.swift_files.get(module)?.iter().copied().filter(|&g| g != f).collect();
            p.top_in(&files, name).map(Target::Def)
        }
        Family::Rust => submodule(p, &[f], name),
        _ => None,
    }
}

/// The edges an import statement makes: `(target, relation, context)`.
pub fn import_edges(
    p: &Project,
    fam: Family,
    _f: usize,
    imp: &ImportFact,
    res: &ModRes,
    used: &HashSet<usize>,
) -> Vec<(Target, &'static str, &'static str)> {
    let mut out = Vec::new();
    let t = match res {
        ModRes::Target(t) => t.clone(),
        ModRes::Ext(label) => Target::Ext(p.ext(label, fam.ecosystem()), Vec::new()),
        ModRes::Missing => return out,
    };
    let module_rel = |fam: Family, form: ImportForm| -> &'static str {
        match (fam, form) {
            (_, ImportForm::Dynamic) => "dynamic_import",
            (_, ImportForm::ReExport) => "re_exports",
            (Family::Python, ImportForm::Module) => "imports",
            (Family::Python | Family::Ecma | Family::Go | Family::Rust, _) => "imports_from",
            _ => "imports",
        }
    };
    let rel = module_rel(fam, imp.form);
    let symbol_rel = if imp.form == ImportForm::ReExport {
        "re_exports"
    } else if fam == Family::Rust {
        "imports_from"
    } else {
        "imports"
    };
    let context = if imp.form == ImportForm::ReExport { "re-export" } else { "import" };
    let used_or = |all: &[usize]| -> Vec<usize> {
        if used.is_empty() {
            all.to_vec()
        } else {
            // (in file order: a set's own order differs from run to run)
            let mut files: Vec<usize> = used.iter().copied().collect();
            files.sort_unstable();
            files
        }
    };
    match &t {
        Target::Ext(e, _) => {
            // one node per external module (Python, JS, Go, Rust, Ruby,
            // Swift, C) or per imported name (JVM, C#, PHP)
            let per_name = matches!(fam, Family::Jvm | Family::CSharp | Family::Php);
            if per_name && imp.form == ImportForm::From {
                for (name, _) in &imp.names {
                    out.push((Target::Ext(*e, vec![name.clone()]), symbol_rel, context));
                }
            } else {
                out.push((Target::Ext(*e, Vec::new()), rel, context));
            }
        }
        Target::Module(fs) => {
            let package_like = matches!(fam, Family::Go | Family::Swift);
            let files = if package_like { used_or(fs) } else { fs.clone() };
            let file_edge = !(matches!(fam, Family::Rust)
                && imp.form == ImportForm::From
                && !imp.names.is_empty());
            if file_edge && !(fam == Family::Swift && used.is_empty()) {
                out.push((Target::Module(files), rel, context));
            }
            if matches!(imp.form, ImportForm::From | ImportForm::ReExport) {
                for (name, _) in &imp.names {
                    if name == "default" || name == "*" {
                        continue;
                    }
                    // the definition, through re-exports (`export … from`,
                    // a package's `from .impl import X`)
                    let found = p.top_in(fs, name).map(Target::Def).or_else(|| {
                        matches!(fam, Family::Python | Family::Ecma)
                            .then(|| p.member(&t, name, 3))
                            .flatten()
                            .filter(|x| matches!(x, Target::Def(_)))
                    });
                    if let Some(Target::Def(d)) = found {
                        out.push((Target::Def(d), symbol_rel, context));
                    } else if let Some(sub) = submodule(p, fs, name) {
                        if let Target::Module(g) = sub {
                            let ctx =
                                if fam == Family::Python { "submodule_import" } else { context };
                            out.push((Target::Module(g), rel, ctx));
                        }
                    } else if fam == Family::Rust {
                        // re-exported or nested: the module file stands for it
                        if let Some(Target::Def(d)) = p.member(&t, name, 3) {
                            out.push((Target::Def(d), symbol_rel, context));
                        } else {
                            out.push((Target::Module(fs.clone()), rel, context));
                        }
                    }
                }
            }
        }
        Target::Dir(d) => {
            for (name, _) in &imp.names {
                if let Some(Target::Module(g)) = dir_member(p, d, name) {
                    out.push((Target::Module(g), rel, "submodule_import"));
                }
            }
        }
        Target::Ns(_) => {
            // a package or namespace: the files a use reached through it,
            // and each name it imports
            if imp.form == ImportForm::From {
                for (name, _) in &imp.names {
                    match p.member(&t, name, 2) {
                        Some(Target::Def(d)) => out.push((Target::Def(d), symbol_rel, context)),
                        Some(Target::Ns(_)) | None => {}
                        Some(other) => out.push((other, symbol_rel, context)),
                    }
                }
            } else if !used.is_empty() {
                out.push((Target::Module(used.iter().copied().collect()), rel, context));
            }
        }
        Target::Def(d) => {
            // a static or aliased import of a type, or a Rust item
            if imp.form == ImportForm::From {
                for (name, _) in &imp.names {
                    if let Some(Target::Def(m)) = p.member(&t, name, 2) {
                        out.push((Target::Def(m), symbol_rel, context));
                    }
                }
            } else {
                out.push((Target::Def(*d), symbol_rel, context));
            }
        }
    }
    out
}
