# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

**Don't run `ruff format .` across the repo.** `CONTRIBUTING.md` lists it, but CI doesn't check formatting and 8 of the 11 Python files (including `server.py` and `tests/test_tools.py`) are not ruff-formatted, so it rewrites them wholesale. Match the surrounding style by hand unless a reformat is the task.

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
- `apis.py`, `routes.py` — per-file extractors (external package API usage; framework route → handler) built on the `spans.py` pipeline.
- `server.py` — the whole MCP surface in one ~3,500-line module: the `mcp` instance, 28 tools, resources, prompts, toolset trimming, the opt-in watcher, HTTP bearer auth and `main()`.

Tools come in two kinds. **CLI-backed** tools (`build`, `add`, `query`, `path`, `explain`) shell out through `_run_cli`, always as an argv list with no shell; `graphlore_label_communities(method="cli")` is a sixth `_run_cli` caller (`graphify label`). Callers detect failure by the `ERROR` prefix of the returned string. **graph.json analysis** tools read the graph directly and must keep working with no `graphify` binary and no optional extras installed.

Optional dependencies (`semble`, `tree-sitter`, `tiktoken`, `watchdog`, `uvicorn`) are imported lazily inside functions and degrade to a fallback or an install hint; nothing may raise `ImportError` at module import.

## Conventions that span files

- **Read `config.PROJECT_DIR` by attribute**, never `from .config import PROJECT_DIR`: tests repoint it with `monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)`.
- **Settings go through `config.env("NAME")`** so the legacy `GRAPHIFY_*` fallback applies. Several are captured once at import as `server.py` module constants (`TOOLSET`, `TRANSPORT`, `RESTRICT_PATHS`, `API_KEY`, `GRAPHIFY_BIN`, …) and tests patch the attribute; others (`SEMANTIC_BACKEND`, `WATCH`, `ALLOWED_HOSTS`, `TOKENIZER`) are read at call time.
- **Tool shape:** a plain sync function returning `str` (the SDK runs sync tools in a threadpool). `_load_graph()` returns either the graph dict or an error *string*; check `isinstance(graph, str)` and return `_err(msg, as_json)`. Success returns `_fmt(payload, as_json, text)`. With `as_json=True` every path, including errors and no-match, must return parseable JSON.
- **The one async tool** is `graphlore_label_communities` (`async def`, takes `ctx: Context` and a keyword-only `Resolve` parameter that must have no default; see the comment on its signature). An async body runs on the event loop, so blocking work there goes through `anyio.to_thread.run_sync`. Tests drive it over an in-memory session (`_run_in_memory`), not by calling it directly.
- **Schema tolerance:** graphify's `graph.json` shape varies. For id, label, file, line, edge endpoints and relation use the accessors: `_nodes_edges`, `_node_id`, `_node_label`, `_node_file`, `_node_line`, `_edge_ends`, `_edge_rel`. Fields without an accessor are read with `.get()` and a fallback, never `[]`; community is `n.get("community", n.get("cluster"))`.
- **Ambiguous labels are qualified by `_display_labels(nodes)`** (`DigestAuth.auth_flow()` or a `file:line` suffix). Use it for nodes named in edge, neighbor and list rows. The resolved subject of a call (`center`, `node`, `seed` in the payload) keeps its bare `_node_label`.
- **Identity-keyed caches:** `_adjacency`, `_directed_adjacency`, `_edge_set`, `_exact_node_index`, `_nodes_by_file` and `_display_labels` are keyed on `id()` of the nodes/edges list that `_load_graph` hands back. Pass those lists through unchanged; a copied or filtered list misses the cache on every call.
- **Path confinement:** the per-file analyzers and source hydration (spans, APIs, routes, `fetch`, `skeleton`, `prune`) resolve every path from the graph, a semantic chunk or a file/node argument through `spans._resolve_in_project`, usually via `_cached_file_analysis` or `_read_source_lines`. Don't open such paths directly. Two things sit outside it on purpose: `graphlore_build`'s `path` is unconfined by default (absolute and sibling-repo paths are a documented use) and is guarded only by the opt-in `_path_escapes_project` (`RESTRICT_PATHS`, forced on for HTTP transports); and freshness/diff/watch read git-reported paths relative to `config.PROJECT_DIR`.
- **`server.py` re-exports private helpers** from the lower modules (`# noqa: F401`) because tests reach them as `server._x`. A new helper that tests use that way needs adding to the re-export block.
- **stdout is the protocol on stdio transport**; diagnostics (boot banner, warnings, watcher logs) go to stderr.
- **Naming:** `graphlore_*` tools and `GRAPHLORE_*` variables belong to this project. `graphify-out/`, `.graphify_labels.json` and the `graphify` binary are artifacts of the wrapped CLI and keep their names.

## When adding or changing a tool

A tool is registered with `@mcp.tool(annotations=ToolAnnotations(...))` in `server.py`; keep it read-only unless it genuinely mutates state and set the matching hint. The change also touches:

- the tool list in the `server.py` module docstring, and `LEAN_TOOLS` / `LOCATE_TOOLS` if it belongs in a trimmed surface
- `test_tool_and_prompt_registration` in `tests/test_tools.py`, which asserts the exact tool count (28)
- the README tool tables and its "28 tools" counts
- `.claude/skills/graphlore-explore/SKILL.md`
- `CHANGELOG.md` under `[Unreleased]` — every behavior change gets an entry there (Keep a Changelog format)

The version is duplicated in `pyproject.toml` and `server.__version__`, and recorded in `uv.lock`; run `uv lock` after a bump.

## Tests

Everything is in `tests/test_tools.py`. Tests call tool functions directly (`server.graphlore_overview(as_json=True)`) and parse the result. The `project` fixture gives a temp project with the 5-node fixture graph; `empty_project` has no graph. Tests needing a specific shape write their own `graphify-out/graph.json` into `tmp_path` and repoint `PROJECT_DIR`. Protocol-level behavior (schemas, sampling) uses the SDK's in-memory `Client(server.mcp)` driven by `asyncio.run`; there is no pytest-asyncio. Freshness/diff tests build a real git repo in `tmp_path`.
