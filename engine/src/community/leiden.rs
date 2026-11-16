//! Leiden community detection (Traag, Waltman & van Eck 2019) on a weighted,
//! undirected graph, maximising modularity with a resolution parameter.
//!
//! Each level runs the three Leiden phases: fast local moving of nodes, a
//! refinement inside every community (only well-connected nodes merge, so every
//! community stays connected), and aggregation of the refined communities, whose
//! starting partition is the unrefined one. The refinement merges greedily
//! (largest gain, ties to the lower index) rather than at random, and nodes are
//! visited in the order of keys the caller derives from them: the same graph
//! always gives the same partition, on every machine.

use std::collections::VecDeque;

/// A weighted undirected graph. `adj[i]` holds each neighbour once with the
/// summed weight of the edges between them; self-loops are `self_w`.
#[derive(Clone, Debug, Default)]
pub struct Graph {
    pub adj: Vec<Vec<(usize, f64)>>,
    pub self_w: Vec<f64>,
}

impl Graph {
    pub fn new(n: usize) -> Self {
        Graph { adj: vec![Vec::new(); n], self_w: vec![0.0; n] }
    }

    pub fn len(&self) -> usize {
        self.adj.len()
    }

    pub fn is_empty(&self) -> bool {
        self.adj.is_empty()
    }

    /// Build from `(a, b, w)` rows; parallel edges add up, `a == b` is a self-loop.
    pub fn from_edges(n: usize, edges: &[(usize, usize, f64)]) -> Self {
        let mut g = Graph::new(n);
        let mut rows: Vec<(usize, usize, f64)> = Vec::with_capacity(edges.len() * 2);
        for &(a, b, w) in edges {
            if w <= 0.0 {
                continue;
            }
            if a == b {
                g.self_w[a] += w;
            } else {
                rows.push((a, b, w));
                rows.push((b, a, w));
            }
        }
        rows.sort_by_key(|x| (x.0, x.1));
        for (a, b, w) in rows {
            match g.adj[a].last_mut() {
                Some(last) if last.0 == b => last.1 += w,
                _ => g.adj[a].push((b, w)),
            }
        }
        g
    }

    /// Weighted degree: edge weights plus each self-loop twice.
    pub fn strengths(&self) -> Vec<f64> {
        (0..self.len())
            .map(|i| self.adj[i].iter().map(|&(_, w)| w).sum::<f64>() + 2.0 * self.self_w[i])
            .collect()
    }

    /// The graph induced by `nodes` (indices into `self`), re-indexed in order.
    pub fn subgraph(&self, nodes: &[usize]) -> Graph {
        let mut index = vec![usize::MAX; self.len()];
        for (k, &v) in nodes.iter().enumerate() {
            index[v] = k;
        }
        let mut g = Graph::new(nodes.len());
        for (k, &v) in nodes.iter().enumerate() {
            g.self_w[k] = self.self_w[v];
            for &(u, w) in &self.adj[v] {
                if index[u] != usize::MAX {
                    g.adj[k].push((index[u], w));
                }
            }
            g.adj[k].sort_by_key(|&(u, _)| u);
        }
        g
    }
}

/// Nodes in the order of their keys (ties by index).
fn by_key(nodes: &mut [usize], keys: &[u64]) {
    nodes.sort_by_key(|&v| (keys[v], v));
}

const EPS: f64 = 1e-12;

