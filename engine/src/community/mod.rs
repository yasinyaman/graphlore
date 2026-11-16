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

use regex::Regex;
use serde::Serialize;
use serde_json::{json, Map, Value};

use leiden::Graph;

pub const RESOLUTION: f64 = 1.0;
const SEED: u64 = 42;
/// graphify's thresholds (`cluster.py`).
const MAX_COMMUNITY_FRACTION: f64 = 0.25;
const MIN_SPLIT_SIZE: usize = 10;
const COHESION_SPLIT_MIN_SIZE: usize = 50;
const COHESION_SPLIT_THRESHOLD: f64 = 0.05;
/// Share of members in one directory from which a name carries the directory.
const DIR_NAME_SHARE: f64 = 0.6;
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
            f *= 1.25;
        } else if top(&a.file) != top(&b.file) {
            f *= 0.6;
        }
    }
    if a.test != b.test {
        f *= 0.5;
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
}

impl Previous {
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
        prev
    }
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
}

pub struct Outcome {
    pub report: CommunityReport,
    /// `graph.communities` in graph.json.
    pub header: Value,
    /// The saved names with the stale ones dropped, when any were.
    pub labels: Option<BTreeMap<String, String>>,
}

fn info(n: &Value) -> NodeInfo {
    let s = |k: &str| n.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let file = s("source_file");
    let external = n.get("type").and_then(Value::as_str) == Some("external");
    NodeInfo {
        id: s("id"),
        label: s("label"),
        test: n.get("test").and_then(Value::as_bool).unwrap_or(false)
            || (!file.is_empty() && is_test_path(&file)),
        is_file: n.get("kind").and_then(Value::as_str) == Some("file"),
        file,
        external,
    }
}

/// Detect communities and write them on `nodes`.
pub fn detect(
    nodes: &mut [Value],
    links: &[Value],
    prev: &Previous,
    saved: Option<&BTreeMap<String, String>>,
) -> Outcome {
    let started = Instant::now();
    let mut order: Vec<usize> = (0..nodes.len()).collect();
    let infos: Vec<NodeInfo> = nodes.iter().map(info).collect();
    order.sort_by(|&a, &b| infos[a].id.cmp(&infos[b].id));
    let infos: Vec<NodeInfo> = {
        let mut by: Vec<Option<NodeInfo>> = infos.into_iter().map(Some).collect();
        order.iter().map(|&i| by[i].take().unwrap()).collect()
    };
    let n = infos.len();
    let index: HashMap<&str, usize> =
        infos.iter().enumerate().map(|(i, x)| (x.id.as_str(), i)).collect();

    // raw edges (degree, cohesion, externals) and weighted ones (partition)
    let mut raw: Vec<(usize, usize)> = Vec::new();
    let mut weighted: Vec<(usize, usize, f64)> = Vec::new();
    for e in links {
        let end = |k: &str| e.get(k).and_then(Value::as_str).and_then(|s| index.get(s).copied());
        let (Some(a), Some(b)) = (end("source"), end("target")) else { continue };
        if a == b {
            continue;
        }
        raw.push((a, b));
        if infos[a].external || infos[b].external {
            continue;
        }
        let rel = e.get("relation").and_then(Value::as_str).unwrap_or("");
        let mut w = relation_weight(rel, infos[b].is_file);
        if e.get("confidence").and_then(Value::as_str) == Some("INFERRED") {
            w *= 0.5;
        }
        w *= structure_factor(&infos[a], &infos[b]);
        weighted.push((a, b, w));
    }
    weighted.extend(directory_ties(&infos));
    let mut degree = vec![0usize; n];
    let mut neighbours: Vec<Vec<usize>> = vec![Vec::new(); n];
    for &(a, b) in &raw {
        degree[a] += 1;
        degree[b] += 1;
        neighbours[a].push(b);
        neighbours[b].push(a);
    }
    let graph = Graph::from_edges(n, &weighted);

    // partition the internal nodes that have edges
    let connected: Vec<usize> =
        (0..n).filter(|&v| !infos[v].external && !graph.adj[v].is_empty()).collect();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    if !connected.is_empty() {
        let part = leiden::leiden(&graph.subgraph(&connected), RESOLUTION, SEED);
        let count = part.iter().copied().max().map_or(0, |x| x + 1);
        groups = vec![Vec::new(); count];
        for (k, &v) in connected.iter().enumerate() {
            groups[part[k]].push(v);
        }
    }
    groups
        .extend((0..n).filter(|&v| !infos[v].external && graph.adj[v].is_empty()).map(|v| vec![v]));
    // split what is too big, then what is too sparse
    let max_size = MIN_SPLIT_SIZE.max((n as f64 * MAX_COMMUNITY_FRACTION) as usize);
    let mut split: Vec<Vec<usize>> = Vec::new();
    for g in groups {
        if g.len() > max_size {
            split.extend(split_group(&graph, g));
        } else {
            split.push(g);
        }
    }
    let inside = inside_pairs(&raw, &split, n);
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (g, pairs) in split.into_iter().zip(inside) {
        if g.len() >= COHESION_SPLIT_MIN_SIZE && cohesion(pairs, g.len()) < COHESION_SPLIT_THRESHOLD
        {
            groups.extend(split_group(&graph, g));
        } else {
            groups.push(g);
        }
    }
    // externals join their neighbours' community
    let mut of: Vec<usize> = vec![usize::MAX; n];
    for (c, g) in groups.iter().enumerate() {
        for &v in g {
            of[v] = c;
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
        let best = votes.iter().max_by(|a, b| a.1.cmp(b.1).then(b.0.cmp(a.0))).map(|(&c, _)| c);
        match best {
            Some(c) => groups[c].push(v),
            None => groups.push(vec![v]),
        }
    }
    // ids by size, ties by members: a total order
    for g in groups.iter_mut() {
        g.sort_unstable();
    }
    groups.retain(|g| !g.is_empty());
    groups.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
    let ids = remap(&groups, &infos, prev);
    let kept_ids = groups
        .iter()
        .zip(&ids)
        .filter(|(g, &id)| g.iter().any(|&v| prev.community.get(&infos[v].id) == Some(&id)))
        .count();

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
    let report = CommunityReport {
        count: groups.len(),
        largest: groups.first().map_or(0, Vec::len),
        singletons: groups.iter().filter(|g| g.len() == 1).count(),
        modularity,
        renamed_labels: renamed,
        kept_ids,
        ms: started.elapsed().as_millis(),
    };
    let header = json!({
        "count": report.count,
        "modularity": report.modularity,
        "resolution": RESOLUTION,
        "largest": report.largest,
        "singletons": report.singletons,
        "signatures": Value::Object(header_sigs),
    });
    Outcome { report, header, labels }
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
fn split_group(graph: &Graph, g: Vec<usize>) -> Vec<Vec<usize>> {
    let sub = graph.subgraph(&g);
    if sub.adj.iter().all(Vec::is_empty) {
        return g.into_iter().map(|v| vec![v]).collect();
    }
    let part = leiden::leiden(&sub, RESOLUTION, SEED);
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

/// 64-bit FNV-1a over the sorted member ids: the same members, the same value.
fn signature<'a>(ids: impl Iterator<Item = &'a str>) -> String {
    let mut ids: Vec<&str> = ids.collect();
    ids.sort_unstable();
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for id in ids {
        for b in id.bytes().chain(std::iter::once(0)) {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
    format!("{h:016x}")
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
