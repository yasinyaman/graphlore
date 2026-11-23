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
│   ├── server.py           #   MCP server: 34 tools, prompts, resources, transports
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
├── .claude-plugin/         # Claude Code plugin + marketplace manifests (Codex and Copilot read the marketplace)
├── .gemini-extension/      # GRAPHLORE.md, the Gemini CLI extension's context file
├── skills/                 # graphlore-explore skill, shipped by every plugin
├── .claude/skills/         # a link to skills/graphlore-explore, for this repository's sessions
├── .github/workflows/      # CI: ruff + mypy + pytest on py 3.10–3.12, the engine job; PyPI publish on release
├── server.json             # MCP Registry manifest (see "Releasing")
├── plugin.json             # Agent Plugins 1.0 manifest (Codex, Copilot CLI)
├── mcp.json                # the Agent Plugins plugin's MCP server
├── gemini-extension.json   # Gemini CLI extension manifest
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
- Stay schema-tolerant — a `graph.json` from graphify or an older engine can vary in shape, so parse defensively (see the `_node_*` / `_edge_*` helpers in `graph.py`).
- Keep the graph.json analysis tools dependency-free (no graphify CLI required); the tree-sitter span backend stays an optional `[treesitter]` extra that degrades gracefully when absent.

## Releasing

1. Bump the version in `pyproject.toml`, `src/graphlore/server.py` (`__version__`), `.claude-plugin/plugin.json`, `plugin.json`, `gemini-extension.json` and `server.json` (twice: the top-level `version` and the package's), then run `uv lock`.
2. In `CHANGELOG.md`, move the `[Unreleased]` entries under a new version heading.
3. Merge, then publish a GitHub release whose tag is the version (`v0.3.0` or `0.3.0`).

Publishing the release runs `.github/workflows/publish.yml`, which builds the package and uploads it to PyPI. It refuses to upload when the tag and the `pyproject.toml` version differ.

Or, after step 2, run that workflow by hand on `master` (Actions → *Publish to PyPI* → *Run workflow*): it uploads the `pyproject.toml` version, then creates the `v<version>` tag and release with the built files attached. It refuses to run when that tag already exists.

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

### The first PyPI release

graphlore has not been on PyPI yet (0.7.0 was never tagged or uploaded), and
the README is PyPI's long description, so the release commit and the upload
go together. In order:

1. Once: the pending trusted publisher for `graphlore` (above) and an
   environment named `pypi` in the repository's settings (Settings →
   Environments).
2. Decide the engine: `graphlore-engine` 0.5.79 is on PyPI and the `engine`
   extra (`>=0.5.79,<0.6`) installs it; push `engine-v0.5.80` first only to
   ship the engine's search kernel with this release.
3. The release commit, merged only when the upload follows at once:
   - the version (steps 1–2 of *Releasing*: 0.8.0, since `[Unreleased]`
     holds everything after 0.7.0) and `uv lock`;
   - `README.md`: the quick start's "not on PyPI yet" comment and GitHub
     install line become `pip install "graphlore[engine]"` /
     `uvx --from "graphlore[engine]" graphlore`, and the paragraph "Until the
     first PyPI release…" goes;
   - `docs/clients.md`: "Once graphlore is on PyPI, …" becomes a plain
     statement;
   - `docs/review.md`: the GitLab job installs `graphlore[engine]` from PyPI,
     and the Action can install from PyPI (`action.yml`'s install step) or
     keep its own copy.
4. Run *Publish to PyPI* by hand on `master`: it uploads and creates the
   `v0.8.0` tag and release, which is also the tag the Action's users pin.
5. From a clean machine or container: `uvx --from "graphlore[engine]"
   graphlore --help`, then `pip install "graphlore[engine]"` and
   `graphlore review --help`, on Linux, macOS and Windows if you can.
6. `mcp-publisher publish` (*MCP Registry*, above); then the directories
   that read the registry or take a submission.
7. If the upload fails after the merge, revert the README hunk until it
   succeeds: PyPI's page and the README must not promise an install that
   fails.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`.