/// The community of every node, numbered from 0 in order of first appearance.
///
/// `keys` orders the nodes wherever the algorithm visits them in turn: a key
/// that comes from the node itself (a hash of its id) keeps the order of the
/// other nodes when one is added, so a small change to a graph makes a small
/// change to its partition. `initial` starts from a known partition (the
/// previous build's) instead of singletons.
pub fn leiden(g: &Graph, resolution: f64, keys: &[u64], initial: Option<&[usize]>) -> Vec<usize> {
    let n = g.len();
    if n == 0 {
        return Vec::new();
    }
    let m2: f64 = g.strengths().iter().sum();
    if m2 <= 0.0 {
        return (0..n).collect();
    }
    // which aggregate node each original node is part of, and the current level
    let mut member: Vec<usize> = (0..n).collect();
    let mut level = g.clone();
    let mut level_keys = keys.to_vec();
    let mut part: Vec<usize> = match initial {
        Some(p) if p.len() == n => p.to_vec(),
        _ => (0..n).collect(),
    };
    for _ in 0..64 {
        let moved = local_moving(&level, &mut part, resolution, m2, &level_keys);
        let communities = renumber(&mut part);
        if !moved && communities == level.len() {
            break;
        }
        let mut refined = refine(&level, &part, resolution, m2, &level_keys);
        let groups = renumber(&mut refined);
        if groups == level.len() {
            // nothing merges inside the communities: one more level changes nothing
            break;
        }
        // the aggregate's starting partition is the unrefined one, its keys the
        // smallest of its members'
        let mut start = vec![0usize; groups];
        let mut agg_keys = vec![u64::MAX; groups];
        for v in 0..level.len() {
            start[refined[v]] = part[v];
            agg_keys[refined[v]] = agg_keys[refined[v]].min(level_keys[v]);
        }
        level = aggregate(&level, &refined, groups);
        level_keys = agg_keys;
        for m in member.iter_mut() {
            *m = refined[*m];
        }
        part = start;
    }
    let mut out: Vec<usize> = member.iter().map(|&m| part[m]).collect();
    renumber(&mut out);
    out
}

/// Modularity of `part` on `g` with `resolution`.
pub fn modularity(g: &Graph, part: &[usize], resolution: f64) -> f64 {
    let k = g.strengths();
    let m2: f64 = k.iter().sum();
    if m2 <= 0.0 {
        return 0.0;
    }
    let c = part.iter().copied().max().map_or(0, |x| x + 1);
    let mut inside = vec![0.0; c];
    let mut tot = vec![0.0; c];
    for v in 0..g.len() {
        tot[part[v]] += k[v];
        inside[part[v]] += 2.0 * g.self_w[v];
        for &(u, w) in &g.adj[v] {
            if part[u] == part[v] {
                inside[part[v]] += w;
            }
        }
    }
    (0..c).map(|i| inside[i] / m2 - resolution * (tot[i] / m2).powi(2)).sum()
}

/// Number communities 0.. in order of first appearance; returns how many.
fn renumber(part: &mut [usize]) -> usize {
    let mut map: Vec<usize> = vec![usize::MAX; part.len().max(1)];
    let mut next = 0;
    for p in part.iter_mut() {
        if *p >= map.len() {
            map.resize(*p + 1, usize::MAX);
        }
        if map[*p] == usize::MAX {
            map[*p] = next;
            next += 1;
        }
        *p = map[*p];
    }
    next
}

/// Leiden's fast local moving: visit nodes from a queue, move each to the
/// neighbouring community with the largest modularity gain, and requeue the
/// neighbours a move may now pull along. Returns whether anything moved.
fn local_moving(g: &Graph, part: &mut [usize], gamma: f64, m2: f64, keys: &[u64]) -> bool {
    let n = g.len();
    let k = g.strengths();
    let mut tot = vec![0.0; n];
    for v in 0..n {
        tot[part[v]] += k[v];
    }
    let mut order: Vec<usize> = (0..n).collect();
    by_key(&mut order, keys);
    let mut queue: VecDeque<usize> = order.into_iter().collect();
    let mut queued = vec![true; n];
    let mut w_to = vec![0.0; n];
    let mut touched: Vec<usize> = Vec::new();
    let mut moved = false;
    // community ids with no members, for a node better off alone
    let mut empties: Vec<usize> = (0..n).filter(|&c| tot[c] == 0.0).collect();
    while let Some(v) = queue.pop_front() {
        queued[v] = false;
        let own = part[v];
        for &(u, w) in &g.adj[v] {
            let c = part[u];
            if w_to[c] == 0.0 {
                touched.push(c);
            }
            w_to[c] += w;
        }
        tot[own] -= k[v];
        let gain = |c: usize, w: f64| w - gamma * k[v] * tot[c] / m2;
        let mut best = own;
        let mut best_gain = gain(own, w_to[own]);
        for &c in &touched {
            let gc = gain(c, w_to[c]);
            if gc > best_gain + EPS || (gc > best_gain - EPS && c < best && best != own) {
                best = c;
                best_gain = gc;
            }
        }
        // an empty community: only when leaving is better than every neighbour
        if best_gain < -EPS {
            while let Some(c) = empties.pop() {
                if tot[c] == 0.0 && c != own {
                    best = c;
                    break;
                }
            }
        }
        tot[best] += k[v];
        if best != own && tot[own] == 0.0 {
            empties.push(own);
        }
        for c in touched.drain(..) {
            w_to[c] = 0.0;
        }
        w_to[own] = 0.0;
        if best != own {
            part[v] = best;
            moved = true;
            for &(u, _) in &g.adj[v] {
                if part[u] != best && !queued[u] {
                    queued[u] = true;
                    queue.push_back(u);
                }
            }
        }
    }
    moved
}

