# ruff: noqa: E501
"""An update writes the graph a full build writes: checked on real repositories.

    python benchmarks/incremental_check.py /tmp/polyglot/* --edits 5
    python benchmarks/incremental_check.py REPO --edits 20 --seed 3 --keep

Each repository is copied to a temporary directory and built once. Then, edit
after edit (seeded, so a failure can be repeated), one source file the engine
reads is changed: a line deleted, a line doubled, or a line of another file of
its language put in, or a comment line put in (whether the result still
parses does not matter: the update must agree with a full build whatever the
source is; `--kinds` picks among them). After each edit
an update (`--incremental`) is compared, byte for byte, with a full build of
the same tree from the same previous graph (communities start from it in both).
The edits accumulate. A difference stops that repository, prints the edges
that differ and keeps the copy. The table gives, per repository, the edits
checked, the share of files whose resolution the updates took from the
last build (`incremental.resolve_reused`), and the nodes an update moved to
another community, on average per edit (`moved`: a small edit should move few),
with how the updates' settling ended.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def build(root: Path, out: Path, incremental: bool) -> dict:
    cmd = [sys.executable, "-m", "graphlore_engine", "build", str(root), "--out", str(out / "graph.json")]
    if incremental:
        cmd.append("--incremental")
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-2000:])
    return json.loads(done.stdout)


def source_files(graph: Path) -> list[str]:
    g = json.loads(graph.read_text(encoding="utf-8"))
    return sorted({n["source_file"] for n in g["nodes"] if n.get("kind") == "file" and n.get("language")})


KINDS = ["delete", "double", "copy", "comment"]
HASH_COMMENT = {".py", ".rb", ".sh", ".pl", ".r", ".yaml", ".yml", ".toml"}


def edit(root: Path, rel: str, others: list[str], rng: random.Random, kinds: list[str]) -> str:
    path = root / rel
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    kind = rng.choice(kinds)
    at = rng.randrange(len(lines)) if lines else 0
    if kind == "comment":
        mark = "#" if Path(rel).suffix.lower() in HASH_COMMENT else "//"
        indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())] if lines else ""
        lines.insert(at, f"{indent}{mark} graphlore edit {at}")
    elif kind == "delete" and len(lines) > 1:
        del lines[at]
    elif kind == "double" and lines:
        lines.insert(at, lines[at])
    else:
        kind = "copy"
        same = [o for o in others if o != rel and Path(o).suffix == Path(rel).suffix] or [rel]
        src = (root / rng.choice(same)).read_text(encoding="utf-8", errors="replace").split("\n")
        lines.insert(at, rng.choice(src) if src else "")
    path.write_text("\n".join(lines), encoding="utf-8")
    return f"{kind} {rel}:{at + 1}"


def differences(a: Path, b: Path) -> list[str]:
    ga, gb = json.loads(a.read_text()), json.loads(b.read_text())
    key = lambda e: (e["source"], e["target"], e["relation"])  # noqa: E731
    ea, eb = {key(e): e for e in ga["links"]}, {key(e): e for e in gb["links"]}
    out = [f"  update only: {k}" for k in ea if k not in eb][:10]
    out += [f"  full only:   {k}" for k in eb if k not in ea][:10]
    out += [f"  differs:     {k}" for k in ea if k in eb and ea[k] != eb[k]][:10]
    na, nb = {n["id"]: n for n in ga["nodes"]}, {n["id"]: n for n in gb["nodes"]}
    out += [f"  node: {i}" for i in na if i in nb and na[i] != nb[i]][:10]
    if len(na) != len(nb):
        out.append(f"  nodes: {len(na)} after the update, {len(nb)} after the full build")
    if not out:
        out.append("  (same edges and nodes: the order or the header differs)")
    return out


def moved(before: bytes, after: Path) -> int:
    """Nodes in both graphs whose community differs."""
    a = {n["id"]: n.get("community") for n in json.loads(before)["nodes"]}
    return sum(1 for n in json.loads(after.read_text())["nodes"] if n["id"] in a and a[n["id"]] != n.get("community"))


def check(repo: Path, edits: int, seed: int, keep: bool, kinds: list[str]) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"incr-{repo.name}-"))
    root = work / repo.name
    shutil.copytree(repo, root, symlinks=True, ignore=shutil.ignore_patterns("graphify-out", "graphlore-out", "graphlore-native"))
    out, full = work / "out", work / "full"
    build(root, out, incremental=False)
    files = source_files(out / "graph.json")
    rng = random.Random(f"{seed}:{repo.name}")
    reused = total = shifted = 0
    ends: dict[str, int] = {}
    row = {"repo": repo.name, "edits": 0, "same": True, "work": str(work)}
    for i in range(edits):
        if not files:
            break
        what = edit(root, rng.choice(files), files, rng, kinds)
        previous = (out / "graph.json").read_bytes()
        report = build(root, out, incremental=True)
        if full.exists():
            shutil.rmtree(full)
        full.mkdir()
        (full / "graph.json").write_bytes(previous)
        build(root, full, incremental=False)
        inc = report.get("incremental") or {}
        reused += inc.get("resolve_reused", 0)
        total += report.get("files", 0)
        shifted += moved(previous, out / "graph.json")
        end = (report.get("communities") or {}).get("settle_end") or "?"
        ends[end] = ends.get(end, 0) + 1
        row["edits"] = i + 1
        if (out / "graph.json").read_bytes() != (full / "graph.json").read_bytes():
            row["same"] = False
            row["failed_at"] = what
            row["diff"] = differences(out / "graph.json", full / "graph.json")
            return row
    row["reused"] = reused / total if total else 0.0
    row["moved"] = shifted / row["edits"] if row["edits"] else 0.0
    row["settle"] = ends
    if not keep:
        shutil.rmtree(work)
        row.pop("work")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--edits", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", action="store_true", help="keep each copy (a failing one is always kept)")
    ap.add_argument("--json", help="write the rows here")
    ap.add_argument("--kinds", default=",".join(KINDS), help="the edits to make, of " + ", ".join(KINDS))
    args = ap.parse_args()
    rows = []
    failed = 0
    for r in args.repos:
        repo = Path(r).resolve()
        if not repo.is_dir():
            continue
        try:
            row = check(repo, args.edits, args.seed, args.keep, args.kinds.split(","))
        except RuntimeError as e:
            row = {"repo": repo.name, "same": False, "error": str(e)[-400:]}
        rows.append(row)
        if row["same"]:
            ends = " ".join(f"{k} {v}" for k, v in sorted(row.get("settle", {}).items()))
            print(f"{row['repo']:<28} {row['edits']:>3} edits  same   reused {100 * row.get('reused', 0):5.1f}%  moved {row.get('moved', 0):7.1f}  settle {ends}", flush=True)
        else:
            failed += 1
            print(f"{row['repo']:<28} DIFFERS after {row.get('failed_at', row.get('error'))} (copy kept at {row.get('work')})", flush=True)
            for line in row.get("diff", []):
                print(line, flush=True)
    print(f"{len(rows) - failed} of {len(rows)} repositories: every update the same as a full build")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
