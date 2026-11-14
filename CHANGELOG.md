# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Fifth round of feedback: 0.5.0 re-run on the same three repositories.

### Changed
- Scoped results summarize the vendored/minified files they left out instead
  of listing twenty paths on every call: `scope.left_out` keeps
  `vendored_nodes` and `vendored_file_count`, gives three example paths in
  `vendored_files` and the directories holding most of them in
  `vendored_dirs`. `graphlore_validate` returns the full list
  (`vendored_files`) and counts it in its text.
- `graphlore_impact`'s `complete` is false only when the budget dropped rows
  within the hops asked for. A walk that listed everything within `hops` is
  complete even when nodes lie further out; `beyond_hops` and the hint say
  separately what `hops + 1` would add.
- `graphlore_locate` lists each semantic cousin once, in `semantic_cousins`
  with `linked`: hidden links (`linked: false`) first, nearest first, then
  the connected ones. The `hidden_links` list, which repeated the same
  entries, is replaced by `hidden_link_count`. Header chunks the answer
  skips are marked `header: true` in `semantic_hits`.

### Fixed
- `graphlore_surprises` ranked a guessed edge from a file that imports nothing
  of the project at the top: an `indirect_call` from a module importing only
  the standard library, into an unrelated class in another layer. The graph
  records no import edge for such a file, and the name-guess rule only judged
  files it had import edges for. A file without recorded imports is now
  judged from its own import statements (`ImportChecker.reaches`: Python via
  `ast`, JS/TS via tree-sitter); when none can mean the target the edge is
  dropped and counted under `inferred_unimported`. A file whose source cannot
  be read is still not judged.

## [0.5.0] - 2026-11-14

An agent's-eye review: results that say how they were reached, what they
left out and whether their files changed; real MCP structured results; an
offline switch; and a smaller default tool surface.

### Added
- Tool results carry MCP `structuredContent` and `isError`. The payload a tool
  builds for `as_json=True` is now sent as `structuredContent` on every call,
  text or JSON, and errors (node not found, missing graph, a failed CLI call,
  `graphlore_fetch` with every name missing, a failed `git status` in
  `graphlore_freshness`) set `isError: true`. Tool functions still return a
  string; `GRAPHLORE_STRUCTURED=0` restores plain-text results.
- A name that matches nothing comes back with `did_you_mean`: the closest
  node names (stdlib `difflib`, pool bounded on large graphs), in the text and
  in JSON next to `code: "not_found"`. `graphlore_fetch` lists them per
  missing name.
- Every budget or limit cut says what it left out. `graphlore_fetch` names the
  requested nodes it did not fetch (`not_fetched`); `graphlore_skeleton` names
  the files left out; `graphlore_locate` counts the seed's direct neighbors left
  out of the structure and the callees past the five in `calls_into`;
  `graphlore_search`, `graphlore_god_nodes`, `graphlore_surprises` and
  `graphlore_package_apis` report `total` and `truncated`;
  `graphlore_freshness` reports the real length of each capped list in
  `counts`; the `graphlore://community/{id}` resource says when it shows 50 of
  more boundary edges. Each adds a one-line `hint`.
- `graphlore_impact` reports `beyond_hops` (nodes one hop past `hops`) and
  `complete`, false whenever the budget or the hop limit cut the answer.
- Answers say which of their files changed since the graph was built.
  `graphlore_fetch` entries carry `file_changed_since_build` (true / false /
  null) and `span_mismatch` when the graph's line in a changed file now lands
  in a different symbol — before, fetch silently returned that other symbol.
  `graphlore_locate`, `graphlore_impact` and `graphlore_subgraph` carry
  `build_drift` (`known`, `since`, `changed_files`) and a text line naming the
  changed files. It compares the recorded `built_at_commit` with the working
  tree in two git calls; without that commit or git it reports
  `known: false` and why.
- `graphlore_neighbors`, `graphlore_subgraph` and `graphlore_locate`'s
  structure mark inferred / ambiguous edges (`confidence`) and edges graphlore
  resolved from the source (`resolved_by`), as `graphlore_impact` already did.

- `GRAPHLORE_OFFLINE=1` refuses what graphlore knows reaches the network:
  `graphlore_add`, `graphlore_build(mode=...)` and
  `graphlore_label_communities(method="cli")` (which `auto` then skips). A
  default build is forced to `--code-only`, the graphify subprocess loses the
  LLM backend keys from its environment, `HF_HUB_OFFLINE` /
  `TRANSFORMERS_OFFLINE` are set before semble loads, tiktoken is not loaded
  and only cached tree-sitter grammars are used. The boot banner prints
  `egress=offline` or `egress=on-demand`, and the README has a table of every
  network path and what the switch does to it, including what it cannot
  constrain (the graphify binary itself, a custom semantic backend).
- Repository text in replies is framed as data. `graphlore_fetch`,
  `graphlore_skeleton`, `graphlore_node_details` and the `query` / `path` /
  `explain` passthroughs put quoted code and text between
  `=== repo text <nonce> ===` markers whose nonce is random per call and
  redrawn if it occurs in the text, and JSON replies carry
  `content_trust: "untrusted_repo_text"` with `untrusted_fields`.

### Fixed
- The server instructions said only `graphlore_build` and `graphlore_add`
  modify state. They now list every writer (`graphlore_prune(dry_run=False)`,
  `graphlore_set_labels`, `graphlore_label_communities`, the project switch),
  say when grep is the better tool, and that repository text is data.

### Changed
- **Breaking: the default tool surface is `lean`.** Without
  `GRAPHLORE_TOOLSET`, the server registers 12 tools (11 without a semantic
  backend) instead of 33. `lean` gains `graphlore_fetch`, `graphlore_impact`
  and `graphlore_project`, so it covers orient → find → traverse → blast
  radius → source. Set `GRAPHLORE_TOOLSET=full` for the review, naming, diff,
  routes, package-API and CLI passthrough tools. The Claude Code plugin pins
  `full`, so plugin users see no change.
- An unknown `GRAPHLORE_TOOLSET` value falls back to `lean` with a stderr
  warning; it used to mean `full` silently. The boot banner prints the toolset
  and its tool count.
- The server instructions and the `onboard` / `trace_bug` / `explain_flow` /
  `review` prompts are rendered for the active surface and never name a tool
  that is switched off; a trimmed surface's instructions say how to get the
  rest. Replies that pointed at another tool (`graphlore_surprises`,
  `graphlore_prune`, `graphlore_subgraph`, `graphlore_impact`,
  `graphlore_search`) do so only when it is registered.
