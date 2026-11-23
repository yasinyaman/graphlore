# Coding agents on graphlore: a cross-model evaluation

How well do Claude Code, OpenAI Codex CLI, Google Gemini CLI, Kimi Code CLI
and Cursor's agent use graphlore? The other benchmarks measure what graphlore
returns (retrieval quality, tokens, correctness of edges). This one measures
what an agent does with it: the same 16 questions about one repository, asked
of each agent headless, with graphlore as its MCP server.

Back to the [README](../README.md) · [Clients](clients.md) · [Benchmark](benchmark.md)

## Method

- **Tasks** (`benchmarks/agents/tasks.json`): 16 questions about
  [httpx](https://github.com/encode/httpx) at a pinned commit, each with an
  answer checked in its source: which method sends one request, which method
  calls which, where a class is defined, which class inherits from which, the
  default timeout constant, which test exercises `URL.copy_with`. Each task
  names the graphlore tools that answer it best.
- **Runs** (`benchmarks/agents/run.py`): each agent through its documented
  headless mode (`claude -p`, `codex exec --json`, `gemini -p`, `kimi -p`,
  `agent -p`), with the server block `graphlore setup <client>` writes (Gemini,
  Kimi and Cursor run `setup` itself; Claude Code gets the block through
  `--mcp-config` and `--strict-mcp-config`, Codex through `-c mcp_servers.…`
  overrides, so neither touches your own config), on the `lean` toolset unless
  `--toolset` says otherwise (`lean` has no `graphlore_tests_for`, the suited
  tool of the two test questions, so there `graphlore_impact` is the one to
  find), in two variants: `plain` (the tools only) and `agents-md` (also
  the usage guide `graphlore agents-md` writes into the file that agent reads).
  Each run is logged (`results/<time>/<client>-<variant>-<task>.log`).
- **Score** (`benchmarks/agents/score.py`): a task is solved when every
  expected string is in the final answer. Per agent and variant: tasks solved,
  mean graphlore tool calls, runs that called none of the task's suited tools,
  tokens as the agent reports them, seconds.

```bash
python benchmarks/agents/run.py --root /tmp/graphlore-agents --clients claude,codex,gemini,kimi
```

```bash
python benchmarks/agents/score.py benchmarks/agents/results/<time> --markdown
```

Each agent needs its own login or API key on the machine that runs it; the
script asks for none and skips an agent whose command is not installed. The
`fake` client replays a fixed transcript of tool calls against the real server
and needs nothing: the test suite runs the whole pipeline with it
(`test_agent_evaluation_runs_and_scores_with_the_fake_client`). An agent's
output format can change between its releases: read one transcript before
trusting a column, and fix its adapter in `run.py` when the answers come back
empty.

## Results

Not run yet: the agents need their own logins, which the machine this page
was written on does not have. A run fills this table with `score.py
--markdown`.

| Client | Variant | Solved | Tool calls | No suited tool | Input tokens | Output tokens | Seconds | Failed runs |
|---|---|---|---|---|---|---|---|---|
