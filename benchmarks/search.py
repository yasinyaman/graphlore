#!/usr/bin/env python3
"""Search quality and speed: graphlore's built-in index against semble.

Runs the query set semble publishes for its own benchmark (MinishLab/semble,
``benchmarks/annotations/*.json``, MIT; about 1,250 queries over 63 repositories
in 19 languages, written and checked by an LLM) through:

  bm25     graphlore's built-in index, lexical tier only (no extra needed)
  hybrid   the built-in index with its dense tier (the [embed] extra)
  semble   semble itself (the [semble] extra), as the reference

and scores them exactly as semble's ``benchmarks/run_benchmark.py`` does: the
top 10 chunks per query, a target counts at the rank of the first chunk that
covers it (file match, plus line overlap when the target has lines), binary
relevance over primary and secondary targets, NDCG@10 averaged per repository,
then per language, then over languages. Also: recall@10, cold index time and
warm query p50.

The annotations and repositories are fetched at a pinned semble release
(``--semble-ref``), never vendored here, and the fetch is refused under
GRAPHLORE_OFFLINE.

Setup (a venv with graphlore[treesitter,embed] and semble==0.7.3):

  python benchmarks/search.py --setup --root /tmp/searchbench
  python benchmarks/search.py --setup --root /tmp/searchbench --repos flask,axios,gin

Run:

  python benchmarks/search.py --root /tmp/searchbench --json benchmarks/results-search.json
  python benchmarks/search.py --root /tmp/searchbench --systems bm25 --repos flask
  python benchmarks/search.py --root /tmp/searchbench --set _OVERLAY_WEIGHT=0  # an ablation

``--calibrate`` also measures the score distributions behind the duplication
floors (_BUILTIN_DENSE_SCORE_FLOOR / _BUILTIN_LEXICAL_SCORE_FLOOR): near-clones
(a definition with a fifth of its lines dropped and its identifiers renamed)
against unrelated definitions of a similar size.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from graphlore import config  # noqa: E402
from graphlore import search as gl_search  # noqa: E402

SEMBLE_REF = "v0.7.3"
RAW = "https://raw.githubusercontent.com/MinishLab/semble/{ref}/benchmarks/{path}"
TOP_K = 10
SYSTEMS = ("bm25", "hybrid", "semble")


# --- setup -------------------------------------------------------------------

def _fetch(url: str) -> bytes:
    if config.offline():
        raise SystemExit(f"GRAPHLORE_OFFLINE is set: refusing to fetch {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "graphlore-benchmark"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - pinned https URL
        return resp.read()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def setup(root: Path, ref: str, only: set[str] | None) -> None:
    meta = root / "_semble"
    (meta / "annotations").mkdir(parents=True, exist_ok=True)
    repos = json.loads(_fetch(RAW.format(ref=ref, path="repos.json")))
    (meta / "repos.json").write_text(json.dumps(repos, indent=1), encoding="utf-8")
    (meta / "REF").write_text(ref + "\n", encoding="utf-8")
    wanted = [r for r in repos if only is None or r["name"] in only]

    def one(repo: dict[str, Any]) -> str:
        name = repo["name"]
        ann = meta / "annotations" / f"{name}.json"
        if not ann.exists():
            ann.write_bytes(_fetch(RAW.format(ref=ref, path=f"annotations/{name}.json")))
        d = root / name
        if not (d / ".git").exists():
            d.mkdir(parents=True, exist_ok=True)
            _git(d, "init", "-q")
            _git(d, "remote", "add", "origin", repo["url"])
            _git(d, "fetch", "-q", "--depth", "1", "origin", repo["revision"])
            _git(d, "checkout", "-q", "FETCH_HEAD")
            return f"cloned {name}"
        return f"ready {name}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        for msg in pool.map(one, wanted):
            print(msg, flush=True)


# --- tasks and scoring (semble's definitions) ----------------------------------

def _target(raw: Any) -> tuple[str, int | None, int | None]:
    if isinstance(raw, str):
        return raw, None, None
    s, e = raw.get("start_line"), raw.get("end_line")
    return (str(raw["path"]), int(s) if s is not None else None,
            int(e) if e is not None else None)


def load_tasks(root: Path, names: set[str] | None) -> dict[str, list[dict[str, Any]]]:
    meta = root / "_semble"
    repos = {r["name"]: r for r in json.loads((meta / "repos.json").read_text(encoding="utf-8"))}
    out: dict[str, list[dict[str, Any]]] = {}
    for ann in sorted((meta / "annotations").glob("*.json")):
        name = ann.stem
        if name not in repos or (names and name not in names) or not (root / name).is_dir():
            continue
        for item in json.loads(ann.read_text(encoding="utf-8")):
            repo = item.get("repo", name)
            if repo not in repos:
                continue
            targets = [_target(t) for t in item.get("relevant", []) + item.get("secondary", [])]
            out.setdefault(repo, []).append({
                "query": item["query"], "targets": targets,
                "category": item.get("category") or "unknown",
            })
    return out


def _path_matches(file_path: str, target: str) -> bool:
    f, t = file_path.replace("\\", "/"), target.replace("\\", "/")
    return f == t or f.endswith("/" + t) or t.endswith("/" + f)


def _covers(chunk: Any, target: tuple[str, int | None, int | None]) -> bool:
    path, s, e = target
    if not _path_matches(str(chunk.file_path), path):
        return False
    if s is None or e is None:
        return True
    return not (int(chunk.end_line) < s or int(chunk.start_line) > e)


def _dcg(rels: list[int]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


_Target = tuple[str, "int | None", "int | None"]


def score(results: list[Any], targets: list[_Target]) -> tuple[float, float]:
    """(NDCG@10, recall@10) of one query, as semble's run_benchmark scores it."""
    if not targets:
        return 0.0, 0.0
    ranks = []
    for t in targets:
        for i, r in enumerate(results, 1):
            if _covers(r.chunk, t):
                ranks.append(i)
                break
    rels = [0] * TOP_K
    for rank in ranks:
        if 1 <= rank <= TOP_K:
            rels[rank - 1] = 1
    ideal = _dcg([1] * min(TOP_K, len(targets)))
    return _dcg(rels) / ideal, len(ranks) / len(targets)