- README: graphlore is not an agent memory and does not replace grep; the
  benchmark states that it counts chars/4 (grep side: bytes) while the
  server's budgets count chars/3.5. The shipped skill says when to use `rg`.
- No `outputSchema` is advertised. Before, the SDK derived
  `{"result": string}` from each `-> str` signature and sent the reply text
  inside it.
- An empty answer is a result, not an error: `graphlore_search` with no hits
  returns `{"matches": [], "total": 0, ...}`, `graphlore_locate` with no
  semantic hits returns `{"query", "seed": null, "semantic_hits": [], "note"}`,
  and `graphlore_communities` on a graph without communities returns
  `{"communities": [], "note"}`, in place of `{"error": ...}`. The text is
  unchanged.
- `graphlore_node_details`, `graphlore_fetch` and `graphlore_skeleton(node=)`
  resolve names like the node-centric tools: a code definition before a doc
  heading of the same name, and a `matched` block naming the other candidates.
  They used to take the first exact or substring match silently, so the node
  they pick for an ambiguous name can change.
- A substring fallback is reported everywhere (`matched.by: "substring"`);
  before, `'async'` resolving to `AsyncClient` looked like an exact hit.
  `matched` gains `by` (`label`, `name`, `path` or `substring`).

## [0.4.1] - 2026-11-14

Fourth round of feedback: 0.4.0 re-run on the three repositories of the earlier
rounds.

### Added
- `graphlore_project` and `graphlore_overview` print the server version. A
  client keeps the tool schemas it loaded before a plugin update; a schema
  without `scope` next to a current version number now reads as "start a new
  session", not as "the parameter does not exist".
- `graphlore_locate` returns `calls_into`: what the seed calls in other files,
  most specific callee first. The tool that exposes a behavior is often a thin
  wrapper over the module that implements it, and the graph knows that.

### Changed
- `graphlore_locate`'s `also_matched` no longer lists hits on a file's header
  (its docstring and imports, before the first definition) — they are counted
  in `header_hits_skipped` — and names the definition each hit falls in
  (`qualname`).
- `graphlore_surprises` scores an inferred edge into a module the source file
  imports like an extracted one (the import resolves it; nobody guessed), and
  withholds the structural bonuses from a pair of directories that ten or more
  edges already join: an inbound adapter using the application layer is the
  architecture, not a finding.

### Fixed
- **Regression in 0.4.0: `graphlore_unreferenced` listed methods that a
  same-named caller calls.** A route handler `remove_example()` calling
  `review.remove_example(...)` lives in a file that defines its own
  `remove_example`, and 0.4.0 dropped every occurrence of a name in such a file.
  Only what can refer to the file's own symbol is dropped now — its `def` line
  and bare uses; an attribute access (`obj.name`, `mod.name`) still counts as a
  mention.
- An import edge graphlore resolved from the source carried no line, so
  `graphlore_impact` reported it at the importing file's line 1 as a
  definition. It now carries the line of the import statement and is reported
  as a reference site.

## [0.4.0] - 2026-11-14

Second and third rounds of feedback: the 0.3.0 review recipe run on the same
774-file repository through the MCP tools, then a side-by-side run with the
hosted Graphify connector on this repository.

### Added
- **Calls through an imported module are resolved from the source.** graphify's
  local extractor records `from pkg import f; f()` but not
  `from pkg import mod; mod.f()`, nor often the import of `mod` — so a tool that
  four tests call as `server.graphlore_unreferenced(...)` had no caller and no
  test in the graph. New `resolve.py` adds those call edges (following
  re-exports) and the missing module imports at read time, marked
  `resolved_by: "graphlore"`; graph.json is never modified. `graphlore_impact`,
  `graphlore_neighbors`, `graphlore_subgraph`, `graphlore_tests_for`,
  `graphlore_unreferenced`, `graphlore_cycles` and `graphlore_layers` read them,
  and `graphlore_validate` reports how many there are. Per language it adds
  what the extractor is seen to leave out: in Python every call through a
  module object; in JS/TS calls through a default import and through a
  dynamically imported module (namespace imports and `require` are already
  linked); in Go and Java nothing, graphify links those itself. One parse per
  Python and JS/TS file, once per process; `GRAPHLORE_RESOLVE=0` turns it off.
- **`graphlore_tests_for`** — the tests that exercise a symbol: `direct` (they
  call it, with the call line), `through_one_caller` (a production function or
  a test helper in between), and `weak` matches kept apart (a test file that
  imports its module, a test named after it).
- `graphlore_impact`, `graphlore_subgraph` and `graphlore_neighbors` take the
  `scope` argument (`-tests`, a path prefix, ...). Unlike the audit tools they
  leave nothing out by default.
- `graphlore_impact(strict=True)` walks only edges the extractor resolved; every
  row carries its edge's `confidence` (and `resolved_by` when graphlore added
  the edge), and inferred rows are marked in the text.
- `graphlore_locate` lists the other top hits under the seed (`also_matched`,
  production code before tests).
- **`graphlore_cycles` classifies import cycles by when their imports run.**
  Each import edge is read back from the importing file (new `imports.py`):
  `runtime` cycles — a loop of module-level imports, the ones that can fail at
  start-up — come first, then `deferred` (the loop needs an import inside a
  function) and `type-only` (it needs one under `if TYPE_CHECKING:`). Each cycle
  carries `import_kind` and `hops` (the kind of every import on its loop), the
  payload adds `runtime_` / `deferred_` / `type_only_import_cycle_count`, and a
  repository without a runtime cycle says so in one line. Python is read with
  `ast`; JS/TS with tree-sitter (`import()` or `require` inside a function is
  deferred, `import type` is type-only); Go, Java, Kotlin, Scala, C#, Rust and
  Swift import at compile time and are always module-level. An import in any
  other language counts as module-level — unless the graph recorded it on a
  function, which makes it deferred — and the result says so when a reported
  runtime cycle rests on one.
- **`graphlore_duplication_scan` has an exact tier, `identical_bodies`.** Every
  function and method body is hashed after normalizing whitespace, comments and
  the docstring (Python via `ast`, other languages via tree-sitter), and bodies
  that occur more than once are reported as groups with `file:line` for each
  copy. No model, and it covers the small helpers that the semantic tier's
  largest-first seeding never reaches. Without the `[semble]` extra the tool now
  returns this tier instead of an error.
- `graphlore_same_names` reports `used_in_own_file` for each copy and takes
  `across_languages`.
