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
GRAPHIFY_PROJECT_DIR=/path/to/repo graphify-mcp-server
# equivalently, collision-proof:
GRAPHIFY_PROJECT_DIR=/path/to/repo python -m graphify_mcp
```

> **Heads-up:** `graphifyy` ships its own `graphify-mcp` console script (its
> embedded server), so if both packages are installed the bare `graphify-mcp`
> command resolves to whichever was installed last. Use `graphify-mcp-server`
> or `python -m graphify_mcp` to always launch *this* server.

### Claude Code

Copy `mcp.json` to a `.mcp.json` at your project root. `GRAPHIFY_PROJECT_DIR: "."` uses the project root.

### Claude Desktop / Cowork

Add the contents of `claude_desktop_config.json` to your Claude Desktop config:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Transport (stdio default, optional HTTP)

stdio is the default and the right choice for a per-developer local server. To
serve over HTTP instead (e.g. a shared graph for a team or a web MCP client):

```bash
GRAPHIFY_TRANSPORT=streamable-http GRAPHIFY_HOST=127.0.0.1 GRAPHIFY_PORT=8000 \
  GRAPHIFY_PROJECT_DIR=/path/to/repo graphify-mcp-server
```

Any HTTP transport **force-enables path containment** (`GRAPHIFY_RESTRICT_PATHS`)
so a network client can't drive `graphify_build` to extract arbitrary filesystem
paths. HTTP binds `127.0.0.1` by default; if you expose it beyond localhost
(`GRAPHIFY_HOST=0.0.0.0`), put it behind your own auth / reverse proxy.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GRAPHIFY_PROJECT_DIR` | `.` | Project root to extract the graph from |
| `GRAPHIFY_OUT_DIR` | `graphify-out` | Output folder name |
| `GRAPHIFY_BIN` | `graphify` | CLI path |
| `GRAPHIFY_TIMEOUT` | `600` | CLI timeout (seconds) |
| `GRAPHIFY_RESTRICT_PATHS` | `0` | Confine `graphify_build`'s `path` to the project dir (auto-on for HTTP) |
| `GRAPHIFY_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `GRAPHIFY_HOST` | `127.0.0.1` | Bind host for HTTP transports |
| `GRAPHIFY_PORT` | `8000` | Bind port for HTTP transports |

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
| `graphify_freshness` | Is the graph stale vs. git HEAD? Returns `recommended_action` (fresh/update/rebuild) + `reason` — deletions/large changes steer to a full rebuild |
| `graphify_validate` | Lint the graph for dangling/duplicate/self-loop edges and orphan nodes (read-only) |

Semantic naming (uses the **host model via MCP sampling** — no API key — or a backend key):

| Tool | Purpose |
|---|---|
| `graphify_sampling_status` | Capability test: reports whether the client supports host-LLM sampling, whether a backend key is set, and which method will be used |
| `graphify_label_communities` | Give Leiden communities human-readable names. `method="auto"` (sampling → key → placeholder), `"sampling"`, `"cli"`, or `"placeholder"` |

## Naming communities without an API key (MCP sampling)

The Leiden clustering is keyless, but turning `Community 7` into `Authentication`
needs a model. Three ways, in `graphify_label_communities`'s preference order:

1. **Host-LLM sampling** — the server asks the *connected client* to run the
   completion via MCP `sampling/createMessage`. The model the user already uses
   (e.g. Claude in a sampling-capable client) does the naming; **the server holds
   no API key**. Subject to client support — call `graphify_sampling_status`
   first; it degrades gracefully when unsupported.
2. **Backend API key** (`method="cli"`) — set `GEMINI_API_KEY` / `OPENAI_API_KEY`
   / `ANTHROPIC_API_KEY` / … (or run a local **ollama**) and graphify's own
   backend names them. This option always remains available.
3. **Placeholders** — no model anywhere: names stay `Community N`.

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
- **Host-LLM sampling** (`graphify_label_communities`) lets the server borrow the client's model via MCP `sampling/createMessage`, so semantic naming works with no server-side API key — with a capability test (`graphify_sampling_status`) and a backend-key fallback.

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
