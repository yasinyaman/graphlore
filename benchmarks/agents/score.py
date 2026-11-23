"""Score the runs of run.py and write the table of docs/benchmark-agents.md.

    python benchmarks/agents/score.py RESULTS_DIR [--markdown]

A run solves its task when every `expect.all` string is in its answer
(case-insensitive). Per client and variant: tasks solved, mean graphlore tool
calls, runs that called none of the task's suited `tools` (a wrong choice, or
no graphlore at all), mean input / output tokens where the client reports
them, and mean seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def solved(task: dict, answer: str) -> bool:
    low = answer.lower()
    return all(s.lower() in low for s in task["expect"]["all"])


def score(rows: list[dict], tasks: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["task"] in tasks:
            groups[(r["client"], r["variant"])].append(r)
    table = []
    for (client, variant), runs in sorted(groups.items()):
        n = len(runs)
        ok = sum(solved(tasks[r["task"]], r["answer"]) for r in runs)
        suited = sum(bool(set(r["tools"]) & set(tasks[r["task"]].get("tools", []))) for r in runs)
        tok_in = [r["input_tokens"] for r in runs if r["input_tokens"] is not None]
        tok_out = [r["output_tokens"] for r in runs if r["output_tokens"] is not None]
        table.append({
            "client": client, "variant": variant, "runs": n, "solved": ok,
            "tool_calls": round(sum(len(r["tools"]) for r in runs) / n, 1),
            "no_suited_tool": n - suited,
            "input_tokens": round(sum(tok_in) / len(tok_in)) if tok_in else None,
            "output_tokens": round(sum(tok_out) / len(tok_out)) if tok_out else None,
            "seconds": round(sum(r["seconds"] for r in runs) / n, 1),
            "failed_runs": sum(r["exit"] != 0 for r in runs),
            "unsolved": sorted(r["task"] for r in runs
                               if not solved(tasks[r["task"]], r["answer"])),
        })
    return table


def markdown(table: list[dict]) -> str:
    head = ("| Client | Variant | Solved | Tool calls | No suited tool | Input tokens | "
            "Output tokens | Seconds | Failed runs |\n|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for t in table:
        fmt = lambda v: "—" if v is None else f"{v:,}" if isinstance(v, int) else str(v)  # noqa: E731
        lines.append(f"| {t['client']} | {t['variant']} | {t['solved']}/{t['runs']} | "
                     f"{t['tool_calls']} | {t['no_suited_tool']} | {fmt(t['input_tokens'])} | "
                     f"{fmt(t['output_tokens'])} | {t['seconds']} | {t['failed_runs']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("results", help="the directory run.py wrote (its runs.jsonl)")
    p.add_argument("--tasks", default=str(HERE / "tasks.json"))
    p.add_argument("--markdown", action="store_true")
    a = p.parse_args(argv)
    tasks = {t["id"]: t for t in json.loads(Path(a.tasks).read_text(encoding="utf-8"))["tasks"]}
    rows = [json.loads(ln) for ln in (Path(a.results) / "runs.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    table = score(rows, tasks)
    if a.markdown:
        sys.stdout.write(markdown(table))
    else:
        print(json.dumps(table, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
