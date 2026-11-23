"""Run coding agents on graphlore's evaluation tasks, each agent through its
own command line, with graphlore as its MCP server.

    python benchmarks/agents/run.py --root /tmp/agents --clients claude,codex,gemini,kimi

For each client and variant (`plain`: the tools only; `agents-md`: also the
usage guide `graphlore agents-md` writes into the file that client reads), it
sets graphlore up in a checkout of the task's repository at the pinned commit
(`graphlore setup <client> --write` into the project, or the client's own
override flag), asks each task's prompt headless, and appends one row per run
to `<out>/runs.jsonl`: the final answer, the graphlore tools called, the tokens
the client reports, the time and the exit code. `score.py` turns that into the
table of docs/benchmark-agents.md.

Each client needs its own login (or API key) on the machine that runs this:
the script never asks for one. The adapters below follow each client's
documented headless mode (docs/clients.md); a client whose output format
changed shows up as answers that come back empty, so read one transcript
(`<out>/<client>-<variant>-<task>.log`) before trusting a column. The `fake`
client replays a fixed transcript against the real server and needs nothing:
it is what the test suite runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from graphlore import agents_md, clients  # noqa: E402

# a tool name, wherever a client prefixes it (mcp__graphlore__x, mcp_graphlore_x):
# its first word after `graphlore_` starts with a letter, never with `_`
TOOL_NAME = re.compile(r"graphlore_[a-z]+(?:_[a-z]+)*")
TOOL_EVENTS = ("tool_use", "tool_call", "mcp_tool_call", "function_call")
# the server's name a client puts before the tool's own (`mcp__graphlore__`,
# Gemini's `mcp_graphlore_`), taken off first
PREFIX = re.compile(r"^(?:mcp_+)?graphlore_+(?=graphlore_)")


@dataclass
class Run:
    """What one run gave: the answer, the graphlore tools called (in order),
    the tokens the client reported (None when it reports none)."""
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


def _events(stdout: str) -> list[Any]:
    """Every JSON value of a JSON-lines output (a single JSON document too)."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not out:
        try:
            out.append(json.loads(stdout))
        except json.JSONDecodeError:
            pass
    return out


def _walk(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)


def _tool_calls(events: list[Any]) -> list[str]:
    """graphlore tools called, read off the events that name a tool (a
    `tool_use` / `mcp_tool_call` / `tool_call` object's name or tool), with the
    client's prefix (`mcp__graphlore__`, `mcp_graphlore_`) taken off."""
    out: list[str] = []
    for ev in events:
        for d in _walk(ev):
            kind = str(d.get("type") or d.get("item_type") or "")
            if not any(k in kind for k in TOOL_EVENTS):
                continue
            for key in ("name", "tool", "tool_name"):
                value = PREFIX.sub("", str(d.get(key) or ""))
                m = TOOL_NAME.search(value)
                if m:
                    out.append(m.group(0))
                    break
    return out


def _usage(events: list[Any], last_only: bool) -> tuple[int | None, int | None]:
    seen = [d for ev in events for d in _walk(ev)
            if isinstance(d.get("input_tokens"), int) and isinstance(d.get("output_tokens"), int)]
    if not seen:
        return None, None
    if last_only:
        return seen[-1]["input_tokens"], seen[-1]["output_tokens"]
    return sum(d["input_tokens"] for d in seen), sum(d["output_tokens"] for d in seen)


def _last_text(events: list[Any], keys: tuple[str, ...]) -> str:
    found = ""
    for ev in events:
        for d in _walk(ev):
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    found = v
    return found


# --- clients -----------------------------------------------------------------

@dataclass
class Ctx:
    repo: Path
    work: Path            # a scratch directory of this run
    toolset: str
    command: str          # how the client starts graphlore


@dataclass
class Adapter:
    setup: str | None                      # `graphlore setup` name, or None
    argv: Callable[[str, Ctx], list[str]]
    parse: Callable[[str], Run]
    env: dict[str, str] = field(default_factory=dict)


