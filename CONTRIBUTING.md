# Contributing

Thanks for your interest in improving graphlore!

## Development setup

```bash
git clone https://github.com/yasinyaman/graphlore
cd graphlore
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,treesitter]"   # treesitter so the multi-language tests run, not skip
```

The native engine (`engine/`) needs a Rust toolchain (stable, 1.90 or newer)
and maturin. Without it, `tests/test_engine.py` skips itself and everything else
runs.

```bash
pip install maturin
maturin develop -m engine/Cargo.toml --release   # inside the venv; installs graphlore_engine
cargo test --manifest-path engine/Cargo.toml
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
│   ├── engine.py           #   which extractor builds the graph (graphify CLI / native engine)
│   └── config.py           #   project dir / out dir
├── engine/                 # the native extractor in Rust (separate package graphlore-engine)
│   ├── src/generic.rs      #   the definition rule, ported from spans.py
│   ├── src/lang/           #   one module per language: only what differs from the generic rule
│   ├── src/docs/           #   Markdown / reStructuredText headings
│   ├── src/graph/          #   ids, labels, containment edges, atomic graph.json write
│   └── tests/              #   grammar ABI check, span goldens, a build of the polyglot fixture
├── tests/                  # pytest suite (in-process MCP client + unit tests)
├── benchmarks/             # multilang benchmark, polyglot audit + committed results JSON
├── docs/                   # tool reference, concepts, configuration, benchmark reports
├── .claude-plugin/         # Claude Code plugin + marketplace manifests (the repo root is the plugin)
├── .claude/skills/         # graphlore-explore skill for Claude Code (shipped by the plugin)
├── .github/workflows/      # CI: ruff + mypy + pytest on py 3.10–3.12, the engine job; PyPI publish on release
├── server.json             # MCP Registry manifest (see "Releasing")
├── mcp.json                # Claude Code example config
└── claude_desktop_config.json
```

`src/graphlore/` is layered bottom → top (`config.py` → `engine.py` → `graph.py` → `spans.py` →
`scope.py` → `apis.py` / `routes.py` → `imports.py` → `resolve.py` → `review.py` →
`server.py`); each module imports only from earlier ones. `server.py` re-exports
the helpers tests reach as `server._x`. [`AGENTS.md`](AGENTS.md) describes each
layer and the conventions that span files.

## Before opening a PR

```bash
ruff check .      # lint
mypy              # types
pytest -q         # tests
# when engine/ changed:
cargo fmt --manifest-path engine/Cargo.toml --check
cargo clippy --manifest-path engine/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path engine/Cargo.toml
```

A change to the definition rule in `spans.py` needs the same change in
`engine/src/generic.rs`; `tests/test_engine.py` compares the two.

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

### The native engine (`graphlore-engine`)

The engine in `engine/` is its own distribution with its own version, kept only in `engine/Cargo.toml` (independent of graphlore's).

1. Bump `version` in `engine/Cargo.toml` (and `SCHEMA_VERSION` in `engine/src/lib.rs` plus `engine.SUPPORTED_SCHEMAS` if graph.json changed in a way readers must know about).
2. Merge, then push a tag `engine-vX.Y.Z` equal to that version.

The tag runs `.github/workflows/engine-wheels.yml`: it builds the abi3 wheels (Linux x86-64 and ARM on glibc, Linux x86-64 on musl, macOS universal2, Windows x64) and an sdist, installs each wheel and builds the polyglot fixture with it, and uploads them to PyPI. It refuses to upload when the tag and the `Cargo.toml` version differ. The same workflow runs without uploading on every pull request that touches `engine/`, and by hand (`workflow_dispatch`) with `publish_to: testpypi` to try a release on TestPyPI first.

One-time setup, before the first engine release: on PyPI (and on TestPyPI, for the dry run), add a *pending trusted publisher* for the project name `graphlore-engine` with owner `yasinyaman`, repository `graphlore`, workflow `engine-wheels.yml` and environment `pypi` (`testpypi` on TestPyPI).

### MCP Registry

`server.json` is the entry for the [official MCP Registry](https://registry.modelcontextprotocol.io). The registry holds metadata only and checks the package itself, so publish there **after** the PyPI release exists:

```bash
brew install mcp-publisher        # or a binary from the modelcontextprotocol/registry releases
mcp-publisher login github        # device-code login; grants the io.github.yasinyaman/ namespace
mcp-publisher publish             # run in the repository root, reads server.json
```

The registry verifies ownership by looking for `mcp-name: io.github.yasinyaman/graphlore` in the README that PyPI shows, which is why that line sits in an HTML comment at the top of `README.md`. Don't remove it, and keep it equal to `name` in `server.json` (a test checks both, and the two versions). The description is capped at 100 characters. `server.json` runs the server as `uvx --with graphlore-engine graphlore`, so the native engine is installed alongside it.

One-time setup, before the first release: on PyPI, add a *pending trusted publisher* for the project name `graphlore` with owner `yasinyaman`, repository `graphlore`, workflow `publish.yml` and environment `pypi`. No API token is needed.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`.
