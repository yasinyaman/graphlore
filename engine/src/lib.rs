//! graphlore's native code-graph extractor.
//!
//! One pass over a project: discover files (`walk`), parse each in parallel with
//! a tree-sitter grammar compiled into this library (`parse`), extract the
//! definitions with the generic walk plus per-language modules (`generic`,
//! `lang`), read document headings (`docs`), and assemble a graph.json that
//! graphlore's readers — and graphify's own `query`/`path`/`explain` — accept
//! (`graph`). Nothing here reaches the network.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use rayon::prelude::*;
use serde_json::{json, Map, Value};

pub mod cache;
pub mod community;
pub mod coupling;
pub mod docs;
pub mod facts;
pub mod generic;
pub mod graph;
pub mod http;
pub mod lang;
pub mod parse;
pub mod refs;
pub mod report;
pub mod resolve;
pub mod resources;
pub mod search;
mod search_digits;
pub mod state;
pub mod versions;
pub mod walk;

#[cfg(feature = "python")]
mod py;

pub use facts::{Def, DefKind, FileFacts};
pub use lang::Lang;
pub use report::BuildReport;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
/// Bumped when graph.json changes in a way graphlore's readers must know about.
pub const SCHEMA_VERSION: u32 = 7;
pub const DEFAULT_MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;
pub const DEFAULT_PARSE_TIMEOUT: Duration = Duration::from_secs(10);

pub struct BuildOptions {
    pub root: PathBuf,
    /// The graph.json to write; `None` builds without writing.
    pub out: Option<PathBuf>,
    /// The commit the working tree was at (`built_at_commit`), from the caller.
    pub commit: Option<String>,
    /// Read Markdown and reStructuredText headings.
    pub documents: bool,
    /// The output directory's name, never walked into.
    pub out_dir_name: String,
    pub max_file_bytes: u64,
    pub parse_timeout: Option<Duration>,
    /// Worker threads; `None` uses every core.
    pub threads: Option<usize>,
    /// Reuse the facts of files unchanged since the last build (`update=True`).
    /// The cache is written either way.
    pub incremental: bool,
    /// Where to write the calls the resolver left without an edge although
    /// their name is one definition of the project (a JSON list, each with
    /// the reason): what `benchmarks/unresolved.py` reads. `None` writes
    /// nothing; graph.json is the same either way.
    pub unresolved: Option<PathBuf>,
    /// Read a Go module's `vendor/` directory (`walk::go_vendor_dir`), which
    /// a build leaves out by default.
    pub read_vendor: bool,
}

impl BuildOptions {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        BuildOptions {
            root: root.into(),
            out: None,
            commit: None,
            documents: true,
            out_dir_name: "graphify-out".into(),
            max_file_bytes: DEFAULT_MAX_FILE_BYTES,
            parse_timeout: Some(DEFAULT_PARSE_TIMEOUT),
            threads: None,
            incremental: false,
            unresolved: None,
            read_vendor: false,
        }
    }
}

#[allow(clippy::large_enum_variant)] // one per file, moved once
enum Read {
    Facts(FileFacts, cache::Stamp, bool),
    Unreadable(String),
}

fn read_all(
    files: &[walk::Candidate],
    timeout: Option<Duration>,
    cached: Option<&cache::Cache>,
) -> Vec<Read> {
    files
        .par_iter()
        .map(|c| {
            let meta = std::fs::metadata(&c.abs).ok();
            let (size, mtime) = meta.as_ref().map_or((0, 0), |m| (m.len(), cache::mtime_ns(m)));
            if let Some((facts, stamp)) = cached.and_then(|k| k.unchanged(&c.rel, size, mtime)) {
                return Read::Facts(facts, stamp, true);
            }
            let src = match std::fs::read(&c.abs) {
                Err(_) => return Read::Unreadable(c.rel.clone()),
                Ok(src) => src,
            };
            let stamp =
                cache::Stamp { size: src.len() as u64, mtime_ns: mtime, hash: cache::hash(&src) };
            if let Some(facts) = cached.and_then(|k| k.same_content(&c.rel, stamp.size, stamp.hash))
            {
                return Read::Facts(facts, stamp, true);
            }
            let facts = match c.kind {
                walk::FileKind::Code(lang) => {
                    let lang = lang::detect(&c.rel, Some(&src)).unwrap_or(lang);
                    parse::file_facts(&c.rel, &src, Some(lang), None, timeout)
                }
                walk::FileKind::Doc(kind) => {
                    parse::file_facts(&c.rel, &src, None, Some(kind), timeout)
                }
            };
            Read::Facts(facts, stamp, false)
        })
        .collect()
}

