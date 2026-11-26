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

/// What a piece of resolution read, once it is done: its own reads (sorted,
/// without duplicates) and the frames of the work it took in (the pieces it
/// opened, the cached answers it used). A file's frame is flattened when
/// the file is done (`close_file`): copying the reads up at every level costs
/// the depth of the work times what it read.
#[derive(Default, Clone, Debug)]
pub struct Frame {
    /// The files whose facts (or per-file tables) were read, by index.
    pub files: Box<[u32]>,
    /// The keys asked of project-wide indexes (`key_hash`).
    pub keys: Box<[u64]>,
    /// Something read every file (a scan): any change counts.
    pub all: bool,
    /// Import uses noted (`Project::used`): `(file, import, file used)`.
    pub used: Box<[(u32, u32, u32)]>,
    /// The frames taken in; empty in a flattened frame.
    pub parts: Box<[Rc<Frame>]>,
}

impl Frame {
    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
            && self.keys.is_empty()
            && !self.all
            && self.used.is_empty()
            && self.parts.is_empty()
    }
}

/// A frame being filled: a bit per file and a set of keys say what is in
/// it already, so that a read is counted once.
#[derive(Default)]
struct Open {
    bits: Vec<u64>,
    files: Vec<u32>,
    key_set: HashSet<u64>,
    keys: Vec<u64>,
    all: bool,
    used: HashSet<(u32, u32, u32)>,
    /// The frames taken in, each once (by address; each is held, so that
    /// its address stays its own).
    parts: Vec<Rc<Frame>>,
    part_set: HashSet<usize>,
}

impl Open {
    fn file(&mut self, i: u32) {
        let (w, b) = ((i / 64) as usize, i % 64);
        if w >= self.bits.len() {
            self.bits.resize(w + 1, 0);
        }
        if self.bits[w] & (1 << b) == 0 {
            self.bits[w] |= 1 << b;
            self.files.push(i);
        }
    }

    fn key(&mut self, k: u64) {
        if self.key_set.insert(k) {
            self.keys.push(k);
        }
    }

    fn part(&mut self, frame: &Rc<Frame>) {
        if frame.is_empty() {
            return;
        }
        if self.part_set.insert(Rc::as_ptr(frame) as usize) {
            self.parts.push(frame.clone());
        }
    }

    /// The finished frame; the buffers are emptied for the next one.
    fn finish(&mut self) -> Frame {
        for &f in &self.files {
            self.bits[(f / 64) as usize] = 0;
        }
        let mut files = std::mem::take(&mut self.files);
        files.sort_unstable();
        self.key_set.clear();
        let mut keys = std::mem::take(&mut self.keys);
        keys.sort_unstable();
        let mut used: Vec<(u32, u32, u32)> = self.used.drain().collect();
        used.sort_unstable();
        self.part_set.clear();
        let parts = std::mem::take(&mut self.parts);
        let all = std::mem::take(&mut self.all);
        Frame {
            files: files.into_boxed_slice(),
            keys: keys.into_boxed_slice(),
            all,
            used: used.into_boxed_slice(),
            parts: parts.into_boxed_slice(),
        }
    }
}

/// Everything a frame and the frames it took in read, each frame visited once.
fn flatten(top: Frame) -> Frame {
    let mut out = Open::default();
    for &f in &top.files {
        out.file(f);
    }
    for &k in &top.keys {
        out.key(k);
    }
    out.all = top.all;
    out.used.extend(top.used.iter().copied());
    let mut seen: HashSet<usize> = HashSet::new();
    let mut todo: Vec<Rc<Frame>> = top.parts.into_vec();
    while let Some(fr) = todo.pop() {
        if !seen.insert(Rc::as_ptr(&fr) as usize) {
            continue;
        }
        for &f in &fr.files {
            out.file(f);
        }
        for &k in &fr.keys {
            out.key(k);
        }
        out.all |= fr.all;
        out.used.extend(fr.used.iter().copied());
        todo.extend(fr.parts.iter().cloned());
    }
    let mut flat = out.finish();
    flat.parts = Box::new([]);
    flat
}

/// What a small frame with no parts is shared by: its files, keys and `all`.
type SmallKey = (Box<[u32]>, Box<[u64]>, bool);