# --- systems -------------------------------------------------------------------

def _benchmark_dir(root: Path, repo: dict[str, Any]) -> Path:
    d = root / repo["name"]
    return d / repo["benchmark_root"] if repo.get("benchmark_root") else d


class _Semble:
    def __init__(self, path: Path) -> None:
        from semble import SembleIndex

        self.index = SembleIndex.from_path(str(path))

    def search(self, query: str, top_k: int) -> list[Any]:
        return self.index.search(query, top_k=top_k)


class _Builtin:
    def __init__(self, path: Path, dense: bool) -> None:
        from graphlore import spans

        config.PROJECT_DIR = path.resolve()
        gl_search._INDEX_CACHE.clear()
        spans._SPAN_CACHE.clear()  # cold: the span engine parses again too
        os.environ["GRAPHLORE_CACHE_DIR"] = "off"  # a cold index, every run
        index = gl_search.index_for_project(dense=dense)
        if index is None:
            raise RuntimeError("nothing to index")
        if dense and index.dense is None:
            raise RuntimeError(f"no dense tier: {index.dense_reason}")
        self.index = index

    def search(self, query: str, top_k: int) -> list[Any]:
        return self.index.search(query, top_k)


def _build(system: str, path: Path) -> Any:
    if system == "semble":
        return _Semble(path)
    return _Builtin(path, dense=(system == "hybrid"))


def _warm_model(systems: list[str]) -> None:
    """Load the model once before timing, so the first repository's cold index
    does not include reading the model from disk."""
    if "hybrid" in systems:
        from graphlore import embed

        model, why = embed._load_model(embed.model_spec())
        if model is None:
            raise SystemExit(f"hybrid needs the [embed] extra and the model: {why}")