def _claude_config(ctx: Ctx) -> str:
    cfg = clients.server_config(ctx.repo, ctx.toolset, ctx.command)
    path = ctx.work / "claude-mcp.json"
    path.write_text(json.dumps({"mcpServers": {"graphlore": cfg}}), encoding="utf-8")
    return str(path)


def _parse_claude(stdout: str) -> Run:
    ev = _events(stdout)
    results = [d for e in ev for d in _walk(e) if d.get("type") == "result"]
    inp, out = _usage(results or ev, last_only=True)
    answer = str(results[-1].get("result") or "") if results else _last_text(ev, ("text",))
    return Run(answer, _tool_calls(ev), inp, out)


def _codex_args(ctx: Ctx) -> list[str]:
    cfg = clients.server_config(ctx.repo, ctx.toolset, ctx.command)
    env = ", ".join(f"{k} = {json.dumps(v)}" for k, v in cfg["env"].items())
    return ["-c", f"mcp_servers.graphlore.command={json.dumps(cfg['command'])}",
            "-c", f"mcp_servers.graphlore.args={json.dumps(cfg['args'])}",
            "-c", f"mcp_servers.graphlore.env={{{env}}}"]


def _parse_codex(stdout: str) -> Run:
    ev = _events(stdout)
    turns = [d for e in ev for d in _walk(e) if d.get("type") == "turn.completed"]
    inp, out = _usage(turns or ev, last_only=False)
    messages = [d for e in ev for d in _walk(e) if d.get("type") == "agent_message"]
    answer = str(messages[-1].get("text") or "") if messages else _last_text(ev, ("text",))
    return Run(answer, _tool_calls(ev), inp, out)


def _parse_generic(stdout: str) -> Run:
    ev = _events(stdout)
    inp, out = _usage(ev, last_only=True)
    answer = _last_text(ev, ("result", "response", "text", "content"))
    return Run(answer or stdout.strip()[-2000:], _tool_calls(ev), inp, out)


ADAPTERS: dict[str, Adapter] = {
    "claude": Adapter(None, lambda p, c: [
        "claude", "-p", p, "--output-format", "stream-json", "--verbose",
        "--mcp-config", _claude_config(c), "--strict-mcp-config",
        "--allowedTools", "mcp__graphlore"], _parse_claude),
    "codex": Adapter(None, lambda p, c: [
        "codex", "exec", "--json", "--skip-git-repo-check", *_codex_args(c), p], _parse_codex),
    "gemini": Adapter("gemini", lambda p, c: [
        "gemini", "-p", p, "--output-format", "stream-json", "--yolo"], _parse_generic,
        env={"GEMINI_CLI_TRUST_WORKSPACE": "true"}),
    "kimi": Adapter("kimi", lambda p, c: [
        "kimi", "-p", p, "--output-format", "stream-json"], _parse_generic,
        env={"KIMI_CODE_TRUST_WORKSPACE": "1"}),
    "cursor": Adapter("cursor", lambda p, c: [
        "agent", "-p", p, "--output-format", "stream-json", "--trust", "--approve-mcps",
        "--force"], _parse_generic),
    "fake": Adapter(None, lambda p, c: [
        sys.executable, str(HERE / "fake_agent.py"), "--project", str(c.repo),
        "--transcript", str(HERE / "fake_transcript.json"), "--prompt", p], _parse_claude),
}

# the file each client reads project instructions from (`agents-md` variant)
GUIDE_FILE = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "gemini": "GEMINI.md",
              "kimi": "AGENTS.md", "cursor": "AGENTS.md", "fake": "AGENTS.md"}