/// Leiden's refinement: inside each community, start from singletons and merge
/// a node that is still a singleton into the refined group it gains most by
/// joining, when both are well connected to the rest of the community.
fn refine(g: &Graph, part: &[usize], gamma: f64, m2: f64, keys: &[u64]) -> Vec<usize> {
    let n = g.len();
    let k = g.strengths();
    let c_count = part.iter().copied().max().map_or(0, |x| x + 1);
    let mut members: Vec<Vec<usize>> = vec![Vec::new(); c_count];
    for v in 0..n {
        members[part[v]].push(v);
    }
    let mut refined: Vec<usize> = (0..n).collect();
    let mut tot = k.clone(); // per refined group
    let mut ext = vec![0.0; n]; // weight from a refined group to the rest of its community
    let mut single = vec![true; n];
    for v in 0..n {
        ext[v] = g.adj[v].iter().filter(|&&(u, _)| part[u] == part[v]).map(|&(_, w)| w).sum();
    }
    let mut w_to = vec![0.0; n];
    let mut touched: Vec<usize> = Vec::new();
    for group in members.iter_mut() {
        let total: f64 = group.iter().map(|&v| k[v]).sum();
        by_key(group, keys);
        for &v in group.iter() {
            if !single[v] {
                continue;
            }
            if ext[v] + EPS < gamma * k[v] * (total - k[v]) / m2 {
                continue; // not well connected to its community
            }
            for &(u, w) in &g.adj[v] {
                if part[u] != part[v] {
                    continue;
                }
                let r = refined[u];
                if r == refined[v] {
                    continue;
                }
                if w_to[r] == 0.0 {
                    touched.push(r);
                }
                w_to[r] += w;
            }
            let mut best: Option<(usize, f64)> = None;
            for &r in &touched {
                if ext[r] + EPS < gamma * tot[r] * (total - tot[r]) / m2 {
                    continue;
                }
                let gain = w_to[r] - gamma * k[v] * tot[r] / m2;
                if gain < -EPS {
                    continue;
                }
                match best {
                    Some((b, bg)) if gain < bg - EPS || (gain < bg + EPS && r > b) => {}
                    _ => best = Some((r, gain)),
                }
            }
            if let Some((r, _)) = best {
                let between = w_to[r];
                ext[r] = ext[r] + ext[v] - 2.0 * between;
                tot[r] += k[v];
                let old = refined[v];
                tot[old] -= k[v];
                refined[v] = r;
                single[v] = false;
                single[r] = false;
            }
            for r in touched.drain(..) {
                w_to[r] = 0.0;
            }
        }
    }
    // `single[r] = false` marks the representative too; groups are the refined ids
    refined
}