def run(root: Path, systems: list[str], names: set[str] | None, verbose: bool) -> dict[str, Any]:
    meta = root / "_semble"
    repos = {r["name"]: r for r in json.loads((meta / "repos.json").read_text(encoding="utf-8"))}
    tasks = load_tasks(root, names)
    _warm_model(systems)
    per_repo: dict[str, dict[str, Any]] = defaultdict(dict)
    for name in sorted(tasks):
        repo = repos[name]
        path = _benchmark_dir(root, repo)
        for system in systems:
            t0 = time.perf_counter()
            try:
                index = _build(system, path)
            except Exception as exc:  # noqa: BLE001 - report and go on
                print(f"{name:<22} {system:<7} FAILED: {type(exc).__name__}: {exc}", flush=True)
                continue
            index_ms = (time.perf_counter() - t0) * 1000
            ndcgs, recalls, lat = [], [], []
            by_cat: dict[str, list[float]] = defaultdict(list)
            for task in tasks[name]:
                t1 = time.perf_counter()
                results = index.search(task["query"], TOP_K)
                lat.append((time.perf_counter() - t1) * 1000)
                n, r = score(results, task["targets"])
                ndcgs.append(n)
                recalls.append(r)
                by_cat[task["category"]].append(n)
                if verbose:
                    print(f"    {system:<7} {n:.3f} {task['query']!r}")
            # warm p50: semble reports the median of consecutive runs on a warm index
            warm = []
            for task in tasks[name]:
                t1 = time.perf_counter()
                index.search(task["query"], TOP_K)
                warm.append((time.perf_counter() - t1) * 1000)
            row = {
                "language": repo["language"],
                "queries": len(ndcgs),
                "ndcg10": statistics.fmean(ndcgs),
                "recall10": statistics.fmean(recalls),
                "index_ms": round(index_ms, 1),
                "query_p50_ms": round(statistics.median(warm), 3),
                "by_category": {c: statistics.fmean(v) for c, v in sorted(by_cat.items())},
            }
            if system != "semble":
                row["chunks"] = len(index.index.chunks)
            per_repo[name][system] = row
            print(
                f"{name:<22} {system:<7} ndcg@10={row['ndcg10']:.3f} "
                f"recall@10={row['recall10']:.3f} index={index_ms:8.0f}ms "
                f"p50={row['query_p50_ms']:.2f}ms",
                flush=True,
            )
    return {"semble_ref": (meta / "REF").read_text(encoding="utf-8").strip(),
            "repos": per_repo, "summary": summarize(per_repo, systems)}


def summarize(per_repo: dict[str, dict[str, Any]], systems: list[str]) -> dict[str, Any]:
    """Mean over languages of the per-language mean over repositories (semble's
    headline), on the repositories every system finished."""
    common = [n for n, rows in per_repo.items() if all(s in rows for s in systems)]
    out: dict[str, Any] = {"repos": len(common)}
    for system in systems:
        by_lang: dict[str, list[float]] = defaultdict(list)
        cats: dict[str, list[float]] = defaultdict(list)
        rec, idx, p50 = [], [], []
        for n in common:
            row = per_repo[n][system]
            by_lang[row["language"]].append(row["ndcg10"])
            rec.append(row["recall10"])
            idx.append(row["index_ms"])
            p50.append(row["query_p50_ms"])
            for c, v in row["by_category"].items():
                cats[c].append(v)
        langs = {lang: statistics.fmean(v) for lang, v in sorted(by_lang.items())}
        out[system] = {
            "ndcg10": round(statistics.fmean(langs.values()), 4) if langs else None,
            "recall10": round(statistics.fmean(rec), 4) if rec else None,
            "index_ms_median": round(statistics.median(idx), 1) if idx else None,
            "query_p50_ms_median": round(statistics.median(p50), 3) if p50 else None,
            "by_language": {k: round(v, 4) for k, v in langs.items()},
            "by_category": {k: round(statistics.fmean(v), 4) for k, v in sorted(cats.items())},
        }
    return out


# --- duplication floor calibration ----------------------------------------------

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")


def _mutate(body: str, rng: random.Random) -> str:
    """A near-clone: a fifth of the lines dropped, three identifiers renamed."""
    lines = body.splitlines()
    keep = [ln for ln in lines if rng.random() > 0.2] or lines[:1]
    text = "\n".join(keep)
    names = sorted(set(_IDENT.findall(text)))
    for old in rng.sample(names, min(3, len(names))):
        text = re.sub(rf"\b{re.escape(old)}\b", old + "_copy", text)
    return text


def _body_of(idx: Any, cid: int) -> str:
    ch = idx.chunks[cid]
    full = gl_search._resolve_in_project(ch.file_path)
    assert full is not None
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[ch.start_line - 1:ch.end_line])


