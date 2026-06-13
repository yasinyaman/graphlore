# Contributing

Thanks for your interest in improving graphify-mcp!

## Development setup

```bash
git clone https://github.com/yasinyaman/graphify-mcp
cd graphify-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

```bash
ruff check .      # lint
ruff format .     # format
pytest -q         # tests
```

Please:
- Keep tools read-only unless they genuinely mutate state, and set the matching MCP annotation (`readOnlyHint` / `destructiveHint`).
- Add a test in `tests/` for any new tool or behavior; reuse the `project` fixture.
- Stay schema-tolerant — graphify's `graph.json` shape can vary, so parse defensively (see the `_node_*` / `_edge_*` helpers).
- Keep the graph.json analysis tools dependency-free (no graphify CLI required).

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`.
