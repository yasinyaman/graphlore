//! graphlore's native code-graph extractor.
//!
//! One pass over a project: discover files (`walk`), parse each in parallel with
//! a tree-sitter grammar compiled into this library (`parse`), extract the
//! definitions with the generic walk plus per-language modules (`generic`,
//! `lang`), read document headings (`docs`), and assemble a graph.json that
//! graphlore's readers — and graphify's own `query`/`path`/`explain` — accept
//! (`graph`). Nothing here reaches the network.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use rayon::prelude::*;
use serde_json::{json, Map, Value};

pub mod cache;
pub mod community;
pub mod docs;
pub mod facts;
pub mod generic;
pub mod graph;
pub mod lang;
pub mod parse;
pub mod refs;
pub mod report;
pub mod resolve;
pub mod resources;
pub mod state;
pub mod walk;

#[cfg(feature = "python")]
mod py;

pub use facts::{Def, DefKind, FileFacts};
pub use lang::Lang;
pub use report::BuildReport;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
/// Bumped when graph.json changes in a way graphlore's readers must know about.
pub const SCHEMA_VERSION: u32 = 4;
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

/// Build the graph and its report. Writes `opts.out` when it is set.
pub fn build_graph(opts: &BuildOptions) -> Result<(Value, BuildReport), String> {
    let started = Instant::now();
    let root = opts
        .root
        .canonicalize()
        .map_err(|e| format!("cannot read {}: {e}", opts.root.display()))?;
    if !root.is_dir() {
        return Err(format!("not a directory: {}", root.display()));
    }
    let found = walk::discover(&root, &opts.out_dir_name, opts.documents, opts.max_file_bytes);
    let mut phase = Instant::now();
    let mut lap = |name: &'static str, phases: &mut Vec<(&'static str, u128)>| {
        phases.push((name, phase.elapsed().as_millis()));
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
    let (ext_nodes, edges, resolution) =
        resolve::resolve(&root, &facts, &mut assembled, found.manifests);
    lap("resolve", &mut phases);
    assembled.nodes.extend(ext_nodes);
    assembled.links.extend(edges);
    let previous = opts.out.as_deref().and_then(community::Previous::read);
    lap("previous", &mut phases);
    let labels_path = opts.out.as_deref().and_then(|o| o.parent()).map(|d| d.join(LABELS_FILE));
    let saved = labels_path.as_deref().and_then(read_labels);
    let communities = community::detect(
        &mut assembled.nodes,
        &assembled.links,
        &previous.unwrap_or_default(),
        saved.as_ref(),
    );
    report.communities = communities.report.clone();
    lap("communities", &mut phases);
    report.nodes = assembled.nodes.len();
    report.edges = assembled.links.len();
    for n in &assembled.nodes {
        if matches!(n["kind"].as_str(), Some("variable" | "property" | "constant")) {
            report.state.state_nodes += 1;
        }
        if n["type"] == "resource" {
            let kind = n["resource"].as_str().unwrap_or("?").to_string();
            *report.state.resources.entry(kind).or_default() += 1;
        }
    }
    for e in &assembled.links {
        match e["relation"].as_str() {
            Some("reads") => report.state.reads += 1,
            Some("writes") => report.state.writes += 1,
            _ => {}
        }
    }
    report.id_collisions = assembled.id_collisions;
    report.resolution = resolution;
    if let Some(r) = report.resolution.remove("resources") {
        report.state.single_touch = r.unresolved;
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
            "documents": report.documents,
            "resolution": report.resolution,
            "communities": communities.header,
        }),
    );
    top.insert("nodes".into(), Value::Array(assembled.nodes));
    top.insert("links".into(), Value::Array(assembled.links));
    top.insert("hyperedges".into(), json!([]));
    if let Some(commit) = opts.commit.as_ref().filter(|c| !c.is_empty()) {
        top.insert("built_at_commit".into(), json!(commit));
    }
    let graph = Value::Object(top);
    if let Some(out) = &opts.out {
        graph::write::write_atomic(out, &graph)?;
        lap("write", &mut phases);
        if let (Some(path), Some(labels)) = (&labels_path, &communities.labels) {
            write_labels(path, labels)?;
        }
        if let Some(path) = &cache_path {
            // a cache that cannot be written costs the next update its speed, nothing else
            report.incremental.cache_written =
                cache::store(path, cache_key, facts.into_iter().zip(stamps).collect()).is_ok();
            lap("cache", &mut phases);
        }
    }
    report.phases_ms = phases.into_iter().collect();
    report.elapsed_ms = started.elapsed().as_millis();
    Ok((graph, report))
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
    build_graph(opts).map(|(_, r)| r)
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
        .filter(|d| !d.extra)
        .map(|d| (d.region_start, d.end_line, d.def_line, d.qualname))
        .collect()
}

/// `(language, extensions)` for every language a module reads.
pub fn languages() -> Vec<(&'static str, Vec<&'static str>)> {
    lang::SPECS.iter().map(|s| (s.name, s.extensions.to_vec())).collect()
}
