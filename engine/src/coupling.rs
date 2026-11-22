//! Implicit coupling: two definitions that share state and that no call joins.
//!
//! Resolution leaves the state in the graph as nodes (a variable, a field, a
//! constant, a resource) with a `reads` / `writes` edge from each definition
//! that touches them. This pass pairs them up, after the rules of graphlore's
//! `graph._shared_state`: for a state node that code in two files or more
//! touches, each writer with each other definition touching it, and whether a
//! call path joins the two (either way, over calls, dispatch and HTTP, within
//! [`CALL_PATH_HOPS`]). A pair no path joins is hidden: nothing a call graph
//! shows says that changing one changes what the other sees.
//!
//! What it writes:
//! - on the state node, `coupling`: how many pairs there are, how many are
//!   hidden, and the pairs themselves as `[writer, other, hops, forward]`
//!   (`hops` null for a hidden pair; `forward` says the path runs from the
//!   writer), the hidden ones first, at most [`PAIRS_LISTED`];
//! - a `couples_with` edge to the writer of a hidden pair from the other
//!   definition, which sees what the writer writes (the way a call goes from
//!   what depends to what it depends on), at most [`EDGES_PER_STATE`] per
//!   state node, naming the state in `via`.
//!
//! Test code is left out (a test setting a global is how tests work) unless
//! the state is test code itself. Nothing here reads a file: the pass is a
//! function of the nodes and links it is given, so an update and a full build
//! write the same pairs.

use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};

use serde::Serialize;
use serde_json::{json, Value};

use crate::community::is_test_path;

/// The relation of the edge a hidden pair gets.
pub const RELATION: &str = "couples_with";
/// How far a call path between two definitions sharing state is looked for.
pub const CALL_PATH_HOPS: u8 = 6;
/// Pairs written on a state node.
pub const PAIRS_LISTED: usize = 200;
/// Hidden pairs of one state node that become edges.
pub const EDGES_PER_STATE: usize = 10;

/// Relations a call path runs over: a call, a function passed as a value, a
/// base method to what is written for it, a route to its handler and a
/// request to its route.
const PATH_RELATIONS: [&str; 5] =
    ["calls", "indirect_call", "dispatches_to", "handles", "http_calls"];

#[derive(Clone, Debug, Default, Serialize)]
pub struct CouplingReport {
    /// State nodes that code in two files or more touches, one writing.
    pub states: usize,
    pub pairs: usize,
    /// Pairs with no call path between them.
    pub hidden: usize,
    /// `couples_with` edges written.
    pub edges: usize,
    /// Hidden pairs left without an edge by the limit per state node.
    pub hidden_without_edge: usize,
    /// State nodes with more pairs than their node lists.
    pub states_cut: usize,
}

/// One use of a state node: how sure the edge was, and where it is written.
#[derive(Clone)]
struct Touch {
    inferred: bool,
    file: Value,
    location: Value,
}

struct Pair {
    writer: usize,
    other: usize,
    both_write: bool,
    /// `(hops, the path runs from the writer)`.
    path: Option<(u8, bool)>,
}

fn text<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

/// Hops from `from` to each of `wanted` it reaches within the limit.
fn hops_to(adj: &[Vec<u32>], from: usize, wanted: &BTreeSet<usize>) -> HashMap<usize, u8> {
    let mut found: HashMap<usize, u8> = HashMap::new();
    let mut seen: HashMap<usize, u8> = HashMap::from([(from, 0)]);
    let mut queue: VecDeque<usize> = VecDeque::from([from]);
    while let Some(n) = queue.pop_front() {
        let d = seen[&n];
        if d == CALL_PATH_HOPS || found.len() == wanted.len() {
            break;
        }
        for &next in &adj[n] {
            let next = next as usize;
            if seen.contains_key(&next) {
                continue;
            }
            seen.insert(next, d + 1);
            if wanted.contains(&next) {
                found.insert(next, d + 1);
            }
            queue.push_back(next);
        }
    }
    found
}

