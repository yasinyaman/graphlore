# graphlore: an MCP server for codebase knowledge graphs

<!-- mcp-name: io.github.yasinyaman/graphlore -->

[![CI](https://github.com/yasinyaman/graphlore/actions/workflows/ci.yml/badge.svg)](https://github.com/yasinyaman/graphlore/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**graphlore is a Model Context Protocol (MCP) server that lets AI coding
assistants navigate a codebase through its knowledge graph, instead of grepping
and reading file after file.** It builds the graph with its own extractor
(Rust and tree-sitter, 14 languages), and still reads one built by
[Graphify](https://graphify.net); it joins every node to its real `start..end`
source span (Python via `ast`, other languages via tree-sitter), and answers
the questions text search cannot: who calls this, what breaks if it changes,
where a behavior lives.

- **Find code by what it does.** `graphlore_locate("where are failed requests retried?")`
  returns the enclosing symbol, its neighborhood and similar-but-unconnected
  code in one call: about 235 tokens, against about 61,800 for grep + read on
  httpx ([benchmark](#benchmark)).
- **See the blast radius of a change.** `graphlore_impact("Response")` lists
  what depends on a symbol, production code before tests, each row with
  `file:line`.
- **Read only the code you need.** `graphlore_fetch` returns a symbol's exact
  span under a token budget.
- **Know how far to trust an answer.** Results say how a name was matched, what
  a budget cut left out, and which files changed since the graph was built.
- **Local by default.** The analysis tools read local files and need no API
  key; `GRAPHLORE_OFFLINE=1` refuses every network path graphlore knows.

It works with any MCP client. Setup for Claude Code and Claude Desktop is below.

## Quick start

**Claude Code plugin** (needs [`uv`](https://docs.astral.sh/uv/)): installs the
server, graphlore's native engine and an exploration skill, with nothing to configure.

```
/plugin install graphlore --marketplace yasinyaman/graphlore
```

On Claude Code older than 2.1.275, run `/plugin marketplace add yasinyaman/graphlore`
and then `/plugin install graphlore@graphlore`.

**Any other MCP client**: install graphlore with its native engine, which builds
the graph (prebuilt wheels, no Rust toolchain), then register the server.

```bash
# graphlore is not on PyPI yet: install from GitHub
pip install "graphlore[engine] @ git+https://github.com/yasinyaman/graphlore"
```

```json
{
  "mcpServers": {
    "graphlore": {
      "command": "graphlore",
      "env": { "GRAPHLORE_PROJECT_DIR": "/absolute/path/to/your/project" }
    }
  }
}
```

- Claude Code: save this as `.mcp.json` in the project root (`"."` works as the path).
- Claude Desktop: add it to `claude_desktop_config.json`
  (macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`).

Optional extras, installed as `graphlore[extra]`: `engine` (graphlore's native
extractor, which builds the graph), `embed` (static code
embeddings for the built-in search behind `graphlore_locate`), `semble` (semble
as that search instead), `treesitter` (spans for non-Python
languages; without it the native engine's compiled-in grammars give them), `tiktoken` (exact token
counts), `watch` (re-sync on file changes). Until the first PyPI release, use
the GitHub form wherever a message says `pip install "graphlore[extra]"`:
`pip install "graphlore[semble] @ git+https://github.com/yasinyaman/graphlore"`.

**Then ask.** The assistant builds the graph once and explores from there
(output from the repository's small test fixture):

```text
graphlore_build(".")                 # once; graphlore_build(".", update=True) after changes
graphlore_overview()                 # size, hubs, subsystems, suggested next steps
graphlore_impact("Response", hops=1)

2 node(s) depend on Response (≤1 hops, direction=dependents):
  Client  (distance 1)  [returns]  def: httpx/_client.py:50
  DigestAuth  (distance 1)  [inferred]  def: httpx/_auth.py:120
```

A misspelled name gets a suggestion, not a guess:
`No node matching 'Clinet'. Did you mean: Client (httpx/_client.py:50)?`

**Or look at it.** Every build also writes `graphify-out/graph.html`, one file
that opens in any browser with no network: the communities first, then one
community or one symbol's neighbourhood, with search and a details panel. The
same page opens any graph.json, graphlore's or Graphify's, without graphlore
installed: [the viewer on GitHub Pages](https://yasinyaman.github.io/graphlore/viewer/)
reads the file you drop in your browser and uploads nothing
([demo](https://yasinyaman.github.io/graphlore/viewer/demo.html)). A node's page copies
the `graphlore_explain` / `graphlore_fetch` call for it, so what you find there goes
straight to the assistant, and the address bar keeps the view, so a link opens it again.
The page also shows what graphlore's tools find: import and call cycles, shared
state with no call path, surprising links and unreferenced code (**Findings**, `G`),
and a node's blast radius as `graphlore_impact` walks it (**Impact**, `I`).

## Tools

The server starts on the `lean` surface, 12 tools that cover the whole explore
loop (orient, find, traverse, blast radius, read the code). A small surface
helps a model pick the right tool.

| Tool | What it answers |
|---|---|
| `graphlore_overview` | Call first: size, most connected nodes, communities, suggested next steps |
| `graphlore_locate` | A question or a name → the enclosing symbol, its subgraph and hidden links (built-in search; better with `[embed]` or `[semble]`) |
| `graphlore_search` | Which nodes match this name or id |
| `graphlore_neighbors` | The direct neighbors of a node |
| `graphlore_subgraph` | The neighborhood around a node, under a token budget |
| `graphlore_impact` | Blast radius: what depends on a node, or what it depends on, and the chokepoints it funnels through |
| `graphlore_node_details` | A node's type, `file:line`, docstring and community |
| `graphlore_fetch` | A node's source code: the exact span, under a token budget |
| `graphlore_communities` | The subsystems (Leiden communities) of the codebase |
| `graphlore_freshness` | Is the graph stale against git: fresh, update or rebuild |
| `graphlore_build` | Build or update the graph with graphlore's native engine (graphify when the engine is not installed) |
| `graphlore_project` | Show or switch the project directory the server reads |

`GRAPHLORE_TOOLSET=full` exposes all 34: structure review (import and call
cycles, layer rules, bridges and cut points, dead-code candidates, same-named
symbols, duplication, PageRank and betweenness),
framework routes and package API usage, tests for a symbol, signatures, git
diffs, community naming, natural-language queries and paths between nodes. The Claude Code
plugin runs on `full`.

→ [Tool reference](https://github.com/yasinyaman/graphlore/blob/master/docs/tools.md)
(all 34 tools, resources, prompts) ·
[choosing a toolset](https://github.com/yasinyaman/graphlore/blob/master/docs/configuration.md#which-toolset)

## Benchmark

Tokens that reach the model to orient it in a code area, averaged over 6
queries on `encode/httpx` (2,101-node graph):

| Approach | Tokens | Calls | Graph structure | Semantic search | Hidden links |
|---|---|---|---|---|---|
| grep + read | 61,836 | ~14 | — | — | 0 |
| semble alone | 1,613 | 1 | — | ✓ | 0 |
| Graphify alone | 1,107 | 1 | ✓ | — | 0 |
| semble + Graphify, separately | 2,721 | 4 | ✓ | ✓ | 0 |
| **`graphlore_locate`** | **235** | **1** | ✓ | ✓ | **7** |

That is 263× fewer tokens than grep + read and 11.6× fewer than running the two
tools separately. Those 235 tokens are a map (`file:line` plus neighborhood),
not the code; the code is fetched only where needed. Across Python, JS/TS, Go,
Java, Rust and C++ repositories, 70–96% of semantic hits land inside the
resolved symbol's real span.

Every repository measured is an HTTP-client library and the baseline is a naive
grep, so treat the numbers as indicative.

The search behind `locate` was measured on its own on semble's published query
set (1,251 queries, 63 repositories, 19 languages, semble's scoring): the
built-in BM25 index, with no extra installed, reaches NDCG@10 0.814 against
semble's 0.852, with a median cold index of 0.6 s and a median query of 1 ms.
→ [Method, per-language results and caveats](https://github.com/yasinyaman/graphlore/blob/master/docs/benchmark.md)

## FAQ

### What is graphlore used for?

Giving an AI coding assistant a cheap, structural view of a repository:
onboarding to unfamiliar code, tracing how a feature works end to end, finding
what a change would break, and reviewing structure (cycles, layering, dead or
duplicated code).

### How is it different from Graphify's own MCP server?

graphlore builds its own graph (its native engine, in Rust), so it does not
need graphify installed, and it reads a graph Graphify built too. Graphify's embedded server has graph navigation,
token-budgeted subgraphs, an HTTP transport, multi-project serving and PR
triage tools graphlore lacks. graphlore adds the span engine and
what it makes possible: semantic `locate` joined to the enclosing symbol,
source hydration (`fetch`, `skeleton`), route and package-API extraction,
cosmetic-vs-structural freshness, and calls through a module object
(`mod.func()`) that the graph leaves out. Prefer one server per project.
→ [How graphlore works](https://github.com/yasinyaman/graphlore/blob/master/docs/concepts.md)

### Does it replace grep?

No. An exact identifier is found faster with `rg`. graphlore earns its place on
multi-hop questions (who calls this, what breaks if it changes) and on
orientation in unfamiliar code. Every answer carries `file:line`, so it can be
checked with a plain read.

### Is it an agent memory?

No. It has no remember or recall tools and keeps no notes between sessions. It
is a read-mostly view of a code graph; the only thing it persists is community
names.

### Does it need an API key or send my code anywhere?

The analysis tools read local files and need no key. A few paths can reach the
network: the first embedding search (`[embed]` or `[semble]`) downloads a model, the first use
of a language downloads its tree-sitter grammar (the native engine's grammars
are compiled in and download nothing), graphify, where it still builds the
graph, can call an LLM backend when you set its key, and `graphlore_add`
fetches a URL.
`GRAPHLORE_OFFLINE=1` refuses or restricts the ones graphlore controls; the
table linked below also lists what it cannot constrain.
→ [What reaches the network, and when](https://github.com/yasinyaman/graphlore/blob/master/docs/configuration.md#what-reaches-the-network-and-when)

### Which languages are supported?

The native engine builds the graph for Python, JavaScript, TypeScript/TSX, Go,
Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Ruby and PHP, and gives their
spans from its compiled-in grammars. Without the engine, Python works through
the standard library and the other languages tree-sitter covers need the
`[treesitter]` extra; without either, non-Python files fall back to
nearest-line matching. Package-API extraction reads all of
these; route extraction reads Python, JS/TS, Go, Java, Kotlin, C#, Rust, Ruby
and PHP frameworks
([tool reference](https://github.com/yasinyaman/graphlore/blob/master/docs/tools.md)),
and both say which files they did not read.
[What was measured in each language](https://github.com/yasinyaman/graphlore/blob/master/docs/benchmark-polyglot.md),
on 31 real repositories.

### Does graphlore_locate need semble or an embedding model?

No. graphlore has its own search index: one chunk per function, method or
class, ranked with identifier-aware BM25, so `graphlore_locate` works in a
default install and every hit lands on its graph node. The `[embed]` extra adds
static code embeddings, which match a question to code that uses other words;
with the `[semble]` extra, semble answers instead. Each reply's `backend` field
says which search answered.
→ [The search bridge](https://github.com/yasinyaman/graphlore/blob/master/docs/concepts.md#search-bridge-built-in-index-or-semble)

### Why do I see only 12 tools?

`lean` is the default surface. Set `GRAPHLORE_TOOLSET=full` for all 34.

### How does the graph stay up to date?

`graphlore_freshness` says whether to update or rebuild, and
`graphlore_build(".", update=True)` re-extracts changed files. Answers also flag
their own files that changed since the build. A git post-commit hook or the
opt-in watcher automates it.
→ [Keeping the graph fresh](https://github.com/yasinyaman/graphlore/blob/master/docs/concepts.md#keeping-the-graph-fresh)

### Was this project called graphify-mcp?

Yes. It was renamed because `graphifyy` ships a `graphify-mcp` command of its
own. The old `GRAPHIFY_*` environment variables still work.

## Known limits

- One active project at a time; `graphlore_project` switches it (stdio only).
- graphlore's own extractor (`engine/`, Rust, the `engine` extra) builds the
  graph: definitions, spans, imports, calls, bases and type references in 14
  languages, resolved from the source, with its own communities and incremental
  updates. Without it the `graphify` CLI builds it; graphify, and with it
  `graphlore_add` and `mode="deep"`, goes in the next release.
- No PR or review-thread tooling.
- Calls through an object, a callback or dynamic dispatch are not in the graph,
  so an empty answer means "no edge was indexed", not "nothing calls this".

## Documentation

- [Tool reference](https://github.com/yasinyaman/graphlore/blob/master/docs/tools.md): all 34 tools, resources, prompts, what a result carries
- [How graphlore works](https://github.com/yasinyaman/graphlore/blob/master/docs/concepts.md): resolved edges, import checks, analysis scope, freshness, the search bridge, naming communities
- [Configuration](https://github.com/yasinyaman/graphlore/blob/master/docs/configuration.md): toolsets, HTTP transport and security, offline mode, environment variables
- [Benchmark](https://github.com/yasinyaman/graphlore/blob/master/docs/benchmark.md): method, cross-language results, caveats
- [Native engine](https://github.com/yasinyaman/graphlore/blob/master/docs/native-engine.md): graphlore's own extractor in Rust, compared with graphify on 31 repositories, and the roadmap
- [Changelog](https://github.com/yasinyaman/graphlore/blob/master/CHANGELOG.md) · [Contributing](https://github.com/yasinyaman/graphlore/blob/master/CONTRIBUTING.md) · [Guidance for coding agents](https://github.com/yasinyaman/graphlore/blob/master/AGENTS.md)

## Development

```bash
pip install -e ".[dev,treesitter]"
ruff check .
mypy
pytest -q
```

Licensed under [MIT](LICENSE).
