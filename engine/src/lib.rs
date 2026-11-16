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

pub mod docs;
pub mod facts;
pub mod generic;
pub mod graph;
pub mod lang;
pub mod parse;
pub mod refs;
pub mod report;
pub mod resolve;
pub mod walk;

#[cfg(feature = "python")]
mod py;

pub use facts::{Def, DefKind, FileFacts};
pub use lang::Lang;
pub use report::BuildReport;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
/// Bumped when graph.json changes in a way graphlore's readers must know about.
pub const SCHEMA_VERSION: u32 = 2;
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
        }
    }
}

enum Read {
    Facts(FileFacts),
    Unreadable(String),
}

fn read_all(files: &[walk::Candidate], timeout: Option<Duration>) -> Vec<Read> {
    files
        .par_iter()
        .map(|c| match std::fs::read(&c.abs) {
            Err(_) => Read::Unreadable(c.rel.clone()),
            Ok(src) => Read::Facts(match c.kind {
                walk::FileKind::Code(lang) => {
                    let lang = lang::detect(&c.rel, Some(&src)).unwrap_or(lang);
                    parse::file_facts(&c.rel, &src, Some(lang), None, timeout)
                }
                walk::FileKind::Doc(kind) => {
                    parse::file_facts(&c.rel, &src, None, Some(kind), timeout)
                }
            }),
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
    let reads = match opts.threads {
        Some(n) => rayon::ThreadPoolBuilder::new()
            .num_threads(n.max(1))
            .build()
            .map_err(|e| e.to_string())?
            .install(|| read_all(&found.files, opts.parse_timeout)),
        None => read_all(&found.files, opts.parse_timeout),
    };

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
    let mut unreadable = Vec::new();
    let mut timed_out = Vec::new();
    for r in reads {
        match r {
            Read::Unreadable(rel) => unreadable.push(rel),
            Read::Facts(f) => {
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
    (report.unreadable, report.unreadable_count) = report::sample(unreadable);
    (report.timed_out, report.timed_out_count) = report::sample(timed_out);

    let mut assembled = graph::assemble::assemble(&facts);
    let (ext_nodes, edges, resolution) =
        resolve::resolve(&root, &facts, &mut assembled, found.manifests);
    assembled.nodes.extend(ext_nodes);
    assembled.links.extend(edges);
    report.nodes = assembled.nodes.len();
    report.edges = assembled.links.len();
    report.id_collisions = assembled.id_collisions;
    report.resolution = resolution;

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
    }
    report.elapsed_ms = started.elapsed().as_millis();
    Ok((graph, report))
}

/// Build and write the graph; the report says what was read and left out.
pub fn build(opts: &BuildOptions) -> Result<BuildReport, String> {
    build_graph(opts).map(|(_, r)| r)
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
