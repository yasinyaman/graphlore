---
name: graphify-explore
description: Explore, understand, or debug a codebase through its Graphify knowledge graph using the graphify MCP tools. Use when onboarding to an unfamiliar repository, tracing how a named flow or feature works end-to-end, finding likely root-cause locations for a bug, or answering "where/how is X implemented" — especially when a graphify-out/graph.json exists. Favors token-budgeted subgraph extraction over opening many files.
---

# Exploring a codebase with Graphify

The `graphify` MCP server exposes a codebase knowledge graph as tools. Reach for
it **before** grepping or reading many files: it answers structural questions
("what connects to X", "which subsystems exist", "where does this flow run")
cheaply, returning just the relevant slice instead of whole files.

If the graphify tools are not available, the MCP server isn't connected — see the
project README for the `.mcp.json` / Claude Desktop config.

## Preconditions: make sure the graph exists and is fresh

1. `graphify_freshness()` — is `graph.json` stale vs. the current git HEAD?
2. If missing or stale: `graphify_build(".", update=True)` (AST-only update needs
   no API key; `mode="deep"` adds semantic edges but needs a backend key).

## Canonical flow (cheap → targeted)

1. **`graphify_overview()`** — ALWAYS first. Size, god nodes, community count,
   surprise edges, and suggested next steps.
2. **`graphify_communities()`** — the major subsystems. Read these like a table
   of contents.
3. **`graphify_subgraph("<node>", hops=2, budget_tokens=1500)`** — the workhorse.
   A token-budgeted BFS slice around a node; this is the cheap way to feed the
   model just the relevant structure. Start from a god node or a community member.
4. **`graphify_query("<natural-language question>", budget=1500)`** — ask the
   graph directly (BFS/DFS traversal, no LLM key needed).
5. **`graphify_node_details("<node>")`** — resolve a node to its `file:line`,
   type, community, and docstring when you need to jump to source.

Supporting tools: `graphify_search` (find nodes by name), `graphify_neighbors`
(1-hop), `graphify_god_nodes` (most connected), `graphify_surprises`
(unexpected cross-domain couplings), `graphify_path(a, b)` (exact route between
two nodes), `graphify_explain(node)` (everything about one node).

## Token discipline (the whole point)

On large graphs, never dump the full graph. Use `graphify_subgraph` with a
`budget_tokens` cap and widen `hops` only as needed — the result reports
`truncated` and `approx_tokens` so you can tell when you hit the budget. Pass
`as_json=True` on any analysis tool when you want to chain on structured output
instead of re-parsing prose.

## Task recipes

- **Onboarding** → overview → communities → `graphify_subgraph` on the top 2-3
  god nodes → `graphify_surprises` for hidden coupling → write a short
  architecture summary. (The `onboard` MCP prompt scripts this.)
- **Tracing a bug** → `graphify_search` the symptom → `graphify_subgraph` around
  the best match → `graphify_path` between suspects → check `graphify_surprises`.
  (The `trace_bug` MCP prompt scripts this.)
- **Explaining a flow** → `graphify_query` the flow for entry points →
  `graphify_subgraph(hops=2)` around them → `graphify_node_details` for
  `file:line`. (The `explain_flow` MCP prompt scripts this.)

## Naming communities (optional, for readable subsystems)

Leiden clusters are numbered (`Community 7`) until a model names them.
`graphify_sampling_status()` reports the options; `graphify_label_communities()`
then names them via **host-LLM sampling** (no server API key), a backend key
(`method="cli"`), or placeholders — `method="auto"` picks the best available.

## After code changes

Re-sync before trusting the graph again: `graphify_freshness()` →
`graphify_build(".", update=True)`.
