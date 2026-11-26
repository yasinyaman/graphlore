//! What resolving one file's references read, for incremental resolution.
//!
//! The main loop opens a frame for each file; every read of another file's
//! facts (through `Traced`), every key asked of a project-wide index (through
//! `Named`) and every import use noted (`note_use`) goes into the innermost
//! open frame. A cache entry keeps the frame of the work that filled it, and a
//! hit hands that frame on (`absorb`), so a reused result never hides a read.
//! A file whose recorded reads are all unchanged resolves as it did: its
//! result can be taken from the last build.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::hash::Hash;
use std::ops::{Deref, Index};
use std::rc::Rc;

/// What a piece of resolution read.
#[derive(Default, Clone, Debug)]
pub struct Frame {
    /// The files whose facts (or per-file tables) were read, by index.
    pub files: Vec<u32>,
    /// The keys asked of project-wide indexes (`key_hash`).
    pub keys: Vec<u64>,
    /// Something read every file (a scan): any change counts.
    pub all: bool,
    /// Import uses noted (`Project::used`): `(file, import, file used)`.
    pub used: Vec<(u32, u32, u32)>,
    /// The cached frames already taken in (by address, each held so that
    /// its address stays its own): a cache hit that comes again adds nothing.
    taken: HashMap<usize, Rc<Frame>>,
    /// The size past which the lists are sorted and deduplicated again
    /// (doubled each time, so that a large frame is not sorted on every
    /// absorb).
    limit: usize,
}

impl Frame {
    fn tidy(&mut self) {
        self.files.sort_unstable();
        self.files.dedup();
        self.keys.sort_unstable();
        self.keys.dedup();
        self.used.sort_unstable();
        self.used.dedup();
        self.limit = (2 * (self.files.len() + self.keys.len() + self.used.len())).max(4096);
    }

    fn absorb(&mut self, other: &Frame) {
        self.files.extend_from_slice(&other.files);
        self.keys.extend_from_slice(&other.keys);
        self.all |= other.all;
        self.used.extend_from_slice(&other.used);
        if self.files.len() + self.keys.len() + self.used.len() > self.limit.max(4096) {
            self.tidy();
        }
    }

    pub fn is_empty(&self) -> bool {
        self.files.is_empty() && self.keys.is_empty() && !self.all && self.used.is_empty()
    }
}

/// The frames open now, innermost last.
#[derive(Default)]
pub struct Trace {
    stack: RefCell<Vec<Frame>>,
    /// Where a scan made a file read everything (a measurement:
    /// `GRAPHLORE_TRACE_STATS`).
    pub scans: RefCell<HashMap<u32, usize>>,
}

impl Trace {
    pub fn file(&self, i: usize) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            if top.files.last() != Some(&(i as u32)) {
                top.files.push(i as u32);
                if top.files.len() > top.limit.max(4096) {
                    top.tidy();
                }
            }
        }
    }

    pub fn key(&self, k: u64) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            if top.keys.last() != Some(&k) {
                top.keys.push(k);
                if top.keys.len() > top.limit.max(4096) {
                    top.tidy();
                }
            }
        }
    }

    #[track_caller]
    pub fn all(&self) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            if !top.all {
                *self
                    .scans
                    .borrow_mut()
                    .entry(std::panic::Location::caller().line())
                    .or_default() += 1;
            }
            top.all = true;
        }
    }

    pub fn used(&self, f: usize, imp: usize, g: usize) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.used.push((f as u32, imp as u32, g as u32));
        }
    }

    pub fn open(&self) {
        self.stack.borrow_mut().push(Frame::default());
    }

    /// Close the innermost frame: what it read is also what the frame
    /// around it read.
    pub fn close(&self) -> Rc<Frame> {
        let mut stack = self.stack.borrow_mut();
        let mut frame = stack.pop().unwrap_or_default();
        frame.tidy();
        frame.taken = HashMap::new();
        if let Some(top) = stack.last_mut() {
            top.absorb(&frame);
        }
        Rc::new(frame)
    }

    /// Close a file's frame, the outermost: a frame still open inside it (an
    /// early return between `open` and `close`) is closed into it, and the
    /// file is then taken to read every file, so that nothing it read is lost.
    pub fn close_file(&self) -> Rc<Frame> {
        let mut stack = self.stack.borrow_mut();
        let mut unbalanced = false;
        while stack.len() > 1 {
            let inner = stack.pop().unwrap_or_default();
            if let Some(top) = stack.last_mut() {
                top.absorb(&inner);
            }
            unbalanced = true;
        }
        let mut frame = stack.pop().unwrap_or_default();
        if unbalanced {
            *self.scans.borrow_mut().entry(0).or_default() += 1;
        }
        frame.all |= unbalanced;
        frame.tidy();
        frame.taken = HashMap::new();
        Rc::new(frame)
    }

    /// Close the innermost frame and drop what it read: work done for the
    /// whole project, which a key stands for (`go_aliases`).
    pub fn discard(&self) {
        self.stack.borrow_mut().pop();
    }

    /// A cached result was taken: what computing it read is read now.
    pub fn absorb(&self, frame: &Rc<Frame>) {
        if frame.is_empty() {
            return;
        }
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            let at = Rc::as_ptr(frame) as usize;
            if let std::collections::hash_map::Entry::Vacant(e) = top.taken.entry(at) {
                e.insert(frame.clone());
                top.absorb(frame);
            }
        }
    }
}

