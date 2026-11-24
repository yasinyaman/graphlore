"""A stand-in coding agent for testing run.py and score.py without any model:
it starts graphlore over stdio, makes the tool calls its transcript lists for
the prompt it is given, and prints Claude Code's stream-json shape (a
`tool_use` per call, then a `result` with the transcript's answer and the
characters the tools returned, /4, as input tokens). With `--no-mcp` (the
baseline) it has no graphlore: one `Grep` call and the transcript's
`baseline_answer`, or "I do not know.".

    python fake_agent.py --project REPO --transcript fake_transcript.json --prompt "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def _run(project: str, calls: list[list]) -> int:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "graphlore"],
        env={**os.environ, "GRAPHLORE_PROJECT_DIR": project, "GRAPHLORE_TOOLSET": "full"})
    seen = 0
    async with Client(params) as client:
        for name, arguments in calls:
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": f"mcp__graphlore__{name}", "input": arguments}]}}))
            result = await client.call_tool(name, arguments)
            seen += sum(len(getattr(c, "text", "") or "") for c in result.content)
    return seen


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--no-mcp", action="store_true")
    a = p.parse_args()
    script = json.load(open(a.transcript, encoding="utf-8"))
    step = next((s for s in script if s["match"] in a.prompt), None)
    if a.no_mcp:
        answer = (step or {}).get("baseline_answer", "I do not know.")
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Grep", "input": {"pattern": a.prompt[:20]}}]}}))
        print(json.dumps({"type": "result", "result": answer,
                          "usage": {"input_tokens": 5000, "output_tokens": len(answer) // 4}}))
        return 0
    if step is None:
        print(json.dumps({"type": "result", "result": "I do not know.",
                          "usage": {"input_tokens": 0, "output_tokens": 4}}))
        return 0
    seen = asyncio.run(_run(a.project, step["calls"]))
    print(json.dumps({"type": "result", "result": step["answer"],
                      "usage": {"input_tokens": seen // 4,
                                "output_tokens": len(step["answer"]) // 4}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
