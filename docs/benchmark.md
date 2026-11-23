# graphlore benchmark

How many tokens and calls it takes to orient an AI coding assistant in a
codebase: `graphlore_locate` against grep + read, semble alone, Graphify alone
and the two run separately.

Back to the [README](../README.md) · [Tool reference](tools.md) ·
[Concepts](concepts.md) · [Configuration](configuration.md)

This page measures one tool, `graphlore_locate`, on six libraries, and then
[the search behind it](#search-quality-built-in-index-and-semble) on 63. For every
tool on 31 repositories in 14 languages — what works in each language and what
does not — see the [polyglot audit](benchmark-polyglot.md).

## Results on httpx

Averaged over **6 queries** spanning httpx subsystems (send path, digest auth,
redirects, content decoding, cookies, timeouts) on the 2,101-node graph. Each query
orients an agent to a code area; *tokens* = what reaches the model's context
(≈ chars/4).

> **How tokens are counted here vs. at runtime.** The benchmark divides by 4;
> the server's own budgets and `approx_tokens` divide by 3.5 (deliberately
> conservative), or count exactly with `GRAPHLORE_TOKENIZER=tiktoken`. A runtime
> figure therefore reads about 14% higher than the same text would here. The
> ratios between approaches are unaffected, since both sides use the same
> divisor; but the grep side counts file *bytes* and the locate side reply
> *characters*, which slightly flatters grep on non-ASCII source. The numbers
> were not re-measured for this note.

![Tokens to orient an agent across 6 httpx queries — lower is better](benchmark.svg)

| Approach | Tokens (avg) | Calls | Structure | Semantic | Hidden links |
|---|---|---|---|---|---|
| Naive grep + read | 61,836 | ~14 | — | — | 0 |
| semble alone | 1,613 | 1 | — | ✓ | 0 |
| graphify alone | 1,107 | 1 | ✓ | — | 0 |
| semble + graphify (separately) | 2,721 | 4 | ✓ | ✓ | 0 |
| **`graphlore_locate`** | **235** | **1** | ✓ | ✓ | **7** |

`graphlore_locate` averages **263× fewer tokens than grep+read** and **11.6× fewer
than running semble and graphify separately** (one call instead of four) — and it's
the only approach that surfaces hidden links (semantically similar but structurally
disconnected code), 5–10 per query.

Those ~235 tokens are a navigational *map* (seed `file:line` + structural
neighborhood + hidden links), not raw code — you fetch the specific code only where
needed. That's the trade graphlore optimizes: cheapest orientation plus the
cross-check signal, then drill in precisely. The `graphify alone` row is its own token-budgeted subgraph output — the gap to 235 is the semantic seed (landing on the right node instead of a degree-sorted expansion), not a budgeting difference.

**Case study — the hidden links are real.** Asked *"does httpx duplicate
request-sending across sync and async?"*, `graphlore_locate` returned the seed
`Client._send_single_request` and flagged hidden links. Checking the source
confirmed every production flag is a genuine sync/async twin:
`Client._send_single_request` (`_client.py:1001`) ↔ `AsyncClient._send_single_request`
(`:1717`); `BaseTransport.handle_request` ↔ `handle_async_request` (in every
transport); `__enter__` ↔ `__aenter__`. ~500 tokens (one `locate` + a targeted read)
surfaced a real architectural pattern that naively reading `_client.py` (~16k tokens)
would. The far-distance bucket also held test files (related, not refactor targets) —
the `distance` field separates production parallels (3–4) from that noise.

**Across languages — real HTTP-client repos.** The span join and freshness check aren't
Python-only. I built AST-only graphs for an HTTP client in five more languages and ran the
same kind of queries (send · redirects · timeout/retry · headers/auth · transport):

![Span-join precision across languages — Python 96%, Go 93%, JS/TS 89%, Java 85%](benchmark-multilang.svg)

| Language | Repo | Span-join precision | Qualname | Hidden / q | locate vs grep | Calls (locate vs naive) |
|---|---|---|---|---|---|---|
| **Python** (ast) | `encode/httpx` | **96%** (52/54) | 67% | 3.2 | 272× | 1 vs 15 (0 vs 14 reads) |
| JavaScript / TS | `sindresorhus/got` | 89% (48/54) | 67% | 2.3 | 583× | 1 vs 9 (0 vs 8 reads) |
| Go | `go-resty/resty` | 93% (50/54) | 100% | 1.8 | 911× | 1 vs 17 (0 vs 16 reads) |
| Java | `square/retrofit` | 85% (46/54) | 50% | 2.3 | 217× | 1 vs 18 (0 vs 17 reads) |
| Rust | `algesten/ureq` | 70% (38/54) | 83% | 3.7 | 577× | 1 vs 22 (0 vs 21 reads) |
| C++ | `libcpr/cpr` | 72% (39/54) | 100% | 4.3 | 195× | 1 vs 16 (0 vs 15 reads) |

