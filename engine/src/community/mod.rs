//! Communities: which nodes belong together, written as `community` and
//! `community_name` on every node of graph.json.
//!
//! The partition is Leiden's (`leiden`) on a weighted graph built from the
//! edges: what an edge says (containment and calls bind tighter than a type
//! mentioned in passing), how sure it is (`INFERRED` counts half) and where its
//! ends live (same directory binds tighter, different top-level directories and
//! test-to-production edges looser), plus a weak tie between the files of a
//! directory. External nodes take no part; they join the community most of their
//! neighbours are in. After that, graphify's rules: isolated nodes are their own
//! community, a community over a quarter of the graph or a sparse one of 50 and
//! more is split by a second pass, ids are by size, and a rebuild keeps the ids
//! of the previous graph's communities (so names saved for them stay attached).

pub mod leiden;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::OnceLock;
use std::time::Instant;

use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use leiden::Graph;

pub const RESOLUTION: f64 = 1.0;
/// graphify's thresholds (`cluster.py`).
const MAX_COMMUNITY_FRACTION: f64 = 0.25;
const MIN_SPLIT_SIZE: usize = 10;
const COHESION_SPLIT_MIN_SIZE: usize = 50;
const COHESION_SPLIT_THRESHOLD: f64 = 0.05;
/// Share of members in one directory from which a name carries the directory.
const DIR_NAME_SHARE: f64 = 0.6;
/// Where an edge's ends live: the same directory, different top-level
/// directories, test code and production code. Measured on 14 of the polyglot
/// audit's repositories: a test edge at 0.1 keeps test code together (4% of
/// test nodes end up in a production community, 11% at 0.5) and lifts the share
/// of a community in one directory from 90% to 93%.
const SAME_DIR: f64 = 1.25;
const OTHER_TOP: f64 = 0.6;
const TEST_TO_PRODUCTION: f64 = 0.1;
/// An `INFERRED` edge (a guess by name) counts this much of an extracted one.
const INFERRED: f64 = 0.5;
/// Total weight of the tie each file gets to the other files of its directory.
const DIR_PRIOR: f64 = 0.1;
/// Directories with more files than this are tied in a ring, not a clique.
const DIR_CLIQUE_MAX: usize = 64;

/// What an edge's relation says about two nodes belonging together.
pub fn relation_weight(rel: &str, target_is_file: bool) -> f64 {
    match rel {
        "contains" | "method" | "defines" | "case_of" => 1.0,
        "calls" => 1.0,
        "inherits" | "implements" | "mixes_in" | "embeds" => 1.5,
        "imports" | "imports_from" if target_is_file => 0.7,
        "imports" | "imports_from" => 0.4,
        _ => 0.5,
    }
}

/// Where an edge's ends live.
fn structure_factor(a: &NodeInfo, b: &NodeInfo) -> f64 {
    let mut f = 1.0;
    if !a.file.is_empty() && !b.file.is_empty() && a.file != b.file {
        if parent(&a.file) == parent(&b.file) {
            f *= SAME_DIR;
        } else if top(&a.file) != top(&b.file) {
            f *= OTHER_TOP;
        }
    }
    if a.test != b.test {
        f *= TEST_TO_PRODUCTION;
    }
    f
}

fn parent(path: &str) -> &str {
    path.rfind('/').map_or("", |i| &path[..i])
}

fn top(path: &str) -> &str {
    path.find('/').map_or(".", |i| &path[..i])
}

/// graphlore's `graph._is_test_path`.
pub fn is_test_path(path: &str) -> bool {
    static NAME: OnceLock<Regex> = OnceLock::new();
    let name = NAME.get_or_init(|| Regex::new(r"[a-z0-9.](?:Tests?|TestCase)(?:$|\.)").unwrap());
    const DIRS: [&str; 7] = ["test", "tests", "spec", "specs", "__tests__", "testing", "e2e"];
    let parts: Vec<&str> = path.split('/').collect();
    let (last, dirs) = parts.split_last().unwrap_or((&"", &[]));
    if dirs.iter().any(|p| DIRS.contains(&p.to_ascii_lowercase().as_str()) || name.is_match(p)) {
        return true;
    }
    let stem = last.rsplit_once('.').map_or(*last, |(s, _)| s);
    let low = stem.to_ascii_lowercase();
    low.starts_with("test_")
        || low == "conftest"
        || ["_test", "_tests", ".test", ".spec", "_spec"].iter().any(|s| low.ends_with(s))
        || name.is_match(stem)
}

struct NodeInfo {
    id: String,
    label: String,
    file: String,
    external: bool,
    is_file: bool,
    test: bool,
}

/// The previous graph's communities, read from the graph.json a build replaces.
#[derive(Default)]
pub struct Previous {
    pub community: HashMap<String, i64>,
    pub signatures: HashMap<i64, String>,
    /// The hash of what detection read when it wrote these communities
    /// (`graph.communities.input`): the same input keeps them as they are.
    /// Never set for `cluster`, which is asked to detect them again.
    pub input: Option<String>,
    /// The group of the first partition each community was split out of, by
    /// its first community (`graph.communities.parents`; a community that is
    /// not listed is its own).
    pub parents: HashMap<i64, i64>,
}

impl Previous {
    fn parent_of(&self, c: i64) -> i64 {
        self.parents.get(&c).copied().unwrap_or(c)
    }
}

/// `graph.communities.parents`: the communities split out of one group of the
/// first partition, one list per group of two or more.
fn parents_from(lists: &[Vec<i64>]) -> HashMap<i64, i64> {
    let mut out = HashMap::new();
    for list in lists {
        if let Some(&first) = list.iter().min() {
            for &c in list {
                out.insert(c, first);
            }
        }
    }
    out
}

#[derive(Deserialize)]
struct PrevFile {
    #[serde(default)]
    nodes: Vec<PrevNode>,
    #[serde(default)]
    graph: Option<PrevMeta>,
}

#[derive(Deserialize)]
struct PrevNode {
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    community: Option<Value>,
}

#[derive(Deserialize)]
struct PrevMeta {
    #[serde(default)]
    communities: Option<PrevCommunities>,
}

#[derive(Deserialize)]
struct PrevCommunities {
    #[serde(default)]
    signatures: HashMap<String, Value>,
    #[serde(default)]
    input: Option<String>,
    #[serde(default)]
    parents: Vec<Vec<i64>>,
}

