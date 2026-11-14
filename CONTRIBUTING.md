# Contributing

Thanks for your interest in improving graphlore!

## Development setup

```bash
git clone https://github.com/yasinyaman/graphlore
cd graphlore
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,treesitter]"   # treesitter so the multi-language tests run, not skip
```

## Project layout

```
graphlore/
├── src/graphlore/          # package
│   ├── server.py           #   MCP server: 33 tools, prompts, resources, transports
│   ├── graph.py            #   graph.json loading, node/edge accessors, BFS, adjacency
│   ├── spans.py            #   span engine: ast + tree-sitter, chunk→node join, structural diff
│   ├── scope.py            #   analysis scope: vendored / external / test filtering
│   ├── apis.py             #   symbol-level external-API extraction
│   ├── routes.py           #   framework route → handler extraction
│   ├── imports.py          #   import statements read from source: what they can mean, when they run
│   ├── resolve.py          #   calls and imports through a module object, resolved from source
│   ├── review.py           #   unreferenced code, same-named symbols, identical bodies, layer rules
│   └── config.py           #   project dir / out dir
├── tests/                  # pytest suite (in-process MCP client + unit tests)
├── benchmarks/             # multilang benchmark + committed results JSON
├── docs/                   # tool reference, concepts, configuration, benchmark reports
├── .claude-plugin/         # Claude Code plugin + marketplace manifests (the repo root is the plugin)
├── .claude/skills/         # graphlore-explore skill for Claude Code (shipped by the plugin)
├── .github/workflows/      # CI: ruff + mypy + pytest on py 3.10–3.12; PyPI publish on release
├── server.json             # MCP Registry manifest (see "Releasing")
├── mcp.json                # Claude Code example config
└── claude_desktop_config.json
```

`src/graphlore/` is layered bottom → top (`config.py` → `graph.py` → `spans.py` →
`scope.py` → `apis.py` / `routes.py` → `imports.py` → `resolve.py` → `review.py` →
`server.py`); each module imports only from earlier ones. `server.py` re-exports
the helpers tests reach as `server._x`. [`AGENTS.md`](AGENTS.md) describes each
layer and the conventions that span files.

## Before opening a PR

```bash
ruff check .      # lint
mypy              # types
pytest -q         # tests
```

Don't run `ruff format .` across the repository: most files are not
ruff-formatted and CI does not check formatting, so it rewrites them wholesale.
Match the surrounding style by hand.

Please:
- Keep tools read-only unless they genuinely mutate state, and set the matching MCP annotation (`read_only_hint` / `destructive_hint`).
- Add a test in `tests/` for any new tool or behavior; reuse the `project` fixture.
- Stay schema-tolerant — graphify's `graph.json` shape can vary, so parse defensively (see the `_node_*` / `_edge_*` helpers in `graph.py`).
- Keep the graph.json analysis tools dependency-free (no graphify CLI required); the tree-sitter span backend stays an optional `[treesitter]` extra that degrades gracefully when absent.

## Releasing

1. Bump the version in `pyproject.toml`, `src/graphlore/server.py` (`__version__`), `.claude-plugin/plugin.json` and `server.json` (twice: the top-level `version` and the package's), then run `uv lock`.
2. In `CHANGELOG.md`, move the `[Unreleased]` entries under a new version heading.
3. Merge, then publish a GitHub release whose tag is the version (`v0.3.0` or `0.3.0`).

Publishing the release runs `.github/workflows/publish.yml`, which builds the package and uploads it to PyPI. It refuses to upload when the tag and the `pyproject.toml` version differ.

### MCP Registry

`server.json` is the entry for the [official MCP Registry](https://registry.modelcontextprotocol.io). The registry holds metadata only and checks the package itself, so publish there **after** the PyPI release exists:

```bash
brew install mcp-publisher        # or a binary from the modelcontextprotocol/registry releases
mcp-publisher login github        # device-code login; grants the io.github.yasinyaman/ namespace
mcp-publisher publish             # run in the repository root, reads server.json
```

The registry verifies ownership by looking for `mcp-name: io.github.yasinyaman/graphlore` in the README that PyPI shows, which is why that line sits in an HTML comment at the top of `README.md`. Don't remove it, and keep it equal to `name` in `server.json` (a test checks both, and the two versions). The description is capped at 100 characters. `server.json` runs the server as `uvx --with graphifyy graphlore`, so the Graphify CLI is installed alongside it.

One-time setup, before the first release: on PyPI, add a *pending trusted publisher* for the project name `graphlore` with owner `yasinyaman`, repository `graphlore`, workflow `publish.yml` and environment `pypi`. No API token is needed.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`.