- `graphlore_layers` violations carry `import` (module-level / type-checking /
  deferred), and the result lists `rejected_imports`.
- `graphlore_unreferenced` rows carry `also_defined` when another definition
  shares the name.
- README: start a new session after updating the plugin — a client keeps the
  tool schemas it loaded before the update.

### Changed
- **`graphlore_impact` and `graphlore_subgraph` put production code before
  tests.** Rows at one distance were alphabetical, so a hub function's budget
  went to test helpers named `_build` and `_graph` and no production caller was
  shown. They are now ordered non-test first, then most connected, and a
  truncated result says how many rows (or direct neighbors) were dropped and how
  many of them were tests. `graphlore_neighbors` is ordered the same way.
- **An ambiguous name is no longer resolved silently** in `graphlore_impact`,
  `graphlore_subgraph`, `graphlore_neighbors` and `graphlore_tests_for`. A name
  shared by several nodes — a function and a README heading, two `Settings`
  classes — resolves to a source definition first, and the result says "Matched
  N nodes ...; using X" with the others' ids (`matched` in JSON). A bare name
  matches its callable (`graphlore_locate` finds `graphlore_locate()`), a path
  names its file's node, and `Client.send` prefers the method of that class.
- An empty answer from those tools says that no edge was indexed — not that
  the code has none — and what kinds of call the index cannot see.
- `graphlore_duplication_scan` leaves out semantic pairs scoring under 0.7 on
  the semble backend (`min_score=0` lists them) and says what the score is. On
  the reporting repository every pair between 0.49 and 0.64 was two classes
  with the same role, not copied code. A custom backend gets no default floor.
- `graphlore_same_names` compares names within one language family, and reads
  nesting from the source: a closure that graphify hangs directly on its file
  is no longer compared with top-level functions.
- `graphlore_surprises`: a *script* nothing depends on counts as a consumer even
  when its directory is depended upon (`docker/smoke/make_catalog.py`), consumer
  detection ignores resolver guesses, and anything test code uses — shared test
  fakes in another test directory included — is routine coupling. Non-test code
  that depends on test code keeps its bonuses.
- `graphlore_unreferenced` names the base in its hook note ("method of a
  subclass of NodeVisitor") and gives it only when that base can call the
  method: a base outside the project, or a project base that defines or mentions
  a method of that name.

### Fixed
- "Nothing depend on ..." now reads "Nothing depends on ...".
- **A third-party import linked to a first-party module of the same name** —
  `import yaml` (PyYAML) recorded by graphify as an import of
  `pkg/.../export/yaml.py`, or `import mcp` as an import of `mcp.json` — was
  reported as a layer violation and could close an import cycle. Import edges
  are now checked against the importing file's own statements before they are
  lifted to file level: an absolute import can only mean a module whose dotted
  path starts in a directory that is not itself a package, and a bare one-word
  import only one that starts at the project root, a source root, a
  distribution root or a directory above the importing file. The JS/TS
  equivalent: a relative specifier is resolved to its file, a bare one-word
  specifier is an npm package, and a path alias is kept. Rejected edges are
  counted and shown, in `graphlore_cycles`, `graphlore_layers` and
  `graphlore_surprises`.
- **`graphlore_unreferenced` missed a dead class when another class had its
  name.** The text cross-check counted the other class and its uses as
  mentions. A file that defines its own symbol of that name is now left out of
  the count.
- `graphlore_unreferenced` marked methods of a class whose only base is `ABC`
  as possible hook overrides. Marker and data bases (`ABC`, `Protocol`,
  `BaseModel`, exceptions) no longer trigger the note.
- A label that already carried its location was printed with it twice in
  `graphlore_duplication_scan` and `graphlore_unreferenced` rows
  (`Settings (a.py:L35) (a.py:35)`).
- An ambiguous top-level function label lost its `()` when qualified and was
  left ambiguous (`run` for both `run()`); it is now shown as
  `run() (file:Lline)`.

## [0.3.0] - 2026-11-14

Feedback from a review session on a 774-file Python + JS repository (graphify
0.9.108, 13,159 nodes): the analysis tools were dominated by code that was not
the project's own, a graph built by path could not be read, and reviewing a
codebase took hand-written scripts over `graph.json`.

### Added
- **`graphlore_project`** — shows the directory the server reads and switches it
  to another one. A second repository (or one just built with
  `graphlore_build("/abs/path")`) no longer needs a second server. Changes no
  file; refused under `GRAPHLORE_RESTRICT_PATHS`, so never available over HTTP.
- **Analysis scope.** `graphlore_god_nodes`, `graphlore_validate`,
  `graphlore_cycles`, `graphlore_surprises` and `graphlore_duplication_scan` take
  one `scope` string: path prefixes or globs to keep, `!pattern` to drop,
  `-tests`, `+external`, `+vendored`, `all`. Every result carries a `scope`
  object and a closing line saying what was left out.
- **`graphlore_unreferenced`** — dead-code candidates: definitions with no
  incoming graph reference, not registered by a decorator (routes, click/typer
  commands, validators, fixtures — read from the graph's decorator edges and
  from the source lines above the definition, in any language the span engine
  reads), and not mentioned anywhere else in the repository's text. Each with
  `file:line` and a mention count; public methods of classes other files use
  are marked "may be API".
- **`graphlore_layers`** — checks import edges against layer rules
  (`{"layer prefix": ["prefixes it may import"]}`, inline or from a JSON file in
  the repository, `.graphlore-layers.json` by default) and returns the
  violations grouped by layer with `file:line`. `strict=True` also objects to
  imports into files outside every declared layer.
- **`graphlore_same_names`** — classes and top-level functions that share a name
  across modules, with how many modules import and reference each copy. Exact,
  no embedding model.
- A **`review` prompt** and a "Reviewing a codebase" recipe in the
  `graphlore-explore` skill: validate → import cycles → layer rules →
  unreferenced code → same-named symbols → hotspots → surprises.
- `graphlore_build(verbose=True)` returns the full CLI log.
- `graphlore_duplication_scan` gains `include_tests` and `min_score`.

### Changed
- **Vendored, minified and external code is left out of the analysis tools by
  default.** Files under `vendor/` / `node_modules/` / `third_party/`, files
  named `*.min.js` or carrying a build hash (`decorate-BgofJUJ5.js`), JS/CSS whose
  lines are too long to be hand-written, and nodes standing for external
  packages or unresolved names (`typing`, `pytest`, `_`) no longer rank as god
  nodes or show up as cycles and self-loops. `graphlore_overview` ranks its god
  nodes the same way. `scope="all"` restores the old behaviour.
- **`graphlore_cycles` reports by relation kind**: import cycles between modules
  first (import edges lifted to file level; a module's import of its own package
  `__init__` is ignored), call cycles after, then cycles that only close through
  another relation. `kind="imports" | "calls" | "other"` asks for one. Each group
  now carries one concrete loop (`path`) and its `kind`; the payload adds
  `import_cycle_count` / `call_cycle_count` / `other_cycle_count`. A repository
  with no import cycle says so in one line. Previously every relation was mixed
  into one list of node groups.
- **`graphlore_build` returns the build's counts and warnings** instead of the
  raw CLI log (progress ticks and stage banners are counted, not printed). It
  also reports when the graph was written somewhere this server does not read —
  graphify writes `graphify-out/` inside the folder it is given, so a sibling
  repository *or a subfolder* leaves the read tools on the old graph or on none
  — and names the `graphlore_project` call that switches to it. Two more notes
  appear when they apply: vendored/minified files found in the graph (with the
  `.graphifyignore` hint), and a `graphify-out/` that git neither ignores nor
  tracks. It writes no `.gitignore` itself: graphify supports a committed graph,
  so whether to ignore the directory stays the repository's decision.
- **`graphlore_duplication_scan` compares definition with definition.** Seeds
  are functions, methods and classes (largest bodies first) instead of the most
  connected nodes, each anchored to the chunk holding its own source rather
  than to whatever chunk mentions its label. A neighbour is kept only if it is
  a definition of the same kind and a similar size and structurally distant.
  Pairs carry the similarity `score` and `a_site` / `b_site` (`file:line`), best
  score first; test ↔ source pairs are left out unless `include_tests=True`.
- **`graphlore_surprises`** scores a directory nothing depends on (`bench/`,
  `demo/`, `examples/`) using a package that several directories depend on as
  expected coupling, and drops an inferred edge whose source file imports neither
  the target's module nor its package (the resolver matched a name).
- **`graphlore_impact`** leaves docstring/rationale nodes (`rationale_for`) out
  of the blast radius and counts them in `rationale_nodes_skipped`; name the
  relation in `relations` to walk them.
- **`graphlore_validate`** counts a function calling itself as `recursive_calls`,
  not as a `self_loops` issue.
- The descriptions of `graphlore_locate` and `graphlore_duplication_scan`, the
  README and the semble-missing error now say that the first semantic call
  downloads semble's embedding model from the Hugging Face Hub, and give the
  command that fetches it ahead of time. The source no longer calls that path
  "offline".

### Fixed
- `graphlore_build` announced `graph.json ready` for the project's own, older
  graph after building a different folder, and said nothing at all when the
  project had no graph.
- A package entry file labelled with part of its path (`pkg/__init__.py`, as
  graphify >= 0.9 writes it) was not recognized as a whole-file node, so
  `graphlore_surprises` scored edges it should have skipped.
- README told users to `pip install graphlore`, but the package has never been
  released on PyPI, so the command failed. Installation now shows the GitHub
  form that works today
  (`pip install "graphlore @ git+https://github.com/yasinyaman/graphlore"`).
- `mypy` failed inside the `uv.lock` environment (`uv run mypy`): the pinned
  `tree-sitter-language-pack` types `get_language`'s argument as a `Literal` of
  grammar names while `detect_language_from_path` returns `str`. The one call
  site carries a targeted ignore. CI, which installs unlocked, stayed green.
- **`graphlore_surprises` was effectively dead on every AST-built graph.** It
  keyed on a `surprise`/`is_surprise` flag no graphify writer emits, and its
  only fallback needed community info those graphs also lack — so it printed an
  empty list under a header that read as "this codebase has no surprising
  couplings". It now scores cross-file edges itself (confidence, file-type /
  directory / community crossing, peripheral→hub), suppressing resolver noise
  and routine test↔source coupling, and reports a `basis` of `flagged` /
  `computed` / `none` — the last naming exactly which signals are missing
  instead of implying a clean codebase. `graphlore_overview` no longer asserts
  "0 surprise edges" when the graph simply carries no flags.