/// FNV-1a: the same in every build, so that a key read last time can be
/// compared with this build's index.
pub struct Fnv(pub u64);

impl Fnv {
    pub fn new() -> Self {
        Fnv(0xcbf2_9ce4_8422_2325)
    }

    pub fn write(&mut self, bytes: &[u8]) {
        for b in bytes {
            self.0 ^= u64::from(*b);
            self.0 = self.0.wrapping_mul(0x0100_0000_01b3);
        }
    }

    /// A separator, so that `("ab", "c")` and `("a", "bc")` differ.
    pub fn sep(&mut self) {
        self.write(&[0xff]);
    }
}

impl Default for Fnv {
    fn default() -> Self {
        Fnv::new()
    }
}

/// The hash of an index's name and a key.
pub fn key_hash<Q: KeyText + ?Sized>(index: &str, key: &Q) -> u64 {
    let mut h = Fnv::new();
    h.write(index.as_bytes());
    h.sep();
    key.feed(&mut h);
    h.0
}

/// A per-file table whose reads by index are recorded.
pub struct Traced<S> {
    inner: S,
    trace: Rc<Trace>,
}

impl<S> Traced<S> {
    pub fn new(inner: S, trace: Rc<Trace>) -> Self {
        Traced { inner, trace }
    }

    /// The table without recording: for building the indexes, and for the
    /// phases after the main loop.
    pub fn raw(&self) -> &S {
        &self.inner
    }

    pub fn raw_mut(&mut self) -> &mut S {
        &mut self.inner
    }
}

impl<T, S: Deref<Target = [T]>> Traced<S> {
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn get(&self, i: usize) -> Option<&T> {
        self.trace.file(i);
        self.inner.get(i)
    }

    /// Every file's: a scan, which any change can affect.
    #[track_caller]
    pub fn iter(&self) -> std::slice::Iter<'_, T> {
        self.trace.all();
        self.inner.iter()
    }
}

impl<T, S: Deref<Target = [T]>> Index<usize> for Traced<S> {
    type Output = T;
    fn index(&self, i: usize) -> &T {
        self.trace.file(i);
        &self.inner[i]
    }
}

/// How a key of a project-wide index goes into `key_hash`.
pub trait KeyText {
    fn feed(&self, h: &mut Fnv);
}

impl KeyText for String {
    fn feed(&self, h: &mut Fnv) {
        h.write(self.as_bytes());
    }
}

impl KeyText for str {
    fn feed(&self, h: &mut Fnv) {
        h.write(self.as_bytes());
    }
}

impl<A: KeyText, B: KeyText> KeyText for (A, B) {
    fn feed(&self, h: &mut Fnv) {
        self.0.feed(h);
        h.sep();
        self.1.feed(h);
    }
}

