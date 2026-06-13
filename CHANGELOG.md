# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `graphify-mcp-server` console script and `python -m graphify_mcp` entry point,
  both collision-free with the `graphify-mcp` script that `graphifyy` also ships.
- `graphify_label_communities` — names Leiden communities via **host-LLM MCP
  sampling** (no server API key), a backend key (`method="cli"`), or placeholders.
- `graphify_sampling_status` — capability test reporting whether the client
  supports sampling, whether a backend key is set, and the preferred method.

### Fixed
- `graphify_node_details` now reads the source line from graphify's real
  `source_location` field (e.g. `"L295"`), not just `line`/`lineno`/`start_line`,
  so `file:line` references resolve against actual graph output.
- Server now reports its own version over MCP instead of the `mcp` library's.
- `graphify_subgraph` no longer re-serializes the whole edge list on every edge
  during the budget check — a running counter replaces the O(n²) `json.dumps`.
- `graphify_freshness` now detects newly-added **untracked** files (via
  `git status --porcelain`), compares against graphify's `built_at_commit`
  (robust across checkouts where mtime resets), and ignores its own
  `graphify-out/` output.
- `graphify_overview` and `graphify_surprises` now share one surprise-edge
  definition (`_is_surprise_edge`); an INFERRED *confidence* is no longer
  miscounted as a surprise, and the two tools agree.

### Changed
- `_load_graph` caches the parsed graph by path + mtime, so a multi-MB
  `graph.json` isn't re-parsed on every tool call.
- Community-naming sampling `max_tokens` raised 16 → 24 to avoid clipped names.

## [0.1.0] - 2026-06-13

### Added
- Initial release.
- CLI-backed tools: `graphify_build`, `graphify_query`, `graphify_path`,
  `graphify_explain`, `graphify_add`.
- graph.json analysis tools (no CLI required): `graphify_overview`,
  `graphify_god_nodes`, `graphify_communities`, `graphify_surprises`,
  `graphify_search`, `graphify_neighbors`, `graphify_subgraph`,
  `graphify_node_details`, `graphify_freshness`.
- Resources: `graphify://report`, `graphify://graph`, `graphify://community/{id}`.
- Prompts: `onboard`, `trace_bug`, `explain_flow`.
- LLM-friendliness: tool annotations, server instructions, `as_json` structured
  output, and token-budgeted subgraph extraction.
- Packaging (`graphify-mcp` console script), pytest suite, ruff config and CI.
