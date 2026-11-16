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
    pub max_file_bytes: u64,
    pub elapsed_ms: u128,
}

pub fn sample(mut v: Vec<String>) -> (Vec<String>, usize) {
    let n = v.len();
    v.truncate(SAMPLE);
    (v, n)
}