/// The previous communities as the build that wrote graph.json left them, in
/// `cache/`, with the size and modification time of that graph.json: reading
/// them takes milliseconds, the graph.json they came from seconds.
#[derive(Serialize, Deserialize)]
struct Sidecar {
    size: u64,
    mtime_ns: u64,
    community: Vec<(String, i64)>,
    signatures: Vec<(i64, String)>,
    input: Option<String>,
    parents: Vec<Vec<i64>>,
}

const SIDECAR: &str = "graphlore-communities.bin";

fn sidecar_path(graph: &std::path::Path) -> Option<std::path::PathBuf> {
    graph.parent().map(|d| d.join(crate::cache::CACHE_DIR).join(SIDECAR))
}

fn stamp(graph: &std::path::Path) -> Option<(u64, u64)> {
    let meta = std::fs::metadata(graph).ok()?;
    Some((meta.len(), crate::cache::mtime_ns(&meta)))
}

impl Previous {
    /// The communities of the graph.json at `path`: from the copy its build
    /// left in `cache/` when that graph.json is still the one it wrote (its
    /// size and modification time), else read from graph.json itself (a
    /// graph another build or `cluster` wrote, or one copied in).
    /// With whether they came from the copy in `cache/`.
    pub fn load(path: &std::path::Path) -> Option<(Self, bool)> {
        match Self::from_sidecar(path) {
            Some(p) => Some((p, true)),
            None => Self::read(path).map(|p| (p, false)),
        }
    }

    fn from_sidecar(path: &std::path::Path) -> Option<Self> {
        let (size, mtime_ns) = stamp(path)?;
        let file = std::fs::File::open(sidecar_path(path)?).ok()?;
        let len = file.metadata().ok()?.len();
        use bincode::Options;
        let side: Sidecar = bincode::DefaultOptions::new()
            .with_fixint_encoding()
            .with_limit(len)
            .deserialize_from(std::io::BufReader::new(file))
            .ok()?;
        if side.size != size || side.mtime_ns != mtime_ns || mtime_ns == 0 {
            return None;
        }
        Some(Previous {
            community: side.community.into_iter().collect(),
            signatures: side.signatures.into_iter().collect(),
            input: side.input,
            parents: parents_from(&side.parents),
        })
    }

    /// Keep the communities of the graph just written at `path` beside it
    /// (`load`). A copy that cannot be written costs the next build seconds.
    pub fn store(path: &std::path::Path, nodes: &[Value], header: &Value) {
        let Some((size, mtime_ns)) = stamp(path) else { return };
        let Some(out) = sidecar_path(path) else { return };
        let community = nodes
            .iter()
            .filter_map(|n| {
                Some((n.get("id")?.as_str()?.to_string(), n.get("community")?.as_i64()?))
            })
            .collect();
        let signatures = header
            .get("signatures")
            .and_then(Value::as_object)
            .into_iter()
            .flatten()
            .filter_map(|(k, v)| Some((k.parse().ok()?, v.as_str()?.to_string())))
            .collect();
        let input = header.get("input").and_then(Value::as_str).map(str::to_string);
        let parents = header_parents(header);
        let side = Sidecar { size, mtime_ns, community, signatures, input, parents };
        use bincode::Options;
        let Ok(bytes) = bincode::DefaultOptions::new().with_fixint_encoding().serialize(&side)
        else {
            return;
        };
        let _ = std::fs::create_dir_all(out.parent().unwrap_or(std::path::Path::new(".")));
        let tmp = out.with_extension(format!("tmp-{}", std::process::id()));
        if std::fs::write(&tmp, bytes).is_ok() && std::fs::rename(&tmp, &out).is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
    }

    /// The communities of the graph.json at `path`, reading only the fields
    /// they need (a graph of 250 MB takes a fraction of a full parse).
    pub fn read(path: &std::path::Path) -> Option<Self> {
        // (read as a stream: the whole file in memory first was 1.1 GB more
        // for kubernetes, to keep two fields of each node)
        let reader = std::io::BufReader::with_capacity(1 << 20, std::fs::File::open(path).ok()?);
        let file: PrevFile = serde_json::from_reader(reader).ok()?;
        let mut prev = Previous::default();
        for n in file.nodes {
            if let (Some(id), Some(c)) = (n.id, n.community.as_ref().and_then(Value::as_i64)) {
                prev.community.insert(id, c);
            }
        }
        let communities = file.graph.and_then(|g| g.communities);
        prev.input = communities.as_ref().and_then(|c| c.input.clone());
        prev.parents = communities.as_ref().map(|c| parents_from(&c.parents)).unwrap_or_default();
        let sigs = communities.map(|c| c.signatures);
        for (k, v) in sigs.into_iter().flatten() {
            if let (Ok(c), Some(s)) = (k.parse::<i64>(), v.as_str()) {
                prev.signatures.insert(c, s.to_string());
            }
        }
        Some(prev)
    }

    pub fn from_graph(graph: &Value) -> Self {
        let mut prev = Previous::default();
        for n in graph.get("nodes").and_then(Value::as_array).into_iter().flatten() {
            if let (Some(id), Some(c)) =
                (n.get("id").and_then(Value::as_str), n.get("community").and_then(Value::as_i64))
            {
                prev.community.insert(id.to_string(), c);
            }
        }
        let sigs = graph.pointer("/graph/communities/signatures").and_then(Value::as_object);
        for (k, v) in sigs.into_iter().flatten() {
            if let (Ok(c), Some(s)) = (k.parse::<i64>(), v.as_str()) {
                prev.signatures.insert(c, s.to_string());
            }
        }
        if let Some(h) = graph.pointer("/graph/communities") {
            prev.parents = parents_from(&header_parents(h));
        }
        prev
    }
}

/// The lists of `parents` in a `graph.communities` header.
fn header_parents(header: &Value) -> Vec<Vec<i64>> {
    header
        .get("parents")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|l| l.as_array().into_iter().flatten().filter_map(Value::as_i64).collect())
        .collect()
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct CommunityReport {
    pub count: usize,
    pub largest: usize,
    pub singletons: usize,
    pub modularity: f64,
    /// Saved names dropped because their community changed beyond recognition.
    pub renamed_labels: usize,
    /// Communities that kept the id they had in the previous graph.
    pub kept_ids: usize,
    pub ms: u128,
    /// Milliseconds per step, how many partitions settling took and how it
    /// ended (`settled`: the previous communities again; `fixed`: a pass
    /// returned its own start; `cycle`; `limit`).
    pub phases_ms: BTreeMap<&'static str, u128>,
    pub settle_passes: usize,
    pub settle_end: &'static str,
}

pub struct Outcome {
    pub report: CommunityReport,
    /// `graph.communities` in graph.json.
    pub header: Value,
    /// The saved names with the stale ones dropped, when any were.
    pub labels: Option<BTreeMap<String, String>>,
}

