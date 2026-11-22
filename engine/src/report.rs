//! What a build reports back: counts per language and everything it left out.

use std::collections::BTreeMap;

use serde::Serialize;

/// Lists in the report are cut to this many entries; the counts stay whole.
pub const SAMPLE: usize = 20;

#[derive(Default, Serialize)]
pub struct LangStats {
    pub files: usize,
    pub definitions: usize,
    pub parse_errors: usize,
}

#[derive(Default, Serialize)]
pub struct BuildReport {
    pub engine: &'static str,
    pub engine_version: &'static str,
    pub schema_version: u32,
    pub root: String,
    pub out: String,
    pub nodes: usize,
    pub edges: usize,
    pub files: usize,
    pub documents: usize,
    pub definitions: usize,
    pub headings: usize,
    /// `documents` edges: a document naming one definition of the project.
    pub doc_links: usize,
    pub languages: BTreeMap<String, LangStats>,
    /// Source files with an extension graphlore knows but no module reads.
    pub not_read: BTreeMap<String, usize>,
    pub too_large: Vec<String>,
    pub too_large_count: usize,
    pub unreadable: Vec<String>,
    pub unreadable_count: usize,
    pub timed_out: Vec<String>,
    pub timed_out_count: usize,
    pub non_utf8_paths: usize,
    pub id_collisions: usize,
    /// Edges by relation, inferred and unresolved counts, per language family.
    pub resolution: BTreeMap<String, crate::resolve::ResolveStats>,
    pub communities: crate::community::CommunityReport,
    pub incremental: Incremental,
    pub state: StateReport,
    pub http: HttpReport,
    /// The definitions that share state, paired up (`coupling.rs`).
    pub coupling: crate::coupling::CouplingReport,
    /// Milliseconds per phase: walk, parse, assemble, resolve, communities, write, cache.
    pub phases_ms: BTreeMap<&'static str, u128>,
    pub max_file_bytes: u64,
    pub elapsed_ms: u128,
}

/// Shared state: what definitions read and write besides calling each other.
#[derive(Default, Serialize)]
pub struct StateReport {
    /// Variables, fields and constants (nodes).
    pub state_nodes: usize,
    pub reads: usize,
    pub writes: usize,
    /// Resource nodes by kind: env, file, table, event, key.
    pub resources: BTreeMap<String, usize>,
    /// Resources only one definition touches: no coupling, so no node.
    pub single_touch: usize,
}

/// Web routes and the HTTP calls that reach them.
#[derive(Default, Serialize)]
pub struct HttpReport {
    /// Route nodes, one per method and full path.
    pub routes: usize,
    pub by_framework: BTreeMap<String, usize>,
    /// Routes with part of a prefix named something whose value is unknown.
    pub prefix_unresolved: usize,
    /// Requests production code makes (`fetch`, `axios`, a generated SDK,
    /// `requests`, `httpx`, …).
    pub client_calls: usize,
    /// Of those, linked to a route of the project by `http_calls`.
    pub linked: usize,
    /// To a path no route has.
    pub unlinked: usize,
    /// To another host.
    pub external: usize,
    /// To a URL the source does not spell out.
    pub dynamic: usize,
    /// Requests in test code: counted, never linked.
    pub test_calls: usize,
    pub http_calls_edges: usize,
}

/// What an update reused from the facts cache.
#[derive(Default, Serialize)]
pub struct Incremental {
    /// A cache for this engine version and these options was found.
    pub cache: bool,
    /// Files whose facts came from the cache.
    pub reused: usize,
    /// Files parsed.
    pub parsed: usize,
    /// Cached files this build did not find.
    pub removed: usize,
    pub cache_written: bool,
}

pub fn sample(mut v: Vec<String>) -> (Vec<String>, usize) {
    let n = v.len();
    v.truncate(SAMPLE);
    (v, n)
}
