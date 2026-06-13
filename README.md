# graphify-mcp

[![CI](https://github.com/yasinyaman/graphify-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/yasinyaman/graphify-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

A Python MCP server that exposes the [Graphify](https://graphify.net) knowledge graph as MCP tools, prompts and resources — so an AI assistant can explore your codebase through the graph during development, cheaply (token-budgeted) and structurally.

> Note: Graphify ships its own embedded MCP server (`graphify ./raw --mcp`). This project adds analysis tools, token-budgeted subgraph extraction, git freshness checks, per-community resources, reusable prompts, and LLM-friendly tool annotations + structured (JSON) output on top.

## Installation

```bash
# graphify-mcp itself
pip install graphify-mcp

# plus the Graphify CLI it wraps (needed for build/query/path/explain/add)
pip install graphifyy && graphify install
```

From source:

```bash
git clone https://github.com/yasinyaman/graphify-mcp
cd graphify-mcp
pip install -e ".[dev]"
```

## Running

```bash
GRAPHIFY_PROJECT_DIR=/path/to/repo graphify-mcp
```

### Claude Code

Copy `mcp.json` to a `.mcp.json` at your project root. `GRAPHIFY_PROJECT_DIR: "."` uses the project root.

### Claude Desktop / Cowork

Add the contents of `claude_desktop_config.json` to your Claude Desktop config:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GRAPHIFY_PROJECT_DIR` | `.` | Project root to extract the graph from |
| `GRAPHIFY_OUT_DIR` | `graphify-out` | Output folder name |
| `GRAPHIFY_BIN` | `graphify` | CLI path |
| `GRAPHIFY_TIMEOUT` | `600` | CLI timeout (seconds) |

## Tools

CLI-backed (the first two write state; the rest are read-only):

| Tool | Purpose |
|---|---|
| `graphify_build` | Build/update the graph (`--update`, `--cluster-only`, `--mode deep`) |
| `graphify_add` | Add a source by URL (arXiv, tweet) |
| `graphify_query` | Natural-language query (`--dfs`, `--budget`) |
| `graphify_path` | Exact path between two nodes |
| `graphify_explain` | Everything about a node |

graph.json analysis (read-only, no CLI needed, `as_json=True` for structured output):

| Tool | Purpose |
|---|---|
| `graphify_overview` | **Call first** — size, god nodes, communities, surprises, suggested next steps |
| `graphify_god_nodes` | Most connected nodes |
| `graphify_communities` | Leiden community summaries |
| `graphify_surprises` | Unexpected cross-domain connections |
| `graphify_search` | Node search |
| `graphify_neighbors` | 1-hop neighbors of a node |
| `graphify_subgraph` | **Token-budgeted** BFS subgraph around a node — the cheap way to feed the model just the relevant slice |
| `graphify_node_details` | Node metadata: type, source file/line, docstring, community |
| `graphify_freshness` | Is the graph stale vs. the current git HEAD? Recommends `update` |

## Resources

- `graphify://report` — GRAPH_REPORT.md
- `graphify://graph` — graph.json (raw)
- `graphify://community/{id}` — per-community wiki (members + internal/boundary edges)

## Prompts

Reusable templates that orchestrate the tools for the assistant:

- `onboard` — orient to the codebase (overview → communities → subgraphs → surprises → summary)
- `trace_bug(symptom)` — find likely root-cause locations through the graph
- `explain_flow(flow)` — end-to-end walkthrough of a named flow with file:line refs

## LLM-friendliness

- **Tool annotations** (`readOnlyHint`, `destructiveHint`, titles) tell the model which tools are safe to call freely vs. which mutate state.
- **Server instructions** describe the recommended flow (overview → targeted subgraph/query → build update).
- **`as_json` output** on every analysis tool returns structured data the model can chain on instead of re-parsing prose.
- **Token budgeting** (`graphify_subgraph`) keeps context small on large graphs — the core of Graphify's ~71× compression.

## Typical workflow

1. `graphify_overview()` — orientation
2. `graphify_communities()` — subsystems
3. `graphify_subgraph("SomeNode")` — token-cheap targeted exploration
4. `graphify_query("how does the auth flow work?")` — questions
5. After code changes: `graphify_freshness()` → `graphify_build(".", update=True)`

## Project layout

```
graphify-mcp/
├── src/graphify_mcp/      # package (server.py, __init__.py)
├── tests/                 # pytest suite + fixture graph.json
├── .github/workflows/     # CI (ruff + pytest, py 3.10–3.12)
├── pyproject.toml         # packaging + console script
├── mcp.json               # Claude Code example config
└── claude_desktop_config.json
```

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [MIT](LICENSE).