fn info(n: &Value, test_file: &mut HashMap<String, bool>) -> NodeInfo {
    let s = |k: &str| n.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let file = s("source_file");
    // an external name, or a resource (an environment variable, a table): no
    // code of the project, so it joins the community of those that use it
    let external = matches!(n.get("type").and_then(Value::as_str), Some("external" | "resource"));
    let in_test_file =
        !file.is_empty() && *test_file.entry(file.clone()).or_insert_with_key(|f| is_test_path(f));
    NodeInfo {
        id: s("id"),
        label: s("label"),
        test: n.get("test").and_then(Value::as_bool).unwrap_or(false) || in_test_file,
        is_file: n.get("kind").and_then(Value::as_str) == Some("file"),
        file,
        external,
    }
}

/// Detect communities and write them on `nodes`.
pub fn detect<L: crate::graph::links::LinkSource + ?Sized>(
    nodes: &mut [Value],
    links: &L,
    prev: &Previous,
    saved: Option<&BTreeMap<String, String>>,
) -> Outcome {
    let started = Instant::now();
    let mut phases: BTreeMap<&'static str, u128> = BTreeMap::new();
    let mut lap_at = Instant::now();
    let mut lap = |name: &'static str| {
        phases.insert(name, lap_at.elapsed().as_millis());
        lap_at = Instant::now();
    };
    let mut order: Vec<usize> = (0..nodes.len()).collect();
    let mut test_file = HashMap::new();
    let infos: Vec<NodeInfo> = nodes.iter().map(|n| info(n, &mut test_file)).collect();
    order.sort_unstable_by(|&a, &b| infos[a].id.cmp(&infos[b].id));
    let infos: Vec<NodeInfo> = {
        let mut by: Vec<Option<NodeInfo>> = infos.into_iter().map(Some).collect();
        order.iter().map(|&i| by[i].take().unwrap()).collect()
    };
    let n = infos.len();
    let index: HashMap<&str, usize> =
        infos.iter().enumerate().map(|(i, x)| (x.id.as_str(), i)).collect();
    lap("nodes");

    // raw edges (degree, cohesion, externals) and weighted ones (partition)
    let mut raw: Vec<(usize, usize)> = Vec::new();
    let mut weighted: Vec<(usize, usize, f64)> = Vec::new();
    for e in links.links() {
        let end = |id: &str| index.get(id).copied();
        let (Some(a), Some(b)) = (end(e.source()), end(e.target())) else { continue };
        if a == b {
            continue;
        }
        let rel = e.relation();
        // a pair sharing state is derived from the edges already counted, and
        // a method written for a base's method repeats what the type's
        // `inherits` / `implements` edge weighs
        // (a document naming code describes it: it is no part of it)
        let derived = rel == crate::coupling::RELATION
            || rel == crate::docs::link::RELATION
            || (rel == "dispatches_to" && e.context() == Some("override"));
        if derived {
            continue;
        }
        raw.push((a, b));
        if infos[a].external || infos[b].external {
            continue;
        }
        let mut w = relation_weight(rel, infos[b].is_file);
        if e.confidence() == "INFERRED" {
            w *= INFERRED;
        }
        w *= structure_factor(&infos[a], &infos[b]);
        weighted.push((a, b, w));
    }
    weighted.extend(directory_ties(&infos));
    let mut degree = vec![0usize; n];
    // (only an external's neighbours are asked for: it joins theirs)
    let mut neighbours: Vec<Vec<usize>> = vec![Vec::new(); n];
    for &(a, b) in &raw {
        degree[a] += 1;
        degree[b] += 1;
        if infos[a].external {
            neighbours[a].push(b);
        }
        if infos[b].external {
            neighbours[b].push(a);
        }
    }
    lap("edges");
    let graph = Graph::from_edges(n, &weighted);
    lap("graph");
    let input = input_hash(&infos, &raw, &weighted);

    // partition the internal nodes that have edges, visiting nodes in an order
    // of their own (a hash of the id) and starting from the previous graph's
    // communities: a small change to the code moves few nodes
    let before: Vec<Option<i64>> =
        infos.iter().map(|x| prev.community.get(&x.id).copied()).collect();
    let mut walk = Walk {
        keys: infos.iter().map(|x| fnv(x.id.as_bytes())).collect(),
        top: before.iter().map(|c| c.map(|c| prev.parent_of(c))).collect(),
        before,
    };
    let warm = walk.before.iter().any(Option::is_some);
    let mut settled = Settled::default();
    // what detection reads is what it read for the previous communities:
    // they stand (Leiden from them need not return them; a graph that has
    // not changed keeps its communities)
    let kept = (prev.input.as_deref() == Some(input.as_str()))
        .then(|| previous_groups(&infos, &walk.before, prev))
        .flatten();
    let Part { mut groups, mut parent } = match kept {
        Some(part) => {
            settled.end = "kept";
            part
        }
        None => settle(&graph, &mut walk, &infos, &raw, warm, &mut settled),
    };
    lap("settle");
    // externals join their neighbours' community; a tie goes to the group
    // with the first member (an order of the graph's, not of how the groups
    // were found)
    let mut of: Vec<usize> = vec![usize::MAX; n];
    let mut first: Vec<usize> = vec![usize::MAX; groups.len()];
    for (c, g) in groups.iter().enumerate() {
        for &v in g {
            of[v] = c;
            first[c] = first[c].min(v);
        }
    }
    for v in 0..n {
        if !infos[v].external {
            continue;
        }
        let mut votes: BTreeMap<usize, usize> = BTreeMap::new();
        for &u in &neighbours[v] {
            if of[u] != usize::MAX && !infos[u].external {
                *votes.entry(of[u]).or_default() += 1;
            }
        }
        let best = votes
            .iter()
            .max_by(|a, b| a.1.cmp(b.1).then(first[*b.0].cmp(&first[*a.0])))
            .map(|(&c, _)| c);
        match best {
            Some(c) => groups[c].push(v),
            None => {
                groups.push(vec![v]);
                parent.push(usize::MAX - v);
            }
        }
    }
    // ids by size, ties by members: a total order
    for g in groups.iter_mut() {
        g.sort_unstable();
    }
    let mut paired: Vec<(Vec<usize>, usize)> =
        groups.into_iter().zip(parent).filter(|(g, _)| !g.is_empty()).collect();
    paired.sort_by(|a, b| b.0.len().cmp(&a.0.len()).then_with(|| a.0.cmp(&b.0)));
    let (groups, parent): (Vec<Vec<usize>>, Vec<usize>) = paired.into_iter().unzip();
    let ids = remap(&groups, &infos, prev);
    // the communities split out of one group of the first partition
    let mut by_parent: BTreeMap<usize, Vec<i64>> = BTreeMap::new();
    for (&p, &id) in parent.iter().zip(&ids) {
        by_parent.entry(p).or_default().push(id);
    }
    let mut parent_lists: Vec<Vec<i64>> = by_parent.into_values().filter(|l| l.len() > 1).collect();
    for l in parent_lists.iter_mut() {
        l.sort_unstable();
    }
    parent_lists.sort_unstable();
    let kept_ids = groups
        .iter()
        .zip(&ids)
        .filter(|(g, &id)| g.iter().any(|&v| prev.community.get(&infos[v].id) == Some(&id)))
        .count();

    lap("ids");
    // names and the saved labels that still apply
    let mut header_sigs = Map::new();
    let mut names: HashMap<i64, String> = HashMap::new();
    let mut members_of: HashMap<i64, HashSet<&str>> = HashMap::new();
    for (g, &id) in groups.iter().zip(&ids) {
        let sig = signature(g.iter().map(|&v| infos[v].id.as_str()));
        header_sigs.insert(id.to_string(), json!(sig));
        names.insert(id, name_of(g, &infos, &degree));
        members_of.insert(id, g.iter().map(|&v| infos[v].id.as_str()).collect());
    }
    let mut renamed = 0;
    let labels = saved.map(|saved| {
        let mut kept = BTreeMap::new();
        for (k, v) in saved {
            let Ok(cid) = k.parse::<i64>() else { continue };
            let Some(members) = members_of.get(&cid) else {
                renamed += usize::from(*v != format!("Community {cid}"));
                continue;
            };
            let sig = header_sigs.get(k).and_then(Value::as_str);
            if still_applies(cid, members, prev, sig) {
                kept.insert(k.clone(), v.clone());
            } else if *v != format!("Community {cid}") {
                renamed += 1;
            }
        }
        kept
    });
    let labels = labels.filter(|l| saved.is_some_and(|s| s != l));

    lap("names");
    // write them on the nodes
    let mut node_comm: Vec<i64> = vec![0; n];
    for (g, &id) in groups.iter().zip(&ids) {
        for &v in g {
            node_comm[v] = id;
        }
    }
    for (pos, &orig) in order.iter().enumerate() {
        let id = node_comm[pos];
        if let Some(obj) = nodes[orig].as_object_mut() {
            obj.insert("community".into(), json!(id));
            obj.insert("community_name".into(), json!(names[&id]));
        }
    }
    let mut part = vec![0usize; n];
    for (c, g) in groups.iter().enumerate() {
        for &v in g {
            part[v] = c;
        }
    }
    let modularity = (leiden::modularity(&graph, &part, RESOLUTION) * 10_000.0).round() / 10_000.0;
    lap("modularity");
    let report = CommunityReport {
        count: groups.len(),
        largest: groups.first().map_or(0, Vec::len),
        singletons: groups.iter().filter(|g| g.len() == 1).count(),
        modularity,
        renamed_labels: renamed,
        kept_ids,
        ms: started.elapsed().as_millis(),
        phases_ms: phases,
        settle_passes: settled.passes,
        settle_end: settled.end,
    };
    let header = json!({
        "count": report.count,
        "modularity": report.modularity,
        "resolution": RESOLUTION,
        "largest": report.largest,
        "singletons": report.singletons,
        "signatures": Value::Object(header_sigs),
        "input": input,
        "parents": parent_lists,
    });
    Outcome { report, header, labels }
}

