# Contributing

Thanks for your interest in improving graphify-mcp!

## Development setup

```bash
git clone https://github.com/yasinyaman/graphify-mcp
cd graphify-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,treesitter]"   # treesitter so the multi-language tests run, not skip
```

## Project layout

`src/graphify_mcp/` is split into layers (bottom → top):

- **`config.py`** — shared `PROJECT_DIR` / `OUT_DIR_NAME` (read by attribute so tests can repoint it).
- **`graph.py`** — `graph.json` loading + schema-tolerant `_node_*` / `_edge_*` / traversal helpers.
- **`spans.py`** — the analysis engine: tree-sitter / `ast` span extraction, the chunk→node join, and the structural diff behind cosmetic-vs-structural freshness.
- **`server.py`** — the MCP surface: the FastMCP instance, the tools, resources, prompts, and `main()`. Re-exports the helpers above so `server._x` keeps resolving.

Add a graph/analysis helper to `graph.py` or `spans.py`; add a new tool to `server.py`.

## Before opening a PR

```bash
ruff check .      # lint
ruff format .     # format
pytest -q         # tests
```

Please:
- Keep tools read-only unless they genuinely mutate state, and set the matching MCP annotation (`readOnlyHint` / `destructiveHint`).
- Add a test in `tests/` for any new tool or behavior; reuse the `project` fixture.
- Stay schema-tolerant — graphify's `graph.json` shape can vary, so parse defensively (see the `_node_*` / `_edge_*` helpers in `graph.py`).
- Keep the graph.json analysis tools dependency-free (no graphify CLI required); the tree-sitter span backend stays an optional `[treesitter]` extra that degrades gracefully when absent.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`.