- **`graphlore_impact` walked every edge type and reported no reference site.**
  It gains `relations=` (presets `all`/`code`/`calls`/`imports`/`types`, or
  literal relation names, orthogonal to `direction`; an unmatched filter names
  the graph's actual vocabulary instead of returning nothing) and each row now
  carries the traversed edge's recorded position, marked `site_kind=reference`
  or `definition` so a definition line is never misread as a call site. When a
  filter excludes containment, the node's own members are seeded so a class's
  methods stay reachable.
- Docs corrected against an audit of the wrapped CLI: token budgeting, HTTP
  auth, multi-project serving and general graph navigation exist in Graphify's
  own embedded MCP server and are no longer presented as things graphlore adds.
  The README now states the real differentiator (the span engine and what it
  makes possible) and the honest limits (no extractor, one project per process,
  no PR tooling).
- `graphlore_package_apis` (JS/TS): clearly-internal import sources are no
  longer counted as npm packages — absolute paths (`/lib/x`, whose "package"
  was the empty string), package.json `#` subpath imports, and the common
  bundler aliases `~/x` and `@/x`. A scoped `@app/utils` is still counted:
  without reading tsconfig it is indistinguishable from a real scoped npm
  package (documented over-approximation).
- `benchmarks/multilang.py` no longer aborts the whole run (discarding every
  measured repo) when a REPOS entry's graph is missing — the row is skipped
  with a stderr note, since the documented setup only builds a subset. The
  freshness table also stops claiming "expect False" for the token-change
  fallback edit, which is a comment in the C-family (expectation unreliable)
  — the row is flagged instead.
- The CLI-backed tools (`graphlore_query`/`path`/`explain`) and `_run_cli`'s
  plumbing (arg wiring incl. `--graph`, missing-binary message, timeout,
  exit-code formatting, exec-OSError) are now covered by tests — previously
  three registered tools had zero coverage.
- Rename leftovers in the docs artifacts: `docs/benchmark.html`,
  `docs/benchmark.tr.html` and `docs/benchmark.svg` still titled/labelled the
  project `graphify-mcp`; they now say `graphlore` (matching the final rename —
  the intermediate `codegraph-mcp` title never shipped).

### Changed
- **README rewritten for the graphlore surface** — restructured around the
  locate-first workflow, tools/tables refreshed for all 28 `graphlore_*` names
  and the newer behaviors (junk-file-tolerant freshness, qualified ambiguous
  labels, pluggable semantic backend, watcher), project-layout/dev sections
  updated (mypy in CI), and the "Comparison — when to use which" section
  removed. `docs/benchmark*.html`/`.svg` retitled from `codegraph-mcp` to
  `graphlore` (measurement numbers untouched).
- **Internals: one confinement boundary, indexed lookups.** The PROJECT_DIR
  confinement check that five per-file analyzers hand-copied is now a single
  `spans._resolve_in_project()`, and the span/API/route per-file caches share
  one `_cached_file_analysis()` pipeline (same mtime-keyed, bounded-FIFO
  semantics). `_resolve_node`'s exact id/label pass and `_node_for_location`'s
  same-file lookup are served from indexes cached on the nodes-list identity
  (the `_ADJ_CACHE` scheme) instead of rescanning all N nodes per call — the
  latter ran once per semantic hit / route row / duplication pair.
- **Ambiguous node labels are qualified at render time.** graphify labels
  methods bare (`.auth_flow()`), so distinct nodes across classes/files used to
  render identically in subgraph arrows and node lists. Wherever a label is
  shared by more than one node, tools now show the span-recovered FQN
  (`DigestAuth.auth_flow()`), falling back to `label (file:Lline)` when no span
  is available; unique labels are untouched. Lazy + memoized (only ambiguous
  ids that actually render pay the parse cost) and cached per loaded graph —
  this also collapses the eleven per-tool `{id: label}` rebuilds into one
  shared `_display_labels()` helper.
- **Tool surface renamed: `graphify_*` → `graphlore_*`** (and the resources
  `graphify://…` → `graphlore://…`), completing the package rename below so
  the commands an assistant sees carry the product's own name. All 28 tools
  are affected (`graphlore_locate`, `graphlore_build`, …); the bundled
  explore skill moved to `graphlore-explore` accordingly. The `graphify-out/`
  output directory and `.graphify_labels.json` keep their names — they are
  artifacts of the wrapped Graphify CLI (`graphifyy`), not this package.
- **Environment variables renamed to `GRAPHLORE_*`, with `GRAPHIFY_*` as a
  legacy fallback.** All settings (`GRAPHLORE_PROJECT_DIR`,
  `GRAPHLORE_TOOLSET`, `GRAPHLORE_TRANSPORT`, …) now read the new spelling
  first via one `config.env()` accessor and fall back to the pre-rename
  `GRAPHIFY_*` names, so existing configs keep working; when both are set,
  `GRAPHLORE_*` wins. Server messages/docs lead with the new names; the
  bundled `mcp.json` / `claude_desktop_config.json` templates are updated.
- **Project renamed: `graphify-mcp` → `graphlore`** (before any PyPI release,
  so no published users are affected). The Python module is now `graphlore`,
  the MCP server announces itself as `graphlore`, and the console script is
  the bare `graphlore` — the collision with the `graphify-mcp` script that
  `graphifyy` ships (which forced the old `graphify-mcp-server` entry point)
  is gone. The repository moved to `github.com/yasinyaman/graphlore`; old
  GitHub URLs redirect. (An intermediate rename to `codegraph-mcp` lived for
  a day and was dropped: "codegraph" is already crowded in the MCP niche —
  codegraph-ai/CodeGraph, two GitHub repos literally named codegraph-mcp,
  plus codegraph and codegraph-mcp-server on PyPI.)
- **Upgraded to MCP Python SDK v2** (`mcp>=2.1,<3.0`, was `>=1.26,<2.0`). The
  server now builds on `mcp.server.mcpserver.MCPServer` (v1's `FastMCP`); tool
  and prompt surfaces are unchanged. Notable internals:
  - Server version is passed as `MCPServer(..., version=...)` instead of the old
    private `_mcp_server.version` override.
  - `ToolAnnotations` hints use the v2 snake_case field names
    (`read_only_hint`, `destructive_hint`).
  - HTTP transports take host/port per call (`mcp.run(transport=..., host=...,
    port=...)`, `streamable_http_app(host=...)`) — v2 removed
    `settings.host/port`. The `host=` passed to the app factories also feeds the
    SDK's DNS-rebinding protection, which auto-enables on loopback hosts.
  - Host-LLM sampling (`graphlore_label_communities`, `graphlore_sampling_status`)
    now names all communities in **one batched** `sampling/createMessage`
    request (a JSON name map) instead of one request per community, and picks
    its transport per protocol: on 2026-07-28+ (no in-call back-channel) a v2
    `Resolve`/`Sample` resolver carries the request as input-required rounds;
    on older protocols the tool body samples directly over the back-channel,
    preserving the pre-v2 graceful degradation — a failing host model yields
    placeholder names plus a note instead of erroring the call. (On the
    resolver path that degradation is impossible: `Sample` has no error arm,
    so a host-model failure surfaces as a tool error there.) Unusable or
    partial replies degrade to placeholder names with an explanatory note,
    and an empty naming batch (`limit=0`) no longer sends a request at all.
  - The test suite drives tools through the v2 in-process `Client` (the v1
    `create_connected_server_and_client_session` helper is gone).

### Added
- **Claude Code plugin + marketplace.** The repository root is now a Claude
  Code plugin (`.claude-plugin/plugin.json`) listed by its own marketplace
  (`.claude-plugin/marketplace.json`), so
  `/plugin install graphlore --marketplace yasinyaman/graphlore` adds the MCP
  server and the `graphlore-explore` skill in one step. The server is launched
  with `uvx` from the plugin's own copy of the source (extras `graphify`,
  `treesitter`, `semble`; `uv` required), so the server always matches the
  installed plugin version, and `GRAPHLORE_PROJECT_DIR` is set to the opened
  project. A test keeps the manifest version equal to `server.__version__`.
- **PyPI publish workflow** (`.github/workflows/publish.yml`): publishing a
  GitHub release builds the sdist and wheel and uploads them with PyPI Trusted
  Publishing (no stored token), refusing when the release tag and the
  `pyproject.toml` version differ. The one-time PyPI setup and the release
  steps are in CONTRIBUTING.md under "Releasing".
- `code_only` flag on `graphlore_build` — appends the CLI's `--code-only`, so a
  repo containing doc/paper/image files can be indexed via local AST with no
  LLM API key (those files otherwise demand semantic extraction and the build
  errors without a key).
- README **"Comparison — when to use which"** section: an honest side-by-side
  with [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)
  (same goal, different trade-offs: verbatim-code mega-tool vs token-budgeted
  map, structural-only vs semantic hidden_links, watcher vs git-aware
  freshness), plus one-liners on Graphify's embedded MCP server and semble's
  own MCP server as complements.
- **Benchmark call/read metrics + `--json`** — `benchmarks/multilang.py` now
  also measures the agent-efficiency axis: locate = 1 tool call / 0 file reads
  vs the naive baseline's 1 grep + N file-read calls (N = the files the same
  grep matches, so both metrics share one baseline), and `--json PATH`
  persists the full result set. The 2026-08 re-run (semble 0.5.5) is committed
  as `benchmarks/results-multilang.json` — the first persisted benchmark
  artifact — and the README / `docs/benchmark*.html` cross-language tables now
  carry a "Calls (locate vs naive)" column (89–95% fewer calls; span-join
  percentages follow the overload-family re-count above).
