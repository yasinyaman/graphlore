"""Graph algorithms over plain adjacency maps, with no graph.json knowledge.

Each function takes node ids and successor lists (``{node: [neighbour, ...]}``)
and is deterministic: ties are broken by the order of the lists handed in and,
where nodes are sampled, by a hash of their id, never by a random draw.

- :func:`certain_route` — the path with the fewest uncertain steps, then the
  fewest steps (Dijkstra on that pair of costs).
- :func:`pagerank` — the stationary rank of a walk along the edges.
- :func:`betweenness` — Brandes' betweenness, exact or estimated from sampled
  sources.
- :func:`dominators` — the immediate dominator of each node reachable from a
  root (Cooper, Harvey and Kennedy's iterative algorithm).
- :func:`cut_structure` — the bridges and cut vertices of an undirected graph
  (Tarjan's low-link), with the size of what each one cuts off.

Imports nothing from graphlore.
"""
from __future__ import annotations

import hashlib
import heapq
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

Succ = Mapping[str, Sequence[str]]


def certain_route(
    adj: Mapping[str, Sequence[tuple[str, str]]],
    src: str,
    dst: str,
    uncertain: Callable[[str, str, str], bool],
    skip: frozenset[str] = frozenset(),
    avoid: frozenset[str] | set[str] = frozenset(),
) -> list[tuple[str, str, str]] | None:
    """The ``src -> ... -> dst`` walk over ``adj`` (``node -> [(neighbour,
    relation)]``) with the fewest steps ``uncertain(from, to, relation)`` says
    are uncertain, then the fewest steps, as ``(from, to, relation)`` steps.

    Never along a relation in ``skip`` nor through a node in ``avoid`` (which may
    still be an end); ``[]`` when ``src == dst``, None when ``dst`` cannot be
    reached. When no step is uncertain the answer is the breadth-first one,
    neighbours in ``adj`` order: of two equal walks the one found first wins.
    """
    if src == dst:
        return []
    best: dict[str, tuple[int, int]] = {src: (0, 0)}
    prev: dict[str, tuple[str, str]] = {}
    seq = 0
    heap: list[tuple[int, int, int, str]] = [(0, 0, seq, src)]
    done: set[str] = set()
    while heap:
        unc, steps, _, cur = heapq.heappop(heap)
        if cur in done:
            continue
        done.add(cur)
        if cur == dst:
            break
        for nb, rel in adj.get(cur, ()):
            if rel in skip or nb == src or nb in done or (nb in avoid and nb != dst):
                continue
            cost = (unc + (1 if uncertain(cur, nb, rel) else 0), steps + 1)
            if nb not in best or cost < best[nb]:
                best[nb] = cost
                prev[nb] = (cur, rel)
                seq += 1
                heapq.heappush(heap, (cost[0], cost[1], seq, nb))
    if dst not in prev:
        return None
    out: list[tuple[str, str, str]] = []
    at = dst
    while at != src:
        p, rel = prev[at]
        out.append((p, at, rel))
        at = p
    out.reverse()
    return out


def pagerank(
    ids: Sequence[str], succ: Succ, damping: float = 0.85, tol: float = 1e-10,
    max_iter: int = 200,
) -> dict[str, float]:
    """PageRank of ``ids`` over the edges ``succ`` gives (an edge ``u -> v``
    hands rank from ``u`` to ``v``; one written twice counts twice). A node with
    no edge out spreads its rank over every node. The ranks sum to 1."""
    n = len(ids)
    if not n:
        return {}
    pos = {nid: i for i, nid in enumerate(ids)}
    out_lists = [[pos[v] for v in succ.get(nid, ()) if v in pos] for nid in ids]
    rank = [1.0 / n] * n
    for _ in range(max_iter):
        nxt = [0.0] * n
        dangling = 0.0
        for i, outs in enumerate(out_lists):
            if outs:
                share = rank[i] / len(outs)
                for j in outs:
                    nxt[j] += share
            else:
                dangling += rank[i]
        base = (1.0 - damping) / n + damping * dangling / n
        nxt = [base + damping * x for x in nxt]
        diff = sum(abs(a - b) for a, b in zip(nxt, rank, strict=True))
        rank = nxt
        if diff < tol:
            break
    return {nid: rank[i] for i, nid in enumerate(ids)}


def _hash_order(ids: Iterable[str]) -> list[str]:
    return sorted(ids, key=lambda s: hashlib.sha1(s.encode("utf-8", "replace"),
                                                  usedforsecurity=False).hexdigest())


class Betweenness(NamedTuple):
    scores: dict[str, float]
    sources: int      # how many sources the paths were counted from
    nodes: int        # of how many
    exact: bool