/// A library wrapper's name (`Optional`, `Future`, `Provider`, `Box`) is a
/// wrapper only where the project does not mean its own type of that name.
/// The reader works a file at a time and cannot know; so the files that see a
/// project type named like a wrapper are read again with that name as an
/// ordinary type. A file sees it when it defines it, sits in the directory of
/// a file that does, or imports it from where it is defined (not
/// `java.util.Optional` beside the project's `com.acme.Optional`). The answer
/// is the same from the cache as from the sources: what a file defines and
/// imports does not depend on it.
fn reread_shadowed(reads: &mut [Read], files: &[walk::Candidate], timeout: Option<Duration>) {
    use std::collections::{HashMap, HashSet};
    let same_language = |a: Lang, b: Lang| {
        let ecma = |l: Lang| matches!(l, Lang::JavaScript | Lang::TypeScript | Lang::Tsx);
        a == b || (ecma(a) && ecma(b))
    };
    let dir = |p: &str| p.rfind('/').map_or(String::new(), |i| p[..i].to_string());
    // (language, name) -> the files that define a type of that name
    let mut defined: HashMap<(Lang, String), Vec<usize>> = HashMap::new();
    for (i, r) in reads.iter().enumerate() {
        let Read::Facts(f, _, _) = r else { continue };
        let Some(lang) = f.lang else { continue };
        for d in &f.defs {
            if d.kind.is_class_like() && refs::wrapper_name(&d.name) {
                let at = defined.entry((lang, d.name.clone())).or_default();
                if !at.contains(&i) {
                    at.push(i);
                }
            }
        }
    }
    if defined.is_empty() {
        return;
    }
    let facts_of = |i: usize| match &reads[i] {
        Read::Facts(f, _, _) => Some(f),
        Read::Unreadable(_) => None,
    };
    let mut plan: HashMap<usize, HashSet<String>> = HashMap::new();
    for (i, r) in reads.iter().enumerate() {
        let Read::Facts(f, _, _) = r else { continue };
        let Some(lang) = f.lang else { continue };
        for ((l, name), at) in &defined {
            if !same_language(*l, lang) {
                continue;
            }
            let imported = || {
                f.imports.iter().any(|imp| {
                    let module = imp.module.replace("::", ".").replace(['\\', '/'], ".");
                    let named = imp.names.iter().any(|(n, _)| n == name)
                        || module.rsplit('.').next() == Some(name.as_str());
                    // from where the project defines it: its package, or a
                    // path inside the project
                    let from_project = at.iter().filter_map(|&d| facts_of(d)).any(|d| {
                        match d.package.as_deref() {
                            Some(pkg) => {
                                let pkg = pkg.replace('\\', ".");
                                module == pkg || module == format!("{pkg}.{name}")
                            }
                            None => {
                                imp.module.starts_with('.')
                                    || imp.module.starts_with("crate")
                                    || imp.module.starts_with("super")
                                    || imp.module.starts_with("self")
                                    || lang == Lang::Python
                            }
                        }
                    });
                    let whole = imp.form == facts::ImportForm::Wildcard;
                    from_project && (named || whole)
                })
            };
            let sees = at.contains(&i)
                || at.iter().filter_map(|&d| facts_of(d)).any(|d| dir(&d.path) == dir(&f.path))
                || imported();
            if sees {
                plan.entry(i).or_default().insert(name.clone());
            }
        }
    }
    let again: Vec<(usize, Option<FileFacts>)> = plan
        .into_par_iter()
        .map(|(i, names)| {
            let c = &files[i];
            let walk::FileKind::Code(lang) = c.kind else { return (i, None) };
            let Ok(src) = std::fs::read(&c.abs) else { return (i, None) };
            let lang = lang::detect(&c.rel, Some(&src)).unwrap_or(lang);
            let facts = parse::with_shadowed(std::sync::Arc::new(names), || {
                parse::file_facts(&c.rel, &src, Some(lang), None, timeout)
            });
            (i, Some(facts))
        })
        .collect();
    for (i, facts) in again {
        if let (Some(facts), Read::Facts(old, _, _)) = (facts, &mut reads[i]) {
            *old = facts;
        }
    }
}