/// A hash of everything detection reads (the nodes in id order, the edges
/// and their weights) and of the engine: the same hash, the same input.
fn input_hash(
    infos: &[NodeInfo],
    raw: &[(usize, usize)],
    weighted: &[(usize, usize, f64)],
) -> String {
    let mut h = crate::resolve::trace::Fnv::new();
    h.write(crate::VERSION.as_bytes());
    h.sep();
    for x in infos {
        for part in [&x.id, &x.label, &x.file] {
            h.write(part.as_bytes());
            h.sep();
        }
        h.write(&[u8::from(x.external), u8::from(x.is_file), u8::from(x.test)]);
    }
    h.sep();
    for &(a, b) in raw {
        h.write(&(a as u64).to_le_bytes());
        h.write(&(b as u64).to_le_bytes());
    }
    h.sep();
    for &(a, b, w) in weighted {
        h.write(&(a as u64).to_le_bytes());
        h.write(&(b as u64).to_le_bytes());
        h.write(&w.to_bits().to_le_bytes());
    }
    format!("{:016x}", h.0)
}

/// The previous communities as the groups settling gives: every node that is
/// not external, by the community it had (`None` when one had none), each
/// with the group of the first partition it came from.
fn previous_groups(infos: &[NodeInfo], before: &[Option<i64>], prev: &Previous) -> Option<Part> {
    let mut by: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for (v, x) in infos.iter().enumerate() {
        if !x.external {
            by.entry(before[v]?).or_default().push(v);
        }
    }
    let mut index: BTreeMap<i64, usize> = BTreeMap::new();
    let mut part = Part::default();
    for (c, g) in by {
        let next = index.len();
        part.parent.push(*index.entry(prev.parent_of(c)).or_insert(next));
        part.groups.push(g);
    }
    Some(part)
}

/// A weak tie between the files of each directory, so a file with few edges
/// stays with its neighbours on disk.
fn directory_ties(infos: &[NodeInfo]) -> Vec<(usize, usize, f64)> {
    let mut dirs: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (i, x) in infos.iter().enumerate() {
        if x.is_file && !x.external && !x.file.is_empty() {
            dirs.entry(parent(&x.file)).or_default().push(i);
        }
    }
    let mut out = Vec::new();
    for files in dirs.values() {
        let k = files.len();
        if k < 2 {
            continue;
        }
        if k <= DIR_CLIQUE_MAX {
            let w = DIR_PRIOR / (k - 1) as f64;
            for (i, &a) in files.iter().enumerate() {
                for &b in &files[i + 1..] {
                    out.push((a, b, w));
                }
            }
        } else {
            for (i, &a) in files.iter().enumerate() {
                out.push((a, files[(i + 1) % k], DIR_PRIOR / 2.0));
            }
        }
    }
    out
}

