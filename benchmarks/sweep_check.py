# ruff: noqa: E501
"""The checks the strongest model runs on what a sweep returned.

A sweep (`.claude/workflows/rule-sweep.js`, docs/rule-sources.md 'Phase 2b:
the sweep') has a smaller model read a language's specification and binder
section by section and return rows: a rule held by a case, a new case, a rule
not followed, or a section with no rule. Nothing it returns is trusted. This
script takes the workflow's result and, for each new case:

1. gives it to the language's compiler (`verify_cases.check`): a case it
   rejects is a mistake in the list;
2. asks the type checker what its names are bound to (`verify_cases.binding`,
   where one answers): an expectation the compiler contradicts is wrong;
3. builds it with the engine (`conformance.run_case`): held, or an open defect.

It also checks that every `held` row names cases that exist and every section
named is one of the group's. What it prints is the list to read; it writes
nothing into the repository. With `--emit FILE` the cases that pass 1 and 2
are written as `case(...)` calls to paste under `found_by("sweep")`.

    python benchmarks/sweep_check.py RESULT.json
    python benchmarks/sweep_check.py RESULT.json --emit /tmp/sweep-cases.py --json /tmp/sweep-check.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conformance  # noqa: E402
import verify_cases  # noqa: E402


def load(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # the Workflow tool's result, or the script's return value alone
    return data.get("result", data) if isinstance(data, dict) else data


def as_case(lang: str, nc: dict[str, Any], where: str) -> dict[str, Any]:
    return {"lang": lang, "id": f"{lang}/{nc['cid']}", "rule": nc["rule"], "where": where,
            "files": nc["files"], "expect": [tuple(e) for e in nc.get("expect", [])],
            "absent": [tuple(e) for e in nc.get("absent", [])], "source": nc.get("source"),
            "since": nc.get("since") or None, "until": None, "found": "sweep"}


def check_one(c: dict[str, Any], tool: Path | None) -> dict[str, Any]:
    compiled = verify_cases.verify(c)
    out: dict[str, Any] = {"id": c["id"], "compiles": compiled["ok"], "compile_error": compiled["first"]}
    if compiled["ok"] and tool is not None:
        b = verify_cases.binding(c, tool)
        out["binding_wrong"] = b["wrong"]
        out["unjudged"] = b["unjudged"]
        out["binding_error"] = b["error"]
    if compiled["ok"]:
        r = conformance.run_case(c)
        out["engine"] = "holds" if r["ok"] else "broken"
        out["missing"], out["unwanted"] = r["missing"], r["unwanted"]
    return out


def binder_tool(lang: str, tmp: Path) -> Path | None:
    if lang != "go":
        return None
    import oracle_go
    if oracle_go.setup(tmp):
        return None
    built = subprocess.run(["go", "build", "-o", "dump", "."], cwd=tmp, capture_output=True,
                           text=True, env={**os.environ, "GOFLAGS": "-mod=mod"})
    return tmp if built.returncode == 0 else None


def emit(c: dict[str, Any]) -> str:
    files = "{" + ", ".join(f"{k!r}: {v!r}" for k, v in c["files"].items()) + "}"
    extra = f", since={c['since']!r}" if c.get("since") else ""
    return (f"case({c['lang']!r}, {c['id'].split('/', 1)[1]!r}, {c['rule']!r},\n"
            f"     {c['where']!r}, {files},\n"
            f"     {[tuple(e) for e in c['expect']]!r}, {[tuple(e) for e in c['absent']]!r},\n"
            f"     source={c['source']!r}{extra})\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("result", help="the sweep workflow's result (JSON)")
    ap.add_argument("--where", default="", help="the `where` of the emitted cases (the specification as a whole)")
    ap.add_argument("--emit", help="write the cases that compile and that the compiler agrees with here")
    ap.add_argument("--json", help="write every verdict here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    res = load(args.result)
    lang = res["lang"]
    existing = {c["id"] for c in conformance.CASES}
    problems: list[str] = []
    new: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for g in res["groups"]:
        for row in g["rows"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            if row["section"] not in g["sections"]:
                problems.append(f"{g['group']}: section {row['section']!r} is not one of the group's")
            if row["status"] == "held":
                for cid in row.get("cases") or []:
                    if cid not in existing:
                        problems.append(f"{g['group']}: held by {cid}, which is no case")
                if not row.get("cases"):
                    problems.append(f"{g['group']}: held, but no case named: {row['rule']}")
            if row["status"] == "new":
                nc = row.get("new_case")
                if not nc:
                    problems.append(f"{g['group']}: new, but no case: {row['rule']}")
                    continue
                c = as_case(lang, nc, args.where or f"{lang} sweep")
                if c["id"] in existing:
                    problems.append(f"{g['group']}: {c['id']} is already a case")
                    continue
                if c["id"] in new:
                    problems.append(f"{g['group']}: {c['id']} twice")
                    continue
                new[c["id"]] = c
    print(f"{lang}: rows {counts}; {len(new)} new cases")
    for p in problems:
        print(f"  LIST     {p}")
    with tempfile.TemporaryDirectory() as tmp:
        tool = binder_tool(lang, Path(tmp))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            verdicts = list(pool.map(lambda c: check_one(c, tool), new.values()))
    good = []
    for v in verdicts:
        if not v["compiles"]:
            print(f"  REJECTED {v['id']:48} {v['compile_error'][:120]}")
            continue
        if v.get("binding_wrong"):
            for w in v["binding_wrong"]:
                print(f"  WRONG    {v['id']:48} {tuple(w['edge'])}: case {w['expected']}, compiler {w['compiler']}")
            continue
        good.append(new[v["id"]])
        flag = "holds " if v["engine"] == "holds" else "BROKEN"
        detail = "" if v["engine"] == "holds" else f" missing {v['missing']} unwanted {v['unwanted']}"
        unj = f" (unjudged {len(v.get('unjudged') or [])})" if v.get("unjudged") else ""
        print(f"  {flag}   {v['id']:48}{unj}{detail}")
    print(f"\n{len(good)} of {len(new)} cases compile and agree with the compiler; "
          f"{sum(1 for v in verdicts if v.get('engine') == 'broken' and not v.get('binding_wrong'))} of them the engine does not hold")
    if args.emit:
        Path(args.emit).write_text("".join(emit(c) for c in good), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(verdicts, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
