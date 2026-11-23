# How graphlore compares

graphlore sits beside tools an agent may already have: a language server, the
client's own code search, and other MCP servers built on a code graph. This
page says what each is for, from the projects' own pages, read on
2026-11-23. Projects change quickly: each row names its source, and a claim we
could not read there is left out rather than guessed.

Back to the [README](../README.md) · [Concepts](concepts.md) · [Benchmark](benchmark.md)

## What graphlore is for

Questions that need the whole repository in one bounded reply: what a change
reaches several hops out (`graphlore_impact`, `graphlore review`), import and
call cycles, layer rules, bridges and cut points, which HTTP route reaches which
handler and which code calls it (`graphlore_routes`), which definitions read
and write the same state with no call between them, and finding code by what
it does joined to its graph node (`graphlore_locate`). Every reply has a token
budget and says what it left out; every edge says how it was found
(`EXTRACTED` from a scope, `INFERRED` by a name), and repository text is
framed as untrusted data.

What it does not do, on purpose: edit code, rename symbols, keep agent
memories, or run as a hosted service. It needs no API key, and
`GRAPHLORE_OFFLINE=1` refuses every network path it knows
([the network table](configuration.md#what-reaches-the-network-and-when)).
The coding agent itself still sends what it reads to its model: graphlore adds
no service of its own to that path, which is all it can promise.

## Beside a language server

A language server gives the compiler's answer for one symbol: its definition,
its references, its implementations, its call hierarchy. In Claude Code it is
the `LSP` tool, inactive until a code-intelligence plugin for the language is
installed (and inactive in cloud sessions)
([tools reference](https://code.claude.com/docs/en/tools-reference),
[code intelligence](https://code.claude.com/docs/en/plugins/code-intelligence)).
Where a question is about one symbol and precision matters, use it.

graphlore's edges come from name and scope rules applied to the source, not a
type checker, so a call through an object it cannot type, a callback or
dynamic dispatch can be missing. It is checked against the compilers where
they can be asked (`benchmarks/oracle_*.py`: TypeScript, Scala, Kotlin, Swift,
Go, Rust, C#; see [the native engine](native-engine.md)). What it adds is the
walk: a language server answers one hop per call, graphlore the blast radius,
the cycles or the routes of the whole repository in one.

## Beside built-in code search

Several clients index code for search themselves. graphlore's own
search (`graphlore_locate`) is a BM25F index over definitions with optional
static embeddings: on [semble](https://github.com/MinishLab/semble)'s query
set it scores NDCG@10 0.814 (BM25), against 0.852 for semble 0.7.3 measured
the same way and 0.855 in semble's own figures ([benchmark](benchmark.md)). Its point is not to beat a search engine but to
land every hit on its graph node, so the next question (who calls it, what it
breaks) is one call away.

## Other code-graph MCP servers

| Project | License | Built on | Notes from its page |
|---|---|---|---|
| graphlore | MIT | Rust engine (tree-sitter) + Python server | 14 languages; 34 tools (12 by default); `graphlore review` and a GitHub Action; no model, no network needed |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | MIT | C, tree-sitter | 17 MCP tools; 164 languages by its badge; "follow a call across services"; indexing and queries "need no hosted service or API key"; a paper, [arXiv:2603.27277](https://arxiv.org/abs/2603.27277) |
| [GitNexus](https://github.com/abhigyanpatwari/GitNexus) | PolyForm Noncommercial 1.0.0 | graph database | `analyze` "registers Claude Code hooks" and writes context files; a unified multi-repository graph is listed under its enterprise offer |
| [Serena](https://github.com/oraios/serena) | GPL-3.0-or-later (its SolidLSP part MIT) | language servers (or a paid JetBrains backend) | symbol-level reading **and editing** (replace a symbol's body, insert, safe delete) and a memory system |
| [code-review-graph](https://github.com/tirth8205/code-review-graph) | MIT | tree-sitter + SQLite | "shows what a change can break" with a risk score; a composite GitHub Action with a comment updated on each push and an optional `fail-on-risk` gate |
| [Claude Context](https://github.com/zilliztech/claude-context) | MIT | hybrid BM25 + dense vectors | embeddings from OpenAI, VoyageAI, Ollama or Gemini; a vector database, Milvus or Zilliz Cloud (its quick start uses Zilliz Cloud) |
| [Graphify](https://github.com/Graphify-Labs/graphify) | Apache-2.0 ([`graphifyy` on PyPI](https://pypi.org/project/graphifyy/)) | tree-sitter | the CLI graphlore used to build graphs with; graphlore still reads its `graph.json` (deprecated, see the [roadmap](native-engine.md)) |

Where graphlore differs from most of them: its replies are budgeted and say
what they cut; its edges carry their confidence; it reads shared state and
HTTP routes into the graph; its review is deterministic (no model, no risk
score from a model); and it is MIT with no service behind it. Where others go
further: more languages (codebase-memory-mcp), editing and memory (Serena),
multi-repository graphs (GitNexus's enterprise offer), and compiler precision
(any language server).

## Measured, and not yet

graphlore's [benchmark](benchmark.md) measures what a tool returns (tokens,
retrieval quality, correctness of edges against compilers). Whether an agent
solves more tasks with it is [a separate evaluation](benchmark-agents.md),
with a baseline arm without graphlore; it has not been run yet.