/// The frames open now, innermost last, and buffers kept for the next ones.
#[derive(Default)]
pub struct Trace {
    stack: RefCell<Vec<Open>>,
    spare: RefCell<Vec<Open>>,
    /// Where a scan made a file read everything (a measurement:
    /// `GRAPHLORE_TRACE_STATS`).
    pub scans: RefCell<HashMap<u32, usize>>,
    /// Reads by the line that made them (a measurement).
    pub sites: RefCell<HashMap<(&'static str, u32), usize>>,
    /// Frames closed, empty ones among them, and their own files, keys and
    /// parts (a measurement).
    pub counts: RefCell<[u64; 5]>,
    /// Kept as: the empty frame, its one part, a shared small one, a new
    /// small one, a new large one, and the large ones' entries (a measurement).
    pub kept: RefCell<[u64; 6]>,
    /// The empty frame, and the small frames with no parts, each shared by
    /// all that read the same (typing every local of a file reads that file):
    /// there are millions of frames, most of them alike.
    empty: Rc<Frame>,
    small: RefCell<HashMap<SmallKey, Rc<Frame>>>,
    /// A counter that marks where a cached value was worked out, and the
    /// marks at which the kept frames now open began (`valid`).
    epoch: std::cell::Cell<u32>,
    kept_starts: RefCell<Vec<u32>>,
}

/// The mark of a cache entry valid everywhere: a cycle's placeholder.
pub const ALWAYS: u32 = u32::MAX;

/// Whether reads are recorded at all: off, every frame is empty, and no
/// file's resolution can be reused (a build that writes no cache).
pub static ENABLED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(true);

fn enabled() -> bool {
    ENABLED.load(std::sync::atomic::Ordering::Relaxed)
}

/// Count reads by call site (`GRAPHLORE_TRACE_STATS=1`).
pub static PROFILE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

impl Trace {
    #[track_caller]
    pub fn file(&self, i: usize) {
        if PROFILE.load(std::sync::atomic::Ordering::Relaxed) {
            let at = std::panic::Location::caller();
            *self.sites.borrow_mut().entry((at.file(), at.line())).or_default() += 1;
        }
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.file(i as u32);
        }
    }