/// A second Leiden pass on one community; unchanged when it finds one part.
/// Leiden over the internal nodes with edges, isolated nodes alone, then
/// graphify's splits of what is too big and what is too sparse.
fn partition(graph: &Graph, walk: &Walk, infos: &[NodeInfo], raw: &[(usize, usize)]) -> Part {
    partition_traced(graph, walk, infos, raw, None)
}

/// A partition, with the group of the first partition (Leiden over the whole
/// graph, before the splits) each group comes from.
#[derive(Default, Clone)]
struct Part {
    groups: Vec<Vec<usize>>,
    parent: Vec<usize>,
}

impl Part {
    /// The groups of the first partition.
    fn tops(&self) -> Vec<Vec<usize>> {
        let mut by: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for (g, &p) in self.groups.iter().zip(&self.parent) {
            by.entry(p).or_default().extend(g);
        }
        by.into_values().collect()
    }
}

/// What one pass of settling did (`GRAPHLORE_SETTLE_TRACE=1`).
#[derive(Default, Debug)]
struct PassTrace {
    top_groups: usize,
    /// Nodes the top Leiden did not leave with the majority of their start group.
    top_moved: usize,
    /// Top groups each the main home of two start groups or more, and how many
    /// start groups they took in.
    merged_into: usize,
    merged_from: usize,
    size_split: (usize, usize),
    cohesion_split: (usize, usize),
}

fn partition_traced(
    graph: &Graph,
    walk: &Walk,
    infos: &[NodeInfo],
    raw: &[(usize, usize)],
    mut trace: Option<&mut PassTrace>,
) -> Part {
    let n = infos.len();
    let connected: Vec<usize> =
        (0..n).filter(|&v| !infos[v].external && !graph.adj[v].is_empty()).collect();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    if !connected.is_empty() {
        // (the first partition starts from the groups the last one's
        // communities were split out of: Leiden would merge the splits again)
        let part = walk.leiden(&graph.subgraph(&connected), &connected, &walk.top);
        let count = part.iter().copied().max().map_or(0, |x| x + 1);
        groups = vec![Vec::new(); count];
        for (k, &v) in connected.iter().enumerate() {
            groups[part[k]].push(v);
        }
    }
    if let Some(t) = trace.as_deref_mut() {
        t.top_groups = groups.len();
        let internal: usize = groups.iter().map(Vec::len).sum();
        t.top_moved = internal - agreement(&groups, &walk.top);
        // each start group's main home among the top groups
        let mut by_start: HashMap<i64, HashMap<usize, usize>> = HashMap::new();
        for (c, g) in groups.iter().enumerate() {
            for &v in g {
                if let Some(b) = walk.top[v] {
                    *by_start.entry(b).or_default().entry(c).or_default() += 1;
                }
            }
        }
        let mut homes: HashMap<usize, usize> = HashMap::new();
        for counts in by_start.values() {
            if let Some((&c, _)) = counts.iter().max_by_key(|(c, x)| (**x, std::cmp::Reverse(**c)))
            {
                *homes.entry(c).or_default() += 1;
            }
        }
        t.merged_into = homes.values().filter(|&&x| x >= 2).count();
        t.merged_from = homes.values().filter(|&&x| x >= 2).sum();
    }
    groups
        .extend((0..n).filter(|&v| !infos[v].external && graph.adj[v].is_empty()).map(|v| vec![v]));
    let max_size = MIN_SPLIT_SIZE.max((n as f64 * MAX_COMMUNITY_FRACTION) as usize);
    // (each split reads the graph only: in parallel, put together in order)
    let split: Vec<Vec<Vec<usize>>> = groups
        .into_par_iter()
        .map(|g| if g.len() > max_size { split_group(graph, walk, g) } else { vec![g] })
        .collect();
    if let Some(t) = trace.as_deref_mut() {
        let parts: Vec<usize> = split.iter().map(Vec::len).filter(|&k| k > 1).collect();
        t.size_split = (parts.len(), parts.iter().sum());
    }
    let (split, parent): (Vec<Vec<usize>>, Vec<usize>) = split
        .into_iter()
        .enumerate()
        .flat_map(|(p, parts)| parts.into_iter().map(move |g| (g, p)))
        .unzip();
    let inside = inside_pairs(raw, &split, n);
    let out: Vec<Vec<Vec<usize>>> = split
        .into_par_iter()
        .zip(inside)
        .map(|(g, pairs)| {
            if g.len() >= COHESION_SPLIT_MIN_SIZE
                && cohesion(pairs, g.len()) < COHESION_SPLIT_THRESHOLD
            {
                split_group(graph, walk, g)
            } else {
                vec![g]
            }
        })
        .collect();
    if let Some(t) = trace {
        let parts: Vec<usize> = out.iter().map(Vec::len).filter(|&k| k > 1).collect();
        t.cohesion_split = (parts.len(), parts.iter().sum());
    }
    let (groups, parent) = out
        .into_iter()
        .zip(parent)
        .flat_map(|(parts, p)| parts.into_iter().map(move |g| (g, p)))
        .unzip();
    Part { groups, parent }
}

/// Partition until a pass starting from the last result returns it again.
///
/// Each pass starts from the previous one's communities. Graphify's splits can
/// make two states alternate (Leiden merges what the cohesion rule split, which
/// splits it again): then the state of the cycle closest to where the build
/// started is kept, which a rebuild starting from it keeps too. A first build
/// settles the same way, so the first update after it moves nothing for nothing.
/// How settling went (a measurement for the report).
#[derive(Default)]
struct Settled {
    passes: usize,
    end: &'static str,
}

