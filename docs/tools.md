# graphlore tool reference

All 33 MCP tools, the resources and the prompts. The default `lean` surface
registers 12 of them: `build`, `overview`, `locate`, `search`, `neighbors`,
`subgraph`, `impact`, `node_details`, `fetch`, `communities`, `freshness` and
`project`. The rest need `GRAPHLORE_TOOLSET=full`.

Back to the [README](../README.md) · [Concepts](concepts.md) ·
[Configuration](configuration.md) · [Benchmark](benchmark.md)

## Tools

See [Which toolset](configuration.md#which-toolset) for choosing a surface.

CLI-backed (the first two write state; the rest are read-only; all outside lean):

| Tool | Purpose |
|---|---|
| `graphlore_build` | Build/update the graph (`update`, `cluster_only`, `code_only`, `mode="deep"`). Returns the build's counts and warnings (`verbose=True` for the full CLI log), says so when the graph landed outside the active project (graphify writes `graphify-out/` inside the folder it reads), and notes vendored/minified files and a `graphify-out/` that git neither ignores nor tracks. When graphify refuses a default build for want of an LLM key (the folder holds docs), it runs again with `code_only` and says so |
| `graphlore_add` | Add a source by URL (arXiv, tweet) |
| `graphlore_query` | Natural-language query (`dfs`, `budget`) |
| `graphlore_path` | Exact path between two nodes; each end may be a bare name, resolved like everywhere else (the reply says which node was used) |
| `graphlore_explain` | Everything about a node; a bare name several nodes share is resolved before graphify sees it |

Project selection:

| Tool | Purpose |
|---|---|
| `graphlore_project` | Show the directory the server reads, or switch it to another one — a second repository, or one just built with `graphlore_build("/abs/path")`. Changes no file; refused under `GRAPHLORE_RESTRICT_PATHS` (always on over HTTP). Also names the server version |

graph.json analysis (read-only, no CLI needed; the payload arrives as `structuredContent`, and `as_json=True` puts it in the text too):

| Tool | Purpose |
|---|---|
| `graphlore_overview` | **Call first** — size, god nodes, communities, surprises, suggested next steps, and the server version |
| `graphlore_god_nodes` | Most connected nodes, ranked inside the [scope](concepts.md#analysis-scope) |
| `graphlore_communities` | Leiden community summaries: the `limit` largest, each with its `size`, its `members` most connected first (`members` of them; the rest counted in `members_left_out`) and its `name` when it has one — set with `graphlore_set_labels` / `graphlore_label_communities`, else the one graphify wrote |
| `graphlore_surprises` | Unexpected cross-file connections — listed from the graph's own flags when it has them, otherwise **computed** by scoring cross-file edges (confidence, file-type/directory/community crossing), with resolver noise, anything test code uses (the source under test, shared test fakes) and consumers — a directory or a script nothing depends on, using the package — scored down, as is a pair of directories that many edges already join (an adapter using the application layer is the architecture); an inferred edge into a module the source file imports scores like an extracted one, and one into a module it never imports is dropped; says plainly when nothing is computable rather than printing an empty list |
| `graphlore_search` | Node search by name or id; `total` / `truncated` say how many matched beyond `limit` |
| `graphlore_neighbors` | 1-hop neighbors of a node, production code before tests; takes `scope`. Inferred edges and edges graphlore resolved from the source are marked |
| `graphlore_subgraph` | Token-budgeted BFS subgraph around a node — the cheap way to feed the model just the relevant slice (Graphify's embedded server does this too; pairing it with `fetch`/`locate` is what's specific here). Walks production code before tests, so a hub's budget goes to its real callers; takes `scope`, and a truncated result says how many direct neighbors were left out and how many were tests |
| `graphlore_impact` | Reverse-dependency / **blast radius** — what breaks if a node changes (`direction=dependents`/`dependencies`/`both`), filterable by relation (`relations="calls"`, `"code"`, `"imports"`, `"types"`) and by `scope`. Ordered by hop distance, production code before tests; each row carries the recorded reference site and the edge's confidence, and `strict=True` walks only extracted edges. A truncated result says how many rows were dropped and how many were tests, and sets `complete: false`; `beyond_hops` says separately what one more hop would add. Docstring/rationale nodes are left out and counted |
| `graphlore_tests_for` | **Tests for a symbol** — tests that call it directly (with the call line), tests that reach it through one caller (a production function, or a helper/fixture in the test code), and weak matches kept apart (a test file that imports its module, a test named after it). Takes `scope`. Rust tests inside a source file (`#[cfg(test)] mod tests`, `#[test] fn`) count as tests |
| `graphlore_node_details` | Node metadata: type, source file/line, docstring, community. The docstring is framed as repository text |
| `graphlore_skeleton` | def/class **signatures** (decorators kept, bodies stripped) for a file/node/community — the middle layer between the map and full code. A budget cut names the files left out |
| `graphlore_fetch` | **Token-budgeted** source hydration — reads the real code for a node (its enclosing def/class span ± context), the map→code other half of `subgraph`/`locate`. Names past the budget come back in `not_fetched`, missing ones with `did_you_mean`; each entry says `file_changed_since_build` |
| `graphlore_freshness` | Is the graph stale vs. git HEAD? Returns `recommended_action` (fresh/update/rebuild) + `reason` — lingering phantom nodes / large changes steer to a rebuild; junk files (`.DS_Store`, logs) land in `non_source_changes` and never hold the graph stale |
| `graphlore_diff` | Structural changeset between two git refs (default `HEAD~1..HEAD`) — added/removed/renamed/modified, with cosmetic-only changes separated (file-level, for review/audit) |
| `graphlore_prune` | Drop phantom nodes (and their edges) for deleted/renamed source files — the surgical alternative to a full rebuild (`dry_run=True` to preview) |
| `graphlore_validate` | Lint the graph for dangling/duplicate/self-loop edges and orphan nodes (read-only). Recursion is counted apart, not as an issue. The one result that lists every vendored/minified file (`vendored_files`) |
| `graphlore_duplication_scan` | **Repo-wide** duplication audit in two tiers. **Identical bodies** (exact, no model, always runs): function and method bodies hashed after normalizing whitespace, comments and docstrings, reported as groups — the small helper copied into three modules. **Hidden links** (similarity, on semble or the built-in index): definitions that are similar but structurally distant, each pair with its score and both `file:line`s; `score_kind` says what the score is (embedding cosine, or tf-idf cosine on the built-in BM25-only index), pairs under the floor (0.7 on embeddings, 0.5 on tf-idf) are left out by default (`min_score=0` lists them), and test ↔ source pairs need `include_tests=True`. On the built-in index each seed is anchored on its own definition's chunk. Outside lean |
| `graphlore_cycles` | Circular dependencies **by kind**. Import cycles between modules come first, each import read back from the source and classified: **runtime** cycles (a loop of module-level imports — the ones that fail at start-up), then **deferred** (closes through an import inside a function) and **type-only** (closes under `if TYPE_CHECKING:`). Then call cycles, then the rest — each with one concrete loop. `kind="imports"` asks for one kind; recursion is listed apart |
| `graphlore_layers` | **Layer rules** — give `{"layer prefix": ["prefixes it may import"]}` (inline, or a JSON file in the repository) and get the import edges that break them, grouped by layer with `file:line` and when the import runs (module-level / `TYPE_CHECKING` / inside a function). An import the source contradicts — `import yaml` linked to a first-party `yaml.py` — is not checked and is listed apart. The same check as an AST-based dependency test, read from the graph. Takes `scope` |
| `graphlore_unreferenced` | **Dead-code candidates** — functions, methods and classes with no incoming graph reference, not registered by a decorator (routes, click/typer commands, validators, fixtures), and not mentioned anywhere else in the repository's text. Another definition of the same name and its uses are not mentions, so the unused one of two same-named classes is still found. Each with `file:line` and a mention count; a lower bound on what is alive, not proof of death |
| `graphlore_same_names` | Classes and top-level functions that **share a name across modules** (within one language; closures are not compared), each copy with how many modules import it and whether its own file uses it — the model-free companion of the duplication scan's identical-bodies tier |
| `graphlore_package_apis` | **Symbol-level external API surface** — which names each external package is actually used for (`fastapi: Depends, APIRouter`), with qualified paths (`numpy.linalg.norm`) for version-diff audits; a lower bound (dynamic/star/getattr use is invisible). Python via stdlib ast and JS/TS, Go, Java from use sites; Kotlin, Scala, C#, Rust, Swift, C/C++, Ruby and PHP from their imports (`[treesitter]`). The project's own packages are left out per language: Python and Go modules, the packages and namespaces its JVM/.NET/PHP files declare, its crates, gems, Swift targets and header directories (`first_party_skipped`). Source files in a language with no reader are counted in `not_read`. Takes `scope` (`-tests` for what production code uses) |
| `graphlore_routes` | **Framework route → handler table** — which URL patterns hit which code, joined back to graph nodes (`GET /items/{id} -> read_item (app.py:5)`). Python: FastAPI/Flask/Sanic/Quart/Litestar/Django. JS/TS: Express-style (labelled express/fastify/koa/restify/polka by the import; also `router.route('/x').get(h)`, and in a file that imports no framework a receiver named like a router or passed in as a parameter, when some file of the project imports one), NestJS. Go: gin/echo/chi/gorilla/net-http (Go 1.22 `"GET /x"`, gin 3-arg `Handle`, chi nesting). Java: Spring, JAX-RS. Kotlin: Spring, Ktor. C#: ASP.NET Core controllers and minimal APIs (`MapGet`, `MapGroup`). Rust: axum, actix-web. Ruby: Rails `routes.draw` (`resources`, `namespace`, `scope`), Sinatra. PHP: Laravel `Route::` chains, Symfony `#[Route]`. A lower bound: prefixes applied in another file and dynamic registration are invisible. Python via stdlib ast; the rest need `[treesitter]`. Takes `scope`; test code is left out by default (`+tests` for a test's throwaway server). Source files in a language with no route reader are counted in `not_read` |

Semantic naming (uses the **host model via MCP sampling** — no API key — or a backend key):

| Tool | Purpose |
|---|---|
| `graphlore_sampling_status` | Capability test: reports whether the client supports host-LLM sampling, whether a backend key is set, and which method will be used |
| `graphlore_label_communities` | Give Leiden communities human-readable names. `method="auto"` (sampling → key → placeholder), `"sampling"`, `"cli"`, or `"placeholder"` |
| `graphlore_set_labels` | Persist **assistant-provided** community names (sampling-free fallback) to `.graphify_labels.json` and patch them into `graph.html` |

Search bridge (code search joined to graph structure; semble when installed, else the built-in index — see [the search bridge](concepts.md#search-bridge-built-in-index-or-semble)):

| Tool | Purpose |
|---|---|
| `graphlore_locate` | NL query → enclosing graph node → token-budgeted subgraph, **plus hidden links**: semantically-similar code that is structurally disconnected (duplication / missing-abstraction candidates) — the `semantic_cousins` with `linked: false`, listed first and counted in `hidden_link_count`. A question often has two homes, so two lists follow the seed: `calls_into` (what the seed calls in other files, most specific first — the implementation behind a thin tool) and `also_matched` (the other top hits by the definition they fall in; hits on a file's docstring-and-imports header are counted, not listed). `backend` says what answered: `name` (`builtin` / `semble` / `custom`) and, for the built-in index, `mode` (`hybrid` or `bm25`), `tiers`, the files and chunks indexed, what was skipped, and `dense_unavailable` when the embedding tier could not load |

## Typical workflow

1. `graphlore_locate("where do we retry failed requests?")` — one-call orientation
   (or `graphlore_overview()` → `graphlore_subgraph("SomeNode")` to start from the map)
2. `graphlore_fetch(["Client._send_single_request"])` — hydrate exactly the code you zeroed in on
3. `graphlore_impact("Response")` — blast radius of a change; with `GRAPHLORE_TOOLSET=full`,
   `graphlore_cycles()` / `graphlore_routes()` for targeted analysis
   (reviewing a codebase: `graphlore_validate` → `graphlore_cycles` → `graphlore_layers` →
   `graphlore_unreferenced` → `graphlore_same_names`; the `review` prompt scripts it)
4. `graphlore_query("how does the auth flow work?")` — free-form questions via the CLI (`full`)
5. After code changes: `graphlore_freshness()` → `graphlore_build(update=True)`
   (plus `graphlore_prune()` after deletes/renames)

## Resources

- `graphlore://report` — GRAPH_REPORT.md
- `graphlore://graph` — graph.json (raw)
- `graphlore://community/{id}` — per-community wiki (members + internal/boundary edges)

## Prompts

Reusable templates that orchestrate the tools for the assistant:

- `onboard` — orient to the codebase (overview → communities → subgraphs → surprises → summary)
- `trace_bug(symptom)` — find likely root-cause locations through the graph
- `explain_flow(flow)` — end-to-end walkthrough of a named flow with file:line refs
- `review` — review structure: cycles, layering, dead and duplicated code (needs `GRAPHLORE_TOOLSET=full`)

Each prompt is rendered for the active toolset: a step whose tool is switched off
is left out, or replaced by one that is on (`trace_bug` uses `graphlore_impact`
where `graphlore_path` is off).

## What a result carries

- **Tool annotations** (`read_only_hint`, `destructive_hint`, titles) tell the model which tools are safe to call freely vs. which mutate state.
- **Server instructions** describe the recommended flow (locate/overview → targeted subgraph/fetch → build update).
- **`as_json` output** on every analysis tool — including error and no-match paths — returns structured data the model can chain on instead of re-parsing prose.
- **MCP structured results.** Over the protocol every analysis tool's payload also arrives as `structuredContent` (the same object `as_json=True` prints), whichever text form was asked for, and failures — node not found, missing graph, a failed CLI call — set `isError: true`. An empty but valid answer (no search hits) is not an error. No `outputSchema` is published: payload shapes differ per path. `GRAPHLORE_STRUCTURED=0` falls back to plain text results.
- **Token budgeting on source, not just structure** — budgeted subgraph rendering is Graphify's own (its embedded server does the same); graphlore extends the discipline to *code*: `graphlore_fetch` and `graphlore_skeleton` hydrate real spans under a cap, so escalating map → signatures → source never blows the context.
- **A cut says how to get the rest** — a result that reports `truncated` carries a count of what a limit or budget dropped and a one-line `hint` naming the argument to raise or the filter to add.
- **Unambiguous names** — when several nodes share a bare label (five `.auth_flow()`s across auth classes), rendered output qualifies them with the span-recovered FQN (`DigestAuth.auth_flow()`) or a `file:line` suffix, so an arrow always names exactly one symbol.
- **Repository text is framed as data.** `graphlore_fetch`, `graphlore_skeleton`, `graphlore_node_details` (the docstring) and the `query` / `path` / `explain` passthroughs put the code and text they quote between `=== repo text <nonce> ===` markers, with a per-call random nonce redrawn if it occurs in the text, so a comment in the repository cannot close the frame and pose as graphlore. JSON replies say `content_trust: "untrusted_repo_text"` and name the `untrusted_fields`. A docstring that says "ignore previous instructions" is still a docstring.
- **Host-LLM sampling** (`graphlore_label_communities`) lets the server borrow the client's model via MCP `sampling/createMessage`, so semantic naming works with no server-side API key — with a capability test (`graphlore_sampling_status`) and a backend-key fallback.