/// Pair up the definitions that share state. Writes `coupling` on the state
/// nodes and returns the edges of the hidden pairs.
pub fn detect(nodes: &mut [Value], links: &[Value]) -> (Vec<Value>, CouplingReport) {
    let mut report = CouplingReport::default();
    let index: HashMap<String, usize> =
        nodes.iter().enumerate().map(|(i, n)| (text(n, "id").to_string(), i)).collect();
    let end = |e: &Value, key: &str| index.get(text(e, key)).copied();

    let mut readers: BTreeMap<usize, BTreeMap<usize, Touch>> = BTreeMap::new();
    let mut writers: BTreeMap<usize, BTreeMap<usize, Touch>> = BTreeMap::new();
    let mut adj: Vec<Vec<u32>> = vec![Vec::new(); nodes.len()];
    for e in links {
        let rel = text(e, "relation");
        let (Some(s), Some(t)) = (end(e, "source"), end(e, "target")) else { continue };
        if s == t {
            continue;
        }
        if PATH_RELATIONS.contains(&rel) {
            adj[s].push(t as u32);
        } else if rel == "reads" || rel == "writes" {
            let touch = Touch {
                inferred: text(e, "confidence") == "INFERRED",
                file: e.get("source_file").cloned().unwrap_or(Value::Null),
                location: e.get("source_location").cloned().unwrap_or(Value::Null),
            };
            let side = if rel == "writes" { &mut writers } else { &mut readers };
            side.entry(t).or_default().entry(s).or_insert(touch);
        }
    }
    if writers.is_empty() {
        return (Vec::new(), report);
    }

    let files: Vec<String> = nodes.iter().map(|n| text(n, "source_file").to_string()).collect();
    let mut test_file: HashMap<&str, bool> = HashMap::new();
    let tests: Vec<bool> = nodes
        .iter()
        .zip(&files)
        .map(|(n, f)| {
            n.get("test").and_then(Value::as_bool).unwrap_or(false)
                || (!f.is_empty() && *test_file.entry(f).or_insert_with(|| is_test_path(f)))
        })
        .collect();

    // the pairs of every state node, by the ids of its touchers
    let mut states: Vec<(usize, Vec<Pair>)> = Vec::new();
    let empty: BTreeMap<usize, Touch> = BTreeMap::new();
    let by_id = |a: &usize, b: &usize| text(&nodes[*a], "id").cmp(text(&nodes[*b], "id"));
    for (&state, ws) in &writers {
        let keep = |i: &usize| !files[*i].is_empty() && (tests[state] || !tests[*i]);
        let mut ws_kept: Vec<usize> = ws.keys().copied().filter(keep).collect();
        ws_kept.sort_by(by_id);
        let rs = readers.get(&state).unwrap_or(&empty);
        let mut rs_kept: Vec<usize> =
            rs.keys().copied().filter(|i| !ws.contains_key(i)).filter(keep).collect();
        rs_kept.sort_by(by_id);
        let touch: Vec<usize> = ws_kept.iter().chain(&rs_kept).copied().collect();
        let in_files: BTreeSet<&str> = touch.iter().map(|i| files[*i].as_str()).collect();
        if ws_kept.is_empty() || touch.len() < 2 || in_files.len() < 2 {
            continue;
        }
        let mut pairs: Vec<Pair> = Vec::new();
        let mut seen: BTreeSet<(usize, usize)> = BTreeSet::new();
        for &w in &ws_kept {
            for &o in &touch {
                if o == w || !seen.insert((w.min(o), w.max(o))) {
                    continue;
                }
                pairs.push(Pair {
                    writer: w,
                    other: o,
                    both_write: ws.contains_key(&o),
                    path: None,
                });
            }
        }
        states.push((state, pairs));
    }

    // one search per definition, for every definition it is paired with
    let mut partners: BTreeMap<usize, BTreeSet<usize>> = BTreeMap::new();
    for (_, pairs) in &states {
        for p in pairs {
            partners.entry(p.writer).or_default().insert(p.other);
            partners.entry(p.other).or_default().insert(p.writer);
        }
    }
    let reach: HashMap<usize, HashMap<usize, u8>> =
        partners.iter().map(|(&a, wanted)| (a, hops_to(&adj, a, wanted))).collect();
    let hops = |a: usize, b: usize| reach.get(&a).and_then(|m| m.get(&b)).copied();

    let mut edges: Vec<Value> = Vec::new();
    let mut edge_at: HashMap<(usize, usize), usize> = HashMap::new();
    for (state, mut pairs) in states {
        for p in pairs.iter_mut() {
            p.path = hops(p.writer, p.other)
                .map(|h| (h, true))
                .or_else(|| hops(p.other, p.writer).map(|h| (h, false)));
        }
        let hidden = pairs.iter().filter(|p| p.path.is_none()).count();
        report.states += 1;
        report.pairs += pairs.len();
        report.hidden += hidden;

        // hidden pairs first, and among them those that cross files, then
        // two writers: what the edges and a cut list keep
        pairs.sort_by_key(|p| (p.path.is_some(), files[p.writer] == files[p.other], !p.both_write));
        let id = |i: usize| text(&nodes[i], "id").to_string();
        let listed: Vec<Value> = pairs
            .iter()
            .take(PAIRS_LISTED)
            .map(|p| match p.path {
                Some((h, forward)) => json!([id(p.writer), id(p.other), h, forward]),
                None => json!([id(p.writer), id(p.other), null, null]),
            })
            .collect();
        let mut summary = json!({"pairs": pairs.len(), "hidden": hidden, "listed": listed});
        if pairs.len() > PAIRS_LISTED {
            summary["left_out"] = json!(pairs.len() - PAIRS_LISTED);
            report.states_cut += 1;
        }

        let state_id = id(state);
        let touch_of = |side: &BTreeMap<usize, BTreeMap<usize, Touch>>, i: usize| {
            side.get(&state).and_then(|m| m.get(&i)).cloned()
        };
        let mut written = 0;
        for p in pairs.iter().filter(|p| p.path.is_none()) {
            if written == EDGES_PER_STATE {
                break;
            }
            written += 1;
            if let Some(&at) = edge_at.get(&(p.writer, p.other)) {
                // the two share other state as well: one edge names all of it
                if let Some(via) = edges[at]["via"].as_array_mut() {
                    via.push(json!(state_id));
                }
                continue;
            }
            let Some(w) = touch_of(&writers, p.writer) else { continue };
            let Some(o) = touch_of(&writers, p.other).or_else(|| touch_of(&readers, p.other))
            else {
                continue;
            };
            let inferred = w.inferred || o.inferred;
            edge_at.insert((p.writer, p.other), edges.len());
            edges.push(json!({
                "source": id(p.other),
                "target": id(p.writer),
                "relation": RELATION,
                "confidence": if inferred { "INFERRED" } else { "EXTRACTED" },
                "confidence_score": if inferred { 0.55 } else { 1.0 },
                "weight": 1.0,
                "source_file": o.file,
                "source_location": o.location,
                "context": "shared_state",
                "effect": if p.both_write { "both write it" } else { "the other reads what it writes" },
                "via": [state_id],
            }));
        }
        report.hidden_without_edge += hidden - written;
        if written > 0 {
            summary["edges"] = json!(written);
        }
        if let Some(obj) = nodes[state].as_object_mut() {
            obj.insert("coupling".into(), summary);
        }
    }
    report.edges = edges.len();
    (edges, report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, file: &str) -> Value {
        json!({"id": id, "label": id, "source_file": file})
    }

    fn edge(s: &str, rel: &str, t: &str) -> Value {
        json!({"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED",
               "source_file": "a.py", "source_location": "L1"})
    }

    #[test]
    fn a_pair_no_call_joins_is_hidden_and_gets_an_edge() {
        let mut nodes = vec![
            node("state", "a.py"),
            node("writer", "a.py"),
            node("reader", "b.py"),
            node("caller", "c.py"),
            node("helper", "c.py"),
        ];
        let links = vec![
            edge("writer", "writes", "state"),
            edge("reader", "reads", "state"),
            edge("caller", "reads", "state"),
            // caller -> helper -> writer: a path, from the other end
            edge("caller", "calls", "helper"),
            edge("helper", "calls", "writer"),
        ];
        let (edges, report) = detect(&mut nodes, &links);
        assert_eq!((report.states, report.pairs, report.hidden, report.edges), (1, 2, 1, 1));
        assert_eq!(edges[0]["source"], "reader");
        assert_eq!(edges[0]["target"], "writer");
        assert_eq!(edges[0]["relation"], RELATION);
        assert_eq!(edges[0]["via"], json!(["state"]));
        assert_eq!(
            nodes[0]["coupling"],
            json!({"pairs": 2, "hidden": 1, "edges": 1, "listed": [
                ["writer", "reader", null, null],
                ["writer", "caller", 2, false],
            ]})
        );
    }

    #[test]
    fn state_one_file_touches_and_test_code_pair_nothing() {
        let mut nodes = vec![
            node("state", "a.py"),
            node("writer", "a.py"),
            node("reader", "a.py"),
            node("check", "tests/test_a.py"),
        ];
        let links = vec![
            edge("writer", "writes", "state"),
            edge("reader", "reads", "state"),
            edge("check", "writes", "state"),
        ];
        let (edges, report) = detect(&mut nodes, &links);
        assert!(edges.is_empty());
        assert_eq!(report.states, 0);
        assert!(nodes[0].get("coupling").is_none());
    }

    #[test]
    fn a_path_longer_than_the_limit_does_not_join() {
        let mut nodes = vec![node("state", "a.py"), node("w", "a.py"), node("r", "b.py")];
        let mut links = vec![edge("w", "writes", "state"), edge("r", "reads", "state")];
        let mut prev = "w".to_string();
        for i in 0..CALL_PATH_HOPS {
            let next = format!("n{i}");
            nodes.push(node(&next, "c.py"));
            links.push(edge(&prev, "calls", &next));
            prev = next;
        }
        links.push(edge(&prev, "calls", "r"));
        let (_, report) = detect(&mut nodes, &links);
        assert_eq!((report.pairs, report.hidden), (1, 1));
        // one hop fewer and it joins them
        links.push(edge("n0", "calls", "r"));
        let (_, report) = detect(&mut nodes, &links);
        assert_eq!((report.pairs, report.hidden), (1, 0));
    }

    #[test]
    fn the_edges_of_one_state_node_are_limited() {
        let mut nodes = vec![node("state", "a.py"), node("w", "a.py")];
        let mut links = vec![edge("w", "writes", "state")];
        for i in 0..EDGES_PER_STATE + 3 {
            let r = format!("r{i:02}");
            nodes.push(node(&r, "b.py"));
            links.push(edge(&r, "reads", "state"));
        }
        let (edges, report) = detect(&mut nodes, &links);
        assert_eq!(edges.len(), EDGES_PER_STATE);
        assert_eq!((report.hidden, report.hidden_without_edge), (EDGES_PER_STATE + 3, 3));
    }
}
