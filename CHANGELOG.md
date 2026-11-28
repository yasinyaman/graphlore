# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Rust: the impl a turbofish names, and blanket impls kept off library
  values** (two conformance cases, found by rust-analyzer on ureq and
  ripgrep; `oracle_rust.py`). `RequestBuilder::<WithBody>::new()` is the
  `new` of `impl RequestBuilder<WithBody>`, not of another impl of the type
  (`Def::owner_targs`, `RefFact::path_targs`). A method of an impl written
  for the impl's own type parameter (`impl<'a, M: Matcher> Matcher for &'a
  M`) is no method a library's value is asked (`Def::blanket`): ripgrep's
  `self.regex.find(…)` on a `regex_automata` value names nothing of the
  project now, and a `String`, `Vec` or primitive field counts as the
  library's where the project defines no type of the name. On ureq the
  edges rust-analyzer contradicts fall from 40 to 34.
- **The Rust sweep** (`docs/rule-sources.md`, 'Phase 2b: the sweep'): the
  Rust Reference file by file and rustc's resolver and method probe, 121
  sections, read by a smaller model and checked by cargo, rust-analyzer
  (`verify_cases.py --binding`, `oracle_rust.py --dump`) and the engine. 208
  new conformance cases (285 Rust cases; the engine holds 177, precise mode
  253) and 509 rules in the inventory, every section read. The binding check
  counts a use rust-analyzer binds to the function of an `impl Trait for X`
  as the trait's method, which the graph names.
- **`graphlore-out/`: the output directory under graphlore's own name**
  (release B's last step, `docs/native-engine.md`). Builds write
  `graphlore-out/graph.json` and `.graphlore_labels.json`. A graph in the
  `graphify-out/` of graphify or graphlore before 0.9.0 is still read while
  `graphlore-out/` holds none. The first build moves that folder to
  `graphlore-out/` before it runs, so the facts cache, the communities and
  their saved names carry over, and the build says so. A `graphify-out/` that
  git tracks is left in place: its saved names are copied and the build starts
  afresh. Both names are left out of every walk (the engine's too), and
  `.graphify_labels.json` is read when it is the only file of names
  (`graphlore_set_labels` writes the new one and removes it).
  `GRAPHLORE_OUT_DIR` is used as given, with no fallback and no move. A
  `.gitignore` line for `graphify-out/` now wants `graphlore-out/`; the
  build's note on an unignored output folder says so. Needs graphlore-engine
  0.5.80, which reads both names of the labels file.
- **JS / TS and Ruby names read as their languages scope them** (three
  conformance cases, found by scip-typescript on zod and got and by
  scip-ruby on faraday). A declaration of a JS / TS module (a file that
  imports or exports) is no name of another file unless imported: only a
  script's top level is global (`new Error()` is the built-in, not another
  module's unexported `type Error`). A method of an object literal is no
  binding of the module (`z.array(…)` through a namespace import is the
  module's function `array`, not `{ array() { … } }`). A Ruby class or module
  nested in another is no bare name outside its nesting (`Logger.new` in a
  spec is the standard library's). On zod the edges scip-typescript
  contradicts fall from 301 to 33, on got from 161 to 14 (98.5% of the edges
  it has an answer for go where it says, from 95.9%). A module's method
  called bare (`URI(url)`) is still guessed by name: a case, open.
- **Java overloads that differ in a library type** (nine conformance cases,
  found by scip-java on gson; `engine/src/resolve/mod.rs` `arg_fits`,
  `java_library_arg`). A class literal is a `Class`, more specific than
  `Type`; a string literal is a `String` (a final class: no `Character`, no
  `Throwable`) and a character literal (`Arg::Char`) a `char`; a cast, a
  local declared with a primitive (now a local, which hides a field of its
  name), both branches of `?:` and `new StringReader(s)` name their type,
  and an object of a library's class is no instance of a class of the
  project. On gson the edges scip-java contradicts fall from 1,090 to 385
  (93.3% of the edges it has an answer for go where it says, from 89.1%);
  of the 45 audit repositories only gson's and retrofit's graphs change.
- **Precise mode reads scip-java and scip-ruby indexes** (`docs/concepts.md`,
  "Java", "Ruby"): a Java constructor `` `<init>` `` is named as its class; a
  Ruby `X.new` names `initialize`, and a writer `x=` is written `x`. gson
  (15,929 confirmed) shows the engine's Java overload choices the compiler
  settles; 30 of faraday's 51 contradicted edges are RSpec `let`s Sorbet does
  not know. `conformance.py --scip` runs the Java cases through javac with
  the SemanticDB plugin (`SEMANTICDB_JAVAC`) and the Ruby cases through
  scip-ruby's native binary: 62 and 35 hold.
- **Precise mode reads `rust-analyzer scip` indexes** (`docs/concepts.md`,
  "Rust"): a crate (`crate/` under its package) is written by the package's
  name or `crate`. On four Rust repositories rust-analyzer confirms 14,940
  edges; the 75 Rust conformance cases hold in precise mode.
- **Precise mode reads scip-typescript indexes** (`docs/concepts.md`,
  "TypeScript and JavaScript"). An accessor (`` `<get>body` `` /
  `` `<set>body` ``, both written at every use) is one use of the property, a
  `` `<constructor>` `` is the class's `.constructor()` node and a use of the
  class, and a member read by a string or a module named by an import's string
  is its name. On got, uses left out as mismatched fall from 6,501 to 687; on
  six repositories scip-typescript confirms 18,364 edges. A constructor's use
  confirms an edge to its class beside one to the constructor (C# too).
- **Precise mode reads scip-go and scip-dotnet indexes**
  (`docs/concepts.md`, "Go and C#"). The SCIP reader takes the two
  indexers' symbols: a Go package named by its import path is its last
  segment (the one before a major version) and an import writes the path; a
  C# `` `.ctor` `` is named as its type (an implicit one is the type) and an
  attribute without its suffix;
  a first line's byte-order mark is skipped; a symbol defined more than once
  (a partial type, or namesakes scip-dotnet gives one symbol) is every
  definition, so an edge to the second is confirmed, not contradicted. A
  definition's signature naming what holds it (Go's receiver) adds no edge,
  and an edge to a constructor where the index names only its type
  (`new T()`) is not contradicted. On chi, uses left out as mismatched fall
  from 1,762 to 0, and contradicted edges on commandline from 496 to 121.
  On four Go repositories scip-go confirms 13,446 edges, adds 1,442 and
  contradicts 3; on four C# repositories scip-dotnet confirms 11,457.
  An edge added from an index is `calls` after a `(`, and to state a `writes`
  after an assignment (or a Go composite literal's key) and `reads` otherwise:
  the two indexers mark no access.
  `benchmarks/conformance.py --scip` reads the cases in precise mode (it runs
  the indexer on each case's project, `--jobs` at once): of the 715 Go and C#
  cases the engine alone holds 600, precise mode 675 (two the engine holds
  break, both the indexers' errors; `results-conformance-scip.json`), and
  `oracle_scip.py` takes every definition of a symbol.
- **An edit of a function body no longer resolves the files that only read
  the file's declarations** (`docs/native-engine.md`, "Incremental updates";
  `engine/src/resolve/decl.rs`). A read of another file is recorded as a read
  of all of it or of its declarations (definitions, the references written in
  declarations, class and namespace locals, imports, scope), whose print keeps
  each position as its rank: a line added to a body or a comment moves the
  positions after it and keeps their order. On kubernetes a line added to a
  function body of `kubelet.go`, which 876 files read, resolves 126 of them
  again and takes 24 s instead of 74 s; a method added still resolves all 876.
  `GRAPHLORE_REUSE_VERIFY=1` resolves every file and says which reused
  resolution would have come out otherwise (none on the audit repositories or
  kubernetes), `GRAPHLORE_REUSE_WHY=1` why a file is resolved again,
  `GRAPHLORE_TRACE_FILE=path` which sites read a file and how much of it.
  `benchmarks/incremental_check.py` adds comment lines to its edits
  (`--kinds`) and reports the nodes an update moved.
- **An update resolves again only the files whose resolution read something
  that changed** (`docs/native-engine.md`, "Incremental updates";
  `engine/src/resolve/trace.rs`, `reuse.rs`). The engine records what each
  file's resolution reads (other files' facts, the keys it asks of the
  project-wide indexes, a name not found included) and writes it with the
  file's edges to `cache/graphlore-resolved.bin`; an update takes a file's
  edges from there when nothing it read changed. The graph is the one a full
  build writes, byte for byte (`tests/incremental.rs`;
  `benchmarks/incremental_check.py` makes seeded edits to whole repositories
  and compares each update with a full build; all 45 audit repositories
  agree). On kubernetes an update with nothing changed takes 24 s instead of
  67 s, one after a function added to a file no other reads 28 s; one after a
  line added to `kubelet.go`, which 876 files read, 74 s. The full build pays
  for the recording: 127 s and 5.6 GB instead of 118 s and 5.0 GB. The report's `incremental` says how many files'
  resolution was reused (`resolve_reused`), `phases_ms` splits resolution
  (`resolve.index`, `resolve.main`, `resolve.post`).
- **A Go module's `vendor/` is left out of a build** unless `GRAPHLORE_VENDOR=1`
  (the engine's `--vendor`, `read_vendor`): a directory named `vendor` with
  `modules.txt`, beside a `go.mod`, which `go mod vendor` writes. It holds other
  projects' code, often more of it than the project's own (4,394 of
  kubernetes' 18,946 Go files); imports of its packages are external modules,
  as they are without one. The build reply names the directories left out and
  their source files (`vendored_skipped`, `vendored_skipped_count`).
- **The engine's report gives the memory after each phase** (`memory_mb`:
  resident and peak, from `/proc/self/status`, Linux only), and
  `benchmarks/scale.py` prints it.
- **Scale, measured on kubernetes** (`docs/benchmark.md`, "Scale: kubernetes";
  `benchmarks/scale.py`): 4.2 million lines of Go, 263,363 nodes and 1.87
  million edges with `vendor/` left out; the graph read in 6.7 s, and the node
  tools at a p50 of 1–243 ms. With `vendor/` read (508,099 nodes), a full
  build took 167 s and 7.6 GB, from 236 s and 11.4 GB.

### Fixed
- **Native engine: Go scopes and identifiers, as the specification has them.**
  An unexported name of another package is another identifier, so a member
  lookup through embedded types goes on past it (`v.run()` with `util.T` and
  `A` embedded is `A.run`); a parameter's scope is the function body, not
  its signature (`func Use(Config Real) *Config`: the result is the type);
  a local's scope begins after its declaration and the value it is declared
  with is outside it (`step := func() { step() }` calls the package's
  `step`); an `else if` init declares a new variable; and a field of an
  anonymous struct type has the members its body declares (`c.DB.Pool.Close()`,
  cobra's `commandCalledAs.name`). Six conformance cases fixed (1157 of 1272,
  none broken); of the audit repositories only cobra's graph changes (seven
  `reads` / `writes` of `commandCalledAs`'s fields).
- **Native engine: a local's value is read where the local is declared.**
  It was resolved where the use being resolved is written: in a generated Go
  deepcopy function, which shadows `in` and `out` in every block (`in, out :=
  &in.X, &out.X`), the value's `in` was the local being typed, then the one of
  the block before, and so on, the whole chain again at every use (8.6 s for
  one file of kubernetes). Read where it is declared it is the receiver, as
  Go's scopes say, and `(*in).DeepCopyInto(*out)` calls the field type's
  method; kotlinpoet gains 13 edges (parameters with a default value). A
  conformance case (Go), found by profiling, and goldens for the rule and the
  time it takes.
- **Native engine: a field assigned in another file resolves its readers
  again.** A type's fields come from every file that assigns one (a Go method
  in another file of the package), but a lookup recorded only the type's own
  file, so an update could keep a reader's resolution after a field appeared
  elsewhere. The fields are an index key with a print of their own now.
- **Native engine: community detection settles.** Each settling pass started
  from the last pass's communities, after graphify's size and cohesion splits:
  Leiden merged the split parts again (on zod 355 of 356), the cohesion rule
  split them again a little differently, and settling stopped at its limit of
  seven partitions (11 of the 45 audit repositories, and 4 in a cycle; every
  update of kubernetes that changed the graph, 8.1 s). Leiden now starts from
  the groups the communities were split out of, kept in graph.json as
  `graph.communities.parents` (and in the cache's copy), and a split from the
  communities: all 45 reach a fixed point, with the same communities by
  every measure (`docs/native-engine.md`, "Communities": modularity, share in
  the main directory, test mixing, agreement with graphify), and on kubernetes
  such an update settles in two partitions (2.2 s; the update 28 s instead of
  34 s). `GRAPHLORE_SETTLE_TRACE=1` prints what each pass did;
  `benchmarks/incremental_check.py` gives the nodes an update moved to another
  community and how settling ended.
- **Native engine: a build that changed nothing moved communities.**
  Detection started from the previous communities and ran Leiden again, which
  need not return them: on kubernetes every update with nothing changed ran
  seven partitions (11 s) and moved 6,215 of 263,363 nodes to another
  community. Detection now writes a hash of what it reads
  (`graph.communities.input`: the nodes, the edges and their weights, the
  engine's version); a build whose input has that hash keeps the previous
  communities as they are. An external node's tie between neighbouring
  communities goes to the one with the first member (it went to the one Leiden
  found first): on the audit repositories 191 of 4,067 external and resource
  nodes are in another community than before, and no other node is.
- **Native engine: a file read again with names it no longer sees kept them.**
  A file that saw a project type named like a wrapper (`Optional`, `Box`) was
  read a second time with that name as the project's; from the cache on, it
  kept those facts after the project's type was gone. The facts cache now
  records the names each file was read with (`cache::FORMAT` 3), and a file
  whose names changed either way is read again.
- **Native engine: an update resolved C and C++ code otherwise than a full
  build.** A definition's byte range was left out of the facts cache, and
  resolution reads it (a struct's fields in source order for the functions put
  in its slots, the bodies of anonymous structs and inline namespaces): on
  hiredis an update found 27 of the 88 `dispatches_to` slot edges. It is
  cached now (`cache::FORMAT` 2; a cache written before is not read).
  `benchmarks/incremental_check.py` found it.
- **Native engine: the bases of a type are the same whoever asks first.** They
  were worked out with the state of the reference that asked (whether a type
  was being named, where the use was), and kept: the `dispatches_to` edges of
  overriding methods could differ with the order things were asked in. Each
  base is resolved with the state the main loop gives a base reference, and
  the passes after the main loop (dispatch, slots, resources) start from no
  reference's state. A type whose base was resolved with the wrong state had
  none of its inherited members: zod has 826 more calls (to `ZodType.decode`,
  `encode` and the like, some `INFERRED` now `EXTRACTED`), gson two more
  overrides (`NullSafeTypeAdapter.read` / `write`); the 43 other audit graphs
  and the conformance cases are as they were.
- **Native engine: a literal of many elements reads in linear time.** Each
  element asked its ancestors for a declared value and the literal answered
  by reading all of its children, so a 400 KB Go table took 200 s
  (`golang.org/x/text`'s `tables15.0.0.go`: 211 s → 1.4 s). Kubernetes builds
  in 236 s instead of 736 s; a golden holds the linear time.
- **Native engine: the same graph.json on every build.** A C# `using` of a
  namespace listed the files it reached in a set's order, which changed from
  run to run; they are in file order now (all 45 audit repositories build to
  the same bytes twice).

### Changed
- **Native engine: a full build of kubernetes takes 67 s instead of 117 s**
  (`docs/native-engine.md`, "Build time"). The files' references are resolved
  by several workers, one per thousand files to resolve, each with a project
  of its own, the largest files first; the graph is the same byte for byte
  with any number (`GRAPHLORE_RESOLVE_THREADS`; `GRAPHLORE_RESOLVE_TIMES=1`
  prints each worker's time and every slow file). Reading a file looks a
  field's id and a kind's name up once per language and thread instead of by
  string at every call (`parse::Fields`, `parse::Kinds`: kubernetes' `pkg/`,
  3,424 files, reads in 10.3 s instead of 15.8 s). `GRAPHLORE_TRACE_STATS=1`
  prints the time each step of reading a file took. The workers' indexes
  cost 0.7 GB at the peak (6.3 GB; 5.5 GB with one worker).
- **An update costs less where nothing it must redo changed** (kubernetes:
  see `docs/benchmark.md`). The facts cache hands each file's facts over
  instead of copying them, keeps the hash of each file's facts and the names
  it was read with, so an unchanged file is neither hashed nor read again,
  and is not written again when nothing was parsed, read again or removed
  (`incremental.cache_kept`); the resolution cache is not written again when
  every file's resolution was reused (`incremental.resolve_cache_kept`).
  Community detection builds each group's subgraph once and with a map for a
  small group, computes the node strengths once per level, starts from the
  graph without copying it and splits groups in parallel; the value filter
  and the dedup after resolution copy no strings. The report's `phases_ms`
  has `cache_load`, `reread`, `resolve_cache_load`, `facts_hash` and
  `resolve_cache_store`, and `communities` its own `phases_ms`,
  `settle_passes` and `settle_end`. The previous graph's communities are
  kept beside it in `cache/graphlore-communities.bin` with the size and
  modification time of the graph.json they came from, and read from there
  while that graph.json is the one this engine wrote
  (`incremental.previous_cached`; 2.4 s on kubernetes, now milliseconds).
- **Native engine: less memory per build, the same graph.json.** The resolved
  edges stay in their compact form until the files' facts are let go (the
  facts cache is written right after resolution), they are kept typed through
  coupling, communities and the write (`graph::links`: a JSON object per edge
  cost about ten times the edge), and the previous graph is read from the file
  as a stream instead of whole. On kubernetes with `vendor/` read, the peak
  went from 11.4 to 7.6 GB.
- **The node tools keep their indexes per graph** instead of rebuilding them on
  every call: the test nodes (`_test_node_ids`, for the production-first order
  of `graphlore_neighbors`, `graphlore_subgraph` and `graphlore_impact`), the
  importers of each file (`graphlore_tests_for`) and the HTTP and state indexes
  (`graphlore_node_details`), each on the identity of its list as the adjacency
  already was. On kubernetes, p50 per call: `neighbors` 1,671 → 2 ms,
  `subgraph` 1,621 → 5 ms, `node_details` 2,139 → 4 ms, `tests_for` 5,239 →
  818 ms.

### Removed
- **graphify as graphlore's extractor** (`docs/native-engine.md`, roadmap
  release B). The native engine builds every graph: `graphlore-engine` is a
  dependency of graphlore now (`pip install graphlore` / `uvx graphlore` is
  enough; the `engine` extra is kept empty so `graphlore[engine]` still
  installs), and the `graphify` extra and `graphifyy` are gone.
  `GRAPHLORE_ENGINE` takes `auto` or `native`; `graphify` is refused with a
  reason (`code: engine`), as is a build where the engine is not installed.
  Gone with it: the `graphlore_add` tool (it only ran the graphify CLI; 33
  tools now), `graphlore_build`'s `mode` (graphify's LLM extraction) and
  `no_viz` (graph.html is always written), `GRAPHLORE_BIN`, and the graphify
  CLI's subprocess, log condensing and offline key stripping. A graph graphify
  wrote is still read, and `update=True` or `cluster_only=True` on one now runs
  the native engine. The plugins install `graphlore[treesitter,embed]`. The
  numbers that decided it are in `docs/native-engine.md` ("Before graphify
  goes"). The output directory keeps its name in this release.

## [0.8.0] - 2026-11-24

### Added
- **The native engine against graphify, before graphify goes**
  (`docs/native-engine.md`, "Before graphify goes"): the 31 repositories
  compared again, with the share of graphify's `EXTRACTED` calls the native
  graph has (84%), and the ones it lacks read at their sites: minified
  bundles, names matched to the wrong definition, a comparison artifact of
  overloads written out of their class, and one real gap, closed below.
- **The sweep: a language's name-binding rules read section by section**
  (`docs/rule-sources.md`, "Phase 2b"; `AGENTS.md`). A smaller model reads
  the specification and the compiler's binder group by group
  (`.claude/workflows/rule-sweep.js`) and returns rules, cases and reasons;
  the strongest model checks every case before it is kept
  (`benchmarks/sweep_check.py`) and fixes the engine.
  `benchmarks/rule_inventory.py --sections` says which sections are read, and
  `benchmarks/verify_cases.py --binding` asks go/types what each case's names
  are bound to. Go first: all 156 sections read, 241 new cases (927 in all),
  of which the engine held 153; a third round of gap hunting added 18 (317
  Go cases), and the fixes they led to bring the engine to 299 of 317: Go's
  comma-ok forms, `<-ch` and `chan` elements, `new(T)`, `&xs[i]`, `make` and
  `append`, slices, composite literals in a chain, conversions (a reference
  to the type, a value of it), aliases (`type A = T`) and defined types
  (`type Copy Base`: its fields, promotions and an interface's methods), a
  type switch's variable per clause, `range`'s key, `select` bindings,
  explicit instantiation (`Map[int](x)`, `NewStack[int]()`), constants of
  defined types and repeated specs, package variables in a path, bounds with
  type arguments, a receiver's type parameters, a statement's init ending
  with the statement, array lengths and map keys as reads. Against
  `go/types` on cobra, chi, resty and gin-examples: 12,419 of 12,426 judged
  edges right (12,311 of 12,317 before), recall 86.0% (85.4%).
  C# second: 190 clauses of the standard and Roslyn's binder, 325 new cases
  (397 in all, every one asked of Roslyn through `verify_cases.py
  --binding`), of which the engine held 207 and now 294: block scopes,
  attribute names (`[Cache]` is `CacheAttribute`), user-defined operators,
  object initializers writing the created type's members, the class a
  static member is reached through, indexers (`g[1]` reads or writes the
  `this[]` its arguments pick, `base[i]`, `s?[0].M()`, `new Bag { ["k"] = 1
  }`), and the calls the language makes by name: `foreach`'s
  `GetEnumerator` (its variable typed by the enumerator's `Current`, or the
  `T` of `IEnumerable<T>`), `Deconstruct` (by the number of names, an
  extension when the type's own does not take them, each name typed by its
  out parameter), `await`'s `GetAwaiter` (the value typed by `GetResult`),
  a collection initializer's `Add`, `with` writing the copy's members, a
  lambda's parameters typed by the delegate it is given as (a declared
  `Func<…>` / `Action<…>`, or the callee's delegate parameter; an overload
  taken by the lambda's parameter count), `out var r` typed by the out
  parameter, and user-defined operators found through either operand's type
  and picked by the operand types, the checked operator in a `checked`
  context, `a && b` as `operator false` and `operator &`, a condition as
  `operator true`, a cast or a typed declaration as a conversion
  operator, and query expressions (range variables ranging over the source
  and hiding a field of their name up to an `into`, `let`, a second `from`,
  `join`, `group … into`, and each clause a call of the source's `Where`,
  `Select`, `OrderBy`, `ThenBy`, `Join`, `GroupBy` or `SelectMany`).
  Against Roslyn on four repositories: 12,355 of 12,575 judged edges right
  (10,731 of 10,944 before), recall 80.0% -> 89.9%.
- **SCIP precision mode** (`GRAPHLORE_SCIP`, `src/graphlore/scip.py`). When
  the project has a SCIP index (`index.scip`, made by `scip-python`,
  `scip-typescript`, `rust-analyzer scip` and others), the edges the
  navigation tools walk are checked against it at read time: an edge the index
  agrees with is confirmed (`confidence: EXTRACTED`, `scip: "confirmed"`), a
  pair the graph lacks is added (`calls` or `references`, `resolved_by:
  "scip"`), and a call or reference it binds elsewhere is marked
  `AMBIGUOUS` (`scip: "contradicted"`), never removed. The index is decoded
  with the standard library (no `protobuf` package), cached on the file, and
  nothing is run or fetched; an index made in another directory is read by
  the project's own paths. Replies carry `resolved_edges.precision` and a
  row's `scip`. On httpx: 3,697 edges confirmed, 333 added, 15 contradicted
  (all 15 the indexer's own slips), under a second. A use whose source text is not its symbol's name (an indexer's slip,
  an alias) is left out and counted (`mismatched`), and a symbol is matched to
  its definition by its descriptors too, so the installed copy of a package
  (`click 8.5.0`, module `click.utils`) finds the project's own (`src/click`).
  `benchmarks/oracle_scip.py` compares a whole native graph with an index as
  the compiler oracles do.
- **Workspaces: several repositories as one graph** (`docs/workspace.md`).
  A project folder that is no git repository and holds repositories one or two
  levels down (or a `.graphlore-workspace.json` naming them) is built as one
  graph by the native engine: each repository's own tracked files, its commit
  in the header (`graph.repos`), imports resolved to the importer's own
  repository first (Python, Java and Kotlin, C#, PHP, Rust crates, JavaScript
  and TypeScript package names, Go module paths, Ruby), route ids led by their
  repository, and a request to a host the workspace file gives a repository
  linked to that repository's routes. `graphlore_freshness`, the graph age,
  the changed-file notes, co-change and the watcher read each repository
  against its own commit; `graphlore_diff` takes `repo`, `graphlore review`
  takes `--repo`, `graphlore_overview` counts each repository and the edges
  between them, and `graphlore_search` / `graphlore_locate` take a `scope`
  (`scope="api"`). The post hook names the repository whose HEAD moved. A
  project that is one repository is built and read as before.
- **Claude Code and Codex hooks** (`graphlore hook pre|post`). Before a
  `Grep` or `Glob`, or a shell search (`rg`, `grep`, `git grep`; Codex has
  no Grep tool), for one identifier the graph defines, a line of context names where
  it is defined and that `graphlore_neighbors` / `graphlore_impact` answer
  the question such a search usually leads to; after a git command that moves
  HEAD past the graph's build commit, or an edit of a source file, a line says
  the graph is behind, once per state. The hook never blocks or changes a
  call, imports none of the server (about 50 ms a call), keeps an index of the
  graph's names under `GRAPHLORE_CACHE_DIR`, passes on only plain paths and is
  silent on any error. The plugin ships them in `hooks/hooks.json`, the
  default place of Claude Code and Codex alike; `graphlore setup
  claude-code|codex --with-hooks` merges them into `.claude/settings.json` or
  `.codex/hooks.json`, keeping every other hook (Codex runs a hook once it is
  trusted in `/hooks`). Codex's file edits (`apply_patch`) are read for the
  files they name. `GRAPHLORE_HOOKS=0` turns them off.
- **A GitHub Action for `graphlore review`** (`action.yml` at the root,
  `uses: yasinyaman/graphlore@<tag>`): it installs graphlore with the native
  engine from its own copy, reviews the pull request against its base commit,
  writes the report to the job summary and posts it as one comment it updates
  on each push (a fork's read-only token: the summary only); `graphlore-version`
  installs a release from PyPI instead of its own copy. `fail-on` fails
  the step on a gate. graphlore's own pull requests run it
  (`.github/workflows/review.yml`). `docs/review.md` covers the command, the
  Action and a GitLab CI job.
- **`graphlore review`: what a commit range changes, read off the code
  graph.** `graphlore review --base origin/main` (or `--base HEAD~1`, the
  default) compares the checked-out commit with its merge base and reports
  the definitions the change touches (each changed line of a source file
  given to the innermost definition around it, a line no definition holds to
  the file's top level), what depends on any of them within `--hops` (one
  walk, nearest first, production before tests, cut at 60 rows with the rest
  counted), the tests that reach the changed definitions (direct or through
  one caller) and the ones no test reaches, the import cycles through changed
  files and the `.graphlore-layers.json` violations in them, each marked
  `introduced` when the import is written on a changed line. Cosmetic-only
  files and blank lines are left out; a heading that names the code and the
  file or class that holds it are not listed as dependents (the files that
  import it still are), and a changed variable, constant or field is never
  "untested". Definitions the change deletes or renames, and those of a
  removed file, are named under "Removed" from the two commits' sources. It
  builds the graph at the head commit with the native
  engine when it is missing or older (`--no-build` refuses instead), writes
  Markdown for a pull-request comment (repository names as code spans, at
  most 20,000 characters) or `--format json`, and exits 1 when a
  `--fail-on cycle,layer,untested` gate fails, 2 when no review can be made
  (an unknown ref, a shallow clone, another commit checked out). No model and
  no network.
- **The agent evaluation has a baseline and repeats** (`benchmarks/agents`).
  `--variants baseline` runs each agent with no graphlore, in a copy of the
  repository without `graphify-out/`; `--repeat N` asks each task N times (no
  agent CLI takes a seed). Every arm keeps the agent's own read and search
  tools: the Claude Code adapter allowed only `mcp__graphlore` before, so its
  `-p` runs had no Read or Grep. Rows record every tool called (`calls`),
  and `score.py` reports solved per repetition as mean ± standard deviation,
  tokens ± their deviation, and each graphlore arm's difference from the same
  agent's baseline. Still not run: it needs each agent's own login.
- **Stateless streamable HTTP** (`GRAPHLORE_STATELESS_HTTP=1`): each request
  is answered on its own, with no `Mcp-Session-Id`, so replicas behind a load
  balancer can share the traffic; `GRAPHLORE_JSON_RESPONSE=1` answers with a
  JSON body instead of an SSE stream. Both are passed to the SDK's
  streamable-HTTP app on every HTTP path (plain, API key, OAuth) and ignored,
  with a warning, for `sse`. Over HTTP, `GRAPHLORE_PROJECT_FROM_CLIENT` now
  stops the server at boot with exit code 2: `graphlore_project` is refused
  there, so no project could ever be set.
- **The built-in search's kernel runs in the native engine** (graphlore-engine
  0.5.80, not yet published: `SearchKernel`, `KERNEL_ABI = 1`). When the
  engine is installed, its port of `search.py`'s tokenizer and BM25F loop
  replaces the Python reference (`GRAPHLORE_SEARCH_KERNEL=python` forces the
  reference); `tests/fixtures/search_golden.json` holds both to the same terms
  and bit-identical scores, with new cases for non-ASCII capitals, `İ`, `ß`,
  final sigma and Unicode digits. On every file of the 45 audit repositories
  the two agree. Tokenizing is about 2.5× faster and scoring about 1.4×; a
  whole index build or query is not measurably faster, since reading the
  definitions dominates.
- **`graphlore_engine.definition_spans`** (engine 0.5.80): `file_spans`
  without the rest of the file's facts, the same spans on all 5,073 source
  files of the audit repositories in a fifth of the time. graphlore's span
  engine uses it where Python tree-sitter is missing. With Python tree-sitter
  installed `spans.py` keeps its own reader, which is as fast or faster in
  most languages.
- **`publish.yml` can be run by hand** (`workflow_dispatch`): it uploads the
  `pyproject.toml` version to PyPI, then creates the `v<version>` tag and
  release. A published release still starts it as before.

### Changed
- **`graphlore_label_communities` is marked read-only** (`read_only_hint`):
  it returns names and saves none (`graphlore_set_labels` does), and the
  server instructions no longer list it among the tools that write under
  `graphify-out/`.
- **The README says what graphlore is for beside a language server and
  built-in code search**: the questions that need the whole repository at once
  (what a change breaks, cycles, layers, routes, shared state), one budgeted
  reply each. A new FAQ entry and `docs/comparison.md` place it beside Claude
  Code's LSP tool, code search and other code-graph MCP servers, each claim
  from the project's own page, dated. Graphify is linked at its source
  repository (`Graphify-Labs/graphify`, which its PyPI page names as the
  official one) instead of graphify.net, in the README, `pyproject.toml` and
  the server's docstring; the plugin descriptions no longer say "instead of
  grepping".
- **`import graphlore` no longer loads the server.** The package's exports
  (`main`, `mcp`, `__version__`, `api_uses_for_source`, `routes_for_source`)
  load on first use, so `graphlore setup`, `clients` and `agents-md` start in
  about 25 ms instead of 0.6 s (the MCP SDK and the 34 tools were imported
  first).
- **Communities are named from the graph by default, with no model.**
  `graphlore_label_communities(method="auto")` now returns derived names
  (`method="derived"`): the build's own `community_name` on a native graph,
  else the engine's rule applied in Python (the member with the most edges,
  internal before external, after the directory more than 60% of the members
  share), which `tests/test_engine.py` holds to the engine's names. `auto`
  used to sample the client's model when it could and fall back to bare
  `Community N` otherwise; MCP's 2026-07-28 revision deprecates sampling
  (SEP-2577) and a stateless server has no session to sample through, so
  `method="sampling"` is now only used when asked for.
  `graphlore_sampling_status` reports `preferred_method: "derived"`.
- **Every `lean` tool's description says what it is for and what it is not
  for** ("Use for: … Not for: … (use X)"). Claude Code defers MCP tools behind
  its tool search, where a tool is picked by its name and description alone;
  the guidance was only in the server instructions. `graphlore_impact`'s and
  `graphlore_locate`'s descriptions are shorter to make room. `docs/clients.md`
  says how to load graphlore's tools up front (`"alwaysLoad": true`).
- **The built-in search index is the default, semble is opt-in.**
  `GRAPHLORE_SEMANTIC_BACKEND` unset (or `auto`) now means graphlore's own
  index whether or not semble is installed; semble answers only with
  `GRAPHLORE_SEMANTIC_BACKEND=semble`, and the plugins (Claude Code, Agent
  Plugins, Gemini CLI) no longer install the `semble` extra (`[semble]` stays
  for those who set it). The built-in index joins every hit to its definition
  and needs no network for BM25; semble downloaded its model on the first call
  whenever it was installed, and a blocked download held every call that
  tried it. The cost is ranking: on semble's own query set the built-in index
  scores NDCG@10 0.814 (BM25) / 0.809 (with `[embed]`) against semble's
  0.852. With `[embed]`, which the plugins still install, the dense tier
  downloads `minishlab/potion-code-16M-v2` on first use as before, and falls
  back to BM25 once when it cannot.

### Fixed
- **Ruby: a bare name as a hash's value is a method call**
  (`{ status: status }`); the key names nothing. faraday's `Response#to_hash`
  had no edge at all (12 calls more in faraday, 6 in thor).
- **Seven name-resolution rules read off the compilers' answers on whole
  repositories** (engine 0.5.80, `benchmarks/oracle_rust.py`,
  `oracle_csharp.py`; each a case in `benchmarks/conformance.py` and a golden
  in `engine/tests/edges_golden.rs`). Rust:
  - a name after `::` or `.` in a macro's arguments is the end of a path or a
    member, never a bare name (`assert!(f(Mode::Count))` read a struct
    `Count` of the module);
  - the bound of `impl<S: Sink> Core<S>` bounds `Core`'s parameter, and an
    impl's own parameters are its methods': a method called on a field
    typed `S`, and `S::Error` in a signature, are the trait's, not a blanket
    `impl Sink for &mut S`'s;
  - an item of another module is in scope only through a `use`: `Result` in
    a file that imports none is the prelude's, not a module's `type Result`;
  - `len` / `is_empty` after a call that returns a slice ask the slice, not
    an element's method of the name;
  - a path that starts at a primitive type (`usize::from`) is the
    language's, and `as_str`, `parent`, `is_file`, `peek` and the like are
    standard-library names no guess goes to.

  C#: an argument that is the caller's parameter, declared with exactly the
  type an overload's parameter is written with, picks that overload (an
  identity conversion is the better one). Against rust-analyzer on ureq,
  ripgrep, axum-realworld and actix-realworld the wrong edges go from 169 to
  98 (precision 98.6% to 99.2%), against Roslyn on restsharp, commandline,
  aspnetcore-realworld and todoapi from 224 to 213; no edge the compilers
  confirm is lost, and the neutrality check finds no new leak.
