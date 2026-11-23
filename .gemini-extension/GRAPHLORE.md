<!-- graphlore:begin -->
## graphlore: this repository's code graph (MCP)

The graphlore MCP server holds a graph of this repository: its definitions with exact `file:line` spans, and the calls, imports, references and bases between them. Use it for structure: who calls this, what breaks if it changes, where a behaviour lives, how two parts connect. Use `grep` / `rg` for an exact string or identifier: it is faster there.

- Start with `graphlore_overview`: the graph's size, its most central nodes and its communities.
- To find code by what it does, `graphlore_locate("<question>")`: the best hit, its graph neighbourhood and similar code elsewhere.
- Before changing something, `graphlore_impact("<name>")`: what depends on it, by hop, and the chokepoints it funnels through.
- Around one node: `graphlore_neighbors` (one hop) or `graphlore_subgraph` (a token-budgeted neighbourhood).
- A node's file, line, kind and docstring: `graphlore_node_details`.
- Read the code of the nodes you settled on with `graphlore_fetch([...])`: their exact lines, under one budget.
- Tests that exercise a symbol: `graphlore_tests_for("<name>")`.
- How A reaches B: `graphlore_path("A", "B")`, the most certain path.
- Reviewing structure: `graphlore_cycles` (import cycles, runtime first), `graphlore_layers` (layer rules), `graphlore_unreferenced` (dead-code candidates: confirm each before deleting).
- Where the code hangs by one link: `graphlore_bridges`; what everything leans on: `graphlore_god_nodes(rank="pagerank")`.
- After editing code, `graphlore_freshness` says whether the graph is stale; `graphlore_build(update=True)` brings it up to date.

Reading an answer:

- An edge marked `INFERRED` was matched by name, not resolved: confirm it in the source before relying on it.
- An empty answer means no edge was indexed, not that the code has none: check with `rg`.
- A name several nodes share resolves to one, and the reply says which (`matched`); pass the node's id to pick another. A miss answers `did_you_mean`.
- Answers carry `file:line`. Read those lines before editing them.
- Analysis tools leave vendored files and external packages out by default and say so; `scope="-tests"` leaves test code out.
<!-- graphlore:end -->