fn settle(
    graph: &Graph,
    walk: &mut Walk,
    infos: &[NodeInfo],
    raw: &[(usize, usize)],
    warm: bool,
    settled: &mut Settled,
) -> Part {
    let n = infos.len();
    let tracing = std::env::var("GRAPHLORE_SETTLE_TRACE").is_ok_and(|v| v == "1");
    let pass = |walk: &Walk, prior: Option<&Part>| {
        if !tracing {
            return partition(graph, walk, infos, raw);
        }
        let t0 = Instant::now();
        let mut t = PassTrace::default();
        let out = partition_traced(graph, walk, infos, raw, Some(&mut t));
        let internal: usize = out.groups.iter().map(Vec::len).sum();
        let moved = prior.map(|p| internal - agreement(&out.groups, &labels(&p.groups, n)));
        let mut part = vec![0usize; n];
        for (c, g) in out.groups.iter().enumerate() {
            for &v in g {
                part[v] = c;
            }
        }
        eprintln!(
            "settle pass: {} ms, groups {}, moved {:?} of {}, modularity {:.4}, {:?}",
            t0.elapsed().as_millis(),
            out.groups.len(),
            moved,
            internal,
            leiden::modularity(graph, &part, RESOLUTION),
            t
        );
        out
    };
    let first = pass(walk, None);
    settled.passes = 1;
    if warm && reproduces(&first.groups, &walk.before) && reproduces(&first.tops(), &walk.top) {
        settled.end = "settled";
        return first; // the previous communities, unchanged: already settled
    }
    let reference: Vec<Option<i64>> =
        if warm { walk.before.clone() } else { labels(&first.groups, n) };
    let mut states = vec![first];
    for _ in 0..SETTLE_PASSES {
        let last = states.last().expect("one state");
        walk.before = labels(&last.groups, n);
        walk.top = labels(&last.tops(), n);
        let next = pass(walk, states.last());
        settled.passes += 1;
        let same = |s: &Part| {
            same_partition(&s.groups, &next.groups, n) && same_partition(&s.tops(), &next.tops(), n)
        };
        match states.iter().position(same) {
            Some(at) if at + 1 == states.len() => {
                settled.end = "fixed";
                return states.pop().expect("one state");
            }
            Some(at) => {
                settled.end = "cycle";
                // a cycle: keep its state closest to the start
                let cycle = states.split_off(at);
                return cycle
                    .into_iter()
                    .enumerate()
                    .max_by_key(|(i, s)| (agreement(&s.groups, &reference), std::cmp::Reverse(*i)))
                    .map(|(_, s)| s)
                    .expect("a cycle has states");
            }
            None => states.push(next),
        }
    }
    settled.end = "limit";
    states.pop().expect("one state")
}

const SETTLE_PASSES: usize = 6;

/// Whether `groups` are exactly the communities `before` names: every node had
/// one, each group is one of them, and no two groups share one.
fn reproduces(groups: &[Vec<usize>], before: &[Option<i64>]) -> bool {
    let mut seen: HashSet<i64> = HashSet::new();
    groups.iter().all(|g| {
        let Some(c) = before[g[0]] else { return false };
        seen.insert(c) && g.iter().all(|&v| before[v] == Some(c))
    })
}

fn labels(groups: &[Vec<usize>], n: usize) -> Vec<Option<i64>> {
    let mut out = vec![None; n];
    for (c, g) in groups.iter().enumerate() {
        for &v in g {
            out[v] = Some(c as i64);
        }
    }
    out
}

/// Nodes that sit with the majority of their group's reference community.
fn agreement(groups: &[Vec<usize>], reference: &[Option<i64>]) -> usize {
    groups
        .iter()
        .map(|g| {
            let mut count: HashMap<i64, usize> = HashMap::new();
            for &v in g {
                if let Some(c) = reference[v] {
                    *count.entry(c).or_default() += 1;
                }
            }
            count.values().copied().max().unwrap_or(0)
        })
        .sum()
}

/// Whether two lists of groups put the same nodes together.
fn same_partition(a: &[Vec<usize>], b: &[Vec<usize>], n: usize) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut of = vec![usize::MAX; n];
    for (c, g) in a.iter().enumerate() {
        for &v in g {
            of[v] = c;
        }
    }
    b.iter().all(|g| g.iter().all(|&v| of[v] == of[g[0]]))
}

/// How Leiden visits the nodes, and where it starts: the first partition
/// from `top` (the groups the communities were split out of), a split from
/// `before` (the communities).
struct Walk {
    keys: Vec<u64>,
    before: Vec<Option<i64>>,
    top: Vec<Option<i64>>,
}

impl Walk {
    /// Leiden on `sub`, the graph induced by `nodes`, starting from `start`.
    fn leiden(&self, sub: &Graph, nodes: &[usize], start: &[Option<i64>]) -> Vec<usize> {
        let keys: Vec<u64> = nodes.iter().map(|&v| self.keys[v]).collect();
        let mut label: HashMap<i64, usize> = HashMap::new();
        let mut next = 0;
        let mut initial = Vec::with_capacity(nodes.len());
        for &v in nodes {
            let l = match start[v] {
                Some(c) => *label.entry(c).or_insert_with(|| {
                    next += 1;
                    next - 1
                }),
                None => {
                    next += 1;
                    next - 1
                }
            };
            initial.push(l);
        }
        let start = (!label.is_empty()).then_some(initial.as_slice());
        leiden::leiden(sub, RESOLUTION, &keys, start)
    }
}

fn split_group(graph: &Graph, walk: &Walk, g: Vec<usize>) -> Vec<Vec<usize>> {
    let sub = graph.subgraph(&g);
    if sub.adj.iter().all(Vec::is_empty) {
        return g.into_iter().map(|v| vec![v]).collect();
    }
    let part = walk.leiden(&sub, &g, &walk.before);
    let count = part.iter().copied().max().map_or(0, |x| x + 1);
    if count <= 1 {
        return vec![g];
    }
    let mut out = vec![Vec::new(); count];
    for (k, &v) in g.iter().enumerate() {
        out[part[k]].push(v);
    }
    out
}

/// Distinct linked pairs inside each group.
fn inside_pairs(raw: &[(usize, usize)], groups: &[Vec<usize>], n: usize) -> Vec<usize> {
    let mut of = vec![usize::MAX; n];
    for (c, g) in groups.iter().enumerate() {
        for &v in g {
            of[v] = c;
        }
    }
    let pairs: HashSet<(usize, usize)> = raw
        .iter()
        .filter(|&&(a, b)| of[a] != usize::MAX && of[a] == of[b])
        .map(|&(a, b)| (a.min(b), a.max(b)))
        .collect();
    let mut count = vec![0; groups.len()];
    for (a, _) in pairs {
        count[of[a]] += 1;
    }
    count
}

/// Linked pairs inside a group over the pairs it has (graphify's `cohesion_score`).
fn cohesion(pairs: usize, n: usize) -> f64 {
    if n <= 1 {
        return 1.0;
    }
    pairs as f64 / (n * (n - 1) / 2) as f64
}