    pub fn key(&self, k: u64) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.key(k);
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
            top.used.insert((f as u32, imp as u32, g as u32));
        }
    }

    pub fn open(&self) {
        if !enabled() {
            return;
        }
        let frame = self.spare.borrow_mut().pop().unwrap_or_default();
        self.stack.borrow_mut().push(frame);
    }

    fn pop(&self) -> Frame {
        let mut open = self.stack.borrow_mut().pop().unwrap_or_default();
        let frame = open.finish();
        self.spare.borrow_mut().push(open);
        frame
    }

    /// Close the innermost frame: the frame around it took it in.
    pub fn close(&self) -> Rc<Frame> {
        if !enabled() {
            return self.empty.clone();
        }
        let frame = self.shared(self.pop());
        {
            let mut c = self.counts.borrow_mut();
            c[0] += 1;
            c[1] += u64::from(frame.is_empty());
            c[2] += frame.files.len() as u64;
            c[3] += frame.keys.len() as u64;
            c[4] += frame.parts.len() as u64;
        }
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.part(&frame);
        }
        frame
    }

    /// The mark of a value about to be worked out.
    pub fn epoch(&self) -> u32 {
        self.epoch.get()
    }

    /// Whether a value of a cache emptied at each file, worked out at mark
    /// `at`, can be taken here. What working it out read is in the file's
    /// frame already, and so in every frame it was worked out inside; a
    /// frame kept with a cache across files (`open_kept`) has only what was
    /// worked out since it began, so an older value is worked out again.
    pub fn valid(&self, at: u32) -> bool {
        at == ALWAYS || self.kept_starts.borrow().last().is_none_or(|&start| at >= start)
    }

    /// Open a frame for a value kept across files (the bases of a type).
    pub fn open_kept(&self) {
        self.epoch.set(self.epoch.get() + 1);
        self.kept_starts.borrow_mut().push(self.epoch.get());
        self.open();
    }

    /// Close it flattened: a value kept across files holds no frame of other
    /// files' work alive. The frame around takes it in.
    pub fn close_kept(&self) -> Rc<Frame> {
        self.kept_starts.borrow_mut().pop();
        // (what is worked out after it is younger than any frame open)
        self.epoch.set(self.epoch.get() + 1);
        if !enabled() {
            return self.empty.clone();
        }
        let frame = Rc::new(flatten(self.pop()));
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.part(&frame);
        }
        frame
    }

    /// A frame as it is kept: one with nothing of its own is its one part (or
    /// the empty frame), a small one with no parts the one of its reads.
    fn shared(&self, frame: Frame) -> Rc<Frame> {
        let own = frame.files.is_empty() && frame.keys.is_empty() && frame.used.is_empty();
        if own && !frame.all {
            match frame.parts.len() {
                0 => {
                    self.kept.borrow_mut()[0] += 1;
                    return self.empty.clone();
                }
                1 => {
                    self.kept.borrow_mut()[1] += 1;
                    return frame.parts[0].clone();
                }
                _ => {}
            }
        }
        if frame.parts.is_empty()
            && frame.used.is_empty()
            && frame.files.len() + frame.keys.len() <= 6
        {
            let key = (frame.files.clone(), frame.keys.clone(), frame.all);
            let mut small = self.small.borrow_mut();
            let fresh = !small.contains_key(&key);
            self.kept.borrow_mut()[if fresh { 3 } else { 2 }] += 1;
            return small.entry(key).or_insert_with(|| Rc::new(frame)).clone();
        }
        {
            let mut k = self.kept.borrow_mut();
            k[4] += 1;
            k[5] += (frame.files.len() + frame.keys.len() + frame.parts.len()) as u64;
        }
        Rc::new(frame)
    }

    /// Close a file's frame, the outermost, flattened: a frame still open
    /// inside it (an early return between `open` and `close`) is closed into
    /// it, and the file is then taken to read every file, so that nothing it
    /// read is lost.
    pub fn close_file(&self) -> Rc<Frame> {
        if !enabled() {
            return self.empty.clone();
        }
        let mut unbalanced = false;
        while self.stack.borrow().len() > 1 {
            self.close();
            unbalanced = true;
        }
        if unbalanced {
            *self.scans.borrow_mut().entry(0).or_default() += 1;
        }
        let mut frame = flatten(self.pop());
        frame.all |= unbalanced;
        Rc::new(frame)
    }

    /// Close the innermost frame and drop what it read: work done for the
    /// whole project, which a key stands for (`go_aliases`).
    pub fn discard(&self) {
        if !enabled() {
            return;
        }
        self.pop();
    }

    /// A cached result was taken: what computing it read is read now.
    pub fn absorb(&self, frame: &Rc<Frame>) {
        if let Some(top) = self.stack.borrow_mut().last_mut() {
            top.part(frame);
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

    #[track_caller]
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
    #[track_caller]
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

    /// Each key's `key_hash` with a print of what it holds, for comparing
    /// one build's index with the last one's (file indices are the same in
    /// both: the set of paths is).
    pub fn prints(&self, out: &mut HashMap<u64, u64>)
    where
        V: Print,
    {
        for (k, v) in &self.inner {
            let mut h = Fnv::new();
            v.print(&mut h);
            out.insert(key_hash(self.name, k), h.0 | 1);
        }
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

    pub fn prints(&self, out: &mut HashMap<u64, u64>) {
        for k in &self.inner {
            out.insert(key_hash(self.name, k), 1);
        }
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

    #[track_caller]
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

/// What an index holds at a key, into a print (never 0: `reuse::ABSENT`).
pub trait Print {
    fn print(&self, h: &mut Fnv);
}

impl Print for usize {
    fn print(&self, h: &mut Fnv) {
        h.write(&(*self as u64).to_le_bytes());
    }
}

impl Print for u32 {
    fn print(&self, h: &mut Fnv) {
        h.write(&self.to_le_bytes());
    }
}

impl Print for String {
    fn print(&self, h: &mut Fnv) {
        h.write(self.as_bytes());
        h.sep();
    }
}

impl<A: Print, B: Print> Print for (A, B) {
    fn print(&self, h: &mut Fnv) {
        self.0.print(h);
        self.1.print(h);
    }
}

impl<T: Print> Print for Vec<T> {
    fn print(&self, h: &mut Fnv) {
        h.write(&(self.len() as u64).to_le_bytes());
        for x in self {
            x.print(h);
        }
    }
}