Python uses the stdlib `ast`; JS/TS · Go · Java · Rust · C++ go through tree-sitter with
automatic language detection — **one tool, zero per-language config**. *Span-join precision* =
share of semantic hits landing inside the resolved symbol's real span (any overload of it —
C++ collapses same-name overloads into one graph node while each keeps its own span; cpr's
`Session::SetOption` has 46). It's **70–96%** across six 350–2,095-node graphs, hidden-links
keep surfacing 2–4/query, and locate stays **195–911× cheaper** than grep+read. Orientation is
also **one tool call with zero file reads** by construction, where the grep-driven baseline
spends 9–22 calls opening 8–21 files per query — **89–95% fewer calls**, on the same grep
baseline as the token numbers. Rust and C++
trail at 70–72% — their misses are mostly file-top/whole-file chunks and namespace-level free
functions where the resolution is still correct (they recover qualified names at 83–100%).
`graphlore_freshness`'s cosmetic-vs-structural check is correct in every language too
(comment/reformat → cosmetic; operator/rename → structural). Re-measured 2026-08 on the MCP v2
SDK, Python 3.14, fresh repo HEADs. Reproduce with
[`benchmarks/multilang.py`](../benchmarks/multilang.py) (`--json` persists a run;
[`benchmarks/results-multilang.json`](../benchmarks/results-multilang.json) is the committed
record of the call/token baseline — its span-join counts predate the overload-family
re-count above).

