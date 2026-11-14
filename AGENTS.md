# AGENTS.md

Guidance for AI coding agents working in this repository. This file is the single source: Codex, Cursor and Copilot's agent features read it directly, and `.claude/CLAUDE.md` and `GEMINI.md` import it. `.github/copilot-instructions.md` is a condensed copy for the Copilot features that read only that file; when a rule changes here, update it there too.

## What this is

graphlore is a Python MCP server that makes a Graphify-built knowledge graph (`graphify-out/graph.json`) navigable and joins it to real source spans. It ships **no extractor**: building the graph is delegated to the external `graphify` CLI (PyPI package `graphifyy`). What lives here is graph analysis plus the span engine (stdlib `ast` for Python, tree-sitter for everything else).

It is built on the **MCP Python SDK v2** (`mcp>=2.1,<3.0`): `mcp.server.mcpserver.MCPServer`, not v1's `FastMCP`, and `ToolAnnotations` with snake_case fields (`read_only_hint`, `destructive_hint`).

## Commands

```bash
pip install -e ".[dev,treesitter]"   # or: uv sync --extra dev --extra treesitter, then prefix commands with `uv run`

ruff check .
mypy                                 # configured in pyproject.toml, checks src/graphlore
pytest -q
pytest -q tests/test_tools.py::test_overview_json   # one test
pytest -q -k freshness                              # by keyword
pytest -q --cov --cov-report=term-missing           # what CI runs; coverage floor is 80%

GRAPHLORE_PROJECT_DIR=/path/to/repo python -m graphlore   # run the server (stdio)
```

CI (`.github/workflows/ci.yml`) runs `ruff check .`, `mypy` and pytest with coverage on Python 3.10–3.12, so code must stay 3.10-compatible. The default branch is `master`.

**Don't run `ruff format .` across the repo.** CI doesn't check formatting and 8 of the 11 Python files (including `server.py` and `tests/test_tools.py`) are not ruff-formatted, so it rewrites them wholesale. Match the surrounding style by hand unless a reformat is the task.

The multi-language tests skip themselves when the `[treesitter]` extra is absent. When it is installed, `tree-sitter-language-pack` downloads grammars from GitHub releases on first use (cached under `~/.cache/tree-sitter-language-pack/`); if that download fails, those tests **fail** rather than skip (`assert None is True`, `KeyError`, empty route lists). Those symptoms look the same as a real regression in the tree-sitter extractors, so check before dismissing them:

```bash
python -c "from tree_sitter_language_pack import get_language; get_language('javascript')"
```

A `LanguageNotFoundError: Download error` means the grammars are unavailable and the failures are environmental. If it succeeds, the failures are real.

## Architecture

`src/graphlore/` is layered bottom → top; each module imports only from earlier entries in this list (plus one sideways import: `routes.py` uses `_strip_quotes` from `apis.py`):

- `config.py` — `env(name)` reads `GRAPHLORE_<name>`, falling back to the legacy `GRAPHIFY_<name>`; holds `PROJECT_DIR` and `OUT_DIR_NAME`.
- `graph.py` — loads `graph.json` (cached by path + mtime), schema-tolerant node/edge accessors, adjacency, BFS, SCC cycles, hop distances, surprise scoring, token estimation. No MCP dependency.
- `spans.py` — the span engine: per-file `(region_start, end_line, def_line, qualname)` spans, `_node_for_location` (semantic chunk → enclosing graph node), `_structurally_equal` (the cosmetic-vs-structural diff behind freshness/diff/watch), and the shared per-file pipeline `_cached_file_analysis`.
- `scope.py` — the analysis scope shared by the analysis tools: parses the `scope` string, detects vendored/minified files, and hands back a cached `ScopeView` of the nodes and edges it keeps.
- `apis.py`, `routes.py` — per-file extractors (external package API usage; framework route → handler) built on the `spans.py` pipeline.
- `imports.py` — import statements read from the importing file's source (Python via `ast`, JS/TS via tree-sitter): `ImportChecker.kind` says when the import behind a file-level import edge runs (module-level, deferred, type-checking) and rejects an edge the source contradicts (`import yaml` linked to a first-party `yaml.py`).
- `resolve.py` — edges the graph lacks and the source proves: `_resolved(nodes, edges)` returns the graph's edges plus calls and imports made through a module object (`mod.func()`; Python, plus JS/TS default and dynamic imports), marked `resolved_by: "graphlore"`, as one cached list.
- `review.py` — the review analyses with no MCP dependency: unreferenced code, same-named symbols, identical bodies, layer rules.
- `server.py` — the whole MCP surface in one ~5,000-line module: the `mcp` instance, 33 tools, resources, prompts, toolset trimming, the opt-in watcher, HTTP bearer auth and `main()`.