/// Build the graph and its report. Writes `opts.out` when it is set.
pub fn build_graph(opts: &BuildOptions) -> Result<(Value, BuildReport), String> {
    build_inner(opts, true)
}

/// The build; the graph is handed back as a `Value` only when `want_graph`
/// (`build` writes it and keeps its edges typed to the end).
fn build_inner(opts: &BuildOptions, want_graph: bool) -> Result<(Value, BuildReport), String> {
    let started = Instant::now();
    let root = opts
        .root
        .canonicalize()
        .map_err(|e| format!("cannot read {}: {e}", opts.root.display()))?;
    if !root.is_dir() {
        return Err(format!("not a directory: {}", root.display()));
    }
    let found = walk::discover(
        &root,
        &opts.out_dir_name,
        opts.documents,
        opts.max_file_bytes,
        opts.read_vendor,
    );
    let mut phase = Instant::now();
    let mut memory: BTreeMap<&'static str, [u64; 2]> = BTreeMap::new();
    let mut lap = |name: &'static str, phases: &mut Vec<(&'static str, u128)>| {
        phases.push((name, phase.elapsed().as_millis()));
        if let Some(m) = memory_mb() {
            memory.insert(name, m);
        }
        phase = Instant::now();
    };
    let mut phases: Vec<(&'static str, u128)> = Vec::new();
    phases.push(("walk", started.elapsed().as_millis()));
    let cache_key = cache::Key {
        engine_version: VERSION.to_string(),
        schema_version: SCHEMA_VERSION,
        documents: opts.documents,
        max_file_bytes: opts.max_file_bytes,
    };
    let cache_path = opts.out.as_deref().and_then(cache::path_for);
    let cached = if opts.incremental {
        cache_path.as_deref().and_then(|p| cache::Cache::load(p, &cache_key))
    } else {
        None
    };
    let reads = match opts.threads {
        Some(n) => rayon::ThreadPoolBuilder::new()
            .num_threads(n.max(1))
            .build()
            .map_err(|e| e.to_string())?
            .install(|| read_all(&found.files, opts.parse_timeout, cached.as_ref())),
        None => read_all(&found.files, opts.parse_timeout, cached.as_ref()),
    };

    let mut reads = reads;
    reread_shadowed(&mut reads, &found.files, opts.parse_timeout);

    lap("parse", &mut phases);
    let mut report = BuildReport {
        engine: "graphlore",
        engine_version: VERSION,
        schema_version: SCHEMA_VERSION,
        root: root.display().to_string(),
        out: opts.out.as_ref().map(|p| p.display().to_string()).unwrap_or_default(),
        not_read: found.not_read,
        non_utf8_paths: found.non_utf8_paths,
        max_file_bytes: opts.max_file_bytes,
        ..Default::default()
    };
    let (too_large, n) = report::sample(found.too_large);
    report.too_large = too_large;
    report.too_large_count = n;
    report.vendored_skipped_count = found.vendored_skipped.iter().map(|(_, n)| n).sum();
    report.vendored_skipped = found.vendored_skipped.into_iter().map(|(d, _)| d).collect();

    let mut facts = Vec::new();
    let mut stamps = Vec::new();
    let mut unreadable = Vec::new();
    let mut timed_out = Vec::new();
    report.incremental.cache = cached.is_some();
    for r in reads {
        match r {
            Read::Unreadable(rel) => unreadable.push(rel),
            Read::Facts(f, stamp, reused) => {
                if reused {
                    report.incremental.reused += 1;
                } else {
                    report.incremental.parsed += 1;
                }
                stamps.push(stamp);
                if f.is_document {
                    report.documents += 1;
                    report.headings += f.headings.len();
                } else if let Some(lang) = f.lang {
                    let stats = report.languages.entry(lang.name().to_string()).or_default();
                    stats.files += 1;
                    stats.definitions += f.defs.len();
                    stats.parse_errors += usize::from(f.parse.has_error);
                    report.files += 1;
                    report.definitions += f.defs.len();
                    if f.parse.timed_out {
                        timed_out.push(f.path.clone());
                    }
                }
                facts.push(f);
            }
        }
    }
    if let Some(c) = &cached {
        let found: std::collections::HashSet<&str> =
            facts.iter().map(|f: &FileFacts| f.path.as_str()).collect();
        report.incremental.removed = c.removed(&found);
    }
    drop(cached);
    (report.unreadable, report.unreadable_count) = report::sample(unreadable);
    (report.timed_out, report.timed_out_count) = report::sample(timed_out);

    let mut assembled = graph::assemble::assemble(&facts);
    lap("assemble", &mut phases);
    // the language version each file's project states, per language
    let declared = versions::read(&root, &found.manifests);
    let mut language_versions: BTreeMap<String, BTreeMap<String, usize>> = BTreeMap::new();
    for f in &facts {
        let Some(lang) = f.lang else { continue };
        if let Some(v) = versions::of_file(&declared, &f.path, lang.name()) {
            *language_versions
                .entry(lang.name().to_string())
                .or_default()
                .entry(v.to_string())
                .or_default() += 1;
        }
    }
    // incremental resolution (`resolve/reuse.rs`): what every file's
    // resolution depends on beside what it reads, and the last build's cache
    let resolved_path = opts.out.as_deref().and_then(resolve::reuse::path_for);
    let resolve_key = resolved_path.as_ref().map(|_| {
        let mut h = resolve::trace::Fnv::new();
        h.write(format!("{cache_key:?}").as_bytes());
        h.sep();
        for f in &facts {
            h.write(f.path.as_bytes());
            h.sep();
        }
        for (rel, abs) in &found.manifests {
            h.write(rel.as_bytes());
            h.sep();
            h.write(&std::fs::read(abs).unwrap_or_default());
            h.sep();
        }
        for r in &found.repos {
            h.write(r.as_bytes());
            h.sep();
        }
        h.0
    });
    let previous_resolution = match (&resolved_path, resolve_key, opts.incremental) {
        (Some(path), Some(key), true) => resolve::reuse::load(path, key),
        _ => None,
    };
    let content: Vec<u64> = stamps.iter().map(|s: &cache::Stamp| s.hash).collect();
    let resolve::Resolution {
        nodes: ext_nodes,
        links: edges,
        stats: resolution,
        unresolved,
        phases: resolve_phases,
        stored: resolved_store,
        reused: resolve_reused,
    } = resolve::resolve(
        &root,
        &facts,
        &mut assembled,
        found.manifests,
        &found.repos,
        opts.unresolved.is_some(),
        resolve::Incremental {
            content: &content,
            previous: previous_resolution.as_ref(),
            key: resolve_key,
        },
    );
    drop(previous_resolution);
    report.incremental.resolve_reused = resolve_reused;
    if let (Some(path), Some(store)) = (&resolved_path, &resolved_store) {
        // (a cache that cannot be written costs the next update its speed)
        report.incremental.resolve_cache_written = resolve::reuse::store(path, store).is_ok();
    }
    drop(resolved_store);
    if let Some(path) = &opts.unresolved {
        let text = serde_json::to_string(&unresolved).map_err(|e| e.to_string())?;
        std::fs::write(path, text).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    }
    lap("resolve", &mut phases);
    phases.extend(resolve_phases);
    assembled.nodes.extend(ext_nodes);
    let documented = docs::link::edges(&facts, &assembled);
    report.doc_links = documented.len();
    // what reads the facts is done: the cache keeps them, and they are let go
    // before the graph's own phases (coupling, communities, the write), which
    // read only nodes and links. (A cache holds each file's facts, whatever the
    // graph's write does.)
    if let (Some(path), true) = (&cache_path, opts.out.is_some()) {
        // a cache that cannot be written costs the next update its speed, nothing else
        report.incremental.cache_written =
            cache::store(path, cache_key, facts.into_iter().zip(stamps).collect()).is_ok();
        lap("cache", &mut phases);
    } else {
        drop(facts);
        drop(stamps);
    }
    // the resolver's edges stay typed to the end (`graph::links`)
    let mut links = graph::links::Links {
        head: std::mem::take(&mut assembled.links),
        resolved: edges,
        tail: documented,
    };
    let (coupled, coupling) = coupling::detect(&mut assembled.nodes, &links);
    links.tail.extend(coupled);
    report.coupling = coupling;
    lap("coupling", &mut phases);
    let previous = opts.out.as_deref().and_then(community::Previous::read);
    lap("previous", &mut phases);
    let labels_path = opts.out.as_deref().and_then(|o| o.parent()).map(|d| d.join(LABELS_FILE));
    let saved = labels_path.as_deref().and_then(read_labels);
    let communities = community::detect(
        &mut assembled.nodes,
        &links,
        &previous.unwrap_or_default(),
        saved.as_ref(),
    );
    report.communities = communities.report.clone();
    lap("communities", &mut phases);
    report.nodes = assembled.nodes.len();
    report.edges = links.len();
    for n in &assembled.nodes {
        if matches!(n["kind"].as_str(), Some("variable" | "property" | "constant")) {
            report.state.state_nodes += 1;
        }
        if n["type"] == "route" {
            report.http.routes += 1;
            let fw = n["framework"].as_str().unwrap_or("?").to_string();
            *report.http.by_framework.entry(fw).or_default() += 1;
            if n["prefix_unresolved"] == true {
                report.http.prefix_unresolved += 1;
            }
        }
        if n["type"] == "resource" {
            let kind = n["resource"].as_str().unwrap_or("?").to_string();
            *report.state.resources.entry(kind).or_default() += 1;
        }
    }
    for e in graph::links::LinkSource::links(&links) {
        match e.relation() {
            "reads" => report.state.reads += 1,
            "writes" => report.state.writes += 1,
            "http_calls" => report.http.http_calls_edges += 1,
            _ => {}
        }
    }
    report.id_collisions = assembled.id_collisions;
    report.resolution = resolution;
    if let Some(r) = report.resolution.remove("resources") {
        report.state.single_touch = r.unresolved;
    }
    if let Some(r) = report.resolution.remove("http") {
        let get = |k: &str| r.edges.get(k).copied().unwrap_or(0);
        report.http.client_calls = get("client_calls");
        report.http.linked = get("linked");
        report.http.unlinked = get("unlinked");
        report.http.external = get("external");
        report.http.dynamic = get("dynamic");
        report.http.test_calls = get("test_calls");
    }

    let languages: Map<String, Value> =
        report.languages.iter().map(|(k, v)| (k.clone(), json!(v.files))).collect();
    let mut top = Map::new();
    top.insert("directed".into(), json!(false));
    top.insert("multigraph".into(), json!(false));
    top.insert(
        "graph".into(),
        json!({
            "engine": "graphlore",
            "engine_version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "languages": languages,
            "language_versions": language_versions,
            "documents": report.documents,
            "resolution": report.resolution,
            "communities": communities.header,
        }),
    );
    top.insert("nodes".into(), Value::Array(assembled.nodes));
    // (a place for the links, which `GraphOut` writes from `links`)
    top.insert("links".into(), Value::Null);
    top.insert("hyperedges".into(), json!([]));
    if let Some(commit) = opts.commit.as_ref().filter(|c| !c.is_empty()) {
        top.insert("built_at_commit".into(), json!(commit));
    }
    if !found.repos.is_empty() {
        // a workspace: each repository's own commit, where the one
        // `built_at_commit` of a single repository cannot say it
        let repos: Map<String, Value> = found
            .repos
            .iter()
            .map(|r| (r.clone(), json!({ "commit": walk::repo_commit(&root, r) })))
            .collect();
        if let Some(Value::Object(g)) = top.get_mut("graph") {
            g.insert("repos".into(), Value::Object(repos));
        }
    }
    if let Some(out) = &opts.out {
        graph::write::write_atomic(out, &graph::links::GraphOut { top: &top, links: &links })?;
        lap("write", &mut phases);
        if let (Some(path), Some(labels)) = (&labels_path, &communities.labels) {
            write_labels(path, labels)?;
        }
    }
    report.phases_ms = phases.into_iter().collect();
    report.memory_mb = memory;
    report.elapsed_ms = started.elapsed().as_millis();
    let graph = if want_graph {
        top.insert("links".into(), Value::Array(links.into_values()));
        Value::Object(top)
    } else {
        Value::Null
    };
    Ok((graph, report))
}