- **`graphlore_routes`** — framework route → handler table: which URL patterns
  hit which code, each row joined back to its graph node and qualified name
  (`GET /items/{id} -> read_item (app.py:5)`). Recognizes the common
  registration idioms per language — Python: Flask `@app.route` /
  Flask-FastAPI verb decorators (labelled by the file's imports) / Django
  `path()`-family (gated on a django import, so a local `path()` never
  registers); JS/TS: Express-style verb calls (plain-identifier receiver +
  leading-`/` literal, which keeps `map.get('key')` out) and NestJS
  `@Controller`+`@Get`; Go: gin/echo/chi verb methods, `Handle`/`HandleFunc`
  with Go 1.22 `"GET /x"` patterns split into method + path, and chi
  `Route(...)` closure nesting with prefix propagation; Java: Spring
  `@GetMapping`-family and `@RequestMapping` (class prefix joined). Scans the
  files the graph knows plus any `urls.py` the graph missed (capped, pruned
  walk). An honest lower bound: dynamic registration, chained builders
  (`router.route().get()`, gorilla `.Methods()`) and variable-bound group
  prefixes are invisible. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `routes.py` engine module with a bounded
  per-(path, mtime) cache and a public `routes_for_source(src, rel)` seam,
  mirroring `apis.py`.
- **`GRAPHIFY_TOOLSET=locate`** — a minimal, mega-tool-style surface of five
  tools: `graphlore_locate` (the one-call orient), `graphlore_fetch` (map → code),
  plus `graphlore_overview` / `graphlore_build` / `graphlore_freshness` to stay
  oriented and in sync. Inspired by single-tool code-graph servers where a
  smaller surface measurably improves tool selection. Needs a semantic backend
  (the `[semble]` extra or `GRAPHIFY_SEMANTIC_BACKEND`); without one the server
  falls back to the lean surface with a stderr warning instead of advertising a
  locate tool that can only return an install hint. `graphlore_overview` now
  suggests `graphlore_locate(...)` first whenever it is on the active surface, so
  the trimmed mode keeps a non-empty `suggested_next`.
- `GRAPHIFY_ALLOWED_HOSTS` — Host-header allowlist for the HTTP transports'
  DNS-rebinding protection (comma-separated, `:*` port wildcards; `*` disables
  it). The MCP v2 SDK auto-enables that protection with a loopback-only
  allowlist when the server binds a loopback host, which rejects
  reverse-proxied requests whose `Host` wasn't rewritten; this variable is the
  escape hatch (v1 enforced no Host check).
- `graphlore_package_apis` — symbol-level external API surface: which names each
  external package is actually used for ("uses `Depends`, `APIRouter` from
  fastapi", not just "imports fastapi"), the input a version-upgrade audit needs.
  Captures from-imports and attribute access through import aliases
  (`np.array(...)` → `numpy: array`) plus qualified paths (`numpy.linalg.norm`)
  from real use sites; an honest lower bound (dynamic import / `getattr` /
  `import *` / wrapper indirection are invisible). First-party packages are
  detected and excluded. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `apis.py` engine module with a bounded
  per-(path, mtime) cache like the span index.
- `api_uses_for_source(src, rel)` — public, stable entry point for external
  consumers (importable from the package root): the same extraction on a source
  blob you already hold, with no file IO / caching / project-dir confinement.
  The underscore-prefixed extractors behind it remain internal.
- Seven new analysis tools: `graphlore_impact` (reverse-dependency blast radius,
  ordered by hop distance), `graphlore_duplication_scan` (repo-wide hidden-link
  audit — semantically similar but structurally distant pairs),
  `graphlore_fetch` (hydrate a node's source, token-capped), `graphlore_skeleton`
  (def/class signatures with bodies stripped), `graphlore_diff` (file-level
  structural changeset between two git refs, cosmetic-only changes separated),
  `graphlore_prune` (drop phantom nodes for deleted/renamed files — the surgical
  alternative to a rebuild, `dry_run` preview), and `graphlore_cycles`
  (Tarjan-SCC dependency cycles).
- Pluggable semantic backend: `GRAPHIFY_SEMANTIC_BACKEND` selects `semble`
  (offline default) or any `module.path:Factory` implementing the
  `SemanticIndex` protocol (`search`/`find_related`); `graphlore_locate` and
  `graphlore_duplication_scan` dispatch through it.
- Opt-in filesystem watcher: `GRAPHIFY_WATCH=1` re-syncs the graph on
  structural source changes (cosmetic edits ignored), debounced via
  `GRAPHIFY_WATCH_DEBOUNCE`; ships as the optional `[watch]` extra (watchdog).

## [0.2.0] - 2026-06-22

### Added
- `graphify-mcp-server` console script and `python -m graphify_mcp` entry point,
  both collision-free with the `graphify-mcp` script that `graphifyy` also ships.
- Boot banner on stderr at startup — `graphify-mcp vX.Y.Z | transport=… |
  toolset=… | project=…` — so it's immediately clear which server (and project
  dir) a client connected to, even when `graphifyy`'s same-named script is around.
- Lightweight `graph_age` field on `graphify_overview` and `graphify_subgraph`
  (e.g. "built 3 commits ago" / "built at HEAD" / "built at an unreachable
  commit") so staleness is visible without a separate `graphify_freshness` call.
  Git-only and cheap; `null` when there's no recorded build commit or no git repo.
  README now documents a first-class **post-commit-hook auto-update** flow to keep
  the graph fresh automatically.
- `graphify_label_communities` — names Leiden communities via **host-LLM MCP
  sampling** (no server API key), a backend key (`method="cli"`), or placeholders.
- `graphify_sampling_status` — capability test reporting whether the client
  supports sampling, whether a backend key is set, and the preferred method.

### Removed
- The bare `graphify-mcp` console script (shipped in 0.1.0). `graphifyy` ships a
  script of the same name, so a bare `graphify-mcp` resolved to whichever package
  installed last and could silently launch the wrong server. **Breaking:** invoke
  the server via `graphify-mcp-server` or `python -m graphify_mcp` instead.

### Fixed
- `graphify_node_details` now reads the source line from graphify's real
  `source_location` field (e.g. `"L295"`), not just `line`/`lineno`/`start_line`,
  so `file:line` references resolve against actual graph output.
- Server now reports its own version over MCP instead of the `mcp` library's.
- `graphify_subgraph` no longer re-serializes the whole edge list on every edge
  during the budget check — a running counter replaces the O(n²) `json.dumps`.
- `graphify_freshness` now detects newly-added **untracked** files (via
  `git status --porcelain`), compares against graphify's `built_at_commit`
  (robust across checkouts where mtime resets), and ignores its own
  `graphify-out/` output.
- `graphify_overview` and `graphify_surprises` now share one surprise-edge
  definition (`_is_surprise_edge`); an INFERRED *confidence* is no longer
  miscounted as a surprise, and the two tools agree.
- Span extraction no longer mistakes a **call/invocation** for a definition. A
  Java `method_invocation` (and C# `invocation_expression`, Ruby `method_call`,
  PHP `function_call_expression`, …) exposes the callee under a `name` field, so a
  call like `get(u)` inside a method body used to leak in as a phantom symbol
  (`Class.method.get`) and pull chunk resolution to it. Caught by the new Java
  golden span test.

### Changed
- Internal refactor (no behaviour change): the 2,000-line `server.py` is split into
  layered modules — `config.py` (shared `PROJECT_DIR`), `graph.py` (graph.json load +
  node/edge/traversal helpers), `spans.py` (the tree-sitter/ast span + structural-diff
  engine) — leaving `server.py` as the MCP surface (tools/resources/prompts). Same
  108 tests, same benchmark numbers.
- `_load_graph` caches the parsed graph by path + mtime, so a multi-MB
  `graph.json` isn't re-parsed on every tool call.
- Community-naming sampling `max_tokens` raised 16 → 24 to avoid clipped names.
- Token budgeting is now conservative: `~3.5` chars/token (vs the old `4.0`) plus
  a JSON-envelope allowance, so `approx_tokens` and `budget_tokens` reflect the
  whole returned payload and stop systematically under-reporting. Documented as an
  estimate (±~20%). Optional **exact** counting via the `[tiktoken]` extra +
  `GRAPHIFY_TOKENIZER=tiktoken` (the budget cap stays heuristic, so it's fast
  either way; the reported `approx_tokens` becomes exact).
- Tightened the `mcp` dependency bound from `>=1.2.0` to `>=1.26,<2.0`. The server
  uses streamable-HTTP, tool annotations and host-LLM sampling, which the old floor
  didn't have (it installed but failed at runtime); the upper bound guards against a
  breaking 2.0 SDK. Tested against mcp 1.26–1.27.
- Docs: the benchmark numbers now carry an explicit **sample-bias** note (every repo
  measured is an HTTP-client library, so results may differ on other architectures),
  and the HTTP hardening section documents the no-shell subprocess invocation and the
  `GRAPHIFY_TIMEOUT` knob for shared deployments.
- Pinned the optional tree-sitter extras: `tree-sitter>=0.22,<1.0` (the stable
  `Parser(Language)` core API) and `tree-sitter-language-pack>=1.6,<2.0` (the
  bundled grammars churn, so the 1.x cap keeps golden span tests from silently
  regressing on a grammar update). Added per-language golden span tests for Java
  and TypeScript (Python · JS · Go · Rust · C++ already covered).
- CI now runs **mypy** (non-strict baseline over the package) and **pytest with
  coverage** (term + XML report, floored at 80% via `--cov`); push-CI also fixed to
  trigger on `master`, the actual default branch. Tool `annotations=` now use typed
  `ToolAnnotations` objects instead of plain dicts, so the type checker validates the
  tool metadata FastMCP turns into each tool's schema. Added a corrupt-`graph.json`
  test alongside the existing missing-graph / unreachable-`built_at` ones.

### Added (transport & hardening)
- Optional HTTP transport: `GRAPHIFY_TRANSPORT=streamable-http|sse` serves over
  `GRAPHIFY_HOST:GRAPHIFY_PORT` (stdio stays the default).
- Opt-in build-path containment via `GRAPHIFY_RESTRICT_PATHS`; **auto-enabled**
  whenever an HTTP transport is selected, so a network client can't drive
  `graphify_build` to extract arbitrary paths.
- `graphify_overview` now reports `id_collisions` and warns when distinct nodes
  collapse to one id (degrees/neighbors would otherwise be silently understated).
- `graphify_validate` — read-only graph linter: dangling edges (endpoint not in
  the node set), duplicate edges, self-loops, and orphan (degree-0) nodes.
- `graphify_freshness` now returns a `recommended_action` (fresh / update /
  rebuild) with a `reason`: deletions, renames, or a large change set steer to a
  full rebuild, since incremental `update` can't drop nodes for removed code.
- `graphify_freshness` now verifies that the recorded `built_at_commit` is
  actually reachable in the clone (`git cat-file -e`). When it isn't (shallow
  clone, gc, rebase or squash), it reports `built_commit_reachable: false` and
  recommends a full rebuild — incremental update can't trust an unknown base —
  instead of mislabeling it "an older commit" and offering an update.
- Fixed a latent bug in `graphify_freshness`'s changed-file list: `_git` stripped
  the leading status column, mangling the first file name for unstaged
  modifications/deletions (` M`/` D`). `_git` now `rstrip`s only.
- `graphify_locate` (optional `[semble]` extra) — joins
  [semble](https://github.com/MinishLab/semble) semantic search to the graph:
  NL query → enclosing node → token-budgeted subgraph, plus `hidden_links`
  (semantically similar but structurally disconnected code, with hop distance).
  Refactored the subgraph BFS into a shared `_bfs_subgraph` helper. The
  chunk→node join prefers `file_type == "code"` symbols over docstring
  (`rationale`) / `document` nodes and uses the chunk's full line range, so a
  seed resolves to the enclosing function/class, not a docstring node.
- `graphify_set_labels` — assistant-driven community naming: the calling
  assistant pushes `{id: name}` (no key, no sampling — works in clients like
  Claude Code that lack sampling), persisted to `.graphify_labels.json` and
  patched into `graph.html`. Surfaced as the fallback in `graphify_label_communities`
  and `graphify_sampling_status` when sampling/keys are unavailable.

### Added (canonical span join)
- `graphify_locate`'s chunk→node join is now span-based, not single-point. Graph
  nodes carry only one `source_location` line (no end-line), so the old
  "greatest line ≤ chunk-start" heuristic could attribute a chunk to a function
  that had already ended, or fall back to the whole-file node. `_node_for_location`
  now resolves a semble chunk to the def/class whose **real line range** encloses
  it — via a decorator-aware AST span pass (stdlib `ast`, Python files; zero new
  deps) — then maps that symbol to its graph node, walking outward to the nearest
  enclosing symbol that has a node. The point heuristic remains the fallback for
  non-Python files or when no source is on disk. Measured on httpx: true
  containment rose from ~86/108 to 101/108 sampled chunks; the rest are
  module-level-start chunks resolved to the first symbol they introduce.
- `graphify_locate` seeds now include a span-recovered `qualname` (FQN, e.g.
  `AsyncClient._send_single_request`), disambiguating same-named symbols.
- The AST span pass is confined to `PROJECT_DIR` (the only code path that reads a
  source file from a chunk-supplied path) and cached per file by mtime.

### Added (multi-language span/structure backend)
- The span/structure extraction behind `graphify_locate` and `graphify_freshness`
  is no longer Python-only. Python keeps the stdlib `ast` fast path (zero deps,
  decorator-aware); every other language is handled by an optional **tree-sitter**
  backend (`[treesitter]` extra — also ships with graphify) with automatic
  language detection from the file path. So the chunk→symbol span join and the
  cosmetic-vs-structural freshness check now work for JS/TS, Go, Rust, Java, Ruby,
  C/C++, and the ~165 other languages the grammar pack covers.
- Symbol detection is generic (a named def/class/method/struct/… node), so no
  per-language table is maintained; qualnames chain enclosing symbols
  (`Service.fetch`). The tree-sitter parser is built from the stable core
  `Parser(Language)` API (not the pack's churning `get_parser` wrapper).
- Cosmetic detection for non-Python compares a comment-stripped tree-sitter
  skeleton over **all** tokens — operators and keywords included — so any semantic
  edit (an operator flip `+`→`-`, `==`→`!=`, a `sync`→`async` or `let`→`const`
  change, a rename or value change) is structural, while only comment/whitespace
  edits compare equal. When the backend or a language is unavailable, both
  features degrade to the prior behaviour (point heuristic / treat-as-structural)
  — never an error.
- tree-sitter spans now absorb a symbol's leading **doc-comment / decorator /
  annotation** lines into `region_start` (mirroring the Python decorator path), so
  a chunk that starts on the doc comment above a Go/Java/JS method resolves to that
  method. Measured on real repos this lifted Go span-join precision from 48%→80%.
- Broader tree-sitter symbol/qualname coverage: an anonymous function bound to a
  name (`const f = () => …`, object property `{ foo: () => … }`, class field
  `handler = (r) => …`, `var h = func(){}`) takes the binding name; a method
  receiver is type-qualified (Go `func (c *Client) Get()` → `Client.Get`); C/C++
  function names are read from the declarator chain (`Session::Get` → `Session.Get`)
  and template calls / sub-declarators no longer leak in as bogus symbols; `region_start`
  also absorbs Rust `#[attribute]` lines. C++ `class_specifier`/`struct_specifier` are
  recognized as definitions.
- **Multi-language validation benchmark** (`benchmarks/multilang.py` + the
  "Across languages" section in `docs/benchmark*.html`): on real HTTP-client repos
  in five more languages (`got` JS/TS, `resty` Go, `retrofit` Java, `ureq` Rust,
  `cpr` C++) span-join precision is 69–91% (vs 91% for Python/httpx), qualname
  recovery 50–100%, locate 200–748× cheaper than grep+read, and the
  cosmetic-vs-structural freshness check is correct in every language.

### Added (Phase 3 hardening)
- Optional **bearer auth** for the HTTP transports: set `GRAPHIFY_API_KEY` and
  every HTTP/WebSocket request must carry `Authorization: Bearer <key>`
  (constant-time compared; 401 otherwise). Unset = prior behaviour (rely on
  loopback binding / a fronting proxy); a stderr warning now fires if an HTTP
  transport binds a non-loopback host without a key.
- `graphify_freshness` now separates **cosmetic from structural** changes: each
  changed `.py` file is AST-diffed against its HEAD version (`ast.dump` equality),
  so a comment/whitespace/formatting-only edit no longer pushes the graph toward
  `update`/`rebuild`. Docstring edits still count as structural. The payload gains
  `structural_changes` / `cosmetic_changes`; the AST diff is skipped for change
  sets > 25 files (which already route to a full rebuild).
- Optional **lean tool surface**: `GRAPHIFY_TOOLSET=lean` exposes a coherent,
  mostly dependency-free core that still supports the whole flow — build, orient
  (`graphify_overview`), find (`graphify_search`), traverse (`graphify_subgraph`,
  `graphify_neighbors`), jump to source (`graphify_node_details`), plus
  `graphify_communities` and `graphify_freshness`. `graphify_locate` is included
  only when the `[semble]` extra is installed (otherwise it would just error), and
  `graphify_overview` filters its suggested next steps to the active surface so it
  never points at a trimmed tool. Default `full` is unchanged.

## [0.1.0] - 2026-06-13

### Added
- Initial release.
- CLI-backed tools: `graphify_build`, `graphify_query`, `graphify_path`,
  `graphify_explain`, `graphify_add`.
- graph.json analysis tools (no CLI required): `graphify_overview`,
  `graphify_god_nodes`, `graphify_communities`, `graphify_surprises`,
  `graphify_search`, `graphify_neighbors`, `graphify_subgraph`,
  `graphify_node_details`, `graphify_freshness`.
- Resources: `graphify://report`, `graphify://graph`, `graphify://community/{id}`.
- Prompts: `onboard`, `trace_bug`, `explain_flow`.
- LLM-friendliness: tool annotations, server instructions, `as_json` structured
  output, and token-budgeted subgraph extraction.
- Packaging (`graphify-mcp` console script), pytest suite, ruff config and CI.