/// The previous ids, matched greedily by overlap (graphify's
/// `remap_communities_to_previous`); new communities take the lowest free ids.
fn remap(groups: &[Vec<usize>], infos: &[NodeInfo], prev: &Previous) -> Vec<i64> {
    if prev.community.is_empty() {
        return (0..groups.len() as i64).collect();
    }
    let mut overlaps: Vec<(usize, i64, usize)> = Vec::new();
    for (k, g) in groups.iter().enumerate() {
        let mut count: HashMap<i64, usize> = HashMap::new();
        for &v in g {
            if let Some(&c) = prev.community.get(&infos[v].id) {
                *count.entry(c).or_default() += 1;
            }
        }
        overlaps.extend(count.into_iter().map(|(c, x)| (x, c, k)));
    }
    overlaps.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    let mut ids: Vec<Option<i64>> = vec![None; groups.len()];
    let mut used: HashSet<i64> = HashSet::new();
    for (_, old, k) in overlaps {
        if ids[k].is_none() && !used.contains(&old) {
            ids[k] = Some(old);
            used.insert(old);
        }
    }
    let mut next = 0i64;
    ids.into_iter()
        .map(|id| {
            id.unwrap_or_else(|| {
                while used.contains(&next) {
                    next += 1;
                }
                used.insert(next);
                next
            })
        })
        .collect()
}

/// A saved name still names community `cid` when its members hash the same,
/// or the members it shares with `cid`'s previous ones are a strict majority
/// of both (graphify's `label_still_applies`).
fn still_applies(cid: i64, members: &HashSet<&str>, prev: &Previous, sig: Option<&str>) -> bool {
    if let (Some(old), Some(new)) = (prev.signatures.get(&cid), sig) {
        if old == new {
            return true;
        }
    }
    let before: Vec<&str> =
        prev.community.iter().filter(|(_, &c)| c == cid).map(|(id, _)| id.as_str()).collect();
    if before.is_empty() || members.is_empty() {
        return false;
    }
    let shared = before.iter().filter(|id| members.contains(*id)).count();
    shared * 2 > before.len() && shared * 2 > members.len()
}

/// 64-bit FNV-1a.
fn fnv(bytes: &[u8]) -> u64 {
    crate::cache::hash(bytes)
}

/// A hash of the sorted member ids: the same members, the same value.
fn signature<'a>(ids: impl Iterator<Item = &'a str>) -> String {
    let mut ids: Vec<&str> = ids.collect();
    ids.sort_unstable();
    format!("{:016x}", fnv(ids.join("\0").as_bytes()))
}

