//! The facts cache behind `update=True`: what each file yielded last time, keyed
//! by its path and checked against its size, modification time and content
//! hash, so an update parses only the files that changed. Resolution and
//! communities always run over the whole project, so an update writes the same
//! graph a full build would.
//!
//! One file, `<out dir>/cache/graphlore-native.bin`, written atomically after
//! every build. A cache from another engine version or other build options is
//! ignored, and so is one that cannot be read: the build parses everything.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use bincode::Options;
use serde::{Deserialize, Deserializer, Serialize};

use crate::facts::FileFacts;

pub const CACHE_DIR: &str = "cache";
pub const CACHE_FILE: &str = "graphlore-native.bin";
/// A file modified this close to the cache being written may have changed
/// again within the same timestamp: its content hash decides.
const MTIME_SLACK_NS: u64 = 2_000_000_000;

/// What a file looked like when its facts were read.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Stamp {
    pub size: u64,
    pub mtime_ns: u64,
    pub hash: u64,
}

#[derive(Serialize, Deserialize)]
struct Entry {
    stamp: Stamp,
    facts: FileFacts,
}

/// Everything a cached fact depends on besides the file itself.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Key {
    pub engine_version: String,
    pub schema_version: u32,
    pub documents: bool,
    pub max_file_bytes: u64,
}

#[derive(Serialize, Deserialize)]
struct Stored {
    key: Key,
    written_ns: u64,
    files: HashMap<String, Entry>,
}

/// A cache read back for one build.
pub struct Cache {
    written_ns: u64,
    files: HashMap<String, Entry>,
}

fn codec() -> impl Options {
    bincode::DefaultOptions::new().with_fixint_encoding()
}

pub fn path_for(out: &Path) -> Option<PathBuf> {
    out.parent().map(|d| d.join(CACHE_DIR).join(CACHE_FILE))
}

pub fn now_ns() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos() as u64)
}

pub fn mtime_ns(meta: &fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos() as u64)
}

/// 64-bit FNV-1a of a file's bytes.
pub fn hash(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

impl Cache {
    /// The cache at `path`, if it was written for `key`.
    pub fn load(path: &Path, key: &Key) -> Option<Cache> {
        let file = fs::File::open(path).ok()?;
        let len = file.metadata().ok()?.len();
        // bounded by the file's size: a damaged length cannot ask for more memory
        let stored: Stored = codec().with_limit(len).deserialize_from(BufReader::new(file)).ok()?;
        (stored.key == *key).then_some(Cache { written_ns: stored.written_ns, files: stored.files })
    }

    pub fn len(&self) -> usize {
        self.files.len()
    }

    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
    }

    /// The cached facts of `rel` when size and modification time say the file
    /// is unchanged (and it was not modified around the time of the cache).
    pub fn unchanged(&self, rel: &str, size: u64, mtime: u64) -> Option<(FileFacts, Stamp)> {
        let e = self.files.get(rel)?;
        let settled = mtime.saturating_add(MTIME_SLACK_NS) < self.written_ns;
        (e.stamp.size == size && e.stamp.mtime_ns == mtime && mtime != 0 && settled)
            .then(|| (e.facts.clone(), e.stamp))
    }

    /// The cached facts of `rel` when its content is the same.
    pub fn same_content(&self, rel: &str, size: u64, hash: u64) -> Option<FileFacts> {
        let e = self.files.get(rel)?;
        (e.stamp.size == size && e.stamp.hash == hash).then(|| e.facts.clone())
    }

    /// Paths cached but not found by this build: deleted, renamed or now ignored.
    pub fn removed(&self, found: &HashSet<&str>) -> usize {
        self.files.keys().filter(|k| !found.contains(k.as_str())).count()
    }
}

/// Write the cache for the facts a build used; files whose parse timed out are
/// left out, to be tried again.
pub fn store(path: &Path, key: Key, facts: Vec<(FileFacts, Stamp)>) -> Result<(), String> {
    let files = facts
        .into_iter()
        .filter(|(f, _)| !f.parse.timed_out)
        .map(|(facts, stamp)| (facts.path.clone(), Entry { stamp, facts }))
        .collect();
    let stored = Stored { key, written_ns: now_ns(), files };
    let dir = path.parent().ok_or("no cache directory")?;
    fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let tmp = dir.join(format!("{CACHE_FILE}.tmp-{}", std::process::id()));
    let result = (|| {
        let file = fs::File::create(&tmp).map_err(|e| e.to_string())?;
        let mut w = BufWriter::new(file);
        codec().serialize_into(&mut w, &stored).map_err(|e| e.to_string())?;
        w.flush().map_err(|e| e.to_string())?;
        fs::rename(&tmp, path).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// Rebuild a `&'static str` field (a reference's `context`) from the cache:
/// each distinct value is allocated once per process.
pub fn static_str<'de, D: Deserializer<'de>>(d: D) -> Result<&'static str, D::Error> {
    let s = String::deserialize(d)?;
    Ok(intern(&s))
}

/// A `&'static str` of `s`: each distinct value is allocated once per process.
pub fn intern(s: &str) -> &'static str {
    static SEEN: OnceLock<Mutex<HashSet<&'static str>>> = OnceLock::new();
    let mut seen = SEEN.get_or_init(Default::default).lock().unwrap_or_else(|e| e.into_inner());
    if let Some(&known) = seen.get(s) {
        return known;
    }
    let leaked: &'static str = Box::leak(s.to_string().into_boxed_str());
    seen.insert(leaked);
    leaked
}