def betweenness(ids: Sequence[str], succ: Succ, samples: int = 0) -> Betweenness:
    """Betweenness of each node of ``ids`` along the directed edges of ``succ``
    (Brandes): the number of shortest paths between two other nodes that pass
    through it. With ``0 < samples < len(ids)`` the paths are counted from that
    many sources, picked by a hash of their id, and scaled up by
    ``len(ids) / samples``: an estimate, said so in ``exact``."""
    n = len(ids)
    pos = {nid: i for i, nid in enumerate(ids)}
    out_lists = [[pos[v] for v in succ.get(nid, ()) if v in pos and v != nid] for nid in ids]
    sources = list(range(n))
    exact = True
    if 0 < samples < n:
        sources = [pos[s] for s in _hash_order(ids)[:samples]]
        exact = False
    score = [0.0] * n
    for s in sources:
        stack: list[int] = []
        preds: list[list[int]] = [[] for _ in range(n)]
        sigma = [0] * n
        sigma[s] = 1
        dist = [-1] * n
        dist[s] = 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in out_lists[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = [0.0] * n
        while stack:
            w = stack.pop()
            for v in preds[w]:
                delta[v] += sigma[v] / sigma[w] * (1.0 + delta[w])
            if w != s:
                score[w] += delta[w]
    scale = n / len(sources) if sources and not exact else 1.0
    return Betweenness({nid: score[i] * scale for i, nid in enumerate(ids)},
                       len(sources), n, exact)


def dominators(root: str, succ: Succ) -> dict[str, str]:
    """The immediate dominator of every node reachable from ``root`` along
    ``succ`` (the root maps to itself): the last node every path from the root
    to it must pass through. Cooper, Harvey and Kennedy, "A Simple, Fast
    Dominance Algorithm" (2001), on the reverse postorder of a depth-first walk."""
    order: list[str] = []
    seen = {root}
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        node, k = stack.pop()
        kids = succ.get(node, ())
        if k < len(kids):
            stack.append((node, k + 1))
            nxt = kids[k]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, 0))
        else:
            order.append(node)
    order.reverse()                       # reverse postorder, root first
    rpo = {nid: i for i, nid in enumerate(order)}
    preds: dict[str, list[str]] = {nid: [] for nid in order}
    for u in order:
        for v in succ.get(u, ()):
            if v in preds and u != v:
                preds[v].append(u)
    idom: dict[str, str] = {root: root}

    def meet(a: str, b: str) -> str:
        while a != b:
            while rpo[a] > rpo[b]:
                a = idom[a]
            while rpo[b] > rpo[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for v in order[1:]:
            done = [p for p in preds[v] if p in idom]
            if not done:
                continue
            new = done[0]
            for p in done[1:]:
                new = meet(p, new)
            if idom.get(v) != new:
                idom[v] = new
                changed = True
    return idom


def dominated_counts(idom: Mapping[str, str]) -> dict[str, int]:
    """How many nodes each node dominates, itself left out (its subtree in the
    dominator tree ``idom`` gives, less one)."""
    kids: dict[str, list[str]] = {}
    root = next((v for v, d in idom.items() if v == d), None)
    for v, d in idom.items():
        if v != d:
            kids.setdefault(d, []).append(v)
    size: dict[str, int] = {}
    if root is None:
        return {}
    stack: list[tuple[str, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            size[node] = 1 + sum(size[k] for k in kids.get(node, ()))
        else:
            stack.append((node, True))
            stack.extend((k, False) for k in kids.get(node, ()))
    return {v: s - 1 for v, s in size.items()}


class CutStructure(NamedTuple):
    # (u, v, nodes on v's side once the edge goes, nodes of their component)
    bridges: list[tuple[str, str, int, int]]
    # node -> (nodes cut off from the largest remaining piece, pieces, component size)
    cuts: dict[str, tuple[int, int, int]]


def cut_structure(ids: Sequence[str], neighbours: Mapping[str, Iterable[str]]) -> CutStructure:
    """The bridges (edges whose removal disconnects their component) and cut
    vertices (nodes whose removal does) of the undirected simple graph
    ``neighbours`` over ``ids``, by Tarjan's low-link on an iterative depth-first
    walk. A bridge carries the size of the side it cuts off from the walk's
    start; a cut vertex, how many nodes lose the largest piece left without it."""
    adj = {nid: sorted({v for v in neighbours.get(nid, ()) if v != nid}) for nid in ids}
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    sub: dict[str, int] = {}
    bridges: list[tuple[str, str, int, int]] = []
    cuts: dict[str, tuple[int, int, int]] = {}
    clock = 0
    for start in ids:
        if start in disc:
            continue
        comp_bridges: list[tuple[str, str, int]] = []
        cut_parts: dict[str, list[int]] = {}
        disc[start] = low[start] = clock
        clock += 1
        sub[start] = 1
        stack: list[tuple[str, str | None, int]] = [(start, None, 0)]
        while stack:
            node, parent, k = stack.pop()
            kids = adj.get(node, [])
            if k < len(kids):
                stack.append((node, parent, k + 1))
                nb = kids[k]
                if nb == parent:
                    continue
                if nb in disc:
                    low[node] = min(low[node], disc[nb])
                else:
                    disc[nb] = low[nb] = clock
                    clock += 1
                    sub[nb] = 1
                    stack.append((nb, node, 0))
                continue
            if parent is None:
                continue
            low[parent] = min(low[parent], low[node])
            sub[parent] += sub[node]
            if low[node] > disc[parent]:
                comp_bridges.append((parent, node, sub[node]))
            if low[node] >= disc[parent]:
                cut_parts.setdefault(parent, []).append(sub[node])
        size = sub[start]
        bridges.extend((u, v, s, size) for u, v, s in comp_bridges)
        for node, parts in cut_parts.items():
            if node == start:
                if len(parts) < 2:
                    continue          # the root cuts only when it has two subtrees
                pieces = parts
            else:
                pieces = parts + [size - 1 - sum(parts)]
            pieces = [p for p in pieces if p > 0]
            if len(pieces) < 2:
                continue
            cuts[node] = (size - 1 - max(pieces), len(pieces), size)
    return CutStructure(bridges, cuts)