- **Six Python name-resolution rules read off scip-python's answers** on
  httpx, click and fastapi-template (engine 0.5.80, `benchmarks/oracle_scip.py`;
  each a case in `benchmarks/conformance.py` and a golden in
  `engine/tests/edges_golden.rs`):
  - a bare name a module neither defines nor imports is a builtin or
    nothing: `open(p)` in one example script was a call of another script's
    `def open`, `EXTRACTED` (a qualname holds no module path, so the bare
    lookup matched every module's; Rust had the same filter already);
  - the class attributes of an `enum.Enum` / `IntEnum` / `StrEnum` / `Flag`
    subclass are its members, and `State.CLOSED` is a reference to the enum;
  - a typed parameter's default value (`timeout: float | UseDefault =
    USE_CLIENT_DEFAULT`) is read by the function: the node's kind
    (`typed_default_parameter`) was taken for a type position;
  - a module variable assigned a type (`TimeoutTypes = Union[…]`,
    `Scope = MutableMapping[…]`, `T = TypeVar("T")`, `X: TypeAlias = …`) is a
    type alias, and an annotation that writes it references it;
  - a field assigned a parameter in `__init__` (`self.request = request`,
    `request: Request`) has the parameter's type: the field and the
    parameter of one name were made one local and the parameter dropped;
  - `Optional[X]` and `X | None` are X, no closed wrapper; a field assigned a
    union-typed parameter (`stream: SyncByteStream | AsyncByteStream | None`)
    answers a member with the one type of the union that has it, `INFERRED`
    (`self.stream.close()` inside `Response.close` was a guess of
    `Response.close` itself).

  Against scip-python, with the same oracle before and after: httpx 99.0% →
  99.1% precision, 88.7% → 92.8% recall; click 97.3% → 97.7%, 90.5% →
  91.3%; fastapi-template 100% → 100%, 87.6% → 92.4%. What remains wrong is
  mostly the indexer's own (a function defined under `try`, two local classes
  of one name taken for one symbol).
- **Go and Kotlin name-resolution rules read off `go/types` and the
  unresolved calls** (engine 0.5.80; each a case in
  `benchmarks/conformance.py` and a golden in `engine/tests/edges_golden.rs`).
  Go:
  - an element of a named slice or map type (`type byName []*Command`,
    `c[i].Name()`, a `range` over it) and of a slice field
    (`n.children[i].Sort()`) is the element type's;
  - an embedded field read by its type's name (`f.basicWriter.flush()`) is
    the embedded type, and a bare read of it writes no edge to the struct;
  - a function that returns a named non-struct type (`func Chain(…)
    Middlewares`) gives that type to a chain (`Chain(…).Handler(h)`);
  - the parameter and result types of a function-typed struct field
    (`RunE func(cmd *Command, args []string) error`) are references.

  Kotlin and C#: an extension's result in the middle of a chain
  (`x.ext().member()`, also through a `typealias`) is its declared type,
  `EXTRACTED`. Against `go/types` on chi, cobra, gin-examples and resty, with
  `benchmarks/oracle_go.py` now counting a method receiver's type as the
  declaration it is: recall 85.2% → 85.4%, missed method calls 91 → 53,
  12,311 of 12,317 judged edges right. Kotlin's real unresolved calls
  (`benchmarks/unresolved.py --real`, kotlinpoet, clikt and two realworld
  apps) 129 → 110.
- **`spans.py` and the native engine read the same definitions on 44 more
  files of the audit repositories** (engine 0.5.80). Compared on all 5,073
  source files of the 45, they disagreed on 45; each case was read against the
  source and the wrong side fixed. `spans.py`: a Python file its `ast` cannot
  parse (3.14's `except A, B:` in fastapi-template, which lost all three of
  its functions) takes the engine's spans; `void *(CJSON_CDECL *f)(…)` and
  `void (Bench::*m)(…)` fields keep their names (a declarator before an
  `ERROR` in the parentheses, the engine's order); `Env::~Env() = default;`
  out of a class is a definition (its grammar reads an assignment). Both:
  `std::function<R(T)> f{[…](T x) { … }};` is a variable, not a function
  (cpr). The engine: `std::thread t(Env::Entry, this);` is a variable, not a
  function declaration, and `Env::Entry` an `indirect_call` (leveldb); Kotlin
  is parsed with `external` among a declaration's modifiers blanked, so
  `internal external interface RegExpMatch { … }` is no longer lost
  (kotlinpoet); and a C# 14 `extension(RestRequest request) { … }` block,
  which its grammar reads as a constructor named `extension`, is a scope
  only: restsharp loses 31 `.extension()` nodes, and the 194 members of the
  blocks are the class's (`BodyExtensions.HasFiles`, not
  `BodyExtensions.extension.HasFiles`) with every call, read and write they
  had, the block's parameter still typing their bodies. On the other 42
  repositories the native graph is unchanged. One file still differs: a
  generic block (`extension<T>(…)`, restsharp's `ResponseThrowExtension.cs`)
  the engine's grammar cannot read ends its class a line early.
- **`graphlore_package_apis(package=…)` fits the 35,000-character reply
  budget.** It cut each symbol's file list (down to three files) but not the
  number of symbols, so a package used for hundreds of names was still over:
  on native graphs (engine 0.5.79) zod's own name in its repository was 44,600
  characters as JSON (29,700 as text, 211 symbols), gson's `java.util` 36,500.
  The reply is now measured in both forms and cut further until it fits
  30,000 characters at the default `limit` (a larger `limit` is a
  proportionally larger budget): the file lists first, then which symbols have
  one (the most used keep theirs; `symbol_files_left_out` counts the rest,
  whose names stay in `symbols` and in the text), then the `files` and
  `qualified_paths` lists, the symbol names last (`symbols_left_out`). zod is
  now 25,100 characters (52 of 211 symbols with their files), gson 29,600,
  and `benchmarks/polyglot.py --engine native --set 2` no longer reports
  `REPLY-TOO-LARGE` for either.
- **`benchmarks/polyglot.py` no longer reports three artefacts of its own
  checks.** `BUILD-SMALL-GRAPH` compared every node of the graph
  `graphify update` built, external names and the `.json`, `.yml` and
  `.csproj` files graphify counts as code included, with the native build
  `graphlore_build(code_only=True)` writes now that `GRAPHLORE_ENGINE=auto`
  picks the engine (aspnetcore-realworld 1,652 against 598, symfony-demo 937
  against 375); both counts now take only the nodes of source files of the
  14 languages the audit reads, none external (637 against 598, 338 against
  375). `FETCH-SHADOW-SPAN` read
  a googletest `TEST(Suite, Name)` span named `Suite.Name` (engine 0.5.76) as
  another symbol's on graphify's `TEST()` node (cpr, 29 rows); a C / C++ test
  macro's span named so is the node's own, by the rule
  `graphlore_unreferenced` skips such tests by. `SKELETON-HEADER-NO-NAME`
  looked for a Rust macro's name with its `!` (engine 0.5.75's `debug!`) in
  `macro_rules! debug {` (ripgrep); the `!` is stripped first.

## [0.7.0] - 2026-11-23

### Fixed
- **`graphlore_unreferenced` no longer lists googletest cases as dead code
  on native graphs.** Since engine 0.5.76 a `TEST(Suite, Name)` /
  `TEST_F(Fixture, Name)` is the method `Suite.Name`, not a function called
  `TEST`, so the rule that skipped tests by name stopped matching: on cpr
  112 of its 220 rows were tests. A C / C++ definition in a test file whose
  line is a test macro (a name in capitals given two bare names, the
  engine's own rule) is skipped. `benchmarks/polyglot.py` now expects 34
  tools, knows the `documents` relation and runs `graphlore_bridges` and
  `graphlore_god_nodes`' `rank=` modes.
- **`benchmarks/polyglot.py --setup --engine native` no longer needs
  graphify.** Setup ran `graphify update .` whatever the engine and stopped
  at the first repository without the deprecated CLI; with `--engine native`
  it now only clones and the native engine builds the graphs.
- **A reply with default arguments stays under 35,000 characters (~10k
  tokens)**, what Claude Code, Codex and Gemini CLI all keep; the result's
  `structuredContent` carries the same payload, so a large JSON payload was a
  large reply even for a text call. Measured on graphlore, httpx, nginx and
  Django, every read-only tool now fits in both forms (it was 522,600
  characters for `graphlore_surprises` on nginx, 75,000 on Django, 47,000 for
  `graphlore_cycles` on Django):
  - JSON replies are compact (no indentation: a third fewer characters);
  - `graphlore_surprises` lists 10 writers, 10 readers and 5 pairs per shared
    state (`writers_total`, `readers_total`, `pairs_left_out` count the rest)
    and its `limit` defaults to 10 instead of 20;
  - `graphlore_cycles` lists at most 50 members per group (`size` counts them
    all; `path` is still the whole loop);
  - `graphlore_skeleton` cuts its first file by symbol past
    `max(budget_tokens, 6000)` tokens and says how many it left out
    (`symbols_left_out`), where a module of thousands of lines came whole.
  `benchmarks/polyglot.py`'s `MAX_REPLY_CHARS` goes from 90,000 to 35,000, and
  a test holds the budget on the polyglot fixture.
- **Every tool parameter has a description, and the tool definitions fit
  every client.** All 141 parameters had none: each tool's whole docstring,
  `Args:` block included, went out as its description, and function-calling
  APIs (Codex, Kimi and other OpenAI-style clients) read a parameter's own
  `description`. `_tool` now moves the `Args:` block onto the input schema
  (`_doc_args`, `_describe_parameters`). The 16 descriptions over 900
  characters were cut to what a model needs to pick and call the tool (the
  detail is in `docs/tools.md`): `graphlore_impact`'s was 4,151 characters,
  past the 4,096 at which Claude Code cuts one. What a client loads: `full`
  60,400 → 48,400 characters (~15k → ~12k tokens), `lean` 20,400 → 16,200,
  `locate` 6,900. The schemas take only plain types now: `graphlore_layers`'
  `rules` and `graphlore_set_labels`' `names` are published as JSON strings
  (an object still works; `_json_object`), `graphlore_duplication_scan`'s
  `min_score` defaults to `-1` (the backend's floor) instead of `null` — no
  `anyOf`, free-key object or `null` left, which strict function-calling
  modes and Gemini's function declarations reject. Tests hold each limit
  (900 characters, a schema under Codex's 5,000 bytes, a tool name Gemini CLI
  keeps, instructions under 4,000 characters).
- **graph.html's Show filters left test code on screen.** Unticking *tests*
  (and likewise *external packages*, *vendored files*) filtered the node views
  but not the overview, the page's first screen: its community bubbles were
  sized and linked over every node, and communities of test code only stayed
  drawn — 1,661 of Django's 3,444. The overview now counts, sizes and links
  each community by what the filters leave (`visibleCommunities`, the page's
  `overview:begin` block, checked under node against `page_data`), leaves out
  the empty ones and says how many (with tests off on Django: 1,893 of test,
  external or vendored code only). The panel's lists follow the filters too —
  a community's members, a node's edges, the impact rows and the findings —
  each with a line counting what it hid. Since external packages are off by
  default, the overview's sizes now leave them out from the start, as the
  community views always did. Search still finds a hidden node by name.
- **graph.html printed "null" under a node's name** in its details panel, for
  every node without a finding badge: the panel handed the missing badge row to
  `append()`, which writes `null` as text. Parts left out are dropped now.
- **`graphlore_unreferenced` no longer grows faster than the graph.** On
  Django's graph (70,195 nodes) it ran for over 15 minutes and did not finish;
  it now answers in 9 s (`limit=200`, spans parsed cold), and in 1.2 s instead
  of 4.3 s on graphlore's own graph (4,630 nodes). The hook check
  (`_hook_base`) re-read and re-tokenized a base class's whole body for every
  method of every subclass, and walked the base chain again for each class of
  the same name: Django's `SimpleTestCase(unittest.TestCase)` leads back to
  its own `TestCase`, so each helper of each test class scanned the test
  case bodies four times over. Its answers are now kept per call, and a
  file's spans (indexed by definition line), a span's identifiers, a class's
  members, a name's other definitions and a class's referencing files are
  each worked out once per call. The answers are unchanged (the same JSON on
  graphlore, httpx and nginx before and after). With that, `graph.html`'s
  findings carry unreferenced code whatever the graph's size:
  `VIZ_UNREFERENCED_MAX` (20,000 nodes) is gone.
- **Native engine: a build no longer panics on a multibyte character**
  (engine 0.5.78). `state.rs`'s `holds_container` and `resources.rs`'s data-model
  detection capped how much of a value they scanned by slicing it at a byte
  index (`&t[..t.len().min(200)]`), which panics when that index lands inside a
  UTF-8 character — a curly quote, an emoji. Found by building the graph of
  Django, where it killed the whole build (`src/state.rs:269 ... is not a char
  boundary; it is inside '”'`). A new `generic::prefix` cuts back to a char
  boundary; both sites use it. The engine release to publish is **0.5.79**
  (this fix and the `documents` edges under Added), not 0.5.77.

### Deprecated
- **graphify as graphlore's extractor**, going in the next release with what
  only it does: `graphlore_add` (a source by URL), `graphlore_build`'s `mode`
  (`"deep"`), `GRAPHLORE_ENGINE=graphify`,
  `GRAPHLORE_BIN` and the `graphify` extra. A build graphify answers says so.
  What graphify does that the engine does not yet is listed in
  `docs/native-engine.md` ("After graphify").

### Fixed
- **Java: a method called `self` on a typed receiver is that method.** The
  rule for `Outer.this` took any `x.self` whose `x` has a class for its type,
  so `r.self()` on a `Repo r` became a call of `Repo` itself. It holds only
  where the path names the class. Found by `benchmarks/neutrality.py`, which
  failed on it before this change too.

### Changed
- **The `engine` extra needs `graphlore-engine` 0.5.79** (`>=0.5.79,<0.6`), the
  first engine release on PyPI: graph.json schema 7, with `documents` edges.
- **The documentation describes the native engine as the default.** The
  agent instructions, the exploration skill, `docs/concepts.md`,
  `docs/native-engine.md` (its introduction and the roadmap's release A, now
  done), the README and CONTRIBUTING no longer speak of a "Graphify-built"
  graph or of an experimental, opt-in engine; graphify is described as the
  deprecated fallback. The post-commit hook example runs the native engine
  (`python -m graphlore_engine build . --incremental`), whose update also drops
  the nodes of deleted files.
- **The native engine builds the graph by default.** `GRAPHLORE_ENGINE`
  defaults to `auto`: graphlore's own extractor when it is installed, graphify
  only when it is not (and for `mode`, and for `update` / `cluster_only` of a
  graph graphify built). The engine is the new `engine` extra
  (`graphlore-engine`, prebuilt wheels: no Rust toolchain), part of `all`;
  the Claude Code plugin installs it, and `server.json` runs the server with
  `--with graphlore-engine` instead of `--with graphifyy`. With neither
  extractor installed, a build says how to install the engine; an unknown
  `GRAPHLORE_ENGINE` is now an error instead of a graphify build. Without
  Python tree-sitter, spans come from the engine's compiled-in grammars unless
  `GRAPHLORE_ENGINE=graphify`.
- **`graphlore_query`, `graphlore_path` and `graphlore_explain` no longer run
  graphify**: they read graph.json like every other analysis tool, so they
  work on a native graph with no `graphify` binary, and take `as_json`.
  - `graphlore_query` searches the question in the code (the `locate` index;
    without one, the nodes whose names share its words), takes up to 3 hits as
    `seeds` and walks the graph out from them until `budget` (2000 tokens) is
    spent: breadth-first 3 hops, or `dfs=True` depth-first 6 hops. It takes
    `scope`, and a cut says so (`truncated`, `hint`).
  - `graphlore_path` is the shortest path along the edges' direction
    (`direction: forward`), else from the second node to the first
    (`backward`), else either way (`undirected`); each step says which way its
    edge points, and `couples_with` is not walked. It takes `scope`.
  - `graphlore_explain` is `graphlore_node_details` plus the node's
    connections, tallied by relation and direction (`counts`) and listed up
    to `limit` (30).
  - The audit's `CLI-*` checks are replaced by `QUERY-NO-SEED`,
    `PATH-MISSED` and `EXPLAIN-BARE-NAME`.
- **Communities are named by MCP sampling only.**
  `graphlore_label_communities(method="cli")` (`graphify label` with a backend
  API key) is refused (`code: "removed"`), and `method="auto"` goes from
  sampling straight to placeholders; `graphlore_set_labels` takes names the
  assistant writes. `graphlore_sampling_status` drops `backend_key_detected`
  and `graphify_cli_available`.
- **`graphlore://report` is made from graph.json** (`graphlore_overview` and
  `graphlore_surprises`) when graphify wrote no `GRAPH_REPORT.md`, or the graph
  is native and that file is a leftover of an earlier build.
- **Shared state leaves out the test code the engine marks**, not only test
  files: a writer inside a Rust `#[cfg(test)]` module of a source file no
  longer pairs with production code (`test` on a native node). ureq: 112
  pairs to 68.

### Added
- **graphlore installs as a plugin in Codex, GitHub Copilot CLI and Gemini CLI**,
  as it does in Claude Code: the repository root is also an
  [Agent Plugins 1.0](https://github.com/agentplugins/agent-plugins-spec) plugin
  (`plugin.json`, `mcp.json`, `skills/`), which Codex and Copilot CLI read
  through the existing `.claude-plugin/marketplace.json`, and a Gemini CLI
  extension (`gemini-extension.json`, with `.gemini-extension/GRAPHLORE.md` as
  its context file). Each starts the same server with `uvx` from its own copy and
  ships the `graphlore-explore` skill, which moved to `skills/` (the fixed place
  the standard reads; Claude Code's manifest points there too). Checked with
  Codex 0.171, Copilot CLI 1.0.114 and Gemini CLI 0.70. The root `mcp.json`, an
  example config, is now the plugin's server.
- **`GRAPHLORE_PROJECT_FROM_CLIENT=1`: the project comes from the client.** An
  Agent Plugins client starts the server in the plugin's own directory and names
  no project. With this set (and no `GRAPHLORE_PROJECT_DIR`), graphlore asks the
  client for its workspace roots on the first tool call and reads the first
  directory; a client that gives none gets `code: project_unknown` from every
  tool but `graphlore_project` and `graphlore_sampling_status`, with the call
  that names the project. The server instructions say so, the `locate` and
  `lean` surfaces keep `graphlore_project`, and the watcher does not start.
- **OAuth for the HTTP transport**, for remote clients (claude.ai and ChatGPT
  connectors, Claude Code or Codex over HTTP): graphlore as a resource server
  (`oauth.py`, the new `remote` extra: pyjwt). `GRAPHLORE_AUTH_ISSUER` and
  `GRAPHLORE_AUTH_RESOURCE_URL` (and `GRAPHLORE_AUTH_SCOPES`) turn it on:
  tokens your OAuth / OpenID provider signs are checked against its keys
  (RFC 8414 / OpenID discovery, asymmetric algorithms only) and for their
  issuer, audience (the resource URL exactly) and expiry; the MCP SDK's
  middleware answers 401 with `WWW-Authenticate: Bearer resource_metadata=…`,
  403 for a missing scope, and serves the RFC 9728 Protected Resource
  Metadata. graphlore issues no token. It excludes `GRAPHLORE_API_KEY`, and
  `GRAPHLORE_OFFLINE` refuses it (the keys come from the provider: a row in
  the network table). Tested end to end over the streamable-HTTP app with a
  locally generated key.
- **A cross-model evaluation** (`benchmarks/agents/`, `docs/benchmark-agents.md`):
  16 questions about httpx at a pinned commit, each answer checked in its
  source, asked headless of Claude Code, Codex CLI, Gemini CLI, Kimi Code CLI
  and Cursor's agent with graphlore set up as `graphlore setup` writes it,
  with and without the `agents-md` guide; `score.py` gives per agent the
  tasks solved, graphlore calls, runs that used none of the task's suited
  tools, tokens and time. Each agent needs its own login, so it is run by
  hand, not in CI; a `fake` client replays a transcript of real tool calls
  over stdio, and the tests run the whole pipeline with it. Not run on the
  real agents yet.
- **`graphlore setup <client>`, `graphlore clients` and `graphlore agents-md`.**
  graphlore's setup docs were Claude's only. `graphlore setup` prints, or with
  `--write` merges, the block that starts graphlore in Claude Code, Claude
  Desktop, OpenAI Codex CLI (TOML), Google Gemini CLI, Cursor, VS Code with
  GitHub Copilot (`.mcp.json`, the current format), Kimi Code CLI, Zed,
  Windsurf or Cline. A merge keeps the file's other servers, saves the old
  file as `<name>.graphlore.bak` and refuses a file it cannot parse (Zed's
  settings with comments). A project file names no absolute path where the
  client can say "here" (`${workspaceFolder}`, `${CLAUDE_PROJECT_DIR:-.}`).
  `graphlore agents-md` writes the usage guide for agents that read a project
  file rather than MCP prompts or server instructions (Codex and Copilot show
  no prompts): between `<!-- graphlore:begin -->` markers in `AGENTS.md`,
  `GEMINI.md` or Copilot's instructions, naming only the toolset's tools;
  `setup --with-agents-md` writes it into the file that client reads. The
  console script is now `graphlore.cli:main`: with no arguments it runs the
  server as before. New page `docs/clients.md`: each client's files, key, add
  command and limits (tool caps, output truncation, prompts, instructions),
  each read from the client's own documentation (Codex's from its source,
  its documentation site being unreachable here).
- **Graph algorithms beyond the breadth-first walk** (`src/graphlore/algorithms.py`,
  checked against networkx on 300 random graphs; on Django's 77,766 nodes each
  answers in seconds):
  - **`graphlore_path` takes the most certain path**: the fewest edges inferred
    by name (`INFERRED` / `AMBIGUOUS`), then the fewest steps (Dijkstra on that
    pair of costs). Each such step carries `confidence`, `inferred_steps`
    counts them, and `shortest` gives the length of a shorter but less certain
    path when one exists. On a graph with no inferred edge the answer is the
    breadth-first one it was. It no longer walks a document's `documents`
    edges unless one end is a document: of 150 random pairs of Django
    functions, 26 had been "joined" only by a page naming both.
  - **`graphlore_god_nodes(rank="pagerank" | "betweenness")`**: PageRank over
    the dependency edges (a utility everything leans on outranks a big module
    few use: on Django, `Model`, `gettext_lazy`, `SimpleTestCase`, `Field`,
    `AltersData`), and Brandes' betweenness, estimated from `samples` (64)
    hash-picked sources and saying so, exact with `samples=0` (on Django, app
    loading and connection setup: `Apps.populate()`, `ensure_connection()`).
    `rank="degree"` stays the default and its answer is unchanged. 2.5 s and
    1.3 s on Django.
  - **`graphlore_impact` names its `chokepoints`**: from the dominator tree of
    the walked nodes (Cooper, Harvey and Kennedy), the nodes every path to the
    start runs through for many of the others, with how many each `shields`.
    `QuerySet`'s 2,024 dependents within 3 hops: 354 reach it only through
    `django/db/models/__init__.py`, 116 only through `Manager`. 0.07 s.
  - **New tool `graphlore_bridges`** (34 tools): the scope's dependency edges as
    an undirected graph of files, directories or nodes; the bridges (the only
    link between two parts) and cut points (what one side goes through), each
    with what it cuts off, by Tarjan's low-link in one pass. Import edges go
    through the import checks. In the `review` prompt after the layer rules.
- **graph.html opens a node's lines on GitHub.** A node's panel has an
  *On GitHub ↗* link (GitLab and Bitbucket alike) to its `start..end` lines at
  the commit the graph was built from (HEAD when the graph names none), in a
  new tab: GitHub refuses to sit in a frame (`X-Frame-Options: deny`), and the
  page keeps making no request of its own. The repository comes from the
  folder's `git remote` (`origin`, else `upstream`, else the first), read
  locally; only the host and the repository's path reach the page, never a
  user name, token or port, and a host it has no link form for gets no link.
  A Markdown or reST page opens with `?plain=1`, so its line anchors work.
- **Documents link to the code they name** (native engine 0.5.79, schema 7).
  A name of code a document writes — a Markdown code span, a reStructuredText
  object role (`:class:`, `:meth:`, `:func:` …), inline literal or object
  directive (`.. class::`, `.. autofunction::`; a method directive is named
  under its class, a name under `.. currentmodule::` is tried in that module
  first) — is a `documents` edge from the heading it is written under to the
  one definition the name means: its last segments end the definition's
  qualified name and the ones before them are a run of its module path. No
  edge when several definitions answer, for test code, for a module or package
  path, for a code span of one plain word, or from a bare code span to a
  variable or field. 12,278 edges on Django, 263 on httpx; a sample of 30 of
  Django's read right but for one, which the variable rule then removed.
  Before, a document's only edges were the `contains` between its headings.
  - `documents` is in the new `graph._DOCUMENT_RELATIONS`, part of
    `_NON_DEPENDENCY_RELATIONS`: cycles, surprises, layers and
    `graphlore_unreferenced` (same answers on httpx and Django) leave it out,
    and the engine's community detection skips it, so communities stay where
    they were. `graphlore_impact` walks it: the pages that describe a
    definition are what a change to it may need to update.
  - The build reply says `documents: N headings, M links …`.
- **Sphinx documentation written in `.txt` files is read.** A `.txt` file is
  reStructuredText under a `conf.py` whose `source_suffix` names `.txt`
  (`requirements*.txt`, `robots.txt` aside). Django's graph went from 8
  documents to 689, and 14 headings to 6,904.
- **graph.html: a `documents` relation group, and document outlines always
  drawn.** The group has its own colour and filter. A document's page and
  headings are joined by `contains`, which is in the structure group that is
  off by default (it would draw a star around every source file): a community
  of headings read "42 nodes · 0 links". Containment between two document
  nodes (flag 8 in the page data, `file_type: document`) is now drawn whatever
  the structure filter says.
- **`graph.html` shows what graphlore's tools find, not only the graph.**
  - *Findings* (`G`, `#f=`): the cycles `graphlore_cycles` reports (runtime
    import cycles first), the shared state and surprising links of
    `graphlore_surprises`, files that change together, and the candidates of
    `graphlore_unreferenced`. Each row opens its nodes, with the cycle's path
    drawn, and each node carries badges for the findings it is in.
  - The server works them out for the active project's graph, from the tools'
    own JSON, so the page and the tools agree; once per graph.json (path,
    mtime). A tool that fails costs its finding, never the page.
  - A page written for another path than the active project has no findings
    and says why.
  - *Impact* (`I`, `#i=`): what depends on a node, by hop (1–3), in columns,
    with test code counted apart, walked as `graphlore_impact` walks by default
    (the page's `impact:begin` block; a test checks the two on every node).
  - *Details*: a node's span, qualified name, language, route method and
    pattern, and a state node's coupling pairs (hidden ones first); each edge
    row says when an import runs, the edge's context and the line it is
    written at.
  - On a graphify graph: the calls and imports graphlore resolves from the
    source are drawn dotted, and vendored or minified files are hidden behind
    a "vendored files" filter.
  - A resource node (an environment variable, a table) is no longer drawn as an
    external package.
  - Django's page grows from 14.3 to 19.3 MB, and its build from 0 to 15 s of
    analysis.
- **`graph.html`, graphlore's own view of the graph.** Every build writes it
  beside graph.json, whichever engine built the graph (`no_viz=True` skips
  it; graphify is always run with `--no-viz` now), and `cluster_only`,
  `graphlore_set_labels` and `graphlore_prune` write it again. One file that
  needs no network (the drawing code is graphlore's, on a canvas, and a
  Content Security Policy forbids every request), made for large codebases:
  it opens on the communities, one bubble each with the strongest links
  between them, and goes down to one community or one node's 1- or 2-hop
  neighbourhood (400 nodes at most, production code first). Search by name
  or file, a details panel (kind, `file:line`, id, community, connections by
  relation and direction, each a link), filters for relation groups, tests
  and external packages, colouring by community or kind, a back button.
  Names, labels and paths from the repository are embedded escaped and shown
  as text only. A graph over 150,000 nodes is not drawn, and the build says so.
- **A graph viewer of its own, for any graph.json.** The page
  (`src/graphlore/viewer.html`) opens a graph.json the reader picks or drops
  on it, graphlore's or graphify's, with its `.graphify_labels.json` for the
  names, read in the browser: nothing is uploaded and the page still makes
  no request. Every `graph.html` has the "Open graph.json…" button, and the
  page with no graph in it is a standalone viewer, published to GitHub Pages
  by `.github/workflows/pages.yml` (`viewer/`, and `viewer/demo.html` with the
  polyglot fixture). Its `compact()` is the JavaScript twin of
  `viz.page_data`; a test runs it under node on graphify's shapes and the
  native engine's and checks the two agree.
- **The viewer is easier to read and to use.**
  - A node's neighbourhood is drawn in columns instead of a star: what uses
    it on the left, what it uses on the right, the second hop a column
    further out, production code first and grouped by file under the file's
    name. It is never fitted too small to read; the columns that do not fit
    are panned to.
  - Labels never overlap one another or a dot: they are placed by importance
    where they fit, so zooming in shows more.
  - The address bar keeps the view (`#n=<id>&h=2`, `#c=<community>`): a link
    opens it again, and the browser's Back and Forward move between views.
  - Keys: `/` find (`↑` `↓` `Enter` to pick), `Esc` back, `F` fit, `+` / `−`
    zoom, `1` / `2` depth, `?` the list. Zoom and fit buttons too.
  - "Copy for the assistant": a node's panel copies `graphlore_explain`,
    `graphlore_fetch` and `graphlore_impact` calls, its id and `file:line`; a
    community's copies `graphlore_skeleton(community=…)`.
  - On a phone: touch, pinch to zoom, long press for what a node is, and the
    panel as a sheet over the foot of the screen.
- **Prebuilt `graphlore-engine` wheels** (`.github/workflows/engine-wheels.yml`):
  abi3 wheels for Linux (x86-64 and ARM on glibc, x86-64 on musl), macOS
  (universal2) and Windows x64, plus an sdist, each wheel installed and run
  on the polyglot fixture before anything is uploaded. An `engine-vX.Y.Z` tag
  publishes them to PyPI by Trusted Publishing; a pull request touching
  `engine/` builds them without uploading. This is the first step of
  replacing graphify: the plugin can take the engine only as a prebuilt
  wheel, never through a Rust toolchain. The release steps are under
  "Releasing" in `CONTRIBUTING.md`.
- **Implicit coupling is worked out by the native engine** (engine 0.5.77,
  `schema_version` 6, `engine/src/coupling.rs`). The pairing of the
  definitions that share state, which `graph._shared_state` did on every
  call, runs at build time by the same rules: for state that code in two
  files or more touches, each writer with each other definition touching it,
  and the call path that joins the two (calls, dispatch and HTTP, 6 hops) or
  none. A state node carries its pairs in `coupling` (`pairs`, `hidden`,
  `listed` as `[writer, other, hops, forward]`, at most 200, the hidden ones
  first), and a hidden pair is a `couples_with` edge from the definition that
  sees the state to the one that writes it, naming the state in `via` (at
  most 10 per state node; the rest stay on the node and are counted in the
  build reply). `_shared_state` takes the engine's answer where a node has
  one and looks for the path itself otherwise (graphify graphs, older
  schemas, a pair past the limit), so `graphlore_surprises` and
  `graphlore_node_details` say the same either way. `couples_with` is no
  dependency of its own: not a reference for `graphlore_unreferenced`, not an
  edge of a cycle or a surprise, not a step of `graphlore_impact` unless
  `shared_state=True` or `relations="state"`; `graphlore_neighbors` and
  `graphlore_subgraph` show it. Community detection leaves it out. On ureq
  68 pairs, 57 hidden; on this repository 1,232 and 1,081; on the 31 audit
  repositories 3,694 and 3,060, with 1,112 edges. Against engine 0.5.76 on
  those repositories every earlier edge and every community is unchanged,
  and the polyglot audit on native graphs has no finding new or gone. Co-change stays on
  graphlore's side: it is read from git, not from the source.
- **A base's method reaches the methods written for it, in every language
  but Go** (`dispatches_to`, `context: override`, `EXTRACTED`; it was written
  for C / C++ function slots only). A method of a type whose name a method of
  its base, interface, trait or mixin has — the nearest base that declares
  it, constructors left out; where the language has overloads, by
  parameter count, by the types as written when either type has several
  such methods of the name, and in Swift by the labels, which are part of
  the name; in Rust the methods under each `impl Trait for Type`, whether
  or not the type is the project's. `x.connect()` on a trait object now has
  a path to each `connect` written for the trait: 136 edges on ureq (110 of
  the methods no call named before), 83 on this repository (all 83), 1,350 on
  the 31 audit repositories. Community detection leaves these edges out: the
  type's `inherits` / `implements` edge already weighs them, and weighing
  them moves half the nodes to a community of another id for a modularity of
  0.586 against 0.585 (`docs/native-engine.md`). Go is left out:
  an interface is satisfied by shape and no type says which it implements.
- **`benchmarks/inference_gaps.py`**: the uses the compilers bind that the
  native graph lacks (every oracle's `--json` rows), sorted by what stands
  before the name: a closure's parameter, a call's result, an untyped name,
  an initializer's member, a type a member is reached through, a type, a
  bare name, an import. Phase 3 of `docs/rule-sources.md` reads it: a
  closure parameter typed by its call is at most 1.3% of the uses, so that
  inference is not built.
- **The TypeScript compiler as the judge of the native engine's edges**
  (`benchmarks/oracle_typescript.py`). The compiler's own test suite keeps,
  for 7,242 programs, a file the compiler wrote: every name of the program
  and the declaration it is bound to. The script builds the native graph of
  each program and compares: of the edges whose site the compiler has an
  answer for, 98.9% went where it says with engine 0.5.51 and 99.1% with
  0.5.52; what was wrong is the list under Fixed. No rule in it was written
  from memory of the language.
- **The Scala compiler as a second judge** (`benchmarks/oracle_scala.py`):
  the Scala 3 compiler's SemanticDB expect files carry, after every name of
  73 programs, the symbol the compiler bound it to. No Scala compiler runs
  here, so this is the first check of the Scala rules by Scala itself: 73.3%
  of the edges it has an answer for were right with engine 0.5.52, 96.7% with
  0.5.53.
- **The Kotlin and Swift compilers as judges**
  (`benchmarks/oracle_kotlin.py`, `benchmarks/oracle_swift.py`). Kotlin's
  Analysis API tests state, for every element of 186 programs, the symbol
  the compiler resolved it to; Swift's index tests state it for the lines
  their authors chose (16 programs with an answer). Neither compiler runs
  here. Of the edges each has an answer for, 99.7% (Kotlin, 612 edges) and
  92.7% (Swift, 55) were right with engine 0.5.54: the resolver's rules for
  both held. What the lists showed was what the reader never read, below.
- **The Go type checker and rust-analyzer as judges, on whole repositories**
  (`benchmarks/oracle_go.py`, `benchmarks/oracle_rust.py`). Not a test
  suite this time: `go/types` and `rust-analyzer lsif` resolve every name of
  a real repository, and the scripts compare the native graph of the same
  repository with them. On cobra, chi, resty and gin-examples the type
  checker has an answer for 11,961 edges; 99.7% went where it says with
  engine 0.5.55 and all but 4 with 0.5.56. On ureq, ripgrep, axum-realworld
  and actix-realworld rust-analyzer has one for 11,924; 97.5% before, 98.5%
  now.
- **`benchmarks/positions.py`: the places a type can be written, per
  grammar, and whether the reader reads them** (phase 1 of that plan). It
  parses a corpus with each tree-sitter grammar, names every place a name of
  the repository's own types is written by the node that holds it, and asks
  the engine (`file_facts`) whether a use was read there. On the audit's
  repositories: 49,097 names in 13 languages, 88.9% read, and 55 places read
  less than nine times in ten, listed with an example each
  (`benchmarks/results-positions.json` is the baseline). Nothing is fixed by
  this change: it is the list.
- **Language versions, phase 0 of that plan** (engine 0.5.61; no edge
  changes):
  - The native graph's header has `graph.language_versions`: the version
    each file's project states, per language (`engine/src/versions.rs`
    reads `go.mod`, `Cargo.toml`, `.csproj`, `Directory.Build.props`,
    `pyproject.toml`, `package.json`, `pom.xml`, Gradle builds and version
    catalogs, `Package.swift`, `build.sbt`, `composer.json`, Gemfiles and
    gemspecs, `CMakeLists.txt`). No rule depends on it yet.
  - `benchmarks/versions.py` reads each compiler's own table of versions
    and features into `benchmarks/versions/<language>.json` (C#, Java,
    Kotlin, Swift, Rust, Go, C/C++, Scala), with the places its name
    resolver asks for the version.
  - `benchmarks/verify_cases.py --min-version` asks each compiler for the
    oldest version that accepts each case of the checklist
    (`benchmarks/results-case-versions.json`); a case can state `since` /
    `until` and is then compiled in that version. Scala's cases are
    compiled too: 510 of 510 are accepted by their languages.
  - A case of `benchmarks/conformance.py` records how it was found and can
    cite where the rule was read (`source`); `docs/resolution-rules.md`
    shows the versions and the counts.
- **The plan for reading the rules out of the languages' own tools**
  (`docs/rule-sources.md`), and `benchmarks/rule_sources.py`, which fetches
  what it names: per language the compiler's or interpreter's name binder,
  its member and overload lookup, its table of language versions and their
  features, and the specification, as sparse checkouts of 17 repositories
  (300 MB, under a minute) with the commits recorded. The plan covers
  language versions (the engine reads none today), a `source` and `since` /
  `until` on each case of the checklist, languages with no open compiler,
  and the phases: positions from the grammars, binding rules from the
  binders, inference by the numbers.
- **graphify's edges are checked before they count, and the confirmed ones
  are a floor** (`benchmarks/verify_graphify.py`). The comparison with
  graphify took each of its edges as a fact. For Go, Rust and C#
  repositories the compiler is now asked about each one first (confirmed,
  refuted, one implementation of a trait's method, no answer), and the share
  the native graph has is given per kind. On twelve repositories the native
  graph has 82% of graphify's Rust calls and 99% of the ones rust-analyzer
  confirms; 481 of graphify's are refuted. `--baseline
  benchmarks/results-verify-graphify.json` exits 1 when a build lacks more
  of the confirmed edges, per repository and kind, than the baseline: 45 of
  14,400 with engine 0.5.60, 115 with 0.5.59, 346 with 0.5.58.
  `compare_engines.compare_edges(..., pairs=True)` returns every pair only
  graphify has; `oracle_go.py` and `oracle_csharp.py` expose their bindings
  (`uses_of`, `setup`), and Roslyn also says which constructor a `new`
  chose.
- **Roslyn as the judge of the C# edges** (`benchmarks/oracle_csharp.py`):
  the C# compiler's semantic model, on one compilation of every source of
  a repository. On todoapi, RestSharp, commandline and aspnetcore-realworld
  it has an answer for 10,251 edges, and 96.2% go where it says with engine
  0.5.57. The two clusters left are named in `docs/native-engine.md`: a
  type and its generic namesake (`RestResponse`, `RestResponse<T>`), and an
  extension overload chosen over an instance method.
- **`benchmarks/verify_cases.py` checks Kotlin, Swift and C# too** (kotlinc,
  swiftc, `dotnet build`): every case of the checklist but Scala's has now
  been compiled by its own language, and all are accepted.
- **`benchmarks/verify_cases.py`** gives each case of the rule checklist to
  the language's own compiler or interpreter (10 of the 14 languages have
  one here): a case the language rejects says nothing about the rule.
- **The name-resolution rules of each language as a list**
  (`docs/resolution-rules.md`, `benchmarks/conformance.py`): 406 rules in 14
  languages, each a small project with the edge the rule says is there and
  the edge that must not be. The first 185 are scopes and shadowing, imports,
  member lookup through bases, mix-ins and extensions, and overloads by
  arity; the other 221 are the places a type is written outside a
  declaration (a cast, a pattern, a loop variable, an alias, a wrapper, a
  declared result) and what each language has of its own. Engine 0.5.45
  followed 356 of them; 0.5.46 follows all 406 (see Fixed), and the list has
  414 with the rules 0.5.47 found on repositories.
  `--baseline benchmarks/results-conformance.json` says what a change fixed
  and exits 1 on what it broke.
- **A built-in code search index**, so `graphlore_locate` and the duplication
  scan's similarity tier work without the `[semble]` extra (`search.py`). Chunks
  are the span engine's definitions (a class holds only its own lines), so every
  hit joins to its graph node exactly; module code between definitions becomes
  low-weight chunks, and files the span engine cannot read are cut into windows
  and counted. Ranking is identifier-aware BM25F over the definition's name, its
  path and its body (`getUserName`, `get_user_name` and their parts share terms),
  an overlay from the graph (a node's docstring and the names of its callers and
  callees), an exact-name boost for symbol queries, and a prior that ranks test
  code, examples and type stubs below the implementation and spreads a ranking
  over files. The index is built once per project and refreshed per call by file
  (mtime, size); hidden, dependency, vendored, minified, oversized and binary
  files are skipped and counted, and a cap says what it left out.
- **An optional dense tier** for it, the `[embed]` extra (model2vec, numpy):
  static code embeddings (`minishlab/potion-code-16M-v2`, or
  `GRAPHLORE_EMBED_MODEL`) fused with BM25 by weighted reciprocal rank.
  Vectors are cached by text hash under `GRAPHLORE_CACHE_DIR` (default
  `~/.cache/graphlore`, never in the project), so an edit re-embeds only the
  definitions it changed. Without the extra, or offline with no cached model,
  the index answers with BM25 and the reply says why. The plugin installs it.
- `backend` in `graphlore_locate` and `graphlore_duplication_scan` replies:
  which backend answered (`builtin` / `semble` / `custom`) and, for the built-in
  index, `mode` (`hybrid` / `bm25`), its tiers, what it indexed and skipped, and
  `dense_unavailable`. The text reply ends with a `Search:` line.
- `score_kind` in `graphlore_duplication_scan`, and a default floor per backend:
  0.7 on embedding scores (semble, built-in hybrid), 0.5 on the built-in
  BM25-only index's tf-idf cosine. On the built-in index each seed is anchored
  on its own definition's chunk instead of a search for its first lines.
- `benchmarks/search.py`: the built-in index (BM25 and hybrid) against semble on
  the query set semble publishes for its own benchmark, scored the way semble
  scores it (NDCG@10, recall@10, cold index time, warm query p50), plus the
  calibration behind the duplication floors. On that set (1,251 queries, 63
  repositories, 19 languages) the built-in index scores NDCG@10 0.814 with BM25
  alone and 0.809 hybrid, against semble's 0.852; on the 44 repositories not
  used to choose its constants, 0.799 and 0.797 against 0.842. The dense tier
  helps semantic queries and some languages and costs others, so on this set it
  does not raise the mean. Results in `docs/benchmark.md` and
  `benchmarks/results-search.json`.
- A kernel seam for an accelerator: the tokenizer and the BM25F loop sit behind
  `_Kernel`, a `graphlore_accel` module with `KERNEL_ABI == 1` replaces the
  Python reference when installed (`GRAPHLORE_SEARCH_KERNEL=python` forces the
  reference), and `tests/fixtures/search_golden.json` holds the terms and
  bit-exact scores it must reproduce.
- **What the native engine leaves without an edge, by reason.** `python -m
  graphlore_engine build ROOT --unresolved PATH` (engine 0.5.15) also writes
  every call that got no edge although its name is the one callable of that
  name in the project, with the reason the walk stopped (`untyped local or
  parameter`, `closure parameter outside any definition`, `type: no such
  member`, …); graph.json is the same with or without it.
  `benchmarks/unresolved.py` counts them by reason and language for one
  repository or a directory of them, leaves vendored and minified files out,
  prints samples and compares with an earlier run. On the 31 audit
  repositories: 6,796 calls, more than a third of them in the file-level
  callbacks of JavaScript, TypeScript and Ruby test DSLs
  ([native-engine.md](docs/native-engine.md#what-is-left-without-an-edge)).
- **Files that change together.** graphlore reads the project's git history
  (one local `git log`: the last 1000 commits that are not merges, renames
  followed, commits of more than 30 files skipped) and measures, per pair of
  files, code-maat's degree of change coupling; cached on `HEAD`, for graphify
  and native graphs alike, nothing written to `graph.json`.
  `graphlore_surprises` lists in `co_change` the source files that change
  together with no edge of the graph between them (tests left out), and
  `graphlore_node_details` the files a node's file changes with most and
  whether the graph joins them (`linked`). Both say how much history was read
  (`history`), and a shallow clone or a history under 10 commits is named
  rather than shown as an empty list. On nine audit repositories deepened to
  their last 1000 commits, 239 such pairs, read by hand all real: a websocket
  client and its server, Rails routes and models with `schema.rb`, parallel
  implementations kept in step; `git log` took at most 230 ms.
- **Shared state, pair by pair.** Each `shared_state` row of
  `graphlore_surprises` lists its `pairs` (a writer and another definition
  touching the state), whether a call path joins them (`call_path`, either
  way, over calls, dispatch and HTTP, within 6 hops) and whether their files
  change together (`co_change`). The score counts hidden pairs (no call path)
  instead of files that import neither the other, and a hidden pair whose
  files change together raises it again; a row whose pairs are all joined by
  calls says so (`every pair is joined by a call path as well`).
- **Routes and HTTP requests in the native engine** (graph schema 5, engine
  0.5.0), in Python, JS/TS, Java, Kotlin, C#, Go, Rust, Ruby and PHP: every
  route is a `type: route` node with its
  `method`, its full `pattern` and a normalized `key` (`GET /api/v1/users/{}`),
  joined to its handler by a `handles` edge. The full path is composed across
  files, which `graphlore_routes` cannot do: FastAPI `APIRouter(prefix=…)` and
  `include_router(…, prefix=settings.API_V1_STR)` (a constant's value is
  read), Flask blueprints, Express `app.use('/api', router)` and
  `app.use('/x', require('./x'))`, Fastify `register(…, { prefix })`, NestJS
  `setGlobalPrefix`; within a file, Spring and JAX-RS class prefixes, Ktor's
  nested `route("p") { … }` and ASP.NET `[Route]` controllers and `MapGroup`
  groups (kept per method, so two `var group = …` do not mix), chi `Route`
  lambdas, actix scopes, the Rails `routes.draw` DSL (`resources`,
  `namespace`, `scope`) and Laravel `prefix()->group()`; across files, gin /
  echo groups (`v1 := router.Group("/v1")`) and the router passed to a
  function (`addUserRoutes(v1)`), axum `.nest("/api", users::router())` /
  `.merge(…)` and actix `.configure(routes)`, and Laravel's route-file prefix
  (`Route::prefix('api')->group(base_path('routes/api.php'))`, Laravel 11's
  `withRouting(api: …)`). A Rails `"articles#index"` or Laravel
  `[UserController::class, 'store']` handler is the controller's method (a
  singular `resource :user` is `UsersController`'s). A handler
  written as a lambda that makes one call (`post { users.register(ctx) }`,
  `(req, res) => ctrl.show(req, res)`) is the method it calls. A prefix whose value is unknown is written as a parameter
  (`/{API_PREFIX}/users`) and the node says `prefix_unresolved`. Requests the
  code makes (`fetch`, `axios` and clients made by `axios.create({ baseURL })`,
  generated SDKs such as `client.get({ url: '/api/v1/items/' })`, jQuery,
  Angular `HttpClient`, `EventSource`, `requests`, `httpx`, `aiohttp`, Feign
  `@RequestLine`, the mappings of a `@FeignClient` / `@HttpExchange` /
  `@RegisterRestClient` interface and Retrofit's `@GET` (requests, not
  routes, unlike `graphlore_routes`), `RestTemplate`, `WebClient` /
  `RestClient`, MockMvc, RestAssured, the Ktor client, C# `HttpClient` with
  a relative URL such as `$"todos/{id}"`, Go `http.Get` / `http.NewRequest`,
  reqwest / ureq, HTTParty / Faraday, Guzzle and Laravel `Http::`, and in
  tests rack-test and Laravel's `$this->getJson(…)`) are
  linked to the route they reach by `http_calls`: `EXTRACTED` when the method
  is known and every segment matches (a parameter or a template hole against
  a parameter), `INFERRED` for a value against a parameter, an unknown method
  or base, or several candidates. A path several routes take goes to the
  nearest. Only production code is linked: requests in tests are counted
  (`report.http.test_calls`), as are those to another host and those whose URL
  the source does not spell out. `graphlore_impact` follows both relations, so
  a handler's dependents reach the client code that requests it, and
  `relations="http"` names them. In fastapi-template all 23 requests of the
  generated SDK reach their 23 handlers, in kotlin-spring-realworld the 6
  Feign requests theirs, and in todoapi 7 of the 9 `HttpClient` requests (the
  other two go to routes `MapIdentityApi` adds at run time), and in
  gin-examples a browser `EventSource('/stream/' + id)` the Go handler of
  `GET /stream/:roomid`. Across the 31 audit repositories, 2375 of
  `graphlore_routes`' 2376 rows are routes of the native graph (69 with a
  longer path; 60 with a closure kept in a variable for a handler, which the
  native graph names by the function around it). Builds take 7% longer
  (median of ten repositories against engine 0.4.0).
- On a native graph (schema 5), `graphlore_routes` lists the graph's route
  nodes (`source: "graph"`): the full path, the handler, and `called_by` /
  `callers`, the code whose requests reach the route; on a graphify graph it
  reads the source as before (`source: "source"`). `graphlore_node_details`
  has an `http` section: the routes a handler serves and who requests them,
  the routes a client definition requests, or a route's handlers and callers.
- **Shared state in the native engine**: variables, constants and class-level
  fields are nodes in all 14 languages (`kind: variable | constant |
  property`), and the definitions that read or write them are linked by
  `reads` / `writes` edges: assignments, `+=`, `x[k] = v`, `del`, `++`, and a
  mutating method (`append`, `set`, `clear`, …) on a collection or a builder,
  `INFERRED` since only its name says so. Locals, parameters and Python names
  assigned without `global` are left out; a constructor setting its own
  object's fields builds it and couples nothing. Two definitions touching one
  such node affect each other without either calling the other. Member reads
  of a property or constant that were `references` are `reads` now. Fields of
  instances count too (`self.x`, `this.x`, `@x`, declared fields), and so do
  **resources**, by a literal key: environment variables (`os.getenv`,
  `process.env.X`, `ENV[...]`, …), files (`open(p, "w")`, `fs.writeFileSync`,
  …), tables named in SQL strings or reached through an ORM model
  (`User.objects.filter`, `Post.create`, `db.Items.Add`; the model's table
  from `__tablename__`, `Meta.db_table`, `@Table`, `$table`, `DbSet<T>`, …),
  events a project both emits and listens for, and cache keys
  (`localStorage`, Redis-like stores). A resource is a `type: resource` node
  only when two definitions touch it. On the 31 audit repositories this adds
  16% edges; builds take 15% longer (median). Schema 4, engine 0.4.0.
- **`graphlore_impact(shared_state=True)`** walks shared state: the
  dependents of a writer include the code that reads what it writes, the
  dependencies of a reader the code that writes what it reads (a `written_by`
  step). By default reads and writes are not walked and are counted in
  `state_nodes_skipped`; `relations="state"` names them.
- **`graphlore_surprises` lists shared state** (`shared_state`): variables,
  fields and resources written in one file and read or written in another,
  scored by whether the files import each other, their top-level directories
  and their communities.
- **`graphlore_node_details` says what a definition reads and writes**, and
  which other definitions share that state with it, and how.
- **A native extraction engine, `engine/`** (experimental, opt-in): graphlore's
  own extractor, written in Rust and built with maturin + PyO3 into a separate
  package, `graphlore-engine`, with tree-sitter grammars for Python,
  JavaScript, TypeScript, TSX, Go, Java, Kotlin, Scala, C#, Rust, Swift, C, C++,
  Ruby and PHP compiled in — nothing is downloaded and nothing reaches the
  network. It writes a graphify-compatible `graph.json` with a node per file,
  per definition and per Markdown / reStructuredText heading, and the
  `contains` / `method` / `case_of` edges between them. Each definition node
  carries its real span (`region_start`, `end_line`, `source_location` as
  `L13-L15`), `kind`, `qualname` and `language`; Rust `#[cfg(test)]` and
  `#[test]` items are marked `test`. Ids and labels follow graphify's scheme
  (25 of the 26 code definitions in the polyglot fixture get the same id and
  parent from both engines). The definition rule is a port of `spans.py`'s: on
  ~95,000 definitions in real Go, JS, TS, Rust, Ruby, Java, C#, PHP, Kotlin,
  Swift, Scala and C/C++ files it returns the same spans as `spans.py`, apart
  from 2.5% of C/C++ headers where the grammar versions differ, and on 30,000
  Python definitions the same spans as the stdlib `ast`. Per language, a module
  overrides only what its grammar does differently. No communities yet; every
  native build says so.
- **Imports, calls, bases and type references in the native engine**, in all
  14 languages. The engine reads import statements, calls and constructions,
  base lists, types in signatures, fields, variables and type arguments,
  decorators and attribute reads, and resolves them the way each language
  does: module paths per language (Python's root rules, JS/TS relative paths
  with `tsconfig.json` paths and workspace packages, `go.mod`, JVM / C# / PHP
  packages and namespaces, Cargo crates and the module tree, Ruby
  `require_relative`, SwiftPM targets, C/C++ includes), names through the
  enclosing scopes, the file, its imports and re-exports, its package or
  namespace, and receivers through typed parameters, fields, assigned
  constructions and declared return types (builder chains included). What that
  finds is `EXTRACTED`; a unique name across the project, with import
  evidence for a method, is `INFERRED`; anything else gets no edge and is
  counted in the build reply. Import edges carry `runs` (module-level,
  deferred, type-checking); external modules and bases are `type: external`
  nodes with an `ecosystem`. Properties (C#, Swift, Kotlin, TS/JS class
  fields) and module-level constants are nodes. graph.json schema 2. Compared
  with graphify on the 31 audit repositories, the native graph has 77% of
  graphify's call edges, 75% of its file-level imports, 96% of its bases and
  85% of its type references, while 15% of its calls are inferred against
  47% of graphify's; most of the rest is graphify's name guesses and artefacts
  (`docs/native-engine.md` has the per-language table and samples). Builds stay
  3–6 times faster than graphify's.
- **Communities in the native engine** (`engine/src/community/`): Leiden on the
  internal nodes, with edges weighted by what they say (containment and calls
  1.0, bases 1.5, imports 0.7 / 0.4, type references 0.5), by confidence
  (`INFERRED` × 0.5) and by where their ends live (same directory × 1.25, other
  top-level directory × 0.6, test to production × 0.1), plus a weak tie between
  the files of a directory; external nodes join their neighbours' community.
  Then graphify's rules (isolated nodes alone, oversized and sparse
  communities split, ids by size) and names after the most connected member,
  prefixed with the directory most members share. Nodes are visited in the
  order of a hash of their id and a build starts from the previous graph's
  communities and settles, so after a one-file change 99.8–100% of nodes keep
  their community; ids are kept by overlap, and a name saved for a community
  stays while its members mostly do (stale ones are dropped from
  `.graphify_labels.json`, and the build says how many). On the 31 audit
  repositories there are as many communities as graphify finds (90.5 against
  90.6 per repository), with 87% of a community's members in one directory
  (graphify: 83%) and 8% of test nodes in a production community (21%).
  graph.json schema 3: `community` / `community_name` on every node and
  `graph.communities` in the header. `python -m graphlore_engine cluster GRAPH`
  detects them again.
- **Incremental native updates**: every native build writes a facts cache
  (`graphify-out/cache/graphlore-native.bin`), and `graphlore_build(update=True)`
  reuses the facts of the files whose size and modification time, or content,
  are unchanged, and parses the rest; resolution and communities run over the
  whole project, so an update writes the graph a full build writes. retrofit:
  2.6 s for a full build, 1.0 s for an update after one file changed. The build
  reply says how many files were parsed and reused; `verbose=True` adds the
  time per phase.
- **`benchmarks/compare_engines.py --communities`** compares the two engines'
  communities: count, sizes, modularity, directory purity, test mixing and
  their agreement (NMI).
- **`graphlore_build` on a native graph trusts its edges**: `ImportChecker`
  takes an edge's `runs` as when the import runs and rejects nothing, and the
  calls and imports `resolve.py` adds to graphify's graphs are not added to a
  native schema-2 graph (`graph._engine_resolved`).
- **`benchmarks/compare_engines.py` compares edges** (imports as file pairs;
  calls, bases and type references over the definitions both engines have),
  and **`benchmarks/polyglot.py --engine native`** runs the whole audit on
  native graphs (built into `graphlore-native/`). On the 31 repositories it
  raises no high finding (7 on graphify's graphs) and no `FETCH-NO-SPAN` (94),
  and every tool but the community ones answers as on graphify's graphs.
- **`GRAPHLORE_ENGINE`** picks the extractor `graphlore_build` uses: `graphify`
  (the default, unchanged), `native` (an error naming the install command when
  the engine is missing; `mode` and `cluster_only` refused; `update` rebuilds
  the whole graph) or `auto` (native when installed, graphify for what native
  cannot do and for updates of a graph graphify built). The native build runs
  in a child process bounded by `GRAPHLORE_TIMEOUT`, and records
  `built_at_commit` for freshness. With the native engine chosen, a file whose
  Python tree-sitter grammar is missing (no `[treesitter]` extra, or offline)
  gets its spans from the engine's compiled-in grammars.
- **`benchmarks/compare_engines.py`**: the native graph of a repository against
  graphify's, per language — exact, near and same-file matches, what only one
  engine has, and whether matched definitions share a parent and relation.

### Changed
- **The tree-sitter span walk is 3–5× faster, with the same spans.** It caches
  the per-type definition check, looks fields up by id (skipping fields the
  grammar does not have), does not descend into leaves, and tries a bound
  anonymous function only on a node with a `value` or `left` field. On
  abseil-cpp the walk went from 4.6 s to 1.0 s, and on guava from 14.7 s to
  4.4 s. Parsing is unchanged. The spans are identical on all 5,511 files of the
  polyglot clones plus abseil-cpp and guava.
- `GRAPHLORE_SEMANTIC_BACKEND` unset (or `auto`) means semble when it is
  installed and builds its index, else the built-in index; when semble is
  installed but fails, the reply's `backend.note` says so. `builtin` and
  `semble` pin one; a custom spec that fails is still an error.
- `graphlore_locate` stays on the `lean` surface and `GRAPHLORE_TOOLSET=locate`
  no longer falls back to `lean` in a default install; both now happen only when
  `GRAPHLORE_SEMANTIC_BACKEND` names a backend that is not available.
- The Hugging Face offline flags are set by `config.export_offline_env()`, shared
  by semble and the built-in dense tier.
- **Three replies that could run past 90 KB are cut, and say what they left
  out** (the polyglot audit's `REPLY-TOO-LARGE` on native graphs, five to
  none). `graphlore_neighbors` takes `limit` (200): past it the result carries
  `total`, `truncated` and `counts` by relation and direction (retrofit's
  `Call`, 752 neighbors: 99 KB to 25 KB as JSON). `graphlore_fetch` returns one
  definition whole up to 8,000 tokens or `budget_tokens`, whichever is larger;
  a longer one is cut at a line with `cut` (`lines_shown`, `lines_total`,
  `tokens_total`), `lines_left_out` and a hint naming `graphlore_skeleton` and the budget that
  returns all of it (guzzle's 3,100-line `CurlFactory`: 124 KB to 29 KB).
  `graphlore_same_names` lists the first 25 definitions of a name and counts
  them all in `definitions_total` (cpr's 647 definitions named `TEST`: 134 KB to 6 KB).
- With the native engine, `graphlore_build(cluster_only=True)` detects the
  communities of the graph on disk again (it was refused), and `update=True`
  is incremental (it rebuilt everything); under `GRAPHLORE_ENGINE=auto` both
  still go to graphify when the graph on disk is graphify's.
- On a native graph built before the engine detected communities,
  `graphlore_communities` says so and names `graphlore_build(cluster_only=True)`.
- `graphlore_fetch` returns the lines a native property or constant node
  carries (`region_start`..`end_line`) when the span engine has no span for it
  and the file is unchanged since the build, instead of a line window.
- **A call of an overloaded name that the arguments do not decide is an
  `INFERRED` edge to each overload that fits** (native engine 0.5.5; Java,
  Kotlin, Scala, C#, Swift, C++), where it was an `EXTRACTED` edge to the
  first. `baseUrl(server.url("/"))` may be `baseUrl(String)`, `baseUrl(URL)`
  or `baseUrl(HttpUrl)`: all three are callers' targets now, so
  `graphlore_impact` on any of them lists the call, and `strict=True` (only
  extracted edges) leaves it out. 999 calls of the 31 audit repositories
  changed this way; the share of `INFERRED` calls goes from 20% to 29%.

### Fixed
- **Macros that made up definitions, a gtest test's name and a C++ class
  before its constructor** (`spans.py` and the native engine 0.5.76). A
  C++ `TEST(Suite, Name) { … }` / `TEST_F(Fixture, Name) { … }` (a function
  with no return type, named in capitals, given two bare names) is
  `Suite.Name`, not a function called `TEST` 810 times over; a `TEST_F`
  test is a method of its fixture, so the fixture's fields type what it
  calls (leveldb: 333 more calls, 104 reads). C and C++ sources are parsed
  with a macro alone on an indented line before a call blanked (Unity's
  `EXPECT_ABORT_BEGIN` / `TEST_ASSERT(0);`): the parser read the pair as a
  declaration and lost the functions after it (118 made-up definitions in
  one cJSON test file, none now). A TypeScript `const` or variable directly
  in a `namespace` is a definition, as a module's is. In a member of
  `Status`, `Status::OK()` names the class before `::`, not the
  constructor of its name the member lookup found first ([class.qual];
  conformance case `cpp/class-before-its-constructor`).
- **Definitions graphify finds that the span rule missed, checked against
  the source** (`spans.py` and the native engine 0.5.75, the same rule).
  Of the 2,020 definitions only graphify had on the 45 audit repositories,
  1,619 are none (PHP closures, Rust `impl` and Swift `extension` blocks of
  a type defined elsewhere, names bound to an import, forward declarations);
  of the rest, these were missing and are now read: a C# `delegate`, a
  conversion or arithmetic `operator` (`operator string`, `operator+`) and an
  indexer (`this[]`); a Rust `macro_rules!` macro, named `name!` so that it
  never answers a lookup of the function `name`; a Swift `subscript` (its
  `name` field is its result type, so a keyword-named definition now takes
  its keyword first) and every name of a `case a, b, c` line, not only the
  first; a C `typedef void (Cb)(int);`, whose name is in parentheses (also
  `TEST_SETUP(Group) {`, named `Group`). C and C++ sources are parsed with a
  capitals-only macro after a declarator or a parameter list blanked
  (`int count GUARDED_BY(mu);`, `void f() REQUIRES(mu);`, on its line or the
  next): the parser took the macro for the declarator, which lost the field
  and made up a method `GUARDED_BY` (leveldb: 41 fields, their reads and
  writes, and calls of `Mutex::Lock` now going to the class that defines
  it).
- **Native engine 0.5.74: Scala's rules, read from the Scala 3 typer**
  (phase 2 of `docs/rule-sources.md`, which every language of the plan has
  now been through; 292 rules in `benchmarks/rule_inventory.py`, 245 held by a
  case): a wildcard import beats a member of the package another compilation
  unit defines, and `export p.m` makes m a member of the object that writes
  it.
- **Native engine 0.5.73: Python's, PHP's and Ruby's rules, read from their
  references** (phase 2 of `docs/rule-sources.md`; 19 cases): Python
  `global` / `nonlocal` make an assignment bind the outer name, and PEP 695
  type parameters hide a class of their name; Ruby looks members up in the
  class, its modules from the last included, then the superclass, a local
  only exists from its first assignment, and a constant is looked up
  lexically before the ancestors; a PHP trait's methods come before the
  parent's, a class name does not fall back to the global namespace, and
  `\Name` is the global one (128 of guzzle's `\InvalidArgumentException`
  references no longer name its own class of that name).
  `benchmarks/rule_inventory.py` covers Python (15 rules, 14 held), PHP
  (12, 10) and Ruby (10, 9).
- **Native engine 0.5.71: Swift's, C's and C++'s rules, read from their
  compilers** (phase 2 of `docs/rule-sources.md`; 19 cases): overloads are
  of one scope (a local function's are its own; `::f` and `ns::f` are not
  overloads of each other); a C struct tag is its own name space and a C++
  function hides the class of its name; `::f()` is the global one; an
  inline namespace's members are the enclosing namespace's; `a + b` calls a
  C++ member `operator+`; an integer literal prefers an `int` parameter and
  a string literal a `const char*`; a non-template (C++) or non-generic
  (Swift) overload wins a tie when every argument is a literal; `p + 1` in C
  and C++ is of unknown kind. `benchmarks/rule_inventory.py` covers Swift
  (16 rules, 13 held) and C / C++ (21 rules, 17 held).
- **Native engine 0.5.69: Kotlin's rules, read from the Kotlin
  specification** (phase 2 of `docs/rule-sources.md`; 13 cases): `a + b`,
  `a[i]` and `a foo b` call the operator or infix function of a's type;
  `super<A>.f()` is A's member; extensions are chosen in the specification's
  order (local, imported by name, same package, star-imported).
  `benchmarks/rule_inventory.py` covers Kotlin (24 rules, 20 held).
- **Native engine 0.5.68: Java's rules, read from the JLS and javac**
  (phase 2 of `docs/rule-sources.md`; 14 cases): `Outer.this.m()` is the
  outer class's method; a field an interface declares is a constant of it
  (read in implementing classes); a number literal picks a primitive overload
  before a boxed one, and an integer literal the narrowest primitive.
  `benchmarks/rule_inventory.py` covers Java (30 rules, 26 held).
- **Native engine 0.5.67: TypeScript's rules, read from the compiler's name
  resolver** (phase 2 of `docs/rule-sources.md`; 10 cases): where a type is
  written, a value of the name does not hide the type (`const Repo = 1`
  beside `interface Repo`); `typeof x` keeps its value meaning; interfaces of
  one name merge. `benchmarks/rule_inventory.py` covers TypeScript and
  JavaScript (31 rules, 26 held), and `verify_cases.py` compiles a case that
  writes its own `tsconfig.json` with `tsc -p`.
- **Native engine 0.5.66: Go's rules, read from the Go specification and
  go/types** (phase 2 of `docs/rule-sources.md`; 15 cases): a selector is the
  field or method at the shallowest depth of embedding, and each clause of a
  `switch` / `select` is a block of its own. `benchmarks/rule_inventory.py`
  covers Go (29 rules, 23 held).
- **Native engine 0.5.65: Rust's rules, read from the Rust Reference and
  rustc** (phase 2 of `docs/rule-sources.md`; 15 cases citing the
  Reference's rule identifiers):
  - a `match` arm's binding is a name of that arm only;
  - `Self` in a struct's own fields is the struct;
  - a module and a function of one name are told apart (`log::write()`,
    `log()`);
  - a `use` written in a block holds in the block and shadows the module's;
  - a glob import's names are kept for resolution, and an item of the module
    shadows them;
  - `use m::f;` in an inline module starts at that module's `m` (2018), and an
    inline module's items are no names outside it;
  - a name in `#[derive(…)]` is no value read.
- **`benchmarks/rule_inventory.py`** covers Rust (38 rules, 31 held).
- **Native engine 0.5.64: C#'s binding rules, read from the C# standard and
  Roslyn's binder** (phase 2 of `docs/rule-sources.md`). 18 cases written
  from the reading, each citing the section and the binder function:
  - an overload is chosen by a parameter written as a predefined type
    (`Write(string)` over `Write(object)` for `Write("x")`), where every
    overload was an `INFERRED` edge before;
  - a `params` / `vararg` candidate that needs its expanded form ranks below
    one that does not (C#, Java, Kotlin);
  - of extensions in sight, those of the closest namespace around the use;
  - a type of another namespace block of the same file is not in sight.
- **`benchmarks/rule_inventory.py`**: every rule a language's specification
  and binder state, with the case that holds it or why the engine does not
  follow it (C# first: 54 rules, 47 held).
- **Native engine 0.5.63: places a value is written that were not
  recorded**, listed by `benchmarks/positions.py --values` (541 cases in the
  checklist, all hold). 97.7% of 162,839 names of the audit
  repositories' own values are read now:
  - Python, JavaScript, TypeScript, Rust: a member read or written through a
    local or a parameter (`ctx.headers`, `request._noPipe = true`). The state
    reader dropped any use whose head the file neither declares nor imports,
    a local included. About 1,900 `reads`, 1,100 `references` and 220
    `writes` more on the audit repositories; many are `INFERRED`.
  - Swift `.case` beside a value whose type gives it (`h.section == .header`,
    an assignment, `case .body:`) is a reference to that type's case.
  - A Rust macro's arguments are read for member reads and writes as well as
    calls (`assert_eq!(false, args.crlf)`).
  - `opt[URI]("x")` (Scala) and `parse::<T>()` (Rust) are calls.
  - A local assigned a bare name holds what the name does (`const request =
    first;`); a member set in a C# initializer (`new Article { Author = author
    }`) declares no local.
- **`benchmarks/positions.py --values`**: the places a name of the project's
  own functions and values is written, with the share the engine records
  (`benchmarks/results-positions-values.json`); the type measure leaves out
  documentation, `dist` and minified files now.
- **Native engine 0.5.62: places a type is written that were never read**,
  listed from the grammars by `benchmarks/positions.py` (536 cases in the
  checklist, all hold). The share of the repositories' own type names the
  reader records went from 88.9% to 99.6%; 4,300 reference edges and
  950 calls more on the 45 audit repositories, with the compilers'
  agreement unchanged (Go 12,273 of 12,278).
  - Every language: a closure's own parameters and result.
  - Go: what a defined type is defined as (`type Key Opt`), a constant's
    type, the types of a slice or map literal, the type handed to `make`; a
    package's `const` / `var` is the source of its own uses.
  - C, C++: the type of a declaration and what a `typedef` names.
  - Java: `throws`, `T.class`, the types of a for-each variable, a varargs
    parameter and an interface's constant.
  - Swift: `throws(E)`, a protocol property's type, `(any P).self`;
    `Box<T>(…)` is a construction of `Box`.
  - C# `new T[n]`; Scala a `val`'s declared type, `new Outer.Inner(…)` and
    `new p.T[A](…)`; Rust the struct a pattern takes apart.
  - C++: `T{…}` is a construction of `T`; the result type of a member
    function declared in its class, explicit template arguments of a call,
    a trailing result type and a `friend` are references.
  - TypeScript: a function type's parameters name their types; their names
    were read as types (`(err: Err) => void` gave a reference to a value
    `err`).
- **Native engine 0.5.60: the rest of graphify's confirmed edges, in Rust
  and Go** (510 cases in the checklist, all hold). Of the 115 confirmed
  edges the native graph lacked 45 are left, none in Go:
  - **Rust: the types of a tuple struct's and a tuple variant's fields**
    (`struct Override(Gitignore);`, `Loaded(Ignore)`) are references; they
    were never read.
  - **Rust: a `const` / `static` item is the source of its own uses**, its
    type and what its value names (they left from the file).
  - **Rust: `crate::` starts at the file a manifest names** (`[[bin]] path =
    "crates/core/main.rs"`). A crate with no `src` directory resolved none
    of its `crate::…` paths: 88 imports and 57 calls on ripgrep.
  - **Rust: an `impl` is of the type its own file names.** Where two
    modules of a crate each declare a private type of one name, a member of
    one was taken from the other's `impl` (`Config::default()` in
    `standard.rs` linked to `json.rs`, `EXTRACTED`).
  - **Rust: `io::Error::error_message(m)`** is the method a trait of the
    project gives the library's type, and the types a trait is written with
    in an `impl` (`impl From<Config> for Agent`) are the implementing
    type's references.
  - **Go: every result of a function names its type** (`([]Completion,
    ShellCompDirective)`: only the first was read), and a method value
    handed to a call (`time.AfterFunc(d, cb.onTimeout)`,
    `r.Use(middleware.Logger)`) is an `indirect_call`, as a bare function
    handed over already was (it was a `references` edge).
- **`compare_engines.py` gives the same numbers on every run.** Of two
  native definitions with one name in a file, which one stood for both
  depended on the order of a set: the C/C++ header references of cpr came
  out as 88% or 91%. A C# partial type is compared as one type.
- **Native engine 0.5.59: the confirmed edges of graphify's the native graph
  lacked** (502 cases in the checklist, all hold). All C#, from
  `verify_graphify.py`; 260 confirmed calls were missing, 28 are:
  - **What a property's accessor calls is the property's call.** The uses in
    `get { … }`, `set => …` and `=> expr` left from the class around; they
    leave from the property now (`Def::computed`), as a method's do from the
    method.
  - **A namespace written as a block sees the extensions of the namespaces
    around it** (`namespace N.Tests.Unit { … }` and an extension class in
    `N.Tests`), as one written for the whole file did.
  - **A brace an `#if` opens and a later `#if` closes** (`#if NET` / `if
    (…) {` / `#endif`) no longer hides the members after it. The parser
    paired the brace inside the group and lost the rest of the class: every
    call of `RestClient.Dispose()` had no target. The directive lines of
    such a group are blanked before parsing (`parse::blank_expression_branches`,
    and `spans._blank_expression_branches` for the span engine).
  - **A local assigned a member read is what the member holds** (`var sut =
    Parser.Default;`): read in Go and Rust since 0.5.56, never in C#.
  - **`new T { … }` with no argument list calls the constructor that takes
    none**, and a partial type's constructors are those of every file that
    declares it.
  - `using RestSharp.Serializers;` beside a property `RestClient.Serializers`
    is the namespace again (the misparsed class had made the property a
    top-level name).
- **Native engine 0.5.58: the gaps the compilers had left open** (496 cases
  in the checklist, all hold):
  - **C#: a type is told from its generic namesake by the type arguments
    written** (`RefFact::arity`): `RestResponse` beside `RestResponse<T>`
    each get their own references and constructions, and `List<string>` is
    never a project class `List` that takes none. A call written with more
    type arguments than any method of the type takes is the extension's
    (`parser.ParseArguments<A, B>(args)`). The type a local is declared
    with and `typeof(T)` are references. With Roslyn as the judge, 98.1% of
    10,344 edges go where it says (96.2% with 0.5.57).
  - **Kotlin: a constructor parameter written `val` / `var` is a property
    of the class**, a node like any other: reads and writes of it had no
    edge at all (586 `reads` and 551 `defines` gained on the audit's
    Kotlin sources). The name of an argument given by name reads nothing.
  - **Swift: `Self()` constructs the type around.**
  - A name read where a type is built (`new Foo { … }`) or inside a
    `generic_name` is no value: C# properties named like their type
    (`HttpClient HttpClient`) were read by every `new HttpClient()`.
- **The comparison with graphify is measured again** on engine 0.5.58
  (`docs/native-engine.md`): the shares of graphify's edges the native graph
  has are what they were (calls 85% and 90% on the two sets), with 16,288
  and 20,030 type references where 0.5.50 had 14,114 and 15,974.
- **Native engine 0.5.57**: a type named as a value through something is a
  reference only when that something is what declares it (or a module):
  C# `Maybe.Nothing<T>()` beside a class `Nothing<T>` was a reference to the
  class, 55 times in commandline. The name of a generic member or callee
  (`generic_name`) is no value read.
- **Native engine 0.5.56: what the Go type checker and rust-analyzer showed
  on whole repositories** (489 cases in the checklist, all hold):
  - **Every declaration is a local**, whatever it is assigned. `endpoint :=
    "/secure"`, `let hostname = 3`, `var body, expected string` were known
    only to the state reader: a call, a value passed on or a type named
    `endpoint` still went to the package's `endpoint`. A local assigned a
    member read (`req := dl.Request`) is typed by what the member holds.
  - **A method on a receiver the source types as a library's is the
    library's**: `jar := newJar()` with `func newJar() *cookiejar.Jar`, `var
    buf bytes.Buffer`, `value.toUpperCase()` on a `String`. The guess by a
    method's name linked them to a project method of that name (`INFERRED`).
    An extension, or a method a project trait gives a library type, still
    answers.
  - **Go**: a field of a struct was a package-level name (the fields of an
    anonymous struct a test table is made of, from any file of the package).
  - **Rust**: `self.proxy` with no call is the field beside a method of its
    name (300 `reads` on ripgrep that were `references` to a method); `->
    Result<Self, E>` gives the type the method is declared for; a name of
    another crate is in reach only through a `use` (every std `Result<…>`
    of ripgrep went to a `type Result` in another crate's tests).
  - **JS / TS**: every name a closure's parameter pattern binds is the
    closure's (`([key, value]) => …`); a block scope ended at the parameter
    list (`formal_parameters` was read as a `for`).
- **Native engine 0.5.55: a type named to test, convert or catch a value is
  a use of it**, in every language. The Kotlin compiler binds 1,771 names of
  its own tests to definitions the graph has; 62% had an edge, 88.5% now
  (Swift: 60% to 65.7%; TypeScript: 83.6% to 86.1%). 479 cases in the
  checklist, all hold. No edge came of:
  - `x is T`, `x as T`, `(T) x`, `x instanceof T`, `x.(T)`, a type switch's
    `case T`, `catch (e: T)` / `catch (T e)`, a pattern's type (`s is Qux
    q`), in Kotlin, Java, C#, Swift, TypeScript, Go, PHP and Rust;
  - the type of a Kotlin property or local (`val engine: Engine`): its
    grammar gives it no field name, so every Kotlin class lacked the
    references to the types of its properties;
  - what a type alias names (Kotlin, Swift, Rust, C++) and what a `where`
    clause bounds a type parameter by (Swift, Kotlin, Rust, C#);
  - a class or object named as a value (`take(Base.Nested)`, `k = Foo`,
    `raise Faraday::Error`), unless the same definition already calls,
    builds or extends it.
  Also: the type an `impl` gives its trait (`type Output = …`) is no name of
  the file (in actix-realworld every `Result<T>` beside an `impl Handler`
  went to another handler's `type Result`); a C# LINQ `where` is no bound.
  On the 45 audit repositories: about 4,000 new `references` edges.
- **Native engine 0.5.54: the rest of the TypeScript compiler's list.**
  99.5% of the 9,283 edges the compiler has an answer for now go where it
  says (98.9% of 7,806 with 0.5.51), and 83.6% of the uses it binds to a
  definition of the graph have an edge (73.3%). 464 cases in the checklist,
  all hold.
  - **Block scopes hold for every kind of use**, not only calls: the place
    a use is written (`Project::site`) decides which local of a name is
    meant for a read, a write, a type or a value passed too, and `state.rs`
    hides a variable only inside the block that declares a local of its
    name. A `let` in a block at the top of a file is that block's. A local
    declared after an inner block no longer hides the field the code before
    it reads (C++, Java, Kotlin, Go: about 50 `reads` / `writes` edges on the 45
    repositories), and a redeclared parameter is the new value after its
    declaration (`let timeout = timeout.for_write()`).
  - **`import x = require("./m")`, `export = E`, `import A = N.M`** are read:
    the module under that name, the module's own value, a name for a path.
  - **A function, class or enum merged with a `namespace` of its name** has
    what the namespace declares (`Cf.foo()`).
  - **A function named as a value is a `references` edge** where a bare
    function name is one (JS / TS, Python, Go): `const q = helper`, `cb =
    helper`, `Run: emptyRun`, `key=sort_key`. About 1,600 edges on the 45
    repositories.
  - JS `for ([a, b] of rows)` with no `let` assigns names declared elsewhere.
- **Native engine 0.5.53: what the Scala compiler showed, and the rest of
  TypeScript's list** (456 cases in the checklist, all hold):
  - **A name of another package is not in reach without an import** (JVM
    languages and PHP). A type or function that was the only one of its name
    in the project was linked from any file, as `EXTRACTED` when it sat at
    the top of its file and as an `INFERRED` guess otherwise: `List[Int]` to
    a `List` nested in an unrelated class, PHP `\DateTimeImmutable` to a
    test's own class, Kotlin `apply { }` to a test rule's `apply`. Now only
    one of the file's own package. C# keeps the guess: a project file can
    import a namespace for every source (`<Using Include="…"/>`), which the
    engine does not read.
  - **An enum member named as a value is a use of its enum**: `return
    Color.Red`, `Mode::Fast`, `Kind.A`, `Level::Debug`. It was an edge only
    where the member was called. TypeScript's enum members were no
    definitions at all.
  - **TypeScript**: the types a type alias is written with, those of an
    interface's property, call, construct and index signatures, and `typeof
    ns.Class` are references; a type alias's own parameters and an `infer`
    name are not.
  - **The types a type parameter is bound by** are references in every
    language (`[In <: Txn[In]]`, `<T extends Foo>`, `<S: Sink>`).
  - **Scala**: `Local(a = 1)` is an argument given by name, not a write of a
    variable `a`; `pkg.Type` is one type path; `case Succ(p)` and `case o:
    Obj` name their types; the type parameters of an `extension` are its
    methods'.
  On the 45 audit repositories: about 4,200 new `references` edges and about
  460 wrong edges gone.
- **Native engine 0.5.52: the rules the TypeScript compiler showed missing**,
  each also where the other languages have it (440 cases in the checklist,
  all hold):
  - **Block scopes.** A name declared in a block is a local of that block
    (`Local::until`): `if (a) { const x = new A() } else { const x = new B() }`
    is two variables, a use beside the block has the outer one, and inside
    the value a declaration is written with the name is still the outer one
    (`let convertible = Wrap(convertible)`). The engine had one local of a
    name per function, the first declared type winning. Not Python, Ruby,
    PHP or a JS `var`, which have no such scope.
  - **A type parameter hides a type of its name** (`interface Box<T>` beside
    a `type T`), and a member asked of it, or of a value typed by it, is a
    member of the type that bounds it: `fn run<S: Sink>(s: S) { s.begin() }`,
    `where T: Sink`, `<T extends Foo>`, `[B <: Base]`, `[T Runner]`,
    `where A: Parsable`. A bound that is not one named type (`FnMut(Match)
    -> bool`, `keyof T`, a Scala context bound) says nothing.
  - **A call of a typed parameter or local is no call of an outer function of
    its name** (`cb('hi')` with `cb: (x: string) => number`), except in Java
    and Kotlin, where a variable and a function are names of two kinds.
  - **TypeScript**: `constructor(public greeting: string)` declares the
    property (it was a node only where a method assigned it); what a
    `namespace` declares is no name beside one the file declares.
  - **Scala**: a generic method's parameters were not read at all (the
    grammar gives its type parameters the same field name), so nothing in
    `def opt[B <: Base](b: B)` knew `b`.
  - **A wrapper holds its first type argument**: `Option<Space<M>>` was read
    as holding `M`.
  On the 45 audit repositories: 501 edges gone (a type parameter or a
  callback parameter linked to an unrelated definition of its name), 675
  new (calls through bounds, parameter properties, the right overload where
  a redeclared local decides it).
- **Native engine 0.5.51: a C++ template's member is typed by the argument
  written.** The type parameters of `template <typename T> class Page` sit on
  the declaration around the class, where the reader did not look, so
  `Page<Item>& p; p.lead().size()` stopped at `lead`.
- **`graphlore_package_apis(package=…)` cut nothing**: a package used in
  hundreds of files (zod's own name across its workspace) returned every
  file under every symbol, 136 KB. Each symbol's list is now cut at `limit`
  and the file list at ten times that, with `symbol_file_counts`,
  `file_count`, `files_left_out`, `truncated` and a `hint`.
- **Native engine 0.5.50: a file git tracks is read whatever an ignore rule
  says of its name.** The engine honoured `.gitignore` for every file; git
  itself ignores only what is untracked. cJSON commits `tests/unity/test/`
  under a rule `test` (581 definitions were not read), and zod commits each
  package's `package.json` under `**/package.json`, so the engine never
  learned that `packages/zod` is the package `zod` and every workspace import
  of it was an external module (2,042 calls). The engine now asks git
  (`git ls-files`; nothing changes without git or outside a repository) and
  reads the tracked files an ignore rule matched — not hidden paths,
  `node_modules`, or what `.graphloreignore` / `.graphifyignore` name. A
  repository that commits its build output gets it read as well; the analysis
  scope leaves it out as vendored. Also: C# / Java / JS / PHP / C++ `new
  Name(…)` names the type, never a method of the class around called `Name`
  (commandline: 22 edges from a factory method to itself).
  `compare_engines.py` counts a type reference as found when the native graph
  states it from the method (graphify writes it on the class) or as a `calls`
  edge to the class (graphify: `uses`): type references 91% → 94% on the first
  set, 85% → 90% on the second.
- **Native engine 0.5.49: a language's names are read only where the language
  means them.** The reader and the resolver are shared by the 14 languages,
  and two rules of one language had been found applied to the others by
  accident (`Box` a wrapper in Java, `$` a sigil in TypeScript).
  `benchmarks/neutrality.py` looks for the rest by differential testing: for
  every name of every table in `engine/src/` (wrappers, collection methods,
  unwrappers, core methods, receiver words, built-ins) and a list of name
  shapes and other languages' keywords, in every language, it builds a small
  project that *defines* the name itself, beside the same project with a
  neutral name, and compares the edges. 39,657 projects; 199 differed:
  **161 were the wrapper tables** — a project's own generic class named
  `Optional`, `Future`, `Lazy`, `Promise`, `Provider`, `Mutex`, `Ref`,
  `Result`, `Task`, … (23 names, in TypeScript, Java, Kotlin, Scala, C#, Rust
  and Swift) was the library's wrapper, so its members named nothing. A file
  that sees a project type of such a name (it defines it, shares its
  directory, or imports it from where it is defined) is now read with the
  name as an ordinary type; `java.util.Optional` imported beside a project
  `Optional` elsewhere stays the library's. **About 20 were `self` / `super`**
  taken for the receiver in languages that have no such word (a Java method
  `self()`, a Go function `super()`): a name like any other there when the
  scope has one. The rest were names the language reserves. No method table
  leaked: a typed project receiver's own `map`, `get`, `unwrap` or `first`
  was already the project's. One row is left (a Python function named
  `super`, which shadows the built-in), in `benchmarks/results-neutrality.json`.
  No edge of the 45 audit repositories changed.
- **Native engine 0.5.47: the lowest cells of the comparison with graphify,
  read.** 0.5.46's 50 rules did not move the share of graphify's calls the
  native graph has (84.7% on the first set, before and after): they were edges
  neither engine had. Reading the edges only graphify had on the second set
  did — calls 84.9% → 90.1%, bases 79.9% → 97.4%, type references 80.3% →
  84.5% — and each was one rule:
  **`class EXPORT_MACRO Name {`** is a function to the C++ grammar, so the
  members belonged to no class; the macro is blanked before parsing, in the
  engine and in `spans.py` (leveldb: 1,173 more calls, 1,128 more references).
  A test's untyped parameter is what the **pytest fixture** of its name returns
  (click: 306 calls, `INFERRED`). C# **`: base(…)` / `: this(…)`** and Java
  **`super(…)` / `this(…)`** are constructor calls (no edge before). C#
  `Maybe.Nothing<T>()` is a member of the static class beside the generic
  `Maybe<T>`, and a **`using N;` of a namespace written as a block** brings its
  extension methods into sight from another directory (commandline: 82% of
  graphify's calls from 68%). **`$` is a sigil only in PHP and Ruby** (and
  Swift's `$0`): it was stripped from JS / TS names, so zod's `$ZodType`
  family named something else (bases 44% → 97%); a TypeScript name that is an
  interface and a constant is the type where a type is written, the value
  elsewhere.
  `benchmarks/compare_engines.py` now counts an edge of a C++ definition
  written out of its class as an edge of the declaration graphify hangs it
  on (C++ header calls on leveldb read 9% for that reason alone).
  Not all of what only graphify has is a gap: 265 of click's are
  `runner.invoke(cli)` linked to the `cli` nested in another test.
- **Native engine 0.5.48: six more rules from the same reading.** A Java
  import of `p.C` named nothing when `module-info.java` declared a module
  `p` (gson: its imports of `com.google.gson.*` had no edge at all). Python
  `except E as e:` / `except (A, B):` are references to the types caught.
  Swift: the types an enum case carries (`case option(ParsedArgument)`), a
  metatype (`Command.Type`), and a case built with what it carries
  (`throw ParserError.noValue("x")`: a reference to the enum, no edge
  before). Kotlin `x.apply { … }` / `x.also { … }` give the receiver back in a
  chain (`CodeBlock.builder().apply(action).build()`).
- **Native engine 0.5.46: the 50 name-resolution rules the checklist found
  missing.** `benchmarks/conformance.py` had 356 of its 406 cases passing;
  all pass now, and each change was read on the 45 audit repositories (edges
  added and removed, at their source lines). By rule:
  a **cast used where it is written** is a value of its type (Java, C#,
  TypeScript, Kotlin, Swift `as!`, Go `x.(*T)`); C# **`a?.b`** and a Kotlin
  parameter typed **`T?`** keep the type; **`if let Some(r) = opt`** /
  Swift `if let` / `guard let` on a parameter, Scala `case r: T`, a Java
  resource variable and the `it` of Kotlin `r.let { … }` are typed; a member
  asked of an **enum's value** is the enum's; a Kotlin / Swift
  **`typealias`**, a Ruby constant assigned a class and C++
  `namespace u = util;` name what they alias; a Go `var`, a Kotlin top-level
  `val` and a Ruby `@field` are **typed by their value**, and a Scala `val`
  in a method is a local; Java `this::m`, Kotlin `::f` and PHP
  `[$this, 'm']` are an **`indirect_call`**, Kotlin and PHP `Foo::class` a
  `references` edge (Kotlin's two were `calls` before); Rust **`self::f`**
  and `super::f` in an inline module, a C# **`global using`**, a TypeScript
  type through `import * as m`, optional parameter and getter, a JavaScript
  object literal's method, `F.prototype.m` and `await import()` resolve;
  Ruby **`super`**, bare or with arguments, calls the method further up; of
  several Rust `let`s of one name **the last before a use** is meant; a C
  variable hides a function of its name; a use above a local's declaration
  keeps the outer name; C++ range-`for`, `Outer::Inner` and `ns::Type` are
  read with their qualifier, the asking file's own definition first; `Box`,
  `Arc`, `Rc`, `Cell`, `RefCell`, `Pin`, `Cow` are wrappers only in Rust.
  On the 45 repositories: `calls` 121,235 → 121,691, `references` 41,711 →
  43,396, `indirect_call` 4,137 → 4,229, `reads` 32,219 → 32,475; unresolved
  rows left unmarked 314 → 305 on the first set, 1,041 → 953 on the second.
  Edges that went away were read too: Kotlin class literals that were
  `calls`, a function linked through a parameter or a later local of its
  name, leveldb's `Comparator` and `log::Writer` linked to a namesake in
  another file. A local's and a reference's position are now kept in the
  facts cache (`update=True` read them as 0).
- **Native engine 0.5.44: fourteen more repositories, and what they found.**
  `benchmarks/polyglot.py` has a second set (`--set 2`): one more repository
  per language, none an HTTP client or a web application — click,
  commander.js, zod, cobra, gson, clikt, scopt, commandline, ripgrep,
  swift-argument-parser, cJSON, leveldb, thor, monolog. The first 31 had
  314 unresolved rows left unmarked; these 14 had 1,387 on the same engine,
  and reading them found what the first set could not:
  - `file_spans` (the span fallback when Python tree-sitter is missing)
    returned 0.5.40's block scopes as unnamed spans; they are no spans. The
    parity snippets had no closure at file level, so nothing compared them.
  - C#: `namespace Shop.Core { … }` written as a block did not see `Shop`'s
    names (only a file-scoped `namespace Shop.Core;` did), and `using
    Shop.Text;` reached one of the files that open the namespace, not all of
    them. commandline: 1,295 edges added, 52 removed (guesses by name to a
    namesake in the tests, now the type the source names).
  - C#: one branch of an `#if` in the middle of an expression (`() =>` /
    `#if X` / `a ? b :` / `#endif` / `c;`) cost the class around; the two
    directive lines are blanked, in the engine and in `spans.py`.
  - Rust: a closure in a macro's arguments has its typed parameters and its
    `let`s (`rgtest!(name, |dir: Dir, mut cmd: TestCommand| { cmd.arg(…) })`),
    and a macro outside any definition is a scope. ripgrep: 69 calls.
  - C++: `Random rnd(test::RandomSeed());` is a variable, not the declaration
    of a function `rnd` (a call where a parameter's type would be). leveldb:
    71 edges added, 23 removed, the removed ones from the functions that were
    none.
  - Kotlin: the grammar drops some comments from the tree (a KDoc block before
    a declaration with modifiers); a definition's region is read from the
    source where the tree has no comment. clikt: 8 files' spans now equal
    `spans.py`'s.
  - Swift: the type of a stored property (`var fav: ColorOptions`, with or
    without a property wrapper) was no reference at all: the annotation is a
    child with no field name. swift-argument-parser: 429 `references`;
    alamofire, of the first set: 592.
  On the first 31 repositories nothing else changes but four redundant
  `using` edges of todoapi. Unmarked rows on the second set: 1,387 → 1,041.
  The audit of every tool on the 14 (`docs/benchmark-polyglot.md`): no tool
  crashes; 38 findings on graphify's graphs and 17 on the native ones, 14 and
  12 of them the orphan-node note.
- **A Rust function under `#[cfg(test)]` is test code**, as a module under it
  was (`spans._test_regions` and the engine's `test_regions`, engine 0.5.45): ripgrep's
  `#[cfg(test)] pub(crate) fn get_limited(&self)` in a source file was listed
  as production code by `graphlore_tests_for`.
- **`graphlore_skeleton` finds a name under more attributes.** It looked
  twelve lines below the first line of a definition for its name, and showed
  `[Theory]` as the header of a C# test under twenty `[InlineData(…)]` lines;
  it looks forty.
- **Native engine 0.5.43: the comparison with graphify measured again, and
  attribute names marked on any receiver.** The diagnostics mark a member of a
  name a type declares with no `def` whatever the receiver (0.5.41 did on an
  untyped one): a typed receiver that lacks the `def` has the attribute or
  nothing. Graph unchanged; unmarked unresolved rows 367 → 314 (Ruby 81 → 28).
  `docs/native-engine.md`'s table against graphify 0.9.111 is measured on
  engine 0.5.42: 44,960 native calls for graphify's 27,842, 85% of them found
  as before (JavaScript 41% from 43%: the vendored bundles), and the polyglot
  audit on native graphs raises nothing but its orphan-node note.
- **Native engine 0.5.42: module patterns, typed function declarations, RSpec
  shared examples.** Three things 0.5.40's scopes left open. A JavaScript
  module written `var Dataset = (function () { function Dataset() {} return
  Dataset })()` names, outside that function, the function written in it
  (`Def::returned_out`; the declaration is no local), and a function assigned
  to a function as its member (`Dataset.extractData = function () {}`) is
  found through it. A function declared with a type (`const first: Lookup =
  () => {}`) is no local that hides itself where it is passed as a value. A
  bare name in RSpec `shared_examples 'x' do … end` that no block around it
  declares is the `let` of the block that says `it_behaves_like 'x'`, when
  every place that includes the examples gives the same one. On the 31 audit
  repositories' own code: 15 edges added, 7 removed (5 of them a call linked
  to the other copy of a file, 2 a guess by name on what is now a `let`); 423
  added in vendored and minified bundles, members assigned to functions.
  Unmarked unresolved rows: 383 → 367. Tried and not kept: refusing the one
  extension in sight on a receiver of a project type it is not written for;
  on the audit repositories it removed 207 edges, the sampled ones all right
  (`client.BuildUri(request)`, `FunSpec.getterBuilder().strictfp()`), because
  the walk's type for a receiver is not exact enough to refuse by.
- **Native engine 0.5.41: what a reopened Ruby class extends, and attributes
  in the diagnostics.** A member a Ruby class has through what another opening
  of it mixes in or extends is found from every opening
  (`Faraday::Response.register_middleware(…)` in a file that opens `class
  Response` again, `extend MiddlewareRegistry` where it is declared), and a
  class written `Request = Struct.new(…) do extend Registry … end` mixes in
  what its block says: faraday's eight `register_middleware` calls,
  `EXTRACTED`, and three `mixes_in` edges. The diagnostics mark a member an
  untyped receiver is asked when a type declares that name with no `def` (a
  `Struct` member, `attr_accessor`, `def_delegators`: `env.status` beside
  `Env = Options.new(:status, …)`): the one `def` of the name is one of
  several things the receiver may be. Unmarked unresolved rows: 501 → 383
  (Ruby 215 → 97).
- **Native engine 0.5.40: a closure no definition holds is a scope.** The
  callback of `test('…', () => { … })`, an RSpec `it '…' do … end`, a module
  wrapped in a function: each is a definition with no node and no name
  (`Def::block`) whose locals are its own, as a function's are, and one inside
  another sees the outer one's. Until now a local declared there was no local
  at all: `const request = …` in one test was the `request` function of
  another file (`EXTRACTED`), and the same name in the next test was the same
  thing. A definition written in such a closure is a name of it and of the
  closures inside, not of the file. On the 31 audit repositories, in the
  projects' own code: 210 edges added (got 96, faraday 59, express 41, most of
  them a test's call through its own local or to its own helper) and 140
  removed, nearly all a local or a callback parameter linked to a definition
  of its name elsewhere (`assert.strictEqual(body, 'ok')` to another file's
  `body`, `.expect(200, cb)` to another test's `cb`). In vendored and minified
  bundles, which the analysis scope leaves out, 1,347 added and 2,587 removed
  (retrofit's generated Dokka bundle). Unmarked unresolved rows: TypeScript 87
  → 39; Ruby 175 → 215, where a receiver that was no name at all is now an
  untyped local of its block and is reported as one. Known cost: an older
  JavaScript module written `var Dataset = (function () { function Dataset()
  {} return Dataset })()` no longer answers to `new Dataset()` outside that
  function (the variable holds what a call returns).
- **Native engine 0.5.39: extensions for a library's type on a receiver the
  walk cannot type, and a C# `#if` inside an expression.** A call the walk
  could not follow, whose name is the one extension in sight written for a
  type the project does not define, is that extension's (`INFERRED`):
  `e.Name.LocalName.RemoveUnderscoresAndDashes()` beside an extension for
  `string`, Kotlin `user.email.isEmailValid()` beside `fun
  String.isEmailValid()`, Swift `NotificationCenter.default.postNotification(…)`,
  Rust `request.headers().has_content_type()` beside `impl HeaderMapExt for
  HeaderMap`. Never when the receiver is a local whose type the source names
  (that type was already asked), when the walk gives the receiver a project
  type, when the name is also a member of a type of the project, for a static
  call on a library's type (`HttpUtility.UrlEncode(s)`), or from the extension
  to itself. 79 calls on the 31 audit repositories, each read at its site
  (kotlinpoet 30, restsharp 23, alamofire 13, ureq 7). A C# `#if` … `#else` …
  `#endif` that opens in the middle of an expression or declaration is parsed
  with its first branch only (`parse::blank_expression_branches`, and
  `spans._blank_expression_branches` for the Python span engine): seeing both
  branches the parser lost the class around, so RestSharp's
  `HttpResponseExtensions` had no node and its extension methods were local
  functions. Unmarked unresolved rows: 576 → 509 (C# 75 → 40, Kotlin 42 → 22,
  Swift 61 → 49).
- **Native engine 0.5.38: a field beside a method of its name, and more of
  the unresolved rows say why they are no gap.** A member read without a call,
  with more after it, is the field when its type has a method of the same name
  (Rust `input: ConsumeBuf` beside a trait's `fn input()`, `self.input.fill()`):
  the walk took the method and stopped. ureq gets 42 calls, nothing else in the
  31 audit graphs changes. The diagnostics (graph unchanged) mark four more
  kinds of row: a receiver whose path reaches a field, variable or function
  written with a library's type (`$this->request->getBody()` with
  `RequestInterface $request`, Rust `self.builder.header()` on an
  `http::request::Builder`) or a local constructed from a type the project does
  not define (`new \ReflectionProperty(…)`), unless the one definition is an
  extension; a bare call where a method is not in scope by its bare name (PHP
  `\rewind($h)` beside a `rewind()` method, a Go closure `h` beside a method
  `h`); and a bare name whose one definition is an RSpec `let` of another block
  of the file. An RSpec `let` is no candidate for a member call
  (`conn.ssl` beside `let(:ssl)`), a PHP `$x` is looked up as the local it is,
  and Ruby's `dup` / `clone` are core names. Unmarked rows on the 31
  repositories: 801 → 576 (PHP 137 → 29, Ruby 239 → 175, Rust 96 → 64).
- **Native engine 0.5.37: JavaScript's unresolved rows say why they are no
  gap.** New marks: a call of the caller's own local or parameter (a
  callback it was handed), a candidate in vendored or minified code, a
  member that is a field assigned a value, a property defined at run time
  (`Object.defineProperty`), a receiver holding a library's value (`const
  app = express()`), a property middleware attaches to a request, the
  request a framework hands a function. The document a Mongoose model's
  static hands its callback is typed (`INFERRED`). JavaScript's unmarked
  rows 18 → 0; all languages 926 → 801.
- **Native engine 0.5.36: JavaScript receivers the source states.** `this`
  in a function expression is what it is attached to (`app[method] =
  function`, a callback a Mongoose schema is handed); in an ES5 constructor,
  its prototype's functions; a Mongoose document has its schema's
  `methods`; express's own `req` / `res` objects
  (`Object.create(http.IncomingMessage.prototype)`) type a handler's untyped
  `req` / `res`. express: 158 calls, the Mongoose demo 49 (`INFERRED` where
  a convention, not the source, says what the receiver is). JavaScript's
  unmarked unresolved rows 55 → 18. The facts cache is rebuilt once.
- **Native engine 0.5.35: C function-pointer slots dispatch to the functions
  put in them.** An initializer (`.close = redisNetClose`, or by position)
  or an assignment (`ac->ev.addRead = redisLibuvAddRead;`) gives a
  `dispatches_to` edge from the slot to the function, so `impact` and call
  paths go from `r->fn->createString(…)` on to `createStringObject`. A field
  of an anonymous struct type answers for its body's members. hiredis: 88
  `dispatches_to` edges, 81 reads and writes. The facts cache is rebuilt
  once (`FileFacts::slots`, `Def::inline_body`).
- **Native engine 0.5.34: a C `typedef struct X { … } X;` is its struct.**
  A local typed by such an alias reaches the struct's members (the target
  0.5.32 wrote for it was the first type in the body, now the struct's own
  name). A function returning a pointer types its parameters; `extern T x;`
  declares the variable defined elsewhere (no node); a file-level variable
  inside an `#if` group or `extern "C" { … }` is a variable. hiredis: 34
  calls through function-pointer fields (`r->fn->createString(…)`), 836
  reads, 324 writes, 312 uses; C's unmarked unresolved rows 33 → 0. The
  facts cache is rebuilt once.
- **Native engine 0.5.33 and `spans.py`: a C/C++ `#if` written once per
  configuration is parsed once.** When every branch of an `#if … #else …
  #endif` leaves the same number of braces open, the parser is given the
  first branch (the rest and the directives are blanked in place); it used
  to read the rest of the file into the function around (cpr's
  `multiperform.cpp`: 25 methods nested in `DoMultiPerform`). C++ `using X
  = …;` is a span of `spans.py`'s rule too (`fetch` on an alias returned the
  namespace around it). C++'s unmarked unresolved rows 6 → 0. 10 calls on a
  structured binding (`for (auto& [session, method] : sessions_)`) had an
  edge only by accident and are gone.
- **Native engine 0.5.32: C++ type aliases and template specializations.**
  `using X = T<…>;` is a definition (graphify has these nodes too) and an
  alias or `typedef` is followed to the type it names; its arguments pick a
  template's specialization (`<Response, true>` fits `<T, true>`). A bare
  template name with several specializations is `INFERRED`, and a member
  several of them declare gets no edge. Deduction guides declare no
  function. `std::vector`, `std::map` and the other standard containers
  are collections. cpr: 50 calls, 23 reads, 98 references, 34 alias nodes;
  C++ definitions shared with graphify 97.8% → 98.7%; C++'s unmarked
  unresolved rows 12 → 6. The facts cache is rebuilt once
  (`Def::alias_args`).
- **Native engine 0.5.31: a C++ namespace is every block that opens it.**
  `cpr::File(…)` is looked up in each `namespace cpr { }` of the project,
  not only in the block the caller's file opens; a method defined for a
  type inside such a block is the type's, not the namespace's; a name called
  is the function of that name and a name with `::` after it the class
  (`cpr::async(…)` beside `class async`). cpr: 273 `EXTRACTED` calls, none
  gone; C++'s unmarked unresolved rows 28 → 12.
- **Native engine 0.5.30: C++ locals declared without a value, and the
  constructions the grammar reads as declarations.** `Session session;` is a
  local of its type, and `cpr::ThreadPool pool(context);` beside a local
  `context` a variable constructed from it (not a function `pool`: it keeps
  its span, gets no node and answers no name). cpr: 538 `EXTRACTED` and 140
  `INFERRED` calls (overloads the arguments do not decide), 13 nodes of
  misread declarations gone; hiredis: 10 reads and writes of struct fields.
  The unresolved rows mark calls on `std` (`std::async`, a member of a
  field or local declared with a `std::` type), even where the project
  opens `namespace std`. C++'s unmarked rows: 50 → 28. The facts cache is
  rebuilt once (`Def::misread`, `Def::written_head`, `Local::written_head`).
- **Native engine 0.5.29: a library interface's one implementation is
  marked in the unresolved rows.** A call left without an edge whose one
  candidate is a member of a type implementing a library type
  (`implements RequestInterface`, `extends Writable`), on a receiver the
  source gives no type, is `library_like` ("the one definition implements a
  library type's member"): the receiver may be any implementation. Marker
  bases (`Sendable`, `JsonSerializable`, `Closeable`, exception roots), Ruby
  and member extension functions are left out. 477 of the 31 repositories'
  6,166 rows (got 191, guzzle 253); 1,009 are left unmarked, from 1,486
  (PHP 393 → 137, TypeScript 315 → 124). graph.json is unchanged.
- **Native engine 0.5.28: RSpec `let` and `subject` are definitions.**
  `let(:conn) { … }`, `let!`, `subject(:request) { … }` and a nameless
  `subject { … }` are methods of the `describe` / `context` block they are
  written in: the innermost of several of one name answers a use, and a use
  outside every such block names nothing. What the block's last expression
  gives is the method's result (`conn.get` reaches `Faraday::Connection#get`
  through `let(:conn) { Faraday::Connection.new }`). A module that
  `RSpec.configure`'s `config.include M` puts in every example answers a
  bare name a spec calls that nothing else answers to. faraday: 374 new
  definitions, 484 calls (442 `EXTRACTED`); 9 `INFERRED` guesses on a block
  parameter inside a `let` are gone. Calls left without an edge: 6,206 →
  6,166, Ruby's unmarked rows 298 → 239. The facts cache is rebuilt once
  (new `Def::scope_lines`, `FileFacts::spec_includes`).
- Native engine 0.5.27, the small cases the diagnostics left. On the 31
  audit repositories: 264 calls (183 `EXTRACTED`) and 302 reads and writes
  gained, 252 calls gone (227 of them kotlinpoet's, moved to another
  overload).
  - **Swift implicit members are typed by more places**: a property's
    initializing closure, a memberwise initializer's label, a library
    type's static member a project extension adds, and the `headers`
    extensions add to `URLRequest` (alamofire: 30 calls, the untyped ones
    from 39 to 7).
  - **Lombok accessors read and write their fields** (`filter.getTags()` on
    a `@Data` class; quarkus-realworld: 301 reads and writes).
  - **Laravel's `auth()->user()` is the class that extends
    `Authenticatable`** (`INFERRED`).
  - **A Rust `Err(e)` binds an untyped local**, and a star-imported module
    passes on what its `pub use` names (actix-realworld: 18 calls of
    `e.error_response()`, `INFERRED`).
  - **An enum case is a value of its enum**: kotlinpoet's
    `addModifiers(KModifier.EXTERNAL)` goes to the `vararg` overload.
  - **A project type named like a language one is the project's** (`enum
    Type`): retrofit's `new PhoneNumber(…, Type.MOBILE)` keeps its
    constructor.
  - **A guess by name never takes Rust's or C++'s own method names**
    (`lock`, `c_str`): 22 wrong `INFERRED` calls gone.
- Native engine 0.5.26, two more kinds of library names in the
  diagnostics (graph.json is unchanged): a capital head that names nothing
  of the project and no local (`Date.now()`, `Assert.NotNull(…)`), and the
  methods of Rust's and C++'s standard types (`lock`, `clone`, `c_str`).
  Of 6,237 unresolved rows, 1,610 are left unmarked (2,066 before).
- Native engine 0.5.25, five readings the 0.5.24 diagnostics pointed to.
  On the 31 audit repositories: 83 calls (60 `EXTRACTED`) and 157 reads,
  writes, uses and references gained, 8 edges gone.
  - **C# primary constructor parameters are typed names of the class**
    (`class QueryHandler(ConduitContext context)`, then
    `context.SaveChangesAsync()`): aspnetcore-realworld's handlers had no
    edge for what they use.
  - **A cast types a local** (C# / Java `(Box)o`, `o as Box`, TypeScript
    `<Cup>o`, Kotlin `o as? Jar`), `EXTRACTED`.
  - **A name taken out of the object a function returns is the function it
    names** (`const { showSuccessToast } = useCustomToast()` beside `return
    { showSuccessToast, … }`), `INFERRED`.
  - **A Python `@property` with more read after it is what it returns**
    (`self.cookies.extract_cookies(…)`).
  - **A C# top-level variable that names nothing in sight, called by an
    extension's name, is its receiver** (`builder.Services.AddJwt()` in
    `Program.cs`), `INFERRED`; never for a capital head, which is a
    library's static call.
- Native engine 0.5.24, the diagnostics tell a library's name from a
  missing edge (graph.json is unchanged). Each row of `build --unresolved`
  carries `library_like`: why it is most likely a library's name the
  project happens to define once (a name the language's own types answer
  to, a library's receiver, `self` in a type with a base the project does
  not define, a definition that is test code for a call that is not), or
  null. `benchmarks/unresolved.py` prints both totals and takes `--real`:
  of 6,285 rows on the 31 audit repositories, 2,114 are not marked.
- Native engine 0.5.23, **the type arguments a call writes say which
  overload it is of** (C#, Java): `client.Get<User>(id)` is the callable
  that declares one type parameter, and a call that writes none, between a
  callable and its generic twin of the same parameters, is the plain one.
  In restsharp 81 tied targets are gone and 27 calls left with one target
  are `EXTRACTED`; overloads that take different things stay tied.
- Native engine 0.5.22, declared types and three lookups. On the 31 audit
  repositories: 249 calls and 150 reads and writes gained, 513 calls
  `INFERRED` → `EXTRACTED`, 9 calls gone; the share of `INFERRED` calls
  goes from 30% to 28%.
  - **The type a declaration writes comes before what is assigned**: Go
    `var rooms *Manager`, Kotlin `val kind: Kind = builder.kind` (no type at
    all before), and C++ `static HttpServer* server = new HttpServer();`,
    whose 509 calls in cpr's tests were a guess by its value.
  - **Of several callables of one name in the middle of a chain, the one
    whose result has what is asked next**: `result.response().isSuccessful()`
    beside a static `response(Response)` (46 calls in retrofit).
  - **A field before a function of its name when a member is asked of it**
    (Kotlin constructor parameters beside `fun builder(…)`, Swift
    `eventMonitor?.request(…)`).
  - **C# names are looked up in every namespace around the file's**; four
    bases written `: Interceptors.Interceptor` are the project's class,
    where they were an external name.
  - With more arguments typed, 9 calls that tied between overloads are one
    (`producerOf(ANY.copy(…))`).
- Native engine 0.5.21, **names bound with a type the source states are
  locals**: a loop variable written with its type (Java `for (Pet pet :
  pets)`, C# `foreach (Pet pet in pets)`), a type pattern (`o instanceof
  Pet p`, `o is Pet p`), and what a wrapper holds (Rust `if let Some(p) =
  e`, `while let`, a `match` arm's `Some(p)` / `Ok(p)` or the fields of an
  enum variant; Swift `if let p = e`, `guard let q = e`). A local assigned a
  field or a variable is typed by what that holds. On the 31 audit
  repositories: 29 calls and 27 reads, uses and writes gained; 24 edges of
  alamofire gone that read such a name as the class's method or property
  of the same name (`task` after `guard let task = …`).
- Native engine 0.5.20, two wrong readings. On the 31 audit repositories:
  21 edges gained, 38 calls gone.
  - **`parent`, `base` and `cls` are the object's base or class only in the
    language that says so**. A Go variable named `parent` was read as a
    base class (`parent.SetProxy(…)` had no edge), and so were a Python
    parameter `base` and a Java parameter `cls`.
  - **C# `base.X()` is the base class's `X`**, where it was read as
    `this.X()`: 13 `EXTRACTED` calls from an override to itself are gone
    (`base.Dispose(disposing)`), and a base the project defines is reached.
  - **A guess by name alone never links a definition to itself**:
    `inner.evaluate()` inside `evaluate()` is a wrapper handing on to what
    it wraps. 25 `INFERRED` self-calls are gone, 4 of them a real recursion
    on another object.
- Native engine 0.5.19, C# extensions and partial types. On the 31 audit
  repositories: 618 calls gained (197 `EXTRACTED`), 51 `INFERRED` ones now
  `EXTRACTED`, 7 gone; restsharp, whose extensions are C# 14 blocks, gets
  570.
  - **The members of an `extension(T x) { … }` block are extensions written
    for `T`**: `client.BuildUri(request)`, `request.AddHeader(…)`. They had
    no edge.
  - **A receiver has the extensions of its bases** (`RestClient` those
    written for `IRestClient`), `EXTRACTED`; several overloads written for
    the receiver's type are one extension, chosen by their arguments.
  - **A C# partial type is all its declarations**: a member, field or base
    one of them writes is found from any (restsharp: 121 more reads, uses
    and references).
  - **A field or property of a library type is answered by the extension
    written for that type** (`string Name` then `request.Name.IsNotEmpty()`,
    `WireMockServer _server`), a local by the type its value constructs,
    and a C# namespace sees the extensions of the namespaces around it.
  - **A name .NET's own strings, lists and types answer to is no evidence**
    on a receiver that is no type of the project (`ForEach`, `Split`,
    `GetProperties`): two wrong edges gone, three right ones with them.
  - A call that names nothing no longer leaves a `references` edge to the
    type it started from when it now resolves (91 in restsharp and
    kotlinpoet).
- Native engine 0.5.18, CommonJS modules and the receivers of a test file.
  On the 31 audit repositories: 329 calls gained, 12 gone; a test of express
  now reaches the method of `lib/` it exercises (`graphlore_tests_for`).
  - **A CommonJS module is what it assigns to `module.exports`**: `const Foo
    = require('./foo'); new Foo()`, `make()` and `Foo.create()` resolve to the
    class or function `foo.js` assigns there (`exports = module.exports =
    createApplication` too). They had no edge.
  - **`module.exports = require('./lib/app')` is that module** (a
    `re_exports` edge), and `exports.name = bound` passes a name on: in
    express-mongoose-demo the OAuth routes are handled by `users.login`
    (`exports.authCallback = login`), not by the routes file's function.
  - **A closure's parameter and a module variable at file level are unknown
    receivers**, as an untyped local is: `res.sendFile(…)` in an `it(…)`
    callback and `app.listen()` after `var app = express()` get an
    `INFERRED` edge by a distinctive method name. A function assigned to an
    object (`res.sendFile = function sendFile() {}`) counts as its method;
    never on a variable holding a library's value, and a name of three
    letters (`app.use`) is still no evidence.
  - **In sight includes what an imported module passes on**: a file that
    requires a package's entry has the files that entry re-exports.
  - **Names JavaScript's own types and Node's emitters and streams answer to
    are no evidence** (`once`, `pipe`, `concat`, `abort`): 12 `INFERRED`
    calls of got's own `pipe` / `unpipe` / `abort` on untyped receivers are
    gone.
- Native engine 0.5.17, Swift's implicit members and the overloads an
  extension adds. In alamofire: 158 calls that had no edge, and 783 that go
  to another definition of the same name than before (none to nothing).
  - **`.status(301)` is typed by its place**: the parameter the argument is
    for, the type a declaration writes (`let e: Endpoint = .made(1)`), or the
    result of the function it is returned from. No type, no edge.
  - **It says which overload a call is of**: an argument `.status(…)` fits a
    parameter whose type has a static `status` and not one whose type has
    none.
  - **A Swift method's overloads include the ones an extension of its type
    adds**: `session.request(.get)` in alamofire's tests is the
    `request(_ endpoint: Endpoint)` the test helpers add to `Session`, where
    it was the library's `request(_: any URLConvertible)`.
  - The parameter's very type scores over one of its bases, and a trailing
    closure goes to the parameter that asks for no label (a function type,
    or a type alias) before a labelled one.
- Native engine 0.5.16: **the calls in a Rust macro's arguments are read**.
  The grammar gives them as tokens, so `assert_eq!(handle_count(&pool), 2)`,
  `assert!(pool.count() > 0)`, `vec![make_pool()]` and `format!("{}",
  c.name())` named nothing; the arguments are parsed again as an expression
  and their calls placed where they stand, a macro inside a macro too. What
  is no expression (a `{ … }` body, `a => b` arms, the pattern of
  `matches!`) is left out. 171 more edges on the 31 audit repositories, none
  gone, nearly all a test's assertions: the share of graphify's Rust calls
  the native graph has goes from 81% to 86%.
- Native engine 0.5.15: the import edges a Swift file gets from the files it
  uses were written in an order that differed from run to run (the same
  edges; a set's own order). They are in file order now.
- Native engine 0.5.14, the rest of collections and tuples. On the 31 audit
  repositories one edge comes back (`copy(id)` in fastapi-template, lost in
  0.5.11); the rest does not occur there with project types:
  - **A map's keys**: `m.keys` / `keySet()` is a collection of the key type,
    and the first of a pair taken apart is a key (`for ((k, v) in m)`,
    `m.forEach((k, v) -> …)`); of a list's pair, the element.
  - **`partition`** taken apart gives two collections of the elements,
    **`zip`** taken apart one of each (`for ((x, y) in xs.zip(ys))`, Python
    `zip(xs, ys)`), **`fold(init)`** / `Aggregate(seed, …)` / `inject(init)`
    / JS `reduce(f, init)` is what `init` is (the accumulator too), and
    **`associateWith`** is a map whose values are what its lambda gives.
  - **A part a callable returns as a literal of names** (`return
    [copiedText, copy]`) is the definition of that name inside it when its
    type names no class; a local holding it calls it (`INFERRED`).
- Native engine 0.5.13, generics through values, nested arguments and
  generic bases. On the 31 audit repositories three undecided overload edges
  become the one overload that fits; the other patterns do not occur there
  with project types on both sides:
  - **A local typed by its value keeps the value's bindings**: `val r =
    call.execute()` on a `Call<Contact>` makes `r.body()` a `Contact`.
  - **A collection type argument binds its element**: `Call<List<Contributor>>`
    makes `call.execute().body().get(0)` a `Contributor`.
  - **A member a generic base declares takes the base's type arguments**:
    `class People : Store<Person>` makes `people.find()` a `Person`, through
    intermediate bases too (Java, Kotlin, C#, TypeScript, Swift).
  - **A loop over a local assigned a call that returns a collection** (`val
    xs = repo.all(); for (x in xs)`) has the element; the element step did
    not look through the callable the local's path ends at.
- Native engine 0.5.12, numbered fields. On the 31 audit repositories 28 of
  ureq's calls change, none is lost: 14 guesses by name are `EXTRACTED`, 13
  move to the trait or type the field holds.
  - **A Rust tuple struct's fields** (`struct Script(Arc<Mutex<State>>)`,
    `struct Tunnel(Box<dyn Transport>)`) are its numbered parts:
    `self.0.lock().unwrap().bump()` calls `State.bump`, `self.0.buffers()`
    calls `Transport::buffers`. `self.0.lock()` named a `lock` of another
    file by its name.
  - **A part of the tuple a local holds**: `let pair = setup(); pair.0.go()`,
    Swift `both.1`, C# `pair.Item2`, Kotlin `p.second` on a `Pair`.
- Native engine 0.5.11, tuples taken apart. On the 31 audit repositories 38
  more edges and 18 gone (14 of them in minified bootstrap bundles):
  - **A declaration that takes a tuple apart types each name by its part**:
    `let (agent, clock) = setup()`, `var (client, probe) = Setup()`, `val
    (l, r) = two()`, `let (t, b) = split()`, `const [door, lock] = parts()`,
    `a, b = two()`, Go `a, b := both()`, for results written as `(A, B)`,
    `[A, B]`, `Pair<A, B>`, `Triple`, `tuple[A, B]` or Go's result list.
  - **The first name of such a declaration was the whole result**, and a
    tuple-returning function's result was one of its parts; a tuple now names
    no one type (Go's `v, err := f()` still takes `f`'s first result).
  - Kotlin and Python declarations that take a value apart are locals: they
    hid nothing, and the names fell through to outer ones.
- Native engine 0.5.10, what a mapping's function gives, and lists of lists.
  On the 31 audit repositories 5 more edges, none gone:
  - **A lambda of several statements** gives its last expression (Kotlin,
    Ruby, Rust, Swift) or what its `return`s give when they agree, and a local
    it declares is what it is assigned (`xs.map { x -> val o = x.owner; o }`).
  - **A function given by reference maps**: `map(Item::owner)`,
    `map(Item::getBoss)`, `map(::Item)`, `map(Item::new)`, Rust
    `map(Part::head)`, Swift `map(\.pilot)`.
  - **Lists of lists and groups**: `chunked`, `windowed`, `chunks`,
    `each_slice` give lists of the elements, `groupBy` a map of them,
    `flatten` / `SelectMany` takes one level off; `associateBy` /
    `ToDictionary` key the elements, `reduce` / `Aggregate` give one.
  - **A closure inside another** names the outer closure's parameter as the
    outer closure's: the inner parameter's type, and a local assigned inside
    a closure from its parameter, were looked up under the bare name.
- Native engine 0.5.9, what a declaration's type arguments bind. On the 31
  audit repositories 19 more edges, none gone, and 12 of retrofit's calls
  that were guesses by name are `EXTRACTED`:
  - **`Response<Contact> r` makes `Response`'s `T` a `Contact`**: `r.body()`
    with `fun body(): T`, `r.value` with `T value`, an element of `r.all()`
    with `List<T> all()` and a constructor's `val item: T` are `Contact`s.
    A result written with the class's own parameter passes it on:
    `call.execute().body().getName()` on a `Call<Contact>` reaches
    `Contact.getName` through `Response<T> execute()`. Locals, parameters,
    fields and declared results all carry their type arguments (`targs`).
  - **A parameter's own declaration** is read for what it holds, whether it
    is a map and its type arguments (`Optional<User> u`, `Map<K, V> m`):
    parameters missed what 0.5.7 and 0.5.8 read for other locals.
  - **A method of a generic Rust impl or C++ template** (`impl<T>
    Holder<T>`, `CurlContainer<T>::Add`) finds its type; the owner was
    looked up with its type parameters in the name.
- Native engine 0.5.8, maps and pairs. On the 31 audit repositories 10 edges
  change, most to the definition the call is of:
  - **A map's values.** `Map<K, V>`, `HashMap`, `Dictionary`, `dict[K, V]`,
    `Record`, Go `map[K]V` and Swift `[K: V]` have their values' type as
    their element: `m[k]`, `m.get(k)`, `getValue`, `getOrDefault`,
    `getOrPut` give a value, `m.values` is a collection of them. Kotlinpoet's
    `constructorProperties[param.name].emit(…)` calls `PropertySpec.emit`
    (a guess by name had linked `ParameterSpec`'s).
  - **Iterating a map gives entries, not values** (Python: keys): one loop
    variable over a map, a lambda's one parameter and `m.first()` name
    nothing; a pair taken apart is its key and value (`for ((k, v) in m)`,
    `for k, v in d.items()`, `for (k, v) in &m`, `for (const [k, v] of m)`,
    `foreach (var (k, v) in m)`, `m.forEach { (k, v) -> … }`), and beside an
    index it is the element (`withIndex()`, `enumerate()`, `enumerated()`).
  - **A loop variable taken apart is a local** (it was not recorded, and the
    name fell through to an outer one: two edges of got named a function of
    another loop).
  - Rust `for x in &xs` reads `xs` (a reference was no path).
  - A local assigned an opened wrapper (`let pool =
    self.pool.lock().unwrap();`) is typed by what it holds; the library's
    method at the end of the value stopped it.
- Native engine 0.5.7, what a wrapper holds. On the 31 audit repositories:
  191 more edges, 2 gone (one a guess by name, one moved to the definition
  the call is of):
  - **A closed wrapper is opened before it is used.** `Option`, `Optional`,
    `Mutex`, `RwLock`, `RefCell`, `Promise`, `Future`, `CompletableFuture`,
    `Deferred`, Rust `Result` and C# `Task` / `ValueTask` are the wrapper
    until `await`, `?`, an unwrapper (`unwrap()`, `get()`, `orElseThrow()`,
    `lock()`, `borrow()`, `.Result`, `.Value`) or a lambda given to them
    (`opt.ifPresent { it.run() }`) opens them: `self.pool.lock().unwrap()
    .get()` on an `Arc<Mutex<Pool>>` calls `Pool.get`, `open()?.stop()`
    calls `Conn.stop`, `(await fetchDoc()).load()` calls `Doc.load`. What
    is called on a closed wrapper (`is_some()`, `isPresent()`, `then()`,
    `Wait()`) is the wrapper's own method: no edge to the type it holds.
  - **A declared result is read through a wrapper**: `-> Option<Foo>`,
    `Promise<Foo>`, `Task<Foo>`, `Box<dyn Foo>` return a `Foo` (they returned
    nothing the walk could use).
  - **C# `var x = await f()` is typed** (a C# declarator names its value with
    no field, and only a plain call was looked for): 141 more `reads` in
    restsharp's tests.
  - **A generic class whose base is the plain class of its name**
    (`RestResponse<T> : RestResponse`) has that class as its base; the base
    resolved to the class itself and was dropped, so no member of the
    hierarchy was found from it.
- Native engine 0.5.6, chains of calls on a collection. On the 31 audit
  repositories: 144 more calls, 479 more `reads` and 46 more `writes`, and 12
  edges gone (ten of them a closure's parameter taken for a method or
  property of its name):
  - **A chain keeps what it is a collection of.** After a name declared as a
    collection, a method that keeps it (`filter`, `sortedBy`, `toList`,
    `stream()`, `iter()`, `Where`, `ToList`) leaves a collection of the same
    elements, one that takes an element (`first()`, `get(i)`, `find`,
    `FirstOrDefault()`, Swift `xs.first`) gives the element, and unwrapping
    an optional (`unwrap()`, `orElseThrow()`, `.get()` after `findFirst()`)
    changes nothing: `xs.filter { it.ok }.first().a()` calls `Foo.a`.
  - **A mapping is the collection of what its function gives**, when that is
    one expression: `xs.map { it.owner }`, `xs.map(x => x.owner)`,
    `items.stream().map(i -> i.boss())`, `dishes.Select(d => d.Chef)`,
    `planes.map { $0.pilot }`, Ruby `xs.map(&:owner)`, Python
    `[j.chief for j in jobs]`; `flatMap { it.kids() }` the elements of what
    each gives. A local assigned such a chain is a collection to the loop
    over it. A mapping the walk cannot name is a collection of nothing known,
    never of the same elements.
  - **Closure parameters in Swift and C#** (`{ p in … }`, `$0`, `d => …`) are
    names of the closure, as in the other languages: they no longer resolve to
    an outer method or property of the same name, and an iteration method
    types them. The element is the second parameter of Kotlin's
    `forEachIndexed` / `mapIndexed`.
  - **Lambda parameters were read in reverse**: with two or more,
    `tasks.forEach((t, i) => t.exec())` typed `i` as the element and left `t`
    untyped (since 0.5.5).
  - A property with more after it is typed where it is declared when its
    class says nothing more (`chief: Chief` in a Python class body, a C++
    member, `appRule.http.createArticle()`).
  - C# `List<Dish>` and the like give their element type (the grammar's
    `type_argument_list` was not read).
  - The overloads of one extension function (`asAFError(or:)` /
    `asAFError(orFailWith:)`, Kotlin `Collection<CodeBlock>.joinToCode`) are
    told apart by the arguments instead of resolving to nothing; a method of
    a Swift `extension` of a type the project does not define answers an
    untyped receiver as a Rust `impl Trait for` one does. A Kotlin extension
    is never the target of a Java `x.name()` (`latch.await()` named
    retrofit's `Call<T>.await()` while this was being written).
  - `xs.first()` as the thing called is the library's method: no edge to the
    element's type.

  The audit on graphs rebuilt by 0.5.6 raises nothing but its note on orphan
  nodes; the 31 builds take 16.8 to 17.8 s (16.1 to 16.6 s with 0.5.5). A
  first version read the callee of every call twice and took minutes on a
  long builder chain (ureq); the timing run caught it.
- Native engine 0.5.5, what 0.5.4 could not decide. 759 more edges on the 31
  audit repositories and 32 gone (most of those a loop variable taken for a
  function of its name: `for (const hook of hooks) hook(x)`):
  - **Untyped receivers, by what is asked of them.** A parameter or local
    with no type is the one type of the project that answers every member
    used on it in its definition, when two names or more that are not the
    language's own are used: Ruby `def call(env)` with `env.status`,
    `env.body` and `env.parallel?` is an `Env`, where `env.status` alone named
    a struct and a class. Python, JavaScript and PHP parameters, and locals
    the walk cannot type in any language, are typed the same way.
  - **More arguments typed for the choice among overloads**: a field,
    property or constant by its declared type or its value's; a lambda
    against a function-typed parameter; a class literal against `Class` /
    `KClass`; a concatenation, a comparison, `x.toString()`, a cast. An
    overload that takes exactly the arguments passed comes before one that
    needs a default; overloads a base class adds compete.
  - **Elements of typed collections.** The variable of a loop over a
    `List<Foo>`, `Foo[]`, `[]Foo` or `list[Foo]` (or over a call that
    declares it returns one), the parameter of a lambda given to `forEach` /
    `map` / `filter` (Kotlin's `it`), `xs[0]` and `xs.first()` are `Foo`.
  - **Type parameters bound by an argument**: `fun <T> pick(x: T): T` gives
    the argument's type, `fun <T> head(xs: List<T>): T` its element's.
  - Go `resp, err := client.Get(url)` takes the first of the function's
    results (it took the last, `error`): 235 calls in resty. Java `var x =
    …` and `for (var x : xs)` are typed by the value; `var` was read as the
    name of a type.
  - A local that holds a value and is called (`handler(x)`, `builder::add`)
    is no longer a construction of its type.
  - A local assigned a call of its own name whose arguments name another
    local (`let session = session(using: c)`) no longer sends the resolver
    round in circles (a crash on four files of alamofire while this was
    being written, caught by the comparison run; a golden holds it).

  The audit on graphs rebuilt by 0.5.5 raises nothing but its note on orphan
  nodes; the 31 builds take 16.1 to 16.6 s (14.6 to 15 s with 0.5.4).
- Native engine 0.5.4, the three things 0.5.3 still named as missing. 1,088
  more edges on the 31 audit repositories and 71 gone, and 1,175 calls moved
  from one overload to another of the same name:
  - **Overloads** (Java, Kotlin, Scala, C#, Swift, C++). A call was linked to
    the first definition of a name; it is now the one that can take the call:
    by the count of arguments (defaults and varargs counted), the name an
    argument is passed under (`named(b = "x")`, `retries: 3`, Swift's labels),
    and the type of each argument the site shows — a literal against `String`
    / `Int` / `Boolean`, a typed local or a construction against a parameter
    typed by a class of the project. `addParameter("value", String::class)`
    no longer lands on `addParameter(parameterSpec)`. Constructors are chosen
    the same way: Swift `View(frame: f)` and `View(name: "x")` are two
    constructors (462 calls in alamofire, where the site decided nothing
    before); a Kotlin primary constructor competes with the secondary ones;
    an overload marked `@Deprecated` / `[Obsolete]` gives way to one that is
    not. Kotlin annotations with arguments (`@Deprecated("old")`) are read as
    the decorators they are (303 references).
  - **The receiver or the class around.** Where a lambda's receiver and the
    class around both have a method of the name, the arguments' types decide
    (`buildCodeString { emit(this) }` passes a `CodeWriter`, which only the
    outer `emit` takes); the guess remains for the calls they do not.
  - **Results that are type parameters.** A method returning the type
    parameter its type binds to itself (`tag(x): T` in `Taggable<T :
    Taggable<T>>`) returns the type it was called on. A local assigned a call
    handed a type (`retrofit.create(Service::class.java)`, `mock<Foo>()`,
    `sp.GetRequiredService<IFoo>()`) is that type, when the function returns
    its own type parameter or is a library's and not named like a wrapper
    (`listOf<Foo>()`).
  - **Kotlin `expect` / `actual`**: of the declarations of one extension, one
    per source set, the nearest to the caller, where several candidates used
    to mean none.
  - **Untyped receivers in Ruby and Swift.** A method named on a parameter or
    untyped local is linked (`INFERRED`) when no other type answers to the
    name: no property or field of it, no `attr_reader` / `Struct.new` member /
    delegator, and not a name of the core library (`shift`, `gsub`,
    `sorted`). `env.needs_body?` is found; `env.status`, which a struct and a
    class both answer, is not guessed.

  The audit on graphs rebuilt by 0.5.4 raises nothing but its note on orphan
  nodes; the 31 builds take 14.6 to 15 s (14.2 s with 0.5.3).
- Native engine 0.5.3, what 0.5.2 still lacked. 1,859 more edges on the 31
  audit repositories (calls 41,063 to 42,408), 121 gone, most of those a
  local taken for a function of its name:
  - **Kotlin locals were typed only when the declaration wrote a type.** In
    `val c = Writer(out)` the value sits beside the declaration, where the
    walk did not look: `c.close()` reached nothing, or a method of that name
    elsewhere. kotlinpoet gains 747 calls, such as `spec.toString()` to the
    right class's `toString`.
  - **A local keeps the chain it is assigned** (`val b = builder(x).tag(y)`,
    Go `res := c.R().SetBody(b)`): it was dropped as untypeable, and is now
    typed by following what each call returns (528 calls in resty). Inside
    its own initializer a name is still the outer one (`val builder =
    builder(x)`, Rust `let tls = tls::Config::builder()`).
  - **Results read from the body.** A function that declares no result
    returns what its `return`s and expression body construct, when they agree
    (`fun builder(n) = Builder(n)`, Python `return Conn()` beside `return
    None`, `return self`, the last expression of a Ruby method); a closure's
    `return` is not the function's. Calls through such a result are
    `INFERRED`. In Ruby a name called without parentheses is followed the same
    way (`stubs.match(env)`, `s = stubs; s.match(env)`), and a local, typed or
    not, is no longer read as a call of the type it holds.
  - **Kotlin lambdas with a receiver.** In `x.apply { … }`, `x.run { … }`,
    `with(x) { … }` and a lambda passed to a parameter typed `T.() -> R`
    (`buildCodeBlock { indent() }`), a bare name is the receiver's member
    before it is the class's around. `out.apply { emit("x") }` inside an
    `emit` was a call of itself. Where both have the name the receiver's is
    taken as a guess, unless it takes fewer parameters than the call passes or
    the call hands over `this` (`buildCodeString { emit(this) }`).
  - **Constructions typed by their declaration**: C# `Box b = new(1, 2)` and
    C++ `Buf b(3)` / `Buf b{3}` call the type and its constructor (98 calls in
    cpr, 28 in the C# repositories).
  - **A parameter or local of no known type is an unknown receiver**: a method
    only one type of the project has, near the caller, is `INFERRED` (as it
    already was on the result of an unknown call). Not in Ruby and Swift,
    which have no import to say which type is meant. In Rust, a method of an
    `impl Trait for` a type of another crate is found on a local of that type
    (38 calls in ureq).
  - **Routes in a framework's own repository.** A file that takes the
    framework by a relative path (`require('../../')` in express's examples)
    imports it: the directory's `package.json` names it. Both `graphlore_routes`
    and the native engine read such files now (express: no route to 53, and
    139 route nodes with its tests' in the native graph).
- `benchmarks/polyglot.py --engine native` read whatever native graph was on
  disk: after an engine change it audited the previous engine's graphs, and
  the rerun reported for engine 0.5.2 had read 0.5.1's. It now rebuilds a
  graph built by another version of the engine and says so. It also no longer
  reports `[Test],` (an element of a list) or `#[cfg(test)]` on a `let` as a
  test the path rules miss, nor Ruby's `class ActiveSupport::TestCase` as a
  span of another symbol. On graphs built by 0.5.3 the audit raises nothing
  but its note on orphan nodes.
- Native engine 0.5.2, the gaps the 0.5.1 comparison left (`calls` recall
  against graphify 82% to 84% on the 31 audit repositories; C# 80% to 93%,
  Ruby 60% to 66%, Kotlin 64% to 70%, Java 81% to 85%):
  - **Extension functions and methods** are members to their callers: Kotlin
    `property.toPropertySpec()` (`fun KmProperty.toPropertySpec()`), C#
    `realm.IsBlank()` (`static bool IsBlank(this string s)`, from a file that
    uses its namespace), and `self.userinfo()` in a Rust `impl Trait for` a
    type of another crate. `EXTRACTED` when the receiver's type is the one the
    extension is written for, `INFERRED` when one visible extension has the
    name.
  - **A closure's parameters are scoped to the closure.** They were locals of
    the whole function: in Go, `r` of `func(w http.ResponseWriter, r
    *http.Request)` hid the `r := chi.NewRouter()` around it, and an untyped
    block parameter could name a function elsewhere (`done` in a test, linked
    to a library's `done()`).
  - **Ruby.** A bare name passed as an argument or as a pair's value
    (`Article.new(article_params)`, `status: status_code`) and the receiver of
    a call (`current_user.follow(@user)`) are calls of the method, no longer an
    `indirect_call` or nothing; `self.class.from(x)` is the class's method. A
    module or class reopened in another file is one scope
    (`Utils::Headers.new`, `Faraday::ParsingError.new` and `class Far::Compact`
    resolve). A method at the top level of a file the caller does not require
    is no longer in scope, and the name in `def headers=(hash)` is no call.
  - **Anonymous classes.** `new Callback<String>() { … }` (Java) and `object :
    Listener<T> { … }` (Kotlin): the method around constructs the type, the
    anonymous class `implements` / `inherits` it (also when the type is
    external), and no name lookup lands on the anonymous class itself.
  - **Constructors.** A construction is a call of the constructor the type
    declares as well as of the type (`new RestRequest(url)` → `RestRequest`
    and its `.RestRequest()`; Python `__init__`, JS/TS `constructor`, Ruby
    `initialize`, PHP `__construct`, Swift `init`), so `graphlore_impact` on a
    constructor lists the code that builds the type. Of several overloads the
    one taking as many arguments as the site passes is named; when that picks
    none or several, only the type is.
- Native engine 0.5.1, from comparing its graphs with graphify's edge by
  edge on the 31 audit repositories:
  - A call in a field assignment inside a method (`self._uri = urlparse(url)`
    in `__init__`, `this.x = make()` in a constructor) was recorded as the
    class's call, not the method's, so `graphlore_impact` on the method missed
    it. Python's `calls` recall against graphify goes from 92% to 94%.
  - PHP variables were never typed: `$factory = new CurlFactory();
    $factory->create()` and a typed parameter's `$given->release()` now reach
    the class's method (PHP 81% to 90%; `$x` stays a variable and is never
    resolved as a function or class named `x`).
  - A name bound to a required module or one of its members (`var parse =
    require('./utils').parse`, `const mongoose = require('mongoose')`) is an
    import, not a variable or constant node: `parse(v)` is a call of the
    function again, and the binding is no longer listed as state.
  - A local whose value the walk cannot type hid nothing: `let mut pool =
    self.pool.lock().unwrap(); pool.get(…)` was an `EXTRACTED` call of an
    unrelated top-level `get`, and a parameter named like a function elsewhere
    (`token_headers: dict`, a minified bundle's `t`) was that function passed
    as a value. Such a local now hides the outer name (1,079 `indirect_call`
    edges gone, nearly all in minified bundles); a bare call beside a typed
    local (`baseUrl(…)` beside `String baseUrl`), a local assigned from the
    function of its own name (`let agent = agent();`) and a field assigned in
    a method keep the outer meaning.
  - In Rust, `use super::*` inside `mod tests { … }` was read as an import of
    the parent module's file, which put that file's names in scope and added
    an import edge to it.
  - In Swift, an `extension Session { … }` in a module that only extends the
    type hid the type it imports: `Session()` and everything typed through it
    was unresolved in such a module (666 calls gained in alamofire's tests).
- `benchmarks/compare_engines.py` compares an edge from a definition only the
  native graph has (a function nested in a method, the setter beside a
  getter) as an edge from the paired definition it belongs to; TypeScript's
  `calls` recall read 78% where 96% of graphify's calls were in the graph.
- `graphlore_unreferenced` and `graphlore_same_names` no longer take a native
  module-level constant for a class (it hangs off its file by `contains`, as a
  class does); the native `kind` decides.
- `graphlore_neighbors` lists both directions when two nodes link each other
  by the same relation (A references B and B references A); it dropped one.
- `graphlore_path` and `graphlore_explain` pass `--graph` to the CLI, as
  `graphlore_query` did, so with `GRAPHLORE_OUT_DIR` set they read the graph
  the other tools read instead of the CLI's default `graphify-out/`.
- Ambiguous `.name()` labels of functions bound to an object key
  (`{ filter: () => … }` in six test callbacks) are qualified by `file:line`
  in member and neighbor lists; the bare `filter()` they got from their span
  told them apart no better than the label did.
- A Go function type written inside a map type (`map[string]func() hash.Hash`)
  is no longer taken for a function named `string`, in `spans.py` and in the
  native engine.

## [0.6.0] - 2026-11-15

Every tool was run on 31 real repositories in 14 languages
(`benchmarks/polyglot.py`, `docs/benchmark-polyglot.md`). The changes below are
what that turned up: package APIs read in all fourteen languages, routes in
nine, a span engine checked on real code in each, and the conventions applied
everywhere. What is still missing is listed at the end of the report.

### Added
- **Package APIs for Kotlin, Scala, C#, Rust, Swift, C / C++, Ruby and PHP**,
  read from each file's imports (`import a.b.C`, `using A.B;`, `use a::b::{c}`,
  `#include <curl/curl.h>`, `require 'faraday/utils'`, `use A\B\C;`). The
  project's own packages are left out per language: the packages and namespaces
  its JVM, .NET and PHP files declare (an exact match, so guzzle's
  `GuzzleHttp\Promise` dependency is not mistaken for guzzle), its crates
  (`Cargo.toml`, plus `crate::` / `self::` / `super::` and modules declared in
  the same file), gems, Swift targets and header directories. Java gained the
  same rule: retrofit's own `retrofit2.*` packages were listed as external.
- **Routes for Kotlin, C#, Rust, Ruby and PHP, and JAX-RS for Java**: Spring
  and Ktor in Kotlin; ASP.NET Core controllers (`[Route("api/[controller]")]`,
  `[HttpGet("{id}")]`) and minimal APIs (`app.MapGet`, prefixes through
  `var g = app.MapGroup("/api")`); axum (`.route("/x", get(h).post(h2))`,
  `.nest`) and actix-web (`#[get("/x")]`, `web::scope` / `resource`); Sinatra
  and the Rails `routes.draw` DSL (`resources` with `only:` / `except:` /
  `param:` and nesting, `namespace`, `scope`, `root`); Laravel `Route::` chains
  (`prefix`, `group`, `resource`, `apiResource`) and Symfony `#[Route]`. Each
  is gated on its framework, and Java's JAX-RS on a `javax.ws.rs` /
  `jakarta.ws.rs` import so Retrofit's client `@GET` stays out. On eleven
  RealWorld-style sample applications every route of the spec is found
  (`docs/benchmark-polyglot.md`). `config/routes.rb` and `routes/*.php` are
  read even when the graph holds no symbol of them.
- `scope` on `graphlore_routes`, `graphlore_package_apis`, `graphlore_layers`
  and `graphlore_tests_for`, the four tools that read the whole graph without
  one. Their results carry the `scope` payload and say what was left out.
- `tests/fixtures/polyglot/`: five languages and the graph graphify 0.9 built of
  them, so the suite runs on `file_type`, `community_name`, `case_of`,
  closures, a Rust test module and a JS barrel — what the 5-node fixture
  cannot show.
- `benchmarks/polyglot.py`: runs all 33 tools, the resources and the prompts on
  pinned commits of real repositories in Python, JavaScript, TypeScript/TSX, Go,
  Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Ruby and PHP, and reports what
  breaks an invariant (parseable JSON on every path, a cut that says what it
  left out, a `file:line` that exists), what two tools disagree on, and where a
  language gets an empty answer its source contradicts. `--baseline` marks
  findings new or gone against an earlier run. Results:
  `benchmarks/results-polyglot.json`; report: `docs/benchmark-polyglot.md`.
- Every result that reports `truncated` now carries a `hint` naming the
  argument to raise or the filter to add. Fifteen tools reported the cut and a
  count but not how to get the rest: `god_nodes`, `surprises`, `search`,
  `subgraph`, `tests_for`, `locate`, `duplication_scan`, `freshness`, `diff`,
  `cycles`, `unreferenced`, `layers`, `same_names`, `package_apis`, `routes`.
- `graphlore_package_apis` and `graphlore_routes` count the source files they
  have no extractor for in `not_read` (`{".rs": 48}`) and say so in the text.
  A Rust or Kotlin project used to get "no routes" in the same words as a
  project that has none.
- `graphlore_communities` shows each community's `name`: the one set with
  `graphlore_set_labels` / `graphlore_label_communities`, else the one graphify
  wrote on the nodes. A name written by `graphlore_set_labels` was visible in
  no tool. Members that share a label are qualified.
- `graphlore_communities(limit=30, members=15)`: the largest communities, each
  with its most connected members first, `size` and `members_left_out`. It
  used to list every member of every community — a 280 KB reply on guzzle, in
  the second step of the onboarding flow.
- `server.json`, the manifest for the official MCP Registry
  (`io.github.yasinyaman/graphlore`, PyPI package, stdio, run as
  `uvx --with graphifyy graphlore`), and the `mcp-name:` line in the README
  that the registry uses to verify PyPI ownership. Publishing there is a manual
  step after the first PyPI release; `CONTRIBUTING.md` has the commands.

### Changed
- `graphlore_routes` leaves test code out by default: got's tests register
  1,354 routes on throwaway servers. `scope="+tests"` brings them back.
- An Express-style row is labelled by the framework the file imports
  (`fastify`, `koa`, `restify`, `polka`), not always `express`.
- `graphlore_build()` with no LLM key on a folder that holds docs: graphify 0.9
  refuses it, so it runs again with `--code-only` and says the docs were
  skipped. A build with `mode=` asked for is not turned into another one.
- `graphlore_explain` and `graphlore_path` resolve a name before graphify sees
  it, and pass graphify the node id. graphify refuses a name several nodes
  share ("Ambiguous: matches 3 nodes"); the reply now says which node was used.
- Dependencies upgraded. `uv.lock` moves to graphifyy 0.9.109 (from 0.8.39:
  what plugin users already got through `uvx`), mcp 2.3, semble 0.7,
  tree-sitter-language-pack 1.25, ruff 0.17, mypy 2.4 and pytest 9.2. The
  floors in `pyproject.toml` are now the oldest versions the suite passes with
  on Python 3.10: `tree-sitter>=0.23` and `tree-sitter-language-pack>=1.11`
  (1.9 and 1.10 return a `Language` that `tree_sitter.Parser` rejects, so the
  span engine had no grammar with them), and `mypy>=1.12` for development (1.11
  crashes on mcp's sources). CI uses `actions/checkout@v7`,
  `actions/setup-python@v7`, `actions/upload-artifact@v7` and
  `actions/download-artifact@v8` (node 24).
- The README is a short landing page: what graphlore is, quick start, the 12
  `lean` tools, the benchmark headline and an FAQ. The detail moved, unchanged,
  to `docs/tools.md` (all 33 tools, resources, prompts), `docs/concepts.md`,
  `docs/configuration.md` (toolsets, transports, the network table,
  environment variables) and `docs/benchmark.md`. README links into the
  repository are absolute, so they work on PyPI.
- Package and plugin metadata describe what the server is for and carry more
  search keywords (`mcp-server`, `model-context-protocol`, `code-navigation`,
  `impact-analysis`, ...); `pyproject.toml` gains Documentation and Changelog
  URLs.
- The server instructions, the docs and the shipped skill no longer name a
  `hidden_links` key (removed in 0.5.1), and the docs describe the fields
  0.5.0 added (`not_fetched`, `did_you_mean`, `complete` / `beyond_hops`,
  `vendored_dirs`).
- `CONTRIBUTING.md` no longer recommends `ruff format .` (CI does not check
  formatting and most files are not ruff-formatted), and its project layout
  lists every module.

### Fixed
- **Express routes in a file that imports no framework.** An Express app's
  `config/routes.js` (`module.exports = function (app) { app.get(...) }`)
  imports nothing, so none of express-mongoose-demo's 26 routes were found;
  all are now. Without a framework import in the file, a call counts when its
  receiver is named like a router or is a parameter of the function around it,
  it has a handler argument, and some file of the project imports a server
  framework — `axios.get('/x', {…})` still does not. `router.route('/x')
  .get(h).post(h2)` chains are read too.
- **Rust tests inside a source file are tests.** `#[cfg(test)] mod tests` and
  `#[test]` functions (26 of ureq's 48 source files hold them) were production
  code to `tests_for`, the production-before-tests order, `-tests` scopes and
  `unreferenced`'s `count_test_references`. A Rust `mod { … }` is a span, so a
  test's qualname reads `tests.it_works`.
- PHP imports are module-level instead of `unclassified` (a `use` cannot be
  deferred).
- Spans for a JS function wrapped in one call (`exports.create = async(function*
  …)`, `const f = wrap(async () => …)`; 44 definitions in
  express-mongoose-demo), for anonymous Java and Kotlin classes
  (`new Callback<Void>() { … }`, `object : Callback<T> { … }`) and Ruby
  `Request = Struct.new(…) do … end`. The skeleton of a C
  `typedef struct { … } name;` shows the closing line with the name.
- `graphlore_unreferenced` does not read a doc comment's `@default` / `@example`
  as a decorator, and counts a class as used when one of its members is called
  elsewhere: C# extension classes are never named at the call site
  (`client.BuildUri(…)`), so RestSharp's were all listed. The row says
  `used_through_members`.
- **Kotlin had no spans at all.** Its grammar names a declaration through an
  identifier child, not a `name` field, so none of kotlinpoet's 2,009
  definitions had a span: `graphlore_fetch` returned one line,
  `graphlore_skeleton` nothing, and annotations were never read. Now 1,997 do.
- **A type written in a signature is no longer a definition.** The span engine
  took any node with a hint word in its type and a `name` field: TypeScript
  `React.ComponentProps`, Rust `io::Result` and struct literals, Swift type
  annotations, Scala constructor parameters, a C `struct config cfg` parameter
  — and every PHP `$parameter`, because `simple_parameter` contains "impl".
  Such a one-line span sat on the definition's own line and won:
  `graphlore_fetch` returned the first line of a function, and qualified names
  read `FormMessage.ComponentProps`.
- A `.h` file that reads as C++ is parsed as C++ (the C grammar turned cpr's
  classes into errors: 146 of 558 definitions had a span, now 532). Function
  prototypes, methods declared in their class, typedefs, operators and
  functions behind a reference return type are spans.
- C# and Java `record`s, Swift `protocol`s, `init` and `deinit`, and a JS
  function assigned to a member (`res.send = function () {…}`,
  `exports.x = …`) are spans.
- `graphlore_fetch`, `graphlore_skeleton(node=)` and qualified labels take the
  span that starts on the node's line under the node's name, not the innermost
  one: `enum Color { RED, GREEN }` no longer answers a request for `Color` with
  one of its cases.
- `graphlore_skeleton` headers run on to the line that names the symbol. A
  Java, C# or Swift declaration starts at its first annotation, and GNU-style C
  puts the return type on a line of its own, so the header of 925 symbols was
  `@Override`, `[Fact]` or `static int` and nothing else.
- Names scoped with `::` (Ruby `Faraday::Utils::Headers`, C++ and PHP
  `Session::SetOption()`) resolve by their last part and as a qualified key,
  instead of falling through to the substring match.
- **JS/TS imports through a barrel are no longer rejected.** graphify links
  `import { X } from './dto'` to the file that defines `X`; the import check
  compared the specifier with that file, found `dto/index.ts` instead and
  dropped the edge as contradicted by the source — 113 of got's 797 import
  edges, so `graphlore_cycles`, `graphlore_layers` and `graphlore_surprises`
  worked on a thinner graph. Re-exports (`export … from`,
  `module.exports = require(…)`) are followed through up to three files; a file
  the entry merely imports is still not accepted. `require('../..')` names the
  root `index.js`.
- A C, C++ or Objective-C `#include` is module-level, like the imports of the
  other compiled languages; those edges were `unclassified`.
- Test files of more layouts are recognised: Gradle source sets (`jvmTest`,
  `androidTest`), .NET test projects (`Foo.Tests/`), `FooTests.java` /
  `FooTest.php` / `FooTestCase.swift`, Ruby `foo_spec.rb`, `*_tests.cpp`.
  `graphlore_tests_for` and the production-before-tests order depend on it.
- `graphlore_unreferenced` no longer lists collected tests as dead code: Go
  `Benchmark…` / `Example…` / `Fuzz…` functions, googletest `TEST`, `…Tests` /
  `…TestCase` classes and `setUp` / `tearDown` fixtures are tests by name, and annotations that start the declaration itself
  (Java and Kotlin `@Test`, C# `[Fact]`) or use another syntax (Rust
  `#[test]`) are read as decorators. Compiler and lint attributes
  (`#[must_use]`, `#[derive]`, `[Obsolete]`, `@JvmStatic`) do not count as a
  registration.
- `graphlore_unreferenced` no longer examines what no caller names: anonymous
  functions (`{closure#3}` — 866 of guzzle's roughly 900 rows), operators (C++
  `operator[]`, Ruby `[]=`), constructors and destructors, and PHP magic
  methods (`__invoke`). Ruby `success?` / `body=` are looked up in the text as
  `success` / `body`, the way a caller writes them.
- A node with no span of its own (a closure, a member declared without a body)
  is qualified by where it lives: `sendDigest.{closure#3}()`. Three closures in
  one method were all rendered `AuthMiddleware.sendDigest()`, the name of the
  method around them.
- Relations graphify 0.9 writes are known: `defines` (a class's field) and
  `case_of` (an enum's case) are containment, so they form no cycle and are no
  reference; `dispatches_to`, `references_constant` and `uses_static_prop` are
  code relations, so `graphlore_impact(relations="code")` walks them.
- `.mts` / `.cts` files count as source in every table; `.swift`, `.kts` and
  `.hxx` have a language family, so `graphlore_same_names` no longer compares a
  Swift class with every other language that had none.
- `type` in `graphlore_god_nodes`, `graphlore_search` and
  `graphlore_node_details` falls back to graphify 0.9's `file_type`; it was
  `""` on every row.
- `graphlore_neighbors` listed a self-loop twice.
- A negative `limit` lists nothing. It was used as a slice end, so `limit=-1`
  meant "all rows but the last": 471 KB from `graphlore_search` on guzzle
  (`search`, `surprises`, `validate`, `package_apis`, `routes`,
  `duplication_scan`).
- `graphlore_locate(top_k=0)` raised `ValueError` from the semantic backend
  instead of answering.
- When semble is installed but cannot build its index (the embedding model
  could not be downloaded), the error says so and quotes the reason, instead
  of "needs the optional 'semble' extra".
- `graphlore_build` adds `code_only=True` to the error graphify 0.9 gives for a
  default build of a repository with docs and no LLM key, which names only the
  CLI flag.

## [0.5.1] - 2026-11-14

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