def checkout(root: Path, name: str, spec: dict[str, str]) -> Path:
    """The task repository at its pinned commit, cloned once under ``root``."""
    path = root / name
    if not (path / ".git").is_dir():
        subprocess.run(["git", "clone", "-q", spec["url"], str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-q", spec["commit"]], check=True)
    return path


def build(repo: Path) -> None:
    if (repo / "graphify-out" / "graph.json").is_file():
        return
    env = {**os.environ, "GRAPHLORE_PROJECT_DIR": str(repo)}
    code = "from graphlore import server; print(server.graphlore_build('.', no_viz=True))"
    subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, check=True)


def _tools_of(toolset: str) -> list[str]:
    from graphlore import server
    names = server._registered_tool_names()
    keep = server.TOOLSETS.get(toolset)
    return sorted(names if keep is None else names & keep)


def run_all(args: argparse.Namespace) -> Path:
    spec = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    out = Path(args.out or (HERE / "results" / time.strftime("%Y%m%d-%H%M%S")))
    out.mkdir(parents=True, exist_ok=True)
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    repos: dict[str, Path] = {}
    for name, rs in spec["repos"].items():
        repos[name] = Path(rs["path"]).resolve() if "path" in rs else checkout(root, name, rs)
        build(repos[name])
    rows = out / "runs.jsonl"
    for client in args.clients.split(","):
        adapter = ADAPTERS[client]
        exe = adapter.argv("x", Ctx(root, out, "lean", ""))[0]
        if client != "fake" and shutil.which(exe) is None:
            print(f"{client}: its command is not installed, skipped", file=sys.stderr)
            continue
        for variant in args.variants.split(","):
            for task in spec["tasks"]:
                if args.only and task["id"] not in args.only.split(","):
                    continue
                repo = repos[task["repo"]]
                ctx = Ctx(repo, out, args.toolset, args.command)
                guide = repo / GUIDE_FILE[client]
                saved = guide.read_text(encoding="utf-8") if guide.is_file() else None
                written: Path | None = None
                try:
                    if adapter.setup:
                        c = clients.CLIENTS[adapter.setup]
                        written = clients.target(c, repo)
                        if written is not None:
                            clients.write(c, written, clients.server_config(
                                repo, args.toolset, args.command))
                    if variant == "agents-md":
                        guide.write_text(agents_md.merge(saved or "", agents_md.render(
                            _tools_of(args.toolset))), encoding="utf-8")
                    started = time.perf_counter()
                    proc = subprocess.run(
                        adapter.argv(task["prompt"], ctx), cwd=repo, capture_output=True,
                        text=True, timeout=args.timeout,
                        env={**os.environ, **adapter.env, "GRAPHLORE_PROJECT_DIR": str(repo)})
                    seconds = time.perf_counter() - started
                    stdout, code = proc.stdout, proc.returncode
                except subprocess.TimeoutExpired as e:
                    seconds, code = float(args.timeout), -1
                    stdout = e.stdout if isinstance(e.stdout, str) else ""
                finally:
                    if variant == "agents-md":
                        if saved is None:
                            guide.unlink(missing_ok=True)
                        else:
                            guide.write_text(saved, encoding="utf-8")
                    if written is not None and written.is_file():
                        bak = written.with_name(written.name + ".graphlore.bak")
                        if bak.is_file():
                            shutil.move(str(bak), str(written))
                        else:
                            written.unlink()
                (out / f"{client}-{variant}-{task['id']}.log").write_text(stdout, encoding="utf-8")
                got = adapter.parse(stdout)
                row = {"client": client, "variant": variant, "task": task["id"],
                       "answer": got.answer, "tools": got.tools,
                       "input_tokens": got.input_tokens, "output_tokens": got.output_tokens,
                       "seconds": round(seconds, 1), "exit": code, "toolset": args.toolset}
                with rows.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{client}/{variant}/{task['id']}: {len(got.tools)} tool call(s), "
                      f"exit {code}, {seconds:.0f}s")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default="/tmp/graphlore-agents",
                   help="where task repositories are cloned")
    p.add_argument("--clients", default="claude,codex,gemini,kimi")
    p.add_argument("--variants", default="plain,agents-md")
    p.add_argument("--toolset", default="lean", choices=("lean", "full", "locate"))
    p.add_argument("--command", default="graphlore", help="how a client starts graphlore")
    p.add_argument("--tasks", default=str(HERE / "tasks.json"))
    p.add_argument("--only", default="", help="comma-separated task ids")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--out", default="")
    args = p.parse_args(argv)
    out = run_all(args)
    print(f"runs: {out / 'runs.jsonl'}  (python benchmarks/agents/score.py {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
