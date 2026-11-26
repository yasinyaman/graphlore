# ruff: noqa: E501
"""graphlore on one large repository: build time and memory, updates, query latency.

    python benchmarks/scale.py REPO --out /tmp/scale-out            # build, update, queries
    python benchmarks/scale.py REPO --out /tmp/scale-out --queries  # queries on a graph already built

Three builds with the native engine in a child process each (its wall time and
its peak resident memory, from `getrusage`): a full build, an update with
nothing changed, and an update after one appended function in a file the
`--touch` option names (taken back afterwards). Then the query tools are called
in this process on 40 definitions sampled with a fixed seed: the first call of
each (which fills the per-graph caches) apart, the rest as p50 / p95; the tools
that read the whole graph are timed twice. Nothing is written to the repository
but the touched file, which is restored.
"""
from __future__ import annotations

import argparse
import json
import random
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build(root: Path, out: Path, incremental: bool) -> dict:
    cmd = [sys.executable, "-m", "graphlore_engine", "build", str(root), "--out", str(out / "graph.json")]
    if incremental:
        cmd.append("--incremental")
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t = time.monotonic()
    done = subprocess.run(cmd, capture_output=True, text=True)
    seconds = time.monotonic() - t
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if done.returncode != 0:
        raise SystemExit(done.stderr[-2000:])
    report = json.loads(done.stdout[done.stdout.find("{"):])
    return {
        "seconds": round(seconds, 1),
        # (the peak of all children so far: each build is at least the last one)
        "max_rss_mb": round(max(peak, before) / 1024),
        "phases_ms": report.get("phases_ms"),
        "memory_mb": report.get("memory_mb"),
        "incremental": report.get("incremental"),
        "nodes": report.get("nodes"), "edges": report.get("edges"), "files": report.get("files"),
    }


def queries(root: Path, out: Path) -> dict:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from graphlore import server  # noqa: E402

    project = out / "project"
    (project / server.config.OUT_DIR_NAME).mkdir(parents=True, exist_ok=True)
    link = project / server.config.OUT_DIR_NAME / "graph.json"
    if not link.exists():
        link.symlink_to(out / "graph.json")
    server.config.PROJECT_DIR = project
    t = time.monotonic()
    graph = server._load_graph()
    load = time.monotonic() - t
    if isinstance(graph, str):
        raise SystemExit(graph)
    defs = [n for n in graph["nodes"]
            if n.get("qualname") and n.get("kind") in ("function", "method", "class", "struct", "interface")]
    random.seed(7)
    names = [n["qualname"] for n in random.sample(defs, min(40, len(defs)))]

    def timed(fn) -> float:
        t = time.monotonic()
        fn()
        return time.monotonic() - t

    rows: dict[str, dict] = {}
    per_node = {
        "graphlore_neighbors": server.graphlore_neighbors,
        "graphlore_impact": server.graphlore_impact,
        "graphlore_subgraph": server.graphlore_subgraph,
        "graphlore_tests_for": server.graphlore_tests_for,
        "graphlore_node_details": server.graphlore_node_details,
    }
    for name, fn in per_node.items():
        first = timed(lambda fn=fn: fn(names[0], as_json=True))
        ts = sorted(timed(lambda q=q, fn=fn: fn(q, as_json=True)) for q in names[1:])
        rows[name] = {"first_s": round(first, 2), "p50_ms": round(1000 * statistics.median(ts)),
                      "p95_ms": round(1000 * ts[max(0, int(0.95 * len(ts)) - 1)])}
    for name in ("graphlore_overview", "graphlore_cycles", "graphlore_god_nodes"):
        fn = getattr(server, name)
        first = timed(lambda fn=fn: fn(as_json=True))
        again = timed(lambda fn=fn: fn(as_json=True))
        rows[name] = {"first_s": round(first, 2), "again_ms": round(1000 * again)}
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"graph_load_s": round(load, 2), "max_rss_mb": round(peak / 1024), "tools": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repo")
    ap.add_argument("--out", required=True, help="a directory for graph.json and its cache")
    ap.add_argument("--touch", help="the file (relative to REPO) the one-file update appends to")
    ap.add_argument("--queries", action="store_true", help="only time the queries")
    ap.add_argument("--json", help="write the results here")
    args = ap.parse_args()
    root, out = Path(args.repo).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"repo": root.name}
    if not args.queries:
        result["full"] = build(root, out, incremental=False)
        result["update_nothing_changed"] = build(root, out, incremental=True)
        if args.touch:
            f = root / args.touch
            original = f.read_bytes()
            try:
                f.write_bytes(original + b"\n// graphlore scale probe\n")
                result["update_one_file"] = build(root, out, incremental=True)
            finally:
                f.write_bytes(original)
    result["queries"] = queries(root, out)
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