/// Collapse each refined group into one node.
fn aggregate(g: &Graph, refined: &[usize], groups: usize) -> Graph {
    let mut edges: Vec<(usize, usize, f64)> = Vec::new();
    let mut self_w = vec![0.0; groups];
    for v in 0..g.len() {
        let a = refined[v];
        self_w[a] += g.self_w[v];
        for &(u, w) in &g.adj[v] {
            let b = refined[u];
            if a == b {
                if v < u {
                    self_w[a] += w;
                }
            } else if a < b {
                edges.push((a, b, w));
            }
        }
    }
    let mut out = Graph::from_edges(groups, &edges);
    out.self_w = self_w;
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(n: usize) -> Vec<u64> {
        (0..n as u64).map(|i| i.wrapping_mul(0x9E37_79B9_7F4A_7C15).rotate_left(17)).collect()
    }

    fn run(g: &Graph) -> Vec<usize> {
        leiden(g, 1.0, &keys(g.len()), None)
    }

    fn clique(edges: &mut Vec<(usize, usize, f64)>, nodes: &[usize]) {
        for (i, &a) in nodes.iter().enumerate() {
            for &b in &nodes[i + 1..] {
                edges.push((a, b, 1.0));
            }
        }
    }

    #[test]
    fn two_cliques_joined_by_a_bridge_are_two_communities() {
        let mut e = Vec::new();
        clique(&mut e, &[0, 1, 2, 3, 4]);
        clique(&mut e, &[5, 6, 7, 8, 9]);
        e.push((4, 5, 1.0));
        let g = Graph::from_edges(10, &e);
        let p = run(&g);
        assert!(p[..5].iter().all(|&c| c == p[0]));
        assert!(p[5..].iter().all(|&c| c == p[5]));
        assert_ne!(p[0], p[5]);
        assert!(modularity(&g, &p, 1.0) > 0.4);
    }

    #[test]
    fn a_ring_of_cliques_finds_every_clique() {
        let mut e = Vec::new();
        for c in 0..8 {
            let base = c * 6;
            clique(&mut e, &(base..base + 6).collect::<Vec<_>>());
            e.push((base + 5, (base + 6) % 48, 1.0));
        }
        let g = Graph::from_edges(48, &e);
        let p = run(&g);
        for c in 0..8 {
            let base = c * 6;
            assert!(p[base..base + 6].iter().all(|&x| x == p[base]), "clique {c}: {p:?}");
        }
        assert_eq!(p.iter().copied().max(), Some(7));
    }

    #[test]
    fn the_same_graph_gives_the_same_partition() {
        let mut e = Vec::new();
        for i in 0..200usize {
            e.push((i, (i * 7 + 3) % 200, 1.0));
            e.push((i, (i + 1) % 200, 0.5));
        }
        let g = Graph::from_edges(200, &e);
        assert_eq!(run(&g), run(&g));
    }

    #[test]
    fn a_start_from_the_answer_keeps_it() {
        let mut e = Vec::new();
        clique(&mut e, &[0, 1, 2, 3, 4]);
        clique(&mut e, &[5, 6, 7, 8, 9]);
        e.push((4, 5, 1.0));
        let g = Graph::from_edges(10, &e);
        let p = run(&g);
        assert_eq!(leiden(&g, 1.0, &keys(10), Some(&p)), p);
    }

    #[test]
    fn communities_are_connected_and_beat_singletons() {
        let mut e = Vec::new();
        for i in 0..300usize {
            e.push((i, (i * 13 + 5) % 300, 1.0));
            if i % 3 != 0 {
                e.push((i, i + 1, 2.0));
            }
        }
        e.retain(|&(a, b, _)| a < 300 && b < 300);
        let g = Graph::from_edges(300, &e);
        let p = run(&g);
        let singletons: Vec<usize> = (0..300).collect();
        assert!(modularity(&g, &p, 1.0) > modularity(&g, &singletons, 1.0));
        // every community is connected within itself
        let c = p.iter().copied().max().unwrap() + 1;
        for x in 0..c {
            let nodes: Vec<usize> = (0..300).filter(|&v| p[v] == x).collect();
            let mut seen = vec![false; 300];
            let mut stack = vec![nodes[0]];
            seen[nodes[0]] = true;
            while let Some(v) = stack.pop() {
                for &(u, _) in &g.adj[v] {
                    if p[u] == x && !seen[u] {
                        seen[u] = true;
                        stack.push(u);
                    }
                }
            }
            assert!(nodes.iter().all(|&v| seen[v]), "community {x} is not connected");
        }
    }

    #[test]
    fn edgeless_and_empty_graphs() {
        assert!(run(&Graph::new(0)).is_empty());
        assert_eq!(run(&Graph::new(3)), vec![0, 1, 2]);
    }
}
