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
  `agent -p`), in three variants:
  - `baseline`: no graphlore, in a copy of the repository without
    `graphlore-out/` or `graphify-out/` (an agent must not find `graph.json` to grep either):
    what the agent does with its own tools;
  - `plain`: graphlore set up the way `graphlore setup <client>` writes it;
  - `agents-md`: also the usage guide `graphlore agents-md` writes into the
    file that agent reads.

  Every arm keeps the agent's own read and search tools (for Claude Code,
  `--allowedTools Read,Grep,Glob,LS`, plus `mcp__graphlore` where graphlore is
  set up: in `-p` mode a tool that needs approval is denied unless allowed,
  and `--allowedTools` pre-approves without restricting). `--repeat N` asks
  each task N times: no agent's command line takes a seed, so repetition is
  the only measure of spread. Each run is logged
  (`results/<time>/<client>-<variant>-<task>[-r<i>].log`).
- **Score** (`benchmarks/agents/score.py`): a task is solved when every
  expected string is in the final answer. Per agent and variant: tasks solved
  (with repeats, the mean per repetition ± its standard deviation, and the
  range), mean graphlore tool calls and mean tool calls of any kind, runs that
  called none of the task's suited tools, tokens as the agent reports them
  (± their standard deviation), seconds, and for `plain` / `agents-md` the
  difference from the same agent's `baseline`: tasks solved, input tokens as
  a ratio.

```bash
python benchmarks/agents/run.py --root /tmp/graphlore-agents --clients claude,codex,gemini --repeat 3
```

```bash
python benchmarks/agents/score.py benchmarks/agents/results/<time> --markdown
```

Each agent needs its own login or API key on the machine that runs it; the
script asks for none and skips an agent whose command is not installed. The
`fake` client replays a fixed transcript of tool calls against the real server
and needs nothing: the test suite runs the whole pipeline with it
(`test_agent_evaluation_runs_and_scores_with_the_fake_client`,
`test_agent_evaluation_baseline_has_no_graphlore_and_repeats_are_scored`). A
`baseline` run of an agent that reads a user-level configuration (Gemini CLI,
Kimi, Cursor) still sees a graphlore server set up there: run it where none is.
Claude Code's built-in LSP tool needs a code-intelligence plugin and a
language server on the machine, so an arm with it is not part of the runner;
a Claude-only run with `pyright-lsp` installed is the way to measure it. An agent's
output format can change between its releases: read one transcript before
trusting a column, and fix its adapter in `run.py` when the answers come back
empty.

## Results

Not run yet: the agents need their own logins, which the machine this page
was written on does not have. A run fills this table with `score.py
--markdown`.

| Client | Variant | Solved | Tool calls | All calls | No suited tool | Input tokens | Output tokens | Seconds | Failed runs | vs baseline |
|---|---|---|---|---|---|---|---|---|---|---|
