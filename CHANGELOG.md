# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `graphlore_package_apis` (JS/TS): clearly-internal import sources are no
  longer counted as npm packages — absolute paths (`/lib/x`, whose "package"
  was the empty string), package.json `#` subpath imports, and the common
  bundler aliases `~/x` and `@/x`. A scoped `@app/utils` is still counted:
  without reading tsconfig it is indistinguishable from a real scoped npm
  package (documented over-approximation).
- `benchmarks/multilang.py` no longer aborts the whole run (discarding every
  measured repo) when a REPOS entry's graph is missing — the row is skipped
  with a stderr note, since the documented setup only builds a subset. The
  freshness table also stops claiming "expect False" for the token-change
  fallback edit, which is a comment in the C-family (expectation unreliable)
  — the row is flagged instead.
- The CLI-backed tools (`graphlore_query`/`path`/`explain`) and `_run_cli`'s
  plumbing (arg wiring incl. `--graph`, missing-binary message, timeout,
  exit-code formatting, exec-OSError) are now covered by tests — previously
  three registered tools had zero coverage.
- Rename leftovers in the docs artifacts: `docs/benchmark.html`,
  `docs/benchmark.tr.html` and `docs/benchmark.svg` still titled/labelled the
  project `graphify-mcp`; they now say `codegraph-mcp`.

### Changed
- **Internals: one confinement boundary, indexed lookups.** The PROJECT_DIR
  confinement check that five per-file analyzers hand-copied is now a single
  `spans._resolve_in_project()`, and the span/API/route per-file caches share
  one `_cached_file_analysis()` pipeline (same mtime-keyed, bounded-FIFO
  semantics). `_resolve_node`'s exact id/label pass and `_node_for_location`'s
  same-file lookup are served from indexes cached on the nodes-list identity
  (the `_ADJ_CACHE` scheme) instead of rescanning all N nodes per call — the
  latter ran once per semantic hit / route row / duplication pair.
- **Ambiguous node labels are qualified at render time.** graphify labels
  methods bare (`.auth_flow()`), so distinct nodes across classes/files used to
  render identically in subgraph arrows and node lists. Wherever a label is
  shared by more than one node, tools now show the span-recovered FQN
  (`DigestAuth.auth_flow()`), falling back to `label (file:Lline)` when no span
  is available; unique labels are untouched. Lazy + memoized (only ambiguous
  ids that actually render pay the parse cost) and cached per loaded graph —
  this also collapses the eleven per-tool `{id: label}` rebuilds into one
  shared `_display_labels()` helper.
- **Tool surface renamed: `graphify_*` → `graphlore_*`** (and the resources
  `graphify://…` → `graphlore://…`), completing the package rename below so
  the commands an assistant sees carry the product's own name. All 28 tools
  are affected (`graphlore_locate`, `graphlore_build`, …); the bundled
  explore skill moved to `graphlore-explore` accordingly. `GRAPHIFY_*`
  environment variables and the `graphify-out/` output directory are
  intentionally unchanged — they configure/name artifacts of the wrapped
  Graphify CLI (`graphifyy`), not this package.
- **Project renamed: `graphify-mcp` → `graphlore`** (before any PyPI release,
  so no published users are affected). The Python module is now `graphlore`,
  the MCP server announces itself as `graphlore`, and the console script is
  the bare `graphlore` — the collision with the `graphify-mcp` script that
  `graphifyy` ships (which forced the old `graphify-mcp-server` entry point)
  is gone. The repository moved to `github.com/yasinyaman/graphlore`; old
  GitHub URLs redirect. (An intermediate rename to `codegraph-mcp` lived for
  a day and was dropped: "codegraph" is already crowded in the MCP niche —
  codegraph-ai/CodeGraph, two GitHub repos literally named codegraph-mcp,
  plus codegraph and codegraph-mcp-server on PyPI.)