def _tfidf(idx: Any, n: int, text: str) -> dict[str, float]:
    """The lexical tier's find_related vector of a text (body terms, as there)."""
    tf: dict[str, int] = defaultdict(int)
    for t in gl_search._tokenize(text):
        tf[t] += 1
    return {t: (1 + math.log(c)) * gl_search._idf(n, len(idx.postings.get(t, ())) or 1)
            for t, c in tf.items()}


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    dot = sum(v * b[t] for t, v in a.items() if t in b)
    return dot / (na * nb) if na and nb else 0.0


def calibrate(root: Path, names: set[str] | None, per_repo: int, seed: int) -> dict[str, Any]:
    """Similarity of near-clones vs unrelated same-size definitions, per tier."""
    import numpy as np

    from graphlore import embed

    meta = root / "_semble"
    repos = {r["name"]: r for r in json.loads((meta / "repos.json").read_text(encoding="utf-8"))}
    rng = random.Random(seed)
    dist: dict[str, dict[str, list[float]]] = {
        "tfidf": {"clone": [], "other": []}, "dense": {"clone": [], "other": []},
    }
    for name in sorted(n for n in repos if (root / n).is_dir() and (not names or n in names)):
        b = _Builtin(_benchmark_dir(root, repos[name]), dense=True)
        idx = b.index
        assert idx.dense is not None
        n = len(idx.chunks)
        defs = [c for c, ch in idx.chunks.items()
                if ch.kind == "definition" and ch.end_line - ch.start_line + 1 >= 5]
        if len(defs) < 10:
            continue
        sample = rng.sample(defs, min(per_repo, len(defs)))
        for cid in sample:
            body = _body_of(idx, cid)
            clone = _mutate(body, rng)
            me = idx.chunks[cid]
            size = me.end_line - me.start_line + 1
            others = [
                o for o in defs if o != cid and idx.chunks[o].file_path != me.file_path
                and 1 / 3 <= (idx.chunks[o].end_line - idx.chunks[o].start_line + 1) / size <= 3
            ]
            if not others:
                continue
            other = _body_of(idx, rng.choice(others))
            base = _tfidf(idx, n, body)
            dist["tfidf"]["clone"].append(_cos(base, _tfidf(idx, n, clone)))
            dist["tfidf"]["other"].append(_cos(base, _tfidf(idx, n, other)))
            vecs = embed._encode(idx.dense.model, [body, clone, other])
            dist["dense"]["clone"].append(float(np.dot(vecs[0], vecs[1])))
            dist["dense"]["other"].append(float(np.dot(vecs[0], vecs[2])))
        print(f"calibrated {name}", flush=True)
    out: dict[str, Any] = {}
    for tier, d in dist.items():
        clone, other = sorted(d["clone"]), sorted(d["other"])
        if not clone:
            continue

        def pct(xs: list[float], p: float) -> float:
            return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)

        out[tier] = {
            "pairs": len(clone),
            "clone": {f"p{int(p * 100)}": pct(clone, p) for p in (0.05, 0.1, 0.25, 0.5)},
            "other": {f"p{int(p * 1000) / 10:g}": pct(other, p) for p in (0.5, 0.9, 0.99, 0.995)},
        }
        for floor in (0.5, 0.6, 0.7, 0.8):
            out[tier][f"at_{floor}"] = {
                "clones_kept": round(sum(x >= floor for x in clone) / len(clone), 3),
                "others_kept": round(sum(x >= floor for x in other) / len(other), 4),
            }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--setup", action="store_true", help="fetch annotations and clone repositories")
    ap.add_argument("--semble-ref", default=SEMBLE_REF)
    ap.add_argument("--repos", default="", help="comma-separated repository names")
    ap.add_argument("--systems", default=",".join(SYSTEMS))
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override a graphlore.search constant (ablations)")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--per-repo", type=int, default=40)
    ap.add_argument("--json", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    names = {n for n in args.repos.split(",") if n} or None
    if args.setup:
        setup(args.root, args.semble_ref, names)
        return
    for item in args.set:
        key, _, value = item.partition("=")
        current = getattr(gl_search, key)
        setattr(gl_search, key, type(current)(json.loads(value)) if not isinstance(current, dict)
                else json.loads(value))
    if args.calibrate:
        result: dict[str, Any] = {"calibration": calibrate(args.root, names, args.per_repo, 7)}
    else:
        systems = [s for s in args.systems.split(",") if s]
        result = run(args.root, systems, names, args.verbose)
    result["overrides"] = args.set
    print(json.dumps(result.get("summary", result.get("calibration")), indent=1))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