/// The process's resident memory and its peak so far, in MB: `VmRSS` and
/// `VmHWM` of /proc/self/status (Linux; None elsewhere).
fn memory_mb() -> Option<[u64; 2]> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let field = |name: &str| {
        status.lines().find_map(|l| {
            let kb: u64 =
                l.strip_prefix(name)?.trim().trim_end_matches("kB").trim().parse().ok()?;
            Some(kb / 1024)
        })
    };
    Some([field("VmRSS:")?, field("VmHWM:")?])
}

/// Where graphlore and graphify keep the names given to communities.
pub const LABELS_FILE: &str = ".graphify_labels.json";

fn read_json(path: &std::path::Path) -> Option<Value> {
    let text = std::fs::read(path).ok()?;
    serde_json::from_slice(&text).ok()
}

/// `{community id: name}`; anything else in the file is left out.
fn read_labels(path: &std::path::Path) -> Option<std::collections::BTreeMap<String, String>> {
    let v = read_json(path)?;
    Some(
        v.as_object()?
            .iter()
            .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
            .collect(),
    )
}

fn write_labels(
    path: &std::path::Path,
    labels: &std::collections::BTreeMap<String, String>,
) -> Result<(), String> {
    graph::write::write_atomic(path, &json!(labels))
}

/// Build and write the graph; the report says what was read and left out.
pub fn build(opts: &BuildOptions) -> Result<BuildReport, String> {
    build_inner(opts, false).map(|(_, r)| r)
}

