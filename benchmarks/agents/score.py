"""Score the runs of run.py and write the table of docs/benchmark-agents.md.

    python benchmarks/agents/score.py RESULTS_DIR [--markdown]

A run solves its task when every `expect.all` string is in its answer
(case-insensitive). Per client and variant: tasks solved (with `--repeat`,
per repetition: the mean, its sample standard deviation and the range), mean
graphlore tool calls and mean tool calls of any kind, runs that called none of
the task's suited `tools` (a wrong choice, or no graphlore at all), mean input
/ output tokens where the client reports them (± their standard deviation
over runs), and mean seconds. An arm of a client that also ran the
`baseline` variant gets its difference from it: tasks solved, and input
tokens as a ratio.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def solved(task: dict, answer: str) -> bool:
    low = answer.lower()
    return all(s.lower() in low for s in task["expect"]["all"])


def _mean_sd(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = sum(values) / len(values)
    return mean, (statistics.stdev(values) if len(values) > 1 else 0.0)


def score(rows: list[dict], tasks: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["task"] in tasks:
            groups[(r["client"], r["variant"])].append(r)
    table = []
    for (client, variant), runs in sorted(groups.items()):
        n = len(runs)
        by_rep: dict[int, list[dict]] = defaultdict(list)
        for r in runs:
            by_rep[int(r.get("rep", 0))].append(r)
        per_rep = [sum(solved(tasks[r["task"]], r["answer"]) for r in rs)
                   for _, rs in sorted(by_rep.items())]
        ok = sum(per_rep)
        mean_ok, sd_ok = _mean_sd([float(x) for x in per_rep])
        suited = sum(bool(set(r["tools"]) & set(tasks[r["task"]].get("tools", []))) for r in runs)
        tok_in = [r["input_tokens"] for r in runs if r["input_tokens"] is not None]
        tok_out = [r["output_tokens"] for r in runs if r["output_tokens"] is not None]
        mean_in, sd_in = _mean_sd([float(x) for x in tok_in])
        table.append({
            "client": client, "variant": variant, "runs": n, "solved": ok,
            "repeats": len(per_rep),
            "tasks": len({r["task"] for r in runs}),
            "solved_per_repeat": per_rep,
            "solved_mean": round(mean_ok, 2) if mean_ok is not None else None,
            "solved_sd": round(sd_ok, 2) if sd_ok is not None else None,
            "tool_calls": round(sum(len(r["tools"]) for r in runs) / n, 1),
            "all_calls": round(sum(len(r.get("calls", r["tools"])) for r in runs) / n, 1),
            "no_suited_tool": n - suited,
            "input_tokens": round(mean_in) if mean_in is not None else None,
            "input_tokens_sd": round(sd_in) if sd_in is not None else None,
            "output_tokens": round(sum(tok_out) / len(tok_out)) if tok_out else None,
            "seconds": round(sum(r["seconds"] for r in runs) / n, 1),
            "failed_runs": sum(r["exit"] != 0 for r in runs),
            "unsolved": sorted({r["task"] for r in runs
                                if not solved(tasks[r["task"]], r["answer"])}),
        })
    base = {t["client"]: t for t in table if t["variant"] == "baseline"}
    for t in table:
        b = base.get(t["client"])
        if b is None or t is b:
            continue
        t["vs_baseline"] = {
            "solved": round((t["solved_mean"] or 0) - (b["solved_mean"] or 0), 2),
            "input_tokens_ratio": (round(t["input_tokens"] / b["input_tokens"], 2)
                                   if t["input_tokens"] and b["input_tokens"] else None),
        }
    return table


def _solved_cell(t: dict) -> str:
    if t["repeats"] <= 1:
        return f"{t['solved']}/{t['runs']}"
    per = t["solved_per_repeat"]
    return (f"{t['solved_mean']:g} ± {t['solved_sd']:g} / {t['tasks']} "
            f"({min(per)}–{max(per)}, {t['repeats']} runs)")


def _vs_cell(t: dict) -> str:
    vs = t.get("vs_baseline")
    if not vs:
        return "—"
    ratio = vs["input_tokens_ratio"]
    return f"{vs['solved']:+g} solved" + (f", {ratio:g}× tokens" if ratio is not None else "")


def markdown(table: list[dict]) -> str:
    fmt = lambda v: "—" if v is None else f"{v:,}" if isinstance(v, int) else str(v)  # noqa: E731
    head = ("| Client | Variant | Solved | Tool calls | All calls | No suited tool | "
            "Input tokens | Output tokens | Seconds | Failed runs | vs baseline |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for t in table:
        tok_in = fmt(t["input_tokens"])
        if t["input_tokens"] is not None and t["repeats"] > 1:
            tok_in += f" ± {t['input_tokens_sd']:,}"
        lines.append(f"| {t['client']} | {t['variant']} | {_solved_cell(t)} | "
                     f"{t['tool_calls']} | {t['all_calls']} | {t['no_suited_tool']} | {tok_in} | "
                     f"{fmt(t['output_tokens'])} | {t['seconds']} | {t['failed_runs']} | "
                     f"{_vs_cell(t)} |")
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
