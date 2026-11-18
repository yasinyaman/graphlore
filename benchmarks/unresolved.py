#!/usr/bin/env python3
"""Count the calls the native engine leaves without an edge, by reason.

The engine links a call only when the source says what is called. This asks
what it leaves out that an edge was plausible for: calls whose name is the one
callable of that name in the project (`graphlore_engine build --unresolved`
writes them, each with the reason the walk stopped). A name a library also has
(`getHeaderLine`, `setHeader`) is counted too: the table is an upper bound,
and the samples say which rows are real.

  python benchmarks/unresolved.py REPO
  python benchmarks/unresolved.py --root /tmp/polyglot            # every repository under it
  python benchmarks/unresolved.py --root /tmp/polyglot --samples 5 --language rust
  python benchmarks/unresolved.py --root /tmp/polyglot --json out.json --baseline before.json

Each repository is built into a temporary directory; nothing is written to it.
Vendored and minified files (a `vendor` / `node_modules` / `wwwroot` directory,
`.min.` in the name, a mean line over 150 bytes) are left out unless --all is
given. --baseline prints the change in every count against an earlier --json.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

Row = dict[str, Any]

_VENDORED_DIRS = {"vendor", "node_modules", "wwwroot", "third_party", "dist", "bower_components"}
_MINIFIED_MEAN_LINE = 150


def vendored(repo: Path, rel: str, seen: dict[str, bool]) -> bool:
    """Whether a file is someone else's code or minified: its calls are not the project's."""
    if rel not in seen:
        parts = Path(rel).parts
        hit = ".min." in parts[-1] or any(p in _VENDORED_DIRS for p in parts[:-1])
        if not hit:
            try:
                with open(repo / rel, "rb") as fh:
                    head = fh.read(65536)
                hit = len(head) / (head.count(b"\n") + 1) > _MINIFIED_MEAN_LINE
            except OSError:
                hit = False
        seen[rel] = hit
    return seen[rel]


def rows_of(repo: Path, everything: bool = False) -> list[Row]:
    """The engine's unresolved-call rows for one repository."""
    with tempfile.TemporaryDirectory() as tmp:
        left = Path(tmp) / "unresolved.json"
        done = subprocess.run(
            [sys.executable, "-m", "graphlore_engine", "build", str(repo),
             "--out", str(Path(tmp) / "graph.json"), "--unresolved", str(left)],
            capture_output=True, text=True,
        )
        if done.returncode != 0 or not left.exists():
            print(f"{repo.name}: build failed: {done.stderr.strip()[:200]}", file=sys.stderr)
            return []
        rows: list[Row] = json.loads(left.read_text())
    seen: dict[str, bool] = {}
    if not everything:
        rows = [r for r in rows if not vendored(repo, r["file"], seen)]
    for r in rows:
        r["repo"] = repo.name
    return rows


def where(r: Row) -> str:
    return "in a definition" if r.get("in_definition") else "at file level"


def table(rows: list[Row]) -> dict[str, dict[str, int]]:
    """language -> reason -> count."""
    out: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in rows:
        out[r.get("language") or "?"][r["reason"]] += 1
    return {lang: dict(reasons) for lang, reasons in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--root", help="a directory of repositories (benchmarks/polyglot.py --setup)")
    ap.add_argument("--language", help="only this language's rows")
    ap.add_argument("--reason", help="only rows whose reason contains this text")
    ap.add_argument("--samples", type=int, default=0, help="print this many rows per reason")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--all", action="store_true", help="count vendored and minified files too")
    ap.add_argument("--json", help="write the counts here")
    ap.add_argument("--baseline", help="an earlier --json to compare the counts with")
    args = ap.parse_args()
    if bool(args.repo) == bool(args.root):
        ap.error("give REPO or --root")
    repos = [Path(args.repo)] if args.repo else sorted(
        p for p in Path(args.root).iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    rows: list[Row] = []
    for repo in repos:
        rows.extend(rows_of(repo, args.all))
    if args.language:
        rows = [r for r in rows if r.get("language") == args.language]
    if args.reason:
        rows = [r for r in rows if args.reason in r["reason"]]

    counts = table(rows)
    before: dict[str, dict[str, int]] = {}
    if args.baseline:
        before = json.loads(Path(args.baseline).read_text()).get("counts", {})

    def delta(now: int, was: int | None) -> str:
        return "" if was is None or was == now else f" ({now - was:+d})"

    total = len(rows)
    print(f"{total} calls without an edge whose name is one definition of the project "
          f"({len(repos)} repositories)")
    by_reason: collections.Counter[str] = collections.Counter(r["reason"] for r in rows)
    print("\nby reason:")
    for reason, n in by_reason.most_common():
        was = sum(v.get(reason, 0) for v in before.values()) if before else None
        file_level = sum(1 for r in rows if r["reason"] == reason and not r.get("in_definition"))
        hidden = sum(1 for r in rows if r["reason"] == reason and not r.get("candidate_visible"))
        print(f"  {n:6}{delta(n, was):9}  {reason}   "
              f"[{file_level} at file level, {hidden} with the candidate out of sight]")
    print("\nby language:")
    for lang in sorted(counts, key=lambda k: -sum(counts[k].values())):
        n = sum(counts[lang].values())
        was = sum(before[lang].values()) if lang in before else (0 if before else None)
        ranked = sorted(counts[lang].items(), key=lambda kv: -kv[1])[:4]
        top = ", ".join(f"{r} {c}" for r, c in ranked)
        print(f"  {lang:12} {n:6}{delta(n, was):9}  {top}")

    if args.samples:
        rng = random.Random(args.seed)
        print("\nsamples:")
        for reason, _ in by_reason.most_common():
            pool = [r for r in rows if r["reason"] == reason]
            print(f"  {reason}")
            for r in rng.sample(pool, min(args.samples, len(pool))):
                cand = r["candidate"]
                print(f"    {r['repo']}/{r['file']}:{r['line']}  {r['path']}  ({where(r)})"
                      f"  -> {cand['file']} {cand['qualname']}")
    if args.json:
        Path(args.json).write_text(json.dumps({"total": total, "counts": counts}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