/// Detect the communities of the graph.json at `path` again and rewrite it, as
/// `build` would (ids kept from the graph's own, stale saved names dropped).
pub fn cluster(path: &std::path::Path) -> Result<community::CommunityReport, String> {
    let mut graph = read_json(path).ok_or_else(|| format!("cannot read {}", path.display()))?;
    let prev = community::Previous::from_graph(&graph);
    let labels_path = path.parent().map(|d| d.join(LABELS_FILE));
    let saved = labels_path.as_deref().and_then(read_labels);
    let obj = graph.as_object_mut().ok_or("graph.json is not an object")?;
    let links = obj.get("links").or_else(|| obj.get("edges")).cloned().unwrap_or(json!([]));
    let links = links.as_array().map(Vec::as_slice).unwrap_or_default();
    let mut nodes = match obj.remove("nodes") {
        Some(Value::Array(nodes)) => nodes,
        _ => return Err("graph.json has no node list".into()),
    };
    let out = community::detect(&mut nodes, links, &prev, saved.as_ref());
    obj.insert("nodes".into(), Value::Array(nodes));
    let meta = obj.entry("graph").or_insert_with(|| json!({}));
    if let Some(meta) = meta.as_object_mut() {
        meta.insert("communities".into(), out.header);
        if meta.get("engine").and_then(Value::as_str) == Some("graphlore") {
            meta.insert("schema_version".into(), json!(SCHEMA_VERSION));
        }
    }
    graph::write::write_atomic(path, &graph)?;
    if let (Some(lp), Some(labels)) = (&labels_path, &out.labels) {
        write_labels(lp, labels)?;
    }
    Ok(out.report)
}