- **Upgraded to MCP Python SDK v2** (`mcp>=2.1,<3.0`, was `>=1.26,<2.0`). The
  server now builds on `mcp.server.mcpserver.MCPServer` (v1's `FastMCP`); tool
  and prompt surfaces are unchanged. Notable internals:
  - Server version is passed as `MCPServer(..., version=...)` instead of the old
    private `_mcp_server.version` override.
  - `ToolAnnotations` hints use the v2 snake_case field names
    (`read_only_hint`, `destructive_hint`).
  - HTTP transports take host/port per call (`mcp.run(transport=..., host=...,
    port=...)`, `streamable_http_app(host=...)`) — v2 removed
    `settings.host/port`. The `host=` passed to the app factories also feeds the
    SDK's DNS-rebinding protection, which auto-enables on loopback hosts.
  - Host-LLM sampling (`graphlore_label_communities`, `graphlore_sampling_status`)
    now names all communities in **one batched** `sampling/createMessage`
    request (a JSON name map) instead of one request per community, and picks
    its transport per protocol: on 2026-07-28+ (no in-call back-channel) a v2
    `Resolve`/`Sample` resolver carries the request as input-required rounds;
    on older protocols the tool body samples directly over the back-channel,
    preserving the pre-v2 graceful degradation — a failing host model yields
    placeholder names plus a note instead of erroring the call. (On the
    resolver path that degradation is impossible: `Sample` has no error arm,
    so a host-model failure surfaces as a tool error there.) Unusable or
    partial replies degrade to placeholder names with an explanatory note,
    and an empty naming batch (`limit=0`) no longer sends a request at all.
  - The test suite drives tools through the v2 in-process `Client` (the v1
    `create_connected_server_and_client_session` helper is gone).

### Added
- `code_only` flag on `graphlore_build` — appends the CLI's `--code-only`, so a
  repo containing doc/paper/image files can be indexed via local AST with no
  LLM API key (those files otherwise demand semantic extraction and the build
  errors without a key).
- README **"Comparison — when to use which"** section: an honest side-by-side
  with [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)
  (same goal, different trade-offs: verbatim-code mega-tool vs token-budgeted
  map, structural-only vs semantic hidden_links, watcher vs git-aware
  freshness), plus one-liners on Graphify's embedded MCP server and semble's
  own MCP server as complements.
- **Benchmark call/read metrics + `--json`** — `benchmarks/multilang.py` now
  also measures the agent-efficiency axis: locate = 1 tool call / 0 file reads
  vs the naive baseline's 1 grep + N file-read calls (N = the files the same
  grep matches, so both metrics share one baseline), and `--json PATH`
  persists the full result set. The 2026-08 re-run (semble 0.5.5) is committed
  as `benchmarks/results-multilang.json` — the first persisted benchmark
  artifact — and the README / `docs/benchmark*.html` cross-language tables now
  carry a "Calls (locate vs naive)" column (89–95% fewer calls; span-join
  percentages follow the overload-family re-count above).