/// The member with the most edges names the community; with most of the members
/// in one directory, the directory comes first (`httpx/_transports: HTTPTransport`).
fn name_of(g: &[usize], infos: &[NodeInfo], degree: &[usize]) -> String {
    let hub = g
        .iter()
        .copied()
        .min_by(|&a, &b| {
            (infos[a].external, std::cmp::Reverse(degree[a]), &infos[a].id).cmp(&(
                infos[b].external,
                std::cmp::Reverse(degree[b]),
                &infos[b].id,
            ))
        })
        .expect("a community has members");
    let x = &infos[hub];
    let mut name = x.label.trim().trim_start_matches('.').to_string();
    if let Some(stripped) = name.strip_suffix("()") {
        name = stripped.to_string();
    }
    if name.is_empty() {
        name = x.id.clone();
    }
    let mut dirs: BTreeMap<&str, usize> = BTreeMap::new();
    let mut placed = 0;
    for &v in g {
        if !infos[v].file.is_empty() {
            placed += 1;
            *dirs.entry(parent(&infos[v].file)).or_default() += 1;
        }
    }
    let top_dir = dirs.iter().max_by(|a, b| a.1.cmp(b.1).then(b.0.cmp(a.0)));
    if let Some((dir, &count)) = top_dir {
        if !dir.is_empty() && g.len() > 1 && count as f64 > DIR_NAME_SHARE * placed as f64 {
            return format!("{dir}: {name}");
        }
    }
    name
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, file: &str, kind: &str) -> Value {
        json!({"id": id, "label": id, "source_file": file, "kind": kind})
    }

    fn edge(a: &str, b: &str, rel: &str) -> Value {
        json!({"source": a, "target": b, "relation": rel})
    }

    const PACKAGES: [&str; 5] = ["a", "b", "c", "d", "e"];

    /// Five packages of three files each, the packages joined in a chain by one call.
    fn packages() -> (Vec<Value>, Vec<Value>) {
        let mut nodes = Vec::new();
        let mut links = Vec::new();
        for pkg in PACKAGES {
            for f in 0..3 {
                let file = format!("{pkg}/f{f}.py");
                let hub = format!("{pkg}_f{f}");
                nodes.push(node(&hub, &file, "file"));
                for d in 0..4 {
                    let def = format!("{hub}_d{d}");
                    nodes.push(node(&def, &file, "function"));
                    links.push(edge(&hub, &def, "contains"));
                    if d > 0 {
                        links.push(edge(&def, &format!("{hub}_d{}", d - 1), "calls"));
                    }
                }
                for g in 0..f {
                    links.push(edge(&hub, &format!("{pkg}_f{g}"), "imports"));
                    for d in 0..4 {
                        links.push(edge(
                            &format!("{hub}_d{d}"),
                            &format!("{pkg}_f{g}_d{d}"),
                            "calls",
                        ));
                    }
                }
            }
        }
        for w in PACKAGES.windows(2) {
            links.push(edge(&format!("{}_f0_d0", w[0]), &format!("{}_f0_d0", w[1]), "calls"));
        }
        nodes
            .push(json!({"id": "ext_py_os", "label": "os", "type": "external", "source_file": ""}));
        links.push(edge("a_f1", "ext_py_os", "imports"));
        links.push(edge("a_f2", "ext_py_os", "imports"));
        (nodes, links)
    }

    fn comm(nodes: &[Value], id: &str) -> i64 {
        nodes.iter().find(|n| n["id"] == id).unwrap()["community"].as_i64().unwrap()
    }

    #[test]
    fn packages_are_communities_and_externals_follow_their_users() {
        let (mut nodes, links) = packages();
        let out = detect(&mut nodes, &links, &Previous::default(), None);
        assert_eq!(out.report.count, 5, "{:?}", out.report);
        assert_eq!(comm(&nodes, "a_f0"), comm(&nodes, "a_f2_d3"));
        assert_eq!(comm(&nodes, "b_f0"), comm(&nodes, "b_f2_d1"));
        assert_ne!(comm(&nodes, "a_f0"), comm(&nodes, "b_f0"));
        assert_eq!(comm(&nodes, "ext_py_os"), comm(&nodes, "a_f0"));
        let name = nodes.iter().find(|n| n["id"] == "a_f0").unwrap()["community_name"].clone();
        assert!(name.as_str().unwrap().starts_with("a: "), "{name}");
        assert!(out.report.modularity > 0.3);
    }

    #[test]
    fn the_same_graph_gives_the_same_communities() {
        let (mut x, links) = packages();
        let (mut y, _) = packages();
        y.reverse();
        let a = detect(&mut x, &links, &Previous::default(), None);
        let b = detect(&mut y, &links, &Previous::default(), None);
        assert_eq!(a.header, b.header);
        for n in &x {
            assert_eq!(n["community"], comm(&y, n["id"].as_str().unwrap()));
        }
    }

    #[test]
    fn a_rebuild_keeps_the_ids_and_the_names_that_still_apply() {
        let (mut nodes, links) = packages();
        detect(&mut nodes, &links, &Previous::default(), None);
        // swap the ids the previous graph used, and save a name for each
        let mut graph = json!({"nodes": nodes.clone()});
        for n in graph["nodes"].as_array_mut().unwrap() {
            let c = n["community"].as_i64().unwrap();
            n["community"] = json!((c + 1) % 5);
        }
        let prev = Previous::from_graph(&graph);
        let a = (comm(&nodes, "a_f0") + 1) % 5;
        let saved: BTreeMap<String, String> =
            [(a.to_string(), "Package A".to_string()), ("7".into(), "Gone".into())].into();
        let (mut again, _) = packages();
        let out = detect(&mut again, &links, &prev, Some(&saved));
        assert_eq!(comm(&again, "a_f0"), a);
        assert_eq!(out.report.kept_ids, 5);
        let labels = out.labels.expect("the stale name is dropped");
        assert_eq!(labels.get(&a.to_string()).map(String::as_str), Some("Package A"));
        assert!(!labels.contains_key("7"));
        assert_eq!(out.report.renamed_labels, 1);
    }

    #[test]
    fn a_rebuild_from_a_result_returns_it() {
        // a graph big and sparse enough for the size and cohesion splits to act
        let mut nodes = Vec::new();
        let mut links = Vec::new();
        for f in 0..40 {
            let hub = format!("f{f}");
            nodes.push(node(&hub, &format!("d{}/f{f}.py", f % 4), "file"));
            for d in 0..6 {
                let def = format!("{hub}_d{d}");
                nodes.push(node(&def, &format!("d{}/f{f}.py", f % 4), "function"));
                links.push(edge(&hub, &def, "contains"));
                links.push(edge(&def, &format!("f{}_d{}", (f * 7 + d) % 40, (d + 1) % 6), "calls"));
            }
        }
        let first = detect(&mut nodes, &links, &Previous::default(), None);
        let prev = Previous::from_graph(&json!({"nodes": nodes.clone(),
                                                "graph": {"communities": first.header}}));
        let mut again = nodes.clone();
        let out = detect(&mut again, &links, &prev, None);
        for (a, b) in nodes.iter().zip(&again) {
            assert_eq!(a["community"], b["community"], "{}", a["id"]);
        }
        assert_eq!(out.report.kept_ids, out.report.count);
    }

    /// Pairs of stars, each pair's hubs joined: in a graph this big Leiden
    /// keeps a pair together, and the cohesion split takes it apart again.
    fn star_pairs() -> (Vec<Value>, Vec<Value>) {
        let mut nodes = Vec::new();
        let mut links = Vec::new();
        for p in 0..40 {
            for s in 0..2 {
                let hub = format!("p{p}_s{s}");
                let file = format!("p{p}/s{s}.py");
                nodes.push(node(&hub, &file, "file"));
                for l in 0..30 {
                    let leaf = format!("{hub}_l{l}");
                    nodes.push(node(&leaf, &file, "function"));
                    links.push(edge(&hub, &leaf, "contains"));
                }
            }
            links.push(edge(&format!("p{p}_s0"), &format!("p{p}_s1"), "calls"));
        }
        (nodes, links)
    }

    #[test]
    fn the_splits_are_written_and_a_rebuild_starts_from_them() {
        let (mut nodes, links) = star_pairs();
        let first = detect(&mut nodes, &links, &Previous::default(), None);
        assert_eq!(comm(&nodes, "p0_s0"), comm(&nodes, "p0_s0_l3"));
        assert_ne!(comm(&nodes, "p0_s0"), comm(&nodes, "p0_s1"), "the pair is split");
        let parents = header_parents(&first.header);
        assert_eq!(parents.len(), 40, "{parents:?}");
        assert!(parents.iter().all(|l| l.len() == 2));
        let pair = [comm(&nodes, "p3_s0"), comm(&nodes, "p3_s1")];
        assert!(parents.iter().any(|l| l.contains(&pair[0]) && l.contains(&pair[1])));
        // another graph (an edge added): the first pass starts from the pairs,
        // the splits from the communities, and both come back as they were
        let graph = json!({"nodes": nodes.clone(), "graph": {"communities": first.header}});
        let prev = Previous::from_graph(&graph);
        assert_eq!(prev.parents.len(), 80);
        let mut more = links.clone();
        more.push(edge("p0_s0_l1", "p0_s0_l2", "calls"));
        let mut again = nodes.clone();
        let out = detect(&mut again, &more, &prev, None);
        assert_eq!(out.report.settle_end, "settled", "{:?}", out.report);
        assert_eq!(out.report.settle_passes, 1);
        for (a, b) in nodes.iter().zip(&again) {
            assert_eq!(a["community"], b["community"], "{}", a["id"]);
        }
        // graph.json and the copy in cache/ give the same previous communities
        let dir = std::env::temp_dir().join(format!("graphlore-parents-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("graph.json");
        std::fs::write(&path, serde_json::to_vec(&graph).unwrap()).unwrap();
        let read = Previous::read(&path).unwrap();
        assert_eq!(read.parents, prev.parents);
        Previous::store(&path, &nodes, &graph["graph"]["communities"]);
        let (cached, from_cache) = Previous::load(&path).unwrap();
        assert!(from_cache);
        assert_eq!(cached.parents, prev.parents);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn an_isolated_node_is_its_own_community() {
        let (mut nodes, links) = packages();
        nodes.push(node("lonely", "c/x.py", "function"));
        let out = detect(&mut nodes, &links, &Previous::default(), None);
        assert_eq!(out.report.singletons, 1);
        assert_eq!(out.report.count, 6);
    }

    #[test]
    fn test_paths_follow_graphlore() {
        for p in [
            "tests/a.py",
            "src/test_x.py",
            "pkg/x_test.go",
            "a/b.spec.ts",
            "Foo.Tests/A.cs",
            "src/jvmTest/A.kt",
            "OwnerControllerTests.java",
            "conftest.py",
        ] {
            assert!(is_test_path(p), "{p}");
        }
        for p in ["src/a.py", "contest.py", "lib/TypeSpec.kt", "latest/x.py"] {
            assert!(!is_test_path(p), "{p}");
        }
    }
}