→ **[Full benchmark report](https://htmlpreview.github.io/?https://github.com/yasinyaman/graphlore/blob/master/docs/benchmark.html)** (interactive HTML, per-query breakdown + the cross-language tables) — or open [`docs/benchmark.html`](benchmark.html) locally. ([Türkçe](https://htmlpreview.github.io/?https://github.com/yasinyaman/graphlore/blob/master/docs/benchmark.tr.html))

<sub>httpx headline measured 2026-06 with semble 0.3.4 (6 queries, per-query locate 189–286
tokens); cross-language table re-measured 2026-08 with semble 0.5.5 + the tree-sitter span
backend — 6 queries × 54 hits each on `httpx` / `got` / `resty` / `retrofit` / `ureq` / `cpr`,
call counts from the same run. **Sample bias:** every repo benchmarked here is
an HTTP-client library — a deliberately uniform family chosen for cross-language comparability.
Token savings and span-join precision will differ on other architectures (data pipelines, GUI
apps, sprawling monorepos), so treat these as indicative, not guarantees. Numbers vary by
codebase and query.</sub>

## One call beats running semble and graphify separately

semble finds **what's relevant**; graphify gives **how it connects**. They're complementary — but stitching them by hand means four calls, ~2.7k tokens, and manually aligning semble's line ranges to graph nodes. graphlore does that join *for* you, in one call:

| _per query_ | semble alone | graphify alone | both, by hand | **`graphlore_locate`** |
|---|:-:|:-:|:-:|:-:|
| Semantic search | ✓ | — | ✓ | ✓ |
| Graph structure | — | ✓ | ✓ | ✓ |
| Chunk → symbol join | — | — | _you wire it_ | **✓ automatic** |
| Hidden-links cross-check | — | — | — | **✓ only here** |
| Calls | 1 | 1 | **4** | **1** |
| Tokens to orient | 1,613 | 1,107 | 2,721 | **235** |

→ **11.6× fewer tokens than running the two separately — in a single call**, and hidden links (semantically similar code that is *structurally disconnected*) is a signal *neither tool produces alone*. So the combined tool isn't just convenience: it's cheaper, and it surfaces something the parts can't.

## Search quality: built-in index and semble

`graphlore_locate` runs on semble when it is installed, else on graphlore's own
index ([how it works](concepts.md#the-built-in-index)). This section measures the
search alone — which chunks come back for a query — on the query set semble
publishes for its own benchmark: 1,251 queries over 63 repositories in 19
languages (703 semantic, 344 architecture, 204 symbol), with the files that
answer each one. The queries and labels were written and checked by an LLM
(Claude Sonnet 4.6) for semble's authors, and semble's ranking was developed
against them.

Scoring is semble's own (`benchmarks/run_benchmark.py` at v0.7.3): the top 10
chunks per query; a target file counts at the rank of the first chunk inside
it; binary relevance over primary and secondary targets; NDCG@10 averaged per
repository, then per language, then over languages. semble scores 0.852 here
against the 0.855 it publishes, so the harness reproduces its setup.

| System | NDCG@10 | held-out 44 | recall@10 | semantic | architecture | symbol | index (median) | query p50 (median) |
|---|---|---|---|---|---|---|---|---|
| **built-in, BM25** (no extra) | **0.814** | 0.799 | 0.952 | 0.802 | 0.785 | 0.901 | 630 ms | 1.1 ms |
| built-in, hybrid (`[embed]`) | 0.809 | 0.797 | 0.959 | 0.809 | 0.757 | 0.901 | 1,120 ms | 1.6 ms |
| semble 0.7.3 | 0.852 | 0.842 | 0.967 | 0.847 | 0.808 | 0.947 | 708 ms | 2.3 ms |
| _semble's published figures_ | _0.855_ | | | | | | | |
| _its BM25 alone, its BM25 + its ranking_ | _0.686, 0.838_ | | | | | | | |

The ranking constants (fusion weights, per-file decay, file coherence, the
exact-name bonus) were chosen on 19 of the repositories, the first of each
language in semble's `repos.json`; "held-out 44" is the mean over the other 44,
which played no part in the choice. Index time is a cold build (the span engine
parses every file); query p50 is the median over a repository's queries on a
warm index, then the median over repositories. 4 CPU cores, no GPU, measured
2026-11-18 with semble 0.7.3 and model2vec 0.10.0.

**What this says.**

- The BM25 tier, with no model and no extra, is within 0.04 of semble and well
  above BM25 run plainly (semble's own BM25 ablation: 0.686). The gain comes
  from what graphlore already knows: one chunk per definition, the definition's
  name as its own field, the file path, and a prior that ranks tests and
  examples below the implementation.
- **On this query set the dense tier does not pay for itself**: hybrid and BM25
  are level overall. It helps semantic queries (0.809 against 0.802) and some
  languages (C++ 0.86 against 0.79, JavaScript 0.90 against 0.85), and hurts
  others (PHP 0.75 against 0.86, bash 0.81 against 0.85). semble's lead is in
  ranking more than in the embedding: the same model, fused differently.
- Symbol lookups (`Engine`, `Client._send`) are where semble is furthest ahead
  (0.947 against 0.901).
- A cold index of a large repository is slow in both: the slowest builds were
  18 s (BM25) and 21 s (hybrid) against semble's 18 s; most of graphlore's time
  is the span engine's tree-sitter walk, which later tools reuse. The index is
  then kept and refreshed per file, so this is paid once per session. These
  index times were measured before the span walk was made 3–5× faster
  (CHANGELOG, 0.7.0); the built-in index builds faster now than this table
  says, and its rankings are unchanged, since the spans are the same.
- The graph overlay (docstrings, call-neighbour names) is not measured here: the
  benchmark runs on the repositories without building a graph of them.

Per language, NDCG@10 (BM25 / hybrid / semble):

| Language | BM25 | hybrid | semble | Language | BM25 | hybrid | semble |
|---|---|---|---|---|---|---|---|
| bash | 0.85 | 0.81 | 0.84 | lua | 0.84 | 0.83 | 0.84 |
| c | 0.72 | 0.71 | 0.75 | php | 0.86 | 0.75 | 0.86 |
| cpp | 0.79 | 0.86 | 0.88 | python | 0.86 | 0.84 | 0.88 |
| csharp | 0.84 | 0.82 | 0.87 | ruby | 0.75 | 0.78 | 0.91 |
| elixir | 0.84 | 0.85 | 0.91 | rust | 0.79 | 0.78 | 0.83 |
| go | 0.85 | 0.87 | 0.91 | scala | 0.85 | 0.85 | 0.92 |
| haskell | 0.76 | 0.78 | 0.78 | swift | 0.80 | 0.77 | 0.85 |
| java | 0.78 | 0.78 | 0.83 | typescript | 0.76 | 0.72 | 0.73 |
| javascript | 0.85 | 0.90 | 0.91 | zig | 0.88 | 0.87 | 0.90 |
| kotlin | 0.77 | 0.78 | 0.80 | | | | |

**Duplication floors.** `--calibrate` takes 719 definitions from the 19 tuning
repositories and scores each against a near-clone of itself (a fifth of its
lines dropped, three identifiers renamed) and against an unrelated definition
of a similar size in another file. On the embedding cosine, 0.7 keeps 89% of the
clones and 0.7% of the unrelated pairs; on the BM25 tier's tf-idf cosine, 0.5
keeps 86% of the clones and 0.3% of the unrelated pairs. Those are the
duplication scan's default floors on the built-in index.

Reproduce (a venv with `graphlore[treesitter,embed]` and `semble==0.7.3`):

```bash
python benchmarks/search.py --setup --root /tmp/searchbench          # semble's labels and the 63 pinned repositories (1.4 GB)
python benchmarks/search.py --root /tmp/searchbench --json benchmarks/results-search.json
python benchmarks/search.py --root /tmp/searchbench --calibrate --repos aiohttp,axios,gin
```

The labels are fetched from semble's repository at the pinned tag, never copied
here; `benchmarks/results-search.json` holds every per-repository row.