/// The facts of one file, its language detected from `rel`.
pub fn file_facts(src: &[u8], rel: &str) -> FileFacts {
    parse::facts_for_source(rel, src, Some(DEFAULT_PARSE_TIMEOUT))
}

/// `spans._spans_treesitter`'s answer for one file:
/// `(region_start, end_line, def_line, qualname)` per definition, source order.
pub fn file_spans(src: &[u8], rel: &str) -> Vec<(u32, u32, u32, String)> {
    file_facts(src, rel)
        .defs
        .into_iter()
        // (a block is a scope of the resolver's, no definition; nor is a
        // variable only this grammar took for a function's declaration)
        .filter(|d| !d.extra && !d.block && !d.unspanned)
        .map(|d| (d.region_start, d.end_line, d.def_line, d.qualname))
        .collect()
}

/// `file_spans`'s answer without the rest of the file's facts: the same
/// spans, for a caller that wants only them (graphlore's span engine).
pub fn definition_spans(src: &[u8], rel: &str) -> Vec<(u32, u32, u32, String)> {
    parse::defs_for_source(rel, src, Some(DEFAULT_PARSE_TIMEOUT))
        .into_iter()
        .filter(|d| !d.extra && !d.block && !d.unspanned)
        .map(|d| (d.region_start, d.end_line, d.def_line, d.qualname))
        .collect()
}

/// `(language, extensions)` for every language a module reads.
pub fn languages() -> Vec<(&'static str, Vec<&'static str>)> {
    lang::SPECS.iter().map(|s| (s.name, s.extensions.to_vec())).collect()
}
