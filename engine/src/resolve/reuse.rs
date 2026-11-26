//! Incremental resolution: each file's resolution from the last build, with
//! what it read (`trace.rs`), and the prints that say whether what it read
//! has changed since.
//!
//! A build stores, per file, its edges and the rest of its output beside the
//! files it read, the index keys it asked and the import uses it noted; per
//! file a print of what others read of it (its content, its scope, its node
//! ids); per key a print of what the index holds there. An update takes a
//! file's output from there when its own print, the prints of the files it
//! read and the prints of the keys it asked are all the same: resolution is a
//! function of those, so it would give the same again. Anything else (another
//! set of paths, other manifests, another engine) resolves every file.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

use bincode::Options;
use serde::{Deserialize, Serialize};

use crate::facts::Runs;

pub const FILE: &str = "graphlore-resolved.bin";

/// The print of a key no index holds: a definition appearing under it later
/// changes the answer, so its absence is a print too.
pub const ABSENT: u64 = 0;

/// One edge of a file's resolution.
#[derive(Clone, Serialize, Deserialize)]
pub struct StoredEdge {
    pub source: String,
    pub target: String,
    #[serde(deserialize_with = "crate::cache::static_str")]
    pub relation: crate::facts::Context,
    pub inferred: bool,
    pub file: u32,
    pub line: u32,
    #[serde(deserialize_with = "crate::cache::static_str")]
    pub context: crate::facts::Context,
    pub runs: Option<Runs>,
}

/// What one file's resolution gave and read.
#[derive(Clone, Default, Serialize, Deserialize)]
pub struct StoredFile {
    pub edges: Vec<StoredEdge>,
    pub orm: Vec<(String, String, u32, u32, bool)>,
    pub ext: Vec<(String, String, String)>,
    pub unresolved: usize,
    pub files: Vec<u32>,
    pub keys: Vec<u64>,
    pub all: bool,
    pub used: Vec<(u32, u32, u32)>,
}

/// The resolution cache of one build.
#[derive(Default, Serialize, Deserialize)]
pub struct Stored {
    /// What every file's resolution depends on beside what it read: the
    /// engine and its options, the paths, the manifests (`global_key`).
    pub key: u64,
    /// Per file (`None`: not resolved, a file of no family).
    pub files: Vec<Option<StoredFile>>,
    /// Per file: its print (`Project::file_print`), and the print of its
    /// declarations (`Project::decl_print`).
    pub prints: Vec<u64>,
    pub decl: Vec<u64>,
    /// The prints of the keys some file asked.
    pub keys: HashMap<u64, u64>,
}

fn codec() -> impl Options {
    bincode::DefaultOptions::new().with_fixint_encoding()
}

pub fn path_for(out: &Path) -> Option<PathBuf> {
    out.parent().map(|d| d.join(crate::cache::CACHE_DIR).join(FILE))
}

/// The cache at `path`, when it was written for `key`.
pub fn load(path: &Path, key: u64) -> Option<Stored> {
    let file = fs::File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    let stored: Stored = codec().with_limit(len).deserialize_from(BufReader::new(file)).ok()?;
    (stored.key == key).then_some(stored)
}

pub fn store(path: &Path, stored: &Stored) -> Result<(), String> {
    let dir = path.parent().ok_or("no cache directory")?;
    fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let tmp = dir.join(format!("{FILE}.tmp-{}", std::process::id()));
    let result = (|| {
        let file = fs::File::create(&tmp).map_err(|e| e.to_string())?;
        let mut w = BufWriter::new(file);
        codec().serialize_into(&mut w, stored).map_err(|e| e.to_string())?;
        w.flush().map_err(|e| e.to_string())?;
        fs::rename(&tmp, path).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// Which files' resolution can be taken from `prev`: every file whose own
/// print, the prints of what it read of other files (all of a file, or its
/// declarations) and the prints of the keys it asked are the same now.
pub fn reusable(
    prev: &Stored,
    prints: &[u64],
    decl: &[u64],
    keys: &HashMap<u64, u64>,
) -> Vec<bool> {
    if prev.prints.len() != prints.len() || prev.decl.len() != decl.len() {
        return vec![false; prints.len()];
    }
    let changed: Vec<bool> = prev.prints.iter().zip(prints).map(|(a, b)| a != b).collect();
    let changed_decl: Vec<bool> = prev.decl.iter().zip(decl).map(|(a, b)| a != b).collect();
    let read_changed = |code: u32| {
        let (g, what) = crate::resolve::trace::part::of(code);
        let by = if what == crate::resolve::trace::part::DECL { &changed_decl } else { &changed };
        by.get(g).copied().unwrap_or(true)
    };
    let changed_keys: HashSet<u64> = prev
        .keys
        .iter()
        .filter(|(k, v)| keys.get(k).copied().unwrap_or(ABSENT) != **v)
        .map(|(k, _)| *k)
        .collect();
    let anything = changed.iter().any(|c| *c) || !changed_keys.is_empty();
    if std::env::var("GRAPHLORE_REUSE_WHY").is_ok_and(|v| v == "1") {
        for (f, stored) in prev.files.iter().enumerate() {
            let Some(s) = stored else { continue };
            let files: Vec<(usize, u32)> = s
                .files
                .iter()
                .filter(|&&c| read_changed(c))
                .map(|&c| crate::resolve::trace::part::of(c))
                .collect();
            let keys = s.keys.iter().filter(|k| changed_keys.contains(k)).count();
            if changed[f] || !files.is_empty() || keys > 0 || (s.all && anything) {
                eprintln!(
                    "reuse why: file {f}: own {}, files read changed {files:?}, keys {keys}, all {}",
                    changed[f], s.all
                );
            }
        }
    }
    prev.files
        .iter()
        .enumerate()
        .map(|(f, stored)| {
            let Some(s) = stored else { return false };
            !changed[f]
                && !(s.all && anything)
                && s.files.iter().all(|&code| !read_changed(code))
                && s.keys.iter().all(|k| !changed_keys.contains(k))
        })
        .collect()
}
