# Copilot instructions for graphlore

This is a condensed copy of `AGENTS.md` at the repository root, which is the full and authoritative guidance. Read it when you can; if the two disagree, `AGENTS.md` wins.

graphlore is a Python MCP server (MCP Python SDK v2: `mcp.server.mcpserver.MCPServer`, not v1 `FastMCP`) that makes a Graphify-built `graphify-out/graph.json` navigable and joins it to real source spans. It has no extractor; the external `graphify` CLI builds the graph.

## Checks

- `ruff check .`, `mypy`, `pytest -q` (CI runs these on Python 3.10–3.12; keep code 3.10-compatible).
- Do not run `ruff format .` across the repo: most files are not ruff-formatted and CI does not check formatting. Match the surrounding style.

## Layout

`src/graphlore/` is layered `config.py` → `graph.py` → `spans.py` → `scope.py` → `apis.py` / `routes.py` → `imports.py` → `resolve.py` → `review.py` → `server.py`. `server.py` holds the whole MCP surface (33 tools, resources, prompts, `main()`). All tests are in `tests/test_tools.py` and call tool functions directly.

## Rules

- Read `config.PROJECT_DIR` by attribute, never `from .config import PROJECT_DIR`; tests repoint it.
- Read settings through `config.env("NAME")` so the legacy `GRAPHIFY_*` fallback applies.
- A tool is a sync function returning `str`, registered with `@_tool(annotations=...)` (not `@mcp.tool`). `_load_graph()` returns the graph dict or an error string; return errors with `_err(msg, as_json)` and results with `_fmt(payload, as_json, text)`. With `as_json=True` every path, errors included, must return parseable JSON. Over MCP, the `_fmt`/`_err` payload becomes `structuredContent` and `_err` sets `isError`; an empty but valid answer uses `_fmt`.
- `graph.json` shape varies: use `_node_id`, `_node_label`, `_node_file`, `_node_line`, `_edge_ends`, `_edge_rel`, `_nodes_edges`. Read other fields with `.get()` and a fallback; community is `n.get("community", n.get("cluster"))`.
- Paths from the graph, a semantic chunk or a file/node argument are resolved through `spans._resolve_in_project` before any read. `graphlore_build`'s `path` is deliberately outside this (opt-in `RESTRICT_PATHS`).
- `_run_cli` passes an argv list with no shell. Never build a shell string.
- A path that can reach the network checks `config.offline()` and is listed in the README's network table; `_run_cli` uses `_cli_env()`.
- Text replies that quote repository content wrap it in `_frame_repo_text(...)` and set `content_trust` / `untrusted_fields` in the payload.
- Optional dependencies (`semble`, `tree-sitter`, `tiktoken`, `watchdog`, `uvicorn`) are imported inside functions and degrade to a fallback or install hint.
- graph.json analysis tools must work with no `graphify` binary and no optional extras.
- Set `read_only_hint` / `destructive_hint` on every tool to match what it does.
- A tool that ranks or audits the graph takes `scope: str = ""` and goes through `_scope_view`; it reports `view.payload()` and wraps its text in `_with_note`, so what the scope left out (vendored/minified files, external nodes) is always stated.
- A tool that lifts import edges to file level passes `ImportChecker().kind` (one instance per call) and reports `checker.rejected`; graphify resolves imports by name and gets same-named modules wrong.
- A new source reader (`imports.py`, `resolve.py`) covers every language the project already parses — Python via `ast`, the rest via tree-sitter, as in `apis.py` — or says per language why there is nothing to read. Don't add a Python-only analysis.
- A row that prints its own `file:line` uses `labels.name(id)`, not `labels.get(id)`, so the location is shown once.
- Every tool taking a node name resolves it with `_pick_node`, reports `_matched(key, picked)` and answers a miss with `_not_found` (adds `did_you_mean`); never `_resolve_node` directly.
- A budget or limit that drops rows says so: `truncated` plus a count or the names left out, and a `hint`.
- Node-centric tools (impact, subgraph, neighbors, tests_for) use `_pick_node` + `_matched` for the node, `_nav_view` for edges and scope (it includes the calls `resolve.py` adds from the source), order production code before tests, and attach `_EMPTY_NOTE` to an empty result.
- `config.PROJECT_DIR` can change at runtime (`graphlore_project`); never cache a value derived from it without keying on it.

## When a tool is added or changed

Also update: the tool list in the `server.py` docstring, `LEAN_TOOLS` / `LOCATE_TOOLS` if relevant (`lean` is the default surface), `_instructions` / the prompt steps if the tool belongs in the recommended flow, `test_tool_and_prompt_registration` (asserts exactly 33 tools), the README tool tables and "33" counts, `.claude/skills/graphlore-explore/SKILL.md`, and `CHANGELOG.md` under `[Unreleased]`.