- **`graphlore_routes`** — framework route → handler table: which URL patterns
  hit which code, each row joined back to its graph node and qualified name
  (`GET /items/{id} -> read_item (app.py:5)`). Recognizes the common
  registration idioms per language — Python: Flask `@app.route` /
  Flask-FastAPI verb decorators (labelled by the file's imports) / Django
  `path()`-family (gated on a django import, so a local `path()` never
  registers); JS/TS: Express-style verb calls (plain-identifier receiver +
  leading-`/` literal, which keeps `map.get('key')` out) and NestJS
  `@Controller`+`@Get`; Go: gin/echo/chi verb methods, `Handle`/`HandleFunc`
  with Go 1.22 `"GET /x"` patterns split into method + path, and chi
  `Route(...)` closure nesting with prefix propagation; Java: Spring
  `@GetMapping`-family and `@RequestMapping` (class prefix joined). Scans the
  files the graph knows plus any `urls.py` the graph missed (capped, pruned
  walk). An honest lower bound: dynamic registration, chained builders
  (`router.route().get()`, gorilla `.Methods()`) and variable-bound group
  prefixes are invisible. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `routes.py` engine module with a bounded
  per-(path, mtime) cache and a public `routes_for_source(src, rel)` seam,
  mirroring `apis.py`.
- **`GRAPHIFY_TOOLSET=locate`** — a minimal, mega-tool-style surface of five
  tools: `graphlore_locate` (the one-call orient), `graphlore_fetch` (map → code),
  plus `graphlore_overview` / `graphlore_build` / `graphlore_freshness` to stay
  oriented and in sync. Inspired by single-tool code-graph servers where a
  smaller surface measurably improves tool selection. Needs a semantic backend
  (the `[semble]` extra or `GRAPHIFY_SEMANTIC_BACKEND`); without one the server
  falls back to the lean surface with a stderr warning instead of advertising a
  locate tool that can only return an install hint. `graphlore_overview` now
  suggests `graphlore_locate(...)` first whenever it is on the active surface, so
  the trimmed mode keeps a non-empty `suggested_next`.
- `GRAPHIFY_ALLOWED_HOSTS` — Host-header allowlist for the HTTP transports'
  DNS-rebinding protection (comma-separated, `:*` port wildcards; `*` disables
  it). The MCP v2 SDK auto-enables that protection with a loopback-only
  allowlist when the server binds a loopback host, which rejects
  reverse-proxied requests whose `Host` wasn't rewritten; this variable is the
  escape hatch (v1 enforced no Host check).
- `graphlore_package_apis` — symbol-level external API surface: which names each
  external package is actually used for ("uses `Depends`, `APIRouter` from
  fastapi", not just "imports fastapi"), the input a version-upgrade audit needs.
  Captures from-imports and attribute access through import aliases
  (`np.array(...)` → `numpy: array`) plus qualified paths (`numpy.linalg.norm`)
  from real use sites; an honest lower bound (dynamic import / `getattr` /
  `import *` / wrapper indirection are invisible). First-party packages are
  detected and excluded. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `apis.py` engine module with a bounded
  per-(path, mtime) cache like the span index.
- `api_uses_for_source(src, rel)` — public, stable entry point for external
  consumers (importable from the package root): the same extraction on a source
  blob you already hold, with no file IO / caching / project-dir confinement.
  The underscore-prefixed extractors behind it remain internal.
- Seven new analysis tools: `graphlore_impact` (reverse-dependency blast radius,
  ordered by hop distance), `graphlore_duplication_scan` (repo-wide hidden-link
  audit — semantically similar but structurally distant pairs),
  `graphlore_fetch` (hydrate a node's source, token-capped), `graphlore_skeleton`
  (def/class signatures with bodies stripped), `graphlore_diff` (file-level
  structural changeset between two git refs, cosmetic-only changes separated),
  `graphlore_prune` (drop phantom nodes for deleted/renamed files — the surgical
  alternative to a rebuild, `dry_run` preview), and `graphlore_cycles`
  (Tarjan-SCC dependency cycles).
- Pluggable semantic backend: `GRAPHIFY_SEMANTIC_BACKEND` selects `semble`
  (offline default) or any `module.path:Factory` implementing the
  `SemanticIndex` protocol (`search`/`find_related`); `graphlore_locate` and
  `graphlore_duplication_scan` dispatch through it.
- Opt-in filesystem watcher: `GRAPHIFY_WATCH=1` re-syncs the graph on
  structural source changes (cosmetic edits ignored), debounced via
  `GRAPHIFY_WATCH_DEBOUNCE`; ships as the optional `[watch]` extra (watchdog).

## [0.2.0] - 2026-06-22

### Added
- `graphify-mcp-server` console script and `python -m graphify_mcp` entry point,
  both collision-free with the `graphify-mcp` script that `graphifyy` also ships.
- Boot banner on stderr at startup — `graphify-mcp vX.Y.Z | transport=… |
  toolset=… | project=…` — so it's immediately clear which server (and project
  dir) a client connected to, even when `graphifyy`'s same-named script is around.
- Lightweight `graph_age` field on `graphify_overview` and `graphify_subgraph`
  (e.g. "built 3 commits ago" / "built at HEAD" / "built at an unreachable
  commit") so staleness is visible without a separate `graphify_freshness` call.
  Git-only and cheap; `null` when there's no recorded build commit or no git repo.
  README now documents a first-class **post-commit-hook auto-update** flow to keep
  the graph fresh automatically.
- `graphify_label_communities` — names Leiden communities via **host-LLM MCP
  sampling** (no server API key), a backend key (`method="cli"`), or placeholders.
- `graphify_sampling_status` — capability test reporting whether the client
  supports sampling, whether a backend key is set, and the preferred method.

### Removed
- The bare `graphify-mcp` console script (shipped in 0.1.0). `graphifyy` ships a
  script of the same name, so a bare `graphify-mcp` resolved to whichever package
  installed last and could silently launch the wrong server. **Breaking:** invoke
  the server via `graphify-mcp-server` or `python -m graphify_mcp` instead.

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
- Span extraction no longer mistakes a **call/invocation** for a definition. A
  Java `method_invocation` (and C# `invocation_expression`, Ruby `method_call`,
  PHP `function_call_expression`, …) exposes the callee under a `name` field, so a
  call like `get(u)` inside a method body used to leak in as a phantom symbol
  (`Class.method.get`) and pull chunk resolution to it. Caught by the new Java
  golden span test.

### Changed
- Internal refactor (no behaviour change): the 2,000-line `server.py` is split into
  layered modules — `config.py` (shared `PROJECT_DIR`), `graph.py` (graph.json load +
  node/edge/traversal helpers), `spans.py` (the tree-sitter/ast span + structural-diff
  engine) — leaving `server.py` as the MCP surface (tools/resources/prompts). Same
  108 tests, same benchmark numbers.
- `_load_graph` caches the parsed graph by path + mtime, so a multi-MB
  `graph.json` isn't re-parsed on every tool call.
- Community-naming sampling `max_tokens` raised 16 → 24 to avoid clipped names.
- Token budgeting is now conservative: `~3.5` chars/token (vs the old `4.0`) plus
  a JSON-envelope allowance, so `approx_tokens` and `budget_tokens` reflect the
  whole returned payload and stop systematically under-reporting. Documented as an
  estimate (±~20%). Optional **exact** counting via the `[tiktoken]` extra +
  `GRAPHIFY_TOKENIZER=tiktoken` (the budget cap stays heuristic, so it's fast
  either way; the reported `approx_tokens` becomes exact).
- Tightened the `mcp` dependency bound from `>=1.2.0` to `>=1.26,<2.0`. The server
  uses streamable-HTTP, tool annotations and host-LLM sampling, which the old floor
  didn't have (it installed but failed at runtime); the upper bound guards against a
  breaking 2.0 SDK. Tested against mcp 1.26–1.27.
- Docs: the benchmark numbers now carry an explicit **sample-bias** note (every repo
  measured is an HTTP-client library, so results may differ on other architectures),
  and the HTTP hardening section documents the no-shell subprocess invocation and the
  `GRAPHIFY_TIMEOUT` knob for shared deployments.
- Pinned the optional tree-sitter extras: `tree-sitter>=0.22,<1.0` (the stable
  `Parser(Language)` core API) and `tree-sitter-language-pack>=1.6,<2.0` (the
  bundled grammars churn, so the 1.x cap keeps golden span tests from silently
  regressing on a grammar update). Added per-language golden span tests for Java
  and TypeScript (Python · JS · Go · Rust · C++ already covered).
- CI now runs **mypy** (non-strict baseline over the package) and **pytest with
  coverage** (term + XML report, floored at 80% via `--cov`); push-CI also fixed to
  trigger on `master`, the actual default branch. Tool `annotations=` now use typed
  `ToolAnnotations` objects instead of plain dicts, so the type checker validates the
  tool metadata FastMCP turns into each tool's schema. Added a corrupt-`graph.json`
  test alongside the existing missing-graph / unreachable-`built_at` ones.

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
- `graphify_freshness` now verifies that the recorded `built_at_commit` is
  actually reachable in the clone (`git cat-file -e`). When it isn't (shallow
  clone, gc, rebase or squash), it reports `built_commit_reachable: false` and
  recommends a full rebuild — incremental update can't trust an unknown base —
  instead of mislabeling it "an older commit" and offering an update.
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
- Broader tree-sitter symbol/qualname coverage: an anonymous function bound to a
  name (`const f = () => …`, object property `{ foo: () => … }`, class field
  `handler = (r) => …`, `var h = func(){}`) takes the binding name; a method
  receiver is type-qualified (Go `func (c *Client) Get()` → `Client.Get`); C/C++
  function names are read from the declarator chain (`Session::Get` → `Session.Get`)
  and template calls / sub-declarators no longer leak in as bogus symbols; `region_start`
  also absorbs Rust `#[attribute]` lines. C++ `class_specifier`/`struct_specifier` are
  recognized as definitions.
- **Multi-language validation benchmark** (`benchmarks/multilang.py` + the
  "Across languages" section in `docs/benchmark*.html`): on real HTTP-client repos
  in five more languages (`got` JS/TS, `resty` Go, `retrofit` Java, `ureq` Rust,
  `cpr` C++) span-join precision is 69–91% (vs 91% for Python/httpx), qualname
  recovery 50–100%, locate 200–748× cheaper than grep+read, and the
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
