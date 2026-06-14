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

### Added (transport & hardening)
- Optional HTTP transport: `GRAPHIFY_TRANSPORT=streamable-http|sse` serves over
  `GRAPHIFY_HOST:GRAPHIFY_PORT` (stdio stays the default).
- Opt-in build-path containment via `GRAPHIFY_RESTRICT_PATHS`; **auto-enabled**
  whenever an HTTP transport is selected, so a network client can't drive
  `graphify_build` to extract arbitrary paths.
- `graphify_overview` now reports `id_collisions` and warns when distinct nodes
  collapse to one id (degrees/neighbors would otherwise be silently understated).
- `graphify_validate` — read-only graph linter: dangling edges (endpoint not in
  the node set), duplicate edges, self-loops, and orphan (degree-0) nodes.
- `graphify_freshness` now returns a `recommended_action` (fresh / update /
  rebuild) with a `reason`: deletions, renames, or a large change set steer to a
  full rebuild, since incremental `update` can't drop nodes for removed code.
- Fixed a latent bug in `graphify_freshness`'s changed-file list: `_git` stripped
  the leading status column, mangling the first file name for unstaged
  modifications/deletions (` M`/` D`). `_git` now `rstrip`s only.
- `graphify_locate` (optional `[semble]` extra) — joins
  [semble](https://github.com/MinishLab/semble) semantic search to the graph:
  NL query → enclosing node → token-budgeted subgraph, plus `hidden_links`
  (semantically similar but structurally disconnected code, with hop distance).
  Refactored the subgraph BFS into a shared `_bfs_subgraph` helper. The
  chunk→node join prefers `file_type == "code"` symbols over docstring
  (`rationale`) / `document` nodes and uses the chunk's full line range, so a
  seed resolves to the enclosing function/class, not a docstring node.
- `graphify_set_labels` — assistant-driven community naming: the calling
  assistant pushes `{id: name}` (no key, no sampling — works in clients like
  Claude Code that lack sampling), persisted to `.graphify_labels.json` and
  patched into `graph.html`. Surfaced as the fallback in `graphify_label_communities`
  and `graphify_sampling_status` when sampling/keys are unavailable.

### Added (canonical span join)
- `graphify_locate`'s chunk→node join is now span-based, not single-point. Graph
  nodes carry only one `source_location` line (no end-line), so the old
  "greatest line ≤ chunk-start" heuristic could attribute a chunk to a function
  that had already ended, or fall back to the whole-file node. `_node_for_location`
  now resolves a semble chunk to the def/class whose **real line range** encloses
  it — via a decorator-aware AST span pass (stdlib `ast`, Python files; zero new
  deps) — then maps that symbol to its graph node, walking outward to the nearest
  enclosing symbol that has a node. The point heuristic remains the fallback for
  non-Python files or when no source is on disk. Measured on httpx: true
  containment rose from ~86/108 to 101/108 sampled chunks; the rest are
  module-level-start chunks resolved to the first symbol they introduce.
- `graphify_locate` seeds now include a span-recovered `qualname` (FQN, e.g.
  `AsyncClient._send_single_request`), disambiguating same-named symbols.
- The AST span pass is confined to `PROJECT_DIR` (the only code path that reads a
  source file from a chunk-supplied path) and cached per file by mtime.

### Added (multi-language span/structure backend)
- The span/structure extraction behind `graphify_locate` and `graphify_freshness`
  is no longer Python-only. Python keeps the stdlib `ast` fast path (zero deps,
  decorator-aware); every other language is handled by an optional **tree-sitter**
  backend (`[treesitter]` extra — also ships with graphify) with automatic
  language detection from the file path. So the chunk→symbol span join and the
  cosmetic-vs-structural freshness check now work for JS/TS, Go, Rust, Java, Ruby,
  C/C++, and the ~165 other languages the grammar pack covers.
- Symbol detection is generic (a named def/class/method/struct/… node), so no
  per-language table is maintained; qualnames chain enclosing symbols
  (`Service.fetch`). The tree-sitter parser is built from the stable core
  `Parser(Language)` API (not the pack's churning `get_parser` wrapper).
- Cosmetic detection for non-Python compares a comment-stripped tree-sitter
  skeleton over **all** tokens — operators and keywords included — so any semantic
  edit (an operator flip `+`→`-`, `==`→`!=`, a `sync`→`async` or `let`→`const`
  change, a rename or value change) is structural, while only comment/whitespace
  edits compare equal. When the backend or a language is unavailable, both
  features degrade to the prior behaviour (point heuristic / treat-as-structural)
  — never an error.
- tree-sitter spans now absorb a symbol's leading **doc-comment / decorator /
  annotation** lines into `region_start` (mirroring the Python decorator path), so
  a chunk that starts on the doc comment above a Go/Java/JS method resolves to that
  method. Measured on real repos this lifted Go span-join precision from 48%→80%.
- **Multi-language validation benchmark** (`benchmarks/multilang.py` + the new
  "Across languages" section in `docs/benchmark*.html`): on real HTTP-client repos
  (`got` JS/TS, `resty` Go, `retrofit` Java) span-join precision holds at 80–85%
  (vs 91% for Python/httpx), locate stays 200–757× cheaper than grep+read, and the
  cosmetic-vs-structural freshness check is correct in every language.

### Added (Phase 3 hardening)
- Optional **bearer auth** for the HTTP transports: set `GRAPHIFY_API_KEY` and
  every HTTP/WebSocket request must carry `Authorization: Bearer <key>`
  (constant-time compared; 401 otherwise). Unset = prior behaviour (rely on
  loopback binding / a fronting proxy); a stderr warning now fires if an HTTP
  transport binds a non-loopback host without a key.
- `graphify_freshness` now separates **cosmetic from structural** changes: each
  changed `.py` file is AST-diffed against its HEAD version (`ast.dump` equality),
  so a comment/whitespace/formatting-only edit no longer pushes the graph toward
  `update`/`rebuild`. Docstring edits still count as structural. The payload gains
  `structural_changes` / `cosmetic_changes`; the AST diff is skipped for change
  sets > 25 files (which already route to a full rebuild).
- Optional **lean tool surface**: `GRAPHIFY_TOOLSET=lean` exposes a coherent,
  mostly dependency-free core that still supports the whole flow — build, orient
  (`graphify_overview`), find (`graphify_search`), traverse (`graphify_subgraph`,
  `graphify_neighbors`), jump to source (`graphify_node_details`), plus
  `graphify_communities` and `graphify_freshness`. `graphify_locate` is included
  only when the `[semble]` extra is installed (otherwise it would just error), and
  `graphify_overview` filters its suggested next steps to the active surface so it
  never points at a trimmed tool. Default `full` is unchanged.

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