Tools come in two kinds. **CLI-backed** tools (`build`, `add`, `query`, `path`, `explain`) shell out through `_run_cli`, always as an argv list with no shell; `graphlore_label_communities(method="cli")` is a sixth `_run_cli` caller (`graphify label`). Callers detect failure by the `ERROR` prefix of the returned string. **graph.json analysis** tools read the graph directly and must keep working with no `graphify` binary and no optional extras installed.

Optional dependencies (`semble`, `tree-sitter`, `tiktoken`, `watchdog`, `uvicorn`) are imported lazily inside functions and degrade to a fallback or an install hint; nothing may raise `ImportError` at module import.

## Conventions that span files

- **Read `config.PROJECT_DIR` by attribute**, never `from .config import PROJECT_DIR`: tests repoint it with `monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)`, and `graphlore_project` reassigns it at runtime. For the same reason nothing may cache a value derived from it without keying on it.
- **Settings go through `config.env("NAME")`** so the legacy `GRAPHIFY_*` fallback applies. Several are captured once at import as `server.py` module constants (`TOOLSET`, `TRANSPORT`, `RESTRICT_PATHS`, `API_KEY`, `GRAPHIFY_BIN`, …) and tests patch the attribute; others (`SEMANTIC_BACKEND`, `WATCH`, `ALLOWED_HOSTS`, `TOKENIZER`, `OFFLINE` via `config.offline()`, `STRUCTURED`) are read at call time.
- **Network egress:** every path that can reach the network checks `config.offline()` (refuse, or degrade to a local-only variant and say so); the "What reaches the network, and when" table in `docs/configuration.md` lists them and must stay in step. `_run_cli` passes `_cli_env()`, which strips the `_BACKEND_ENV` keys offline. A new optional dependency that downloads on first use gets a row there and a gate.
- **Repository text is framed:** a tool whose text reply quotes source, docstrings or CLI output derived from them wraps it in `_frame_repo_text(...)` and puts `content_trust: _CONTENT_TRUST` and `untrusted_fields` in the payload. graphlore's own notes go outside the frame; errors are never framed.
- **Tool shape:** a plain sync function returning `str` (the SDK runs sync tools in a threadpool), registered with `@_tool(annotations=...)`, never `@mcp.tool`. `_load_graph()` returns either the graph dict or an error *string*; check `isinstance(graph, str)` and return `_err(msg, as_json)`. Success returns `_fmt(payload, as_json, text)`. With `as_json=True` every path, including errors and no-match, must return parseable JSON.
- **MCP results:** `_tool` registers a wrapper (no outputSchema) and leaves the module attribute as the plain function, so direct calls and tests get the string. `_fmt` / `_err` record their payload in a per-call `ContextVar`; when the tool returns exactly that string, the payload becomes the result's `structuredContent`. `_err` sets `isError`, and so does `_fmt(..., is_error=True)` for a failure that still has a payload. An empty but valid answer (no search hits) is `_fmt`, not `_err`. `_err(msg, as_json, **extra)` keeps `error` a string and adds sibling keys (`code`, `did_you_mean`). A reply built without `_fmt`/`_err` has no structuredContent and is an error only when it starts with `ERROR`; CLI passthroughs go through `_cli_reply`. `GRAPHLORE_STRUCTURED=0` turns all of this off.
- **The one async tool** is `graphlore_label_communities` (`async def`, takes `ctx: Context` and a keyword-only `Resolve` parameter that must have no default; see the comment on its signature). An async body runs on the event loop, so blocking work there goes through `anyio.to_thread.run_sync`. Tests drive it over an in-memory session (`_run_in_memory`), not by calling it directly.
- **Schema tolerance:** graphify's `graph.json` shape varies. For id, label, file, line, edge endpoints and relation use the accessors: `_nodes_edges`, `_node_id`, `_node_label`, `_node_file`, `_node_line`, `_edge_ends`, `_edge_rel`. Fields without an accessor are read with `.get()` and a fallback, never `[]`; community is `n.get("community", n.get("cluster"))`.
- **Ambiguous labels are qualified by `_display_labels(nodes)`** (`DigestAuth.auth_flow()` or a `file:line` suffix). Use it for nodes named in edge, neighbor and list rows. A row that prints its own `file:line` column uses `labels.name(id)` instead, which never adds a location suffix. The resolved subject of a call (`center`, `node`, `seed` in the payload) keeps its bare `_node_label`.
- **Import edges go through `ImportChecker`.** graphify resolves imports by name, so a tool that lifts import edges to file level (`_file_import_edges`, `_module_import_adjacency`) passes `ImportChecker().kind` — one instance per call — and reports `checker.rejected` rather than silently trusting or dropping edges. It reads Python with `ast` and JS/TS with tree-sitter, treats compile-time languages (Go, Java, Kotlin, Scala, C#, Rust, Swift) as module-level, and returns `unclassified` (kept, counted as module-level) for anything else. A new source reader, here or in `resolve.py`, covers every language the project already parses or says per language why there is nothing to read — the `apis.py` extractors are the model.
- **Identity-keyed caches:** `_adjacency`, `_directed_adjacency`, `_edge_set`, `_exact_node_index`, `_name_index`, `_edge_flags`, `_nodes_by_file` and `_display_labels` are keyed on `id()` of the nodes/edges list that `_load_graph` hands back. Pass those lists through unchanged; a copied or filtered list misses the cache on every call. The sanctioned derived lists are `_resolved(...)`'s extended edge list and a `ScopeView`'s: `_scoped` builds it once per graph and returns the same objects afterwards (and returns the original lists when the scope leaves nothing out).
- **Name resolution:** every tool that takes a node name (impact, subgraph, neighbors, tests_for, node_details, fetch, skeleton) resolves it with `_pick_node`, reports `_matched(key, picked)` (several matches, or a substring fallback, which is never silent) and answers a miss with `_not_found(nodes, key, as_json)`, which adds `code: "not_found"` and `did_you_mean`. Don't call `_resolve_node` from a tool.
- **A cut says what it left out:** a budget or limit that drops rows reports `truncated` plus a count or the names left out (`not_fetched`, `files_left_out`, `total`, `counts`, `beyond_hops`) and a one-line `hint` on how to get the rest. Never cut silently.
- **Node-centric tools** (impact, subgraph, neighbors, tests_for) resolve their node as above; they read `_nav_view(nodes, edges, scope, as_json)`, which is the resolved edge list under a scope that leaves nothing out by default; they order rows production-before-tests; and an empty result carries `_EMPTY_NOTE`. `_resolved` edges are never written back: `graphlore_prune` and `graphlore_validate`'s lint read the raw graph.
- **Analysis scope:** a tool that ranks or audits the graph takes `scope: str = ""` and goes through `_scope_view(nodes, edges, scope, as_json)`. Compute on `view.nodes` / `view.edges`, label with `_display_labels(all_nodes)`, put `view.payload()` under `"scope"` and wrap the text in `_with_note(text, view)` so what was left out is always stated. The default leaves out vendored/minified files and external nodes.
- **Path confinement:** the per-file analyzers and source hydration (spans, APIs, routes, `fetch`, `skeleton`, `prune`) resolve every path from the graph, a semantic chunk or a file/node argument through `spans._resolve_in_project`, usually via `_cached_file_analysis` or `_read_source_lines`. Don't open such paths directly. Three things sit outside it on purpose: `graphlore_build`'s `path` is unconfined by default (absolute and sibling-repo paths are a documented use) and is guarded only by the opt-in `_path_escapes_project` (`RESTRICT_PATHS`, forced on for HTTP transports); `graphlore_project` accepts any directory and is refused outright under `RESTRICT_PATHS`; and freshness/diff/watch read git-reported paths relative to `config.PROJECT_DIR`. `review._count_mentions` walks the project directory itself (like `_route_candidate_files`) and never follows a link out of it.
- **`server.py` re-exports private helpers** from the lower modules (`# noqa: F401`) because tests reach them as `server._x`. A new helper that tests use that way needs adding to the re-export block.
- **stdout is the protocol on stdio transport**; diagnostics (boot banner, warnings, watcher logs) go to stderr.
- **Naming:** `graphlore_*` tools and `GRAPHLORE_*` variables belong to this project. `graphify-out/`, `.graphify_labels.json` and the `graphify` binary are artifacts of the wrapped CLI and keep their names.

## When adding or changing a tool

A tool is registered with `@_tool(annotations=ToolAnnotations(...))` in `server.py`; keep it read-only unless it genuinely mutates state and set the matching hint. The change also touches:

- the tool list in the `server.py` module docstring, and `LEAN_TOOLS` / `LOCATE_TOOLS` if it belongs in a trimmed surface. `lean` is the default surface, so a tool outside it is invisible to most users unless they set `GRAPHLORE_TOOLSET=full` (the plugin does)
- `_instructions(active)` and the prompts (`_render_prompt` steps) if the tool belongs in the recommended flow: they name a tool only when it is registered. A reply that points to another tool wraps the pointer in `_tool_hint(name, text)`
- `test_tool_and_prompt_registration` in `tests/test_tools.py`, which asserts the exact tool count (33)
- the tool tables in `docs/tools.md`, the README's table of the 12 `lean` tools if the tool is in that set, and the "33" / "12" counts in both
- `.claude/skills/graphlore-explore/SKILL.md` (shipped to users by the plugin)
- `CHANGELOG.md` under `[Unreleased]` — every behavior change gets an entry there (Keep a Changelog format)

The version is duplicated in `pyproject.toml`, `server.__version__`, `.claude-plugin/plugin.json` and `server.json` (twice there; tests compare the last three), and recorded in `uv.lock`; run `uv lock` after a bump. The release steps, including the PyPI publish workflow, are under "Releasing" in `CONTRIBUTING.md`.

## Documentation layout

`README.md` is the landing page, and PyPI's long description: what graphlore is, quick start, the 12 `lean` tools, the benchmark headline and an FAQ. Keep it short; detail goes to `docs/`:

- `docs/tools.md` — all 33 tools, resources, prompts, what a result carries
- `docs/concepts.md` — resolved edges, import checks, analysis scope, freshness, the semantic bridge, naming communities
- `docs/configuration.md` — running, the plugin, toolsets, transports, the network table, environment variables
- `docs/benchmark.md` — method and results (`docs/benchmark*.html` are the rendered reports)

Links from the README into the repository are absolute (`https://github.com/yasinyaman/graphlore/blob/master/...`) because relative links break on PyPI; links between the `docs/` files are relative. The README's first paragraph and its FAQ headings are written to be quoted by search engines and AI assistants: keep them plain, literal and true to the code.

## MCP Registry entry

`server.json` at the root is the manifest for the official MCP Registry (schema `2025-12-11`). Its `name` (`io.github.yasinyaman/graphlore`) must also appear as `mcp-name: ...` in `README.md`: the registry verifies PyPI ownership by finding that string in the package's README, so the HTML comment under the README title is load-bearing. The description is limited to 100 characters, the environment variables it lists must be ones the server reads, and its toolset choices must match `TOOLSETS`; `test_mcp_registry_manifest_stays_in_sync` checks all of this. Publishing is a manual step after a PyPI release (see "Releasing" in `CONTRIBUTING.md`).

## Claude Code plugin

The repository root is itself a Claude Code plugin and its own marketplace (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` with `"source": "./"`). Consequences:

- `.claude/skills/` is what the plugin ships to users, not only a project-local skill directory. Anything added there is installed for everyone.
- The plugin's MCP server runs `uvx --from "graphlore[graphify,treesitter,semble] @ ${CLAUDE_PLUGIN_ROOT}" graphlore`, i.e. it builds this package from the installed copy of the repo. Renaming the console script, the package or those extras breaks the plugin.
- Users stay on the manifest's `version` until it changes, so a fix reaches plugin users only with a version bump.
- Check manifest changes with `claude plugin validate --strict .`; it passes clean.
- Claude Code's project instructions live in `.claude/CLAUDE.md`, not at the root. A `CLAUDE.md` at a plugin root is not loaded as plugin context and makes the validator warn, so don't add one there.

## Tests

Everything is in `tests/test_tools.py`. `tests/conftest.py` pins `server.TOOLSET = "full"` for every test: `server.main()` trims the shared `server.mcp` for real, and the default is `lean`. Tests call tool functions directly (`server.graphlore_overview(as_json=True)`) and parse the result. The `project` fixture gives a temp project with the 5-node fixture graph; `empty_project` has no graph. Tests needing a specific shape write their own `graphify-out/graph.json` into `tmp_path` and repoint `PROJECT_DIR`. Protocol-level behavior (schemas, sampling) uses the SDK's in-memory `Client(server.mcp)` driven by `asyncio.run`; there is no pytest-asyncio. Freshness/diff tests build a real git repo in `tmp_path`.
