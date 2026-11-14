# Copilot instructions for graphlore

This is a condensed copy of `AGENTS.md` at the repository root, which is the full and authoritative guidance. Read it when you can; if the two disagree, `AGENTS.md` wins.

graphlore is a Python MCP server (MCP Python SDK v2: `mcp.server.mcpserver.MCPServer`, not v1 `FastMCP`) that makes a Graphify-built `graphify-out/graph.json` navigable and joins it to real source spans. It has no extractor; the external `graphify` CLI builds the graph.

## Checks

- `ruff check .`, `mypy`, `pytest -q` (CI runs these on Python 3.10–3.12; keep code 3.10-compatible).
- Do not run `ruff format .` across the repo: most files are not ruff-formatted and CI does not check formatting. Match the surrounding style.

## Layout

`src/graphlore/` is layered `config.py` → `graph.py` → `spans.py` → `apis.py` / `routes.py` → `server.py`. `server.py` holds the whole MCP surface (28 tools, resources, prompts, `main()`). All tests are in `tests/test_tools.py` and call tool functions directly.

## Rules

- Read `config.PROJECT_DIR` by attribute, never `from .config import PROJECT_DIR`; tests repoint it.
- Read settings through `config.env("NAME")` so the legacy `GRAPHIFY_*` fallback applies.
- A tool is a sync function returning `str`. `_load_graph()` returns the graph dict or an error string; return errors with `_err(msg, as_json)` and results with `_fmt(payload, as_json, text)`. With `as_json=True` every path, errors included, must return parseable JSON.
- `graph.json` shape varies: use `_node_id`, `_node_label`, `_node_file`, `_node_line`, `_edge_ends`, `_edge_rel`, `_nodes_edges`. Read other fields with `.get()` and a fallback; community is `n.get("community", n.get("cluster"))`.
- Paths from the graph, a semantic chunk or a file/node argument are resolved through `spans._resolve_in_project` before any read. `graphlore_build`'s `path` is deliberately outside this (opt-in `RESTRICT_PATHS`).
- `_run_cli` passes an argv list with no shell. Never build a shell string.
- Optional dependencies (`semble`, `tree-sitter`, `tiktoken`, `watchdog`, `uvicorn`) are imported inside functions and degrade to a fallback or install hint.
- graph.json analysis tools must work with no `graphify` binary and no optional extras.
- Set `read_only_hint` / `destructive_hint` on every tool to match what it does.

## When a tool is added or changed

Also update: the tool list in the `server.py` docstring, `LEAN_TOOLS` / `LOCATE_TOOLS` if relevant, `test_tool_and_prompt_registration` (asserts exactly 28 tools), the README tool tables and "28" counts, `.claude/skills/graphlore-explore/SKILL.md`, and `CHANGELOG.md` under `[Unreleased]`.