/// A project-wide index whose lookups by key are recorded (an absent key
/// too: a definition of that name appearing later changes the answer).
pub struct Named<K, V> {
    name: &'static str,
    inner: HashMap<K, V>,
    trace: Rc<Trace>,
}

impl<K: Eq + Hash + KeyText, V> Named<K, V> {
    pub fn new(name: &'static str, trace: Rc<Trace>) -> Self {
        Named { name, inner: HashMap::new(), trace }
    }

    fn note<Q: KeyText + ?Sized>(&self, k: &Q) {
        self.trace.key(key_hash(self.name, k));
    }

    pub fn get<Q>(&self, k: &Q) -> Option<&V>
    where
        K: std::borrow::Borrow<Q>,
        Q: Eq + Hash + KeyText + ?Sized,
    {
        self.note(k);
        self.inner.get(k)
    }

    pub fn contains_key<Q>(&self, k: &Q) -> bool
    where
        K: std::borrow::Borrow<Q>,
        Q: Eq + Hash + KeyText + ?Sized,
    {
        self.note(k);
        self.inner.contains_key(k)
    }

    /// Every entry: a scan.
    #[track_caller]
    pub fn iter(&self) -> std::collections::hash_map::Iter<'_, K, V> {
        self.trace.all();
        self.inner.iter()
    }

    #[track_caller]
    pub fn values(&self) -> std::collections::hash_map::Values<'_, K, V> {
        self.trace.all();
        self.inner.values()
    }

    pub fn raw(&self) -> &HashMap<K, V> {
        &self.inner
    }

    pub fn raw_mut(&mut self) -> &mut HashMap<K, V> {
        &mut self.inner
    }

    /// Each key's `key_hash` with a hash of what it holds, for comparing
    /// one build's index with the last one's.
    pub fn fingerprints(&self, value: impl Fn(&V) -> u64) -> HashMap<u64, u64> {
        self.inner.iter().map(|(k, v)| (key_hash(self.name, k), value(v))).collect()
    }
}

/// A project-wide set whose membership questions are recorded.
pub struct NamedSet<K> {
    name: &'static str,
    inner: HashSet<K>,
    trace: Rc<Trace>,
}

impl<K: Eq + Hash + KeyText> NamedSet<K> {
    pub fn new(name: &'static str, trace: Rc<Trace>) -> Self {
        NamedSet { name, inner: HashSet::new(), trace }
    }

    pub fn contains<Q>(&self, k: &Q) -> bool
    where
        K: std::borrow::Borrow<Q>,
        Q: Eq + Hash + KeyText + ?Sized,
    {
        self.trace.key(key_hash(self.name, k));
        self.inner.contains(k)
    }

    #[track_caller]
    pub fn iter(&self) -> std::collections::hash_set::Iter<'_, K> {
        self.trace.all();
        self.inner.iter()
    }

    pub fn raw_mut(&mut self) -> &mut HashSet<K> {
        &mut self.inner
    }

    pub fn fingerprints(&self) -> HashMap<u64, u64> {
        self.inner.iter().map(|k| (key_hash(self.name, k), 1)).collect()
    }
}

impl KeyText for &str {
    fn feed(&self, h: &mut Fnv) {
        h.write(self.as_bytes());
    }
}

/// A key that names the file it belongs to.
pub trait FileOf {
    fn file_of(&self) -> usize;
}

impl FileOf for (usize, usize) {
    fn file_of(&self) -> usize {
        self.0
    }
}

impl FileOf for (usize, usize, String) {
    fn file_of(&self) -> usize {
        self.0
    }
}

/// A table keyed by file (a file's locals, a type's fields): a lookup is a
/// read of that file.
pub struct FileKeyed<K, V> {
    inner: HashMap<K, V>,
    trace: Rc<Trace>,
}

impl<K: Eq + Hash + FileOf, V> FileKeyed<K, V> {
    pub fn new(trace: Rc<Trace>) -> Self {
        FileKeyed { inner: HashMap::new(), trace }
    }

    pub fn get(&self, k: &K) -> Option<&V> {
        self.trace.file(k.file_of());
        self.inner.get(k)
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn raw_mut(&mut self) -> &mut HashMap<K, V> {
        &mut self.inner
    }
}
