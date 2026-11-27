# How graphlore works

What graphlore adds on top of the graph it reads, how it checks what it reads,
and how it keeps answers honest when the code moves.

Back to the [README](../README.md) · [Tool reference](tools.md) ·
[Configuration](configuration.md) · [Benchmark](benchmark.md)

## What graphlore adds to the graph it reads

This section is about graphs the graphify CLI built (before 0.9.0, which removed
it as an extractor; such a graph is still read); a native graph has these edges
already (see below).

graphify's local extractor links `from pkg import helper; helper()` to the
function, but not `from pkg import mod; mod.helper()` — and often not the import
of `mod` either. Test files are written almost entirely in the second form, so a
function could come back with no callers and no tests while four tests call it.

graphlore closes that gap at read time: for `mod.name(...)` where `mod` is bound
by an import to a first-party module that defines `name` (or re-exports it), it
adds the call edge, marked `resolved_by: "graphlore"`; the missing module import
is added the same way. It fills what each language's extractor is seen to leave
out, and nothing more:

| Language | Added by graphlore |
|---|---|
| Python | every call through a module object, and the module import |
| JS / TS | calls through a default import (`import api from './api'`) and through a dynamically imported module (`const m = await import(...)`) — graphify already links namespace imports and `require` |
| Go, Java | nothing — graphify links package-selector and static calls itself |
 `graphlore_impact`, `graphlore_neighbors`,
`graphlore_subgraph`, `graphlore_tests_for`, `graphlore_unreferenced`,
`graphlore_cycles` and `graphlore_layers` read these edges; `graphlore_validate`
says how many there are. graph.json is never modified.

Python is read with the stdlib `ast`, JS/TS with the `[treesitter]` extra. It
costs one parse per Python and JS/TS file of the graph, once per process (about
a second per hundred files); `GRAPHLORE_RESOLVE=0` turns it off.
A call through an object, a callback or dynamic dispatch stays invisible, which
is why an empty answer from these tools says "no edge was indexed", not "nothing
calls this".

## A compiler's answer: SCIP indexes

A [SCIP](https://github.com/sourcegraph/scip) index records, for every name in
a repository, the symbol a compiler-grade indexer binds it to: `scip-python`,
`scip-typescript`, `scip-java`, `rust-analyzer scip`, `scip-go`, `scip-clang`,
`scip-dotnet` and others make one. When the project has one, graphlore checks
its graph against it at read time. Nothing is installed or run for it: the
index is a local file, read with graphlore's own decoder (no `protobuf`
package), and nothing reaches the network.

```bash
npx @sourcegraph/scip-python index . --project-name myproject   # writes index.scip
```

Each use the index records (not a definition, not an import) is given to the
graph node that encloses it, and its symbol to the node its definition is (by
file, name and nearest line). Then, in the edges the navigation tools walk:

- an edge the graph has between the two is **confirmed**: `confidence:
  EXTRACTED`, `scip: "confirmed"`, so `graphlore_impact(strict=True)` walks it;
- a pair the graph lacks is **added**: `calls` when a `(` follows the name,
  else `references`, with `resolved_by: "scip"`;
- a call or reference the graph has at a site where the index names the same
  name as something else (another definition, or a library's symbol) is
  **contradicted**: `confidence: AMBIGUOUS`, `scip: "contradicted"`. It is
  kept, not removed: an indexer has blind spots of its own (a name defined
  under a condition).

The replies say so: `resolved_edges.precision` holds the index files, the
indexer, and how many edges were confirmed, added and contradicted, and a row
carries `scip` where the index said something of its edge. graph.json is never
modified. `GRAPHLORE_SCIP` chooses the files: `auto` (the default) reads
`index.scip` at the project root or in the output directory, `off` none, or a
comma-separated list of paths inside the project. An index made in another
directory (a CI container's `/src`) is read by the project's own paths.

An index is checked before it is believed. A use whose source text is not its
symbol's name is left out and counted (`precision.mismatched`): scip-python
binds a name a package re-exports to a neighbour of it (`httpx.Client` to
`AsyncClient`), and an alias (`import numpy as np`) names its module by another
name. A symbol is matched to its definition by its descriptors too, so the
installed copy of a package a test imports (`click 8.5.0`, module
`click.utils`) finds the project's own definition (`src/click/utils.py`, which
the indexer names `src.click.utils`).

On httpx, scip-python's index (60 files) confirms 3,697 edges of the native
graph, adds 333 and contradicts 15, in under a second; 1,002 uses are left out
as mismatched. The 15 are the indexer's own blind spots (a `main` defined under
`try`, `.set()` taken for the builtin `set`), which is why a contradicted edge
is kept and marked, never removed. `benchmarks/oracle_scip.py` compares a whole
graph with an index the way the compiler oracles do: on httpx 99.1% of the
edges the index has an answer for go where it says, and 92.8% of the uses it
binds to a definition of the graph have an edge (click 97.7% / 91.3%,
fastapi-template 100% / 92.4%).

### Go and C#

```bash
go install github.com/scip-code/scip-go/cmd/scip-go@latest   # Go 1.26 builds it; any go on PATH loads the packages
scip-go --output index.scip                                  # at a module's root
dotnet tool install --global scip-dotnet
scip-dotnet index --output index.scip                        # at the solution's directory (it runs dotnet restore)
```

scip-go indexes one module: in a repository of several, run it in each
module's directory and list the files, `GRAPHLORE_SCIP=index.scip,grpc/index.scip,…`
(each index is read by its own root). scip-dotnet loads the projects the
solution names and needs the SDK they target: a project it cannot load (a
`net48` target on Linux, a package of a newer runtime) is left out of the
index, and a source written in a newer C# than the SDK's compiler is bound
by guesswork, so its answers are not to be believed there.

The two indexers write symbols differently from scip-python, and the reader
takes both:

- a Go package is named by its import path (`` `net/http`/ ``): its name is
  the last segment, the one before a major version
  (`github.com/go-chi/chi/v5` is `chi`), and an import writes the whole path;
- a C# constructor is `` `.ctor` ``: it is named as its type, as the source
  writes it and the graph names it, an attribute without its `Attribute`
  suffix; one the source does not declare (the implicit one) is its type, and
  a `new T()` the index names only as `T` does not contradict an edge to T's
  constructor;
- scip-dotnet gives namesakes one symbol (`Maybe<T>` and `Maybe` are both
  `` Maybe# ``; a namespace is written by its last part): a symbol defined
  more than once is every one of its definitions, an edge to any of them is
  confirmed, and a use of one adds no edge, since which one is not said;
- a definition's own signature naming what holds it (Go's receiver, `func
  (mx *Mux) Route(`) adds nothing: the containment edge already says it;
- a byte-order mark is no character of the first line.

Neither indexer marks a use as a read or a write, so an added edge takes its
relation from the source after the name: `calls` after a `(`; to a field,
property or variable, `writes` after an assignment (`=`, `+=`, `++`) or as a
Go composite literal's key, else `reads`; else `references`.

What the two give, on the audit repositories (the graph of engine 0.5.80):

| Repository | Indexer | Confirmed | Added | Contradicted |
|---|---|---:|---:|---:|
| chi | scip-go | 1,547 | 96 | 0 |
| cobra | scip-go | 3,031 | 730 | 0 |
| resty | scip-go | 8,617 | 589 | 2 |
| gin-examples (8 modules) | scip-go | 251 | 27 | 1 |
| commandline | scip-dotnet | 4,083 | 1,546 | 121 |
| restsharp | scip-dotnet | 5,797 | 1,303 | 111 |
| aspnetcore-realworld | scip-dotnet | 1,125 | 57 | 0 |
| todoapi | scip-dotnet | 252 | 27 | 10 |

`oracle_scip.py` over the four Go repositories: 99.9% of the edges scip-go
has an answer for go where it says, 89.9% of the uses it binds to a definition
of the graph have an edge (`go/types` itself, `oracle_go.py`: 99.9% / 86.0%,
which counts a method's receiver as a use). Most
of what scip-go adds are keys of composite literals (`Command{Use: "c"}`: the
field, which the engine does not record) and fields read through a named
slice type; resty's two contradicted edges are the engine's errors
(`url.URL.Query` taken for `Request.Query`). Over the four C# repositories
97.5% / 84.6% (Roslyn on the sources alone, `oracle_csharp.py`, 98.3% on
commandline and restsharp); of the 232 edges scip-dotnet contradicts there,
122 Roslyn calls wrong too, and the rest are mostly overload choices the
engine made, plus sources of C# 14 (`extension` blocks) the .NET 8 compiler
reads wrong. The C# indexes here are partial: .NET 8 was the SDK at hand, the
repositories target 9 and 10.

`benchmarks/conformance.py --scip` reads each conformance case in precise
mode (it runs the indexer on the case's project), which says which of the
rules the engine does not follow a compiler's index settles. Of the 715 Go
and C# cases, the engine alone holds 600; in precise mode 675 hold (Go 314
of 318, C# 361 of 397): 77 of the 115 the engine leaves open, most of them
the ones that need types (generic instantiation, a type parameter's core
type, overloads by argument type, delegates, nullable access, patterns).
Two cases the engine holds break, both the indexer's errors: scip-go binds
the `E` of `[S ~[]E, E any]` to the package's type `E`, not to the type
parameter declared after it, and scip-dotnet takes `Take<T>(T)` where C#
picks the more specific `Take<T>(List<T>)` (Roslyn agrees with the engine);
the engine's edge is kept, marked contradicted. `benchmarks/results-conformance-scip.json`
holds the state.

### TypeScript and JavaScript

```bash
npm install -g @sourcegraph/scip-typescript
scip-typescript index --output index.scip                   # where tsconfig.json is
scip-typescript index --infer-tsconfig --output index.scip  # a JavaScript project with none
```

It needs no `node_modules` for the project's own names (a library's are then
unbound, which only leaves them out), but a `tsconfig.json` that `extends` a
package must find it. The reader takes its forms:

- an accessor is `` `<get>body` `` / `` `<set>body` ``, written `body`, and
  scip-typescript writes both at every use of the property: occurrences of one
  name at one range are one use, of whichever their symbols are;
- a constructor is `` `<constructor>` ``: written as its class (`new
  Options()`), it is the graph's `.constructor()` node, and a use of the class
  too;
- a member read by a string (`x["_zod"]`) and a module named by what an
  import writes (`from "./util.js"`) are their names.

A namespace import (`import * as z from "zod"`) names the module by an alias;
its uses are left out as mismatched (19,303 on zod), as an aliased import is
in Python.

| Repository | Confirmed | Added | Contradicted |
|---|---:|---:|---:|
| zod | 14,215 | 3,511 | 301 |
| got | 3,177 | 597 | 161 |
| commander | 515 | 117 | 0 |
| nestjs-realworld | 242 | 59 | 0 |
| express | 174 | 104 | 0 |
| express-mongoose-demo | 41 | 8 | 1 |

Over the six, 95.9% of the edges the index has an answer for go where it says
(`oracle_scip.py`). The contradictions read are the engine's: on zod a call
through a namespace import (`z.array(…)`) linked to a method of the same name
(`ZodType.array`) where it is the module's function, on got `new Error(…)`
linked to a type alias `Error` of the project where it is the built-in. The
89 TypeScript and JavaScript conformance cases all hold in precise mode too.

## Import edges, checked against the source

`graphlore_cycles`, `graphlore_layers` and `graphlore_surprises` read each import
back from the importing file before they trust it — which file it can mean, and
when it runs:

| Language | How | What is told apart |
|---|---|---|
| Python | stdlib `ast` | module-level · inside a function (deferred) · under `if TYPE_CHECKING:` |
| JS / TS | `[treesitter]` | top-level `import` / `export … from` / `require` · `import()` or `require` inside a function (deferred) · `import type` |
| Go, Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Objective-C | — | imports are resolved at compile time (a `#include` by the preprocessor): always module-level |
| PHP | — | a `use` only aliases a name (the file loads on first use, by autoload): always module-level |
| Ruby | — | graphify records no file-level import edge for `require`: nothing to read |
| anything else | — | kept as it is and counted as module-level; the result says so |

An edge the source contradicts is dropped and listed: Python's `import yaml`
linked to a first-party `yaml.py`, or a bare JS specifier (`'yaml'`) linked to
`lib/yaml.js`. A tsconfig path alias cannot be told from a scoped package
without the config, so those edges are kept.

A graph the native engine built (the default) needs none of this:
it resolves each import by path when it reads the statement, and records when it
runs on the edge (`runs`), so the check takes that and rejects nothing. The calls
and imports through a module object that graphlore adds to graphify's graphs
(above) are in such a graph already, and nothing is added to it.

An import through a re-exporting entry file is kept too. graphify links
`import { CreateDto } from './dto'` to the file that defines `CreateDto`, not to
`dto/index.ts`; the statement still loads that file, so the edge holds. Only
re-exports are followed (`export … from`, `module.exports = require(…)`),
through at most three files: a file the entry merely imports is not what the
importer asked for.

## Analysis scope

`graphlore_god_nodes`, `graphlore_validate`, `graphlore_cycles`,
`graphlore_surprises`, `graphlore_duplication_scan`, `graphlore_unreferenced` and
`graphlore_same_names` take one `scope` string. By default it leaves out what is
not the project's own code, and each result says how many nodes that was:

- **vendored and minified files** — under `vendor/`, `node_modules/`,
  `third_party/`; named `*.min.js` or with a build hash (`decorate-BgofJUJ5.js`);
  or JS/CSS whose lines are far too long to be hand-written;
- **external nodes** — imported packages (`typing`, `pytest`) and names graphify
  could not resolve to a file.

| Token | Effect |
|---|---|
| `src/pkg`, `**/*.py` | keep only files under this prefix / matching this glob |
| `!docs` | leave these files out |
| `-tests` / `+tests` | leave out / include test code |
| `+external`, `+vendored` | bring those back |
| `all` | the whole graph |

What was left out comes back in every result as `scope.left_out`: node counts,
`vendored_file_count`, three example paths (`vendored_files`) and the directories
holding most of them (`vendored_dirs`), plus a one-line note in the text.
`graphlore_validate` lists every vendored file.

`graphlore_impact`, `graphlore_subgraph` and `graphlore_neighbors` take the same
`scope` string but leave nothing out by default: they walk out from a node you
named, and `scope="-tests"` or `scope="src"` is how to narrow the walk.

Tokens are separated by commas or spaces: `scope="src/pkg -tests"`. To keep
vendored files out of the graph itself, list them in `.graphifyignore` —
`graphlore_build` names them when it finds any.

## Keeping the graph fresh

The analysis tools surface staleness for you: `graphlore_overview` and
`graphlore_subgraph` carry a lightweight `graph_age` ("built 3 commits ago"), and
`graphlore_freshness` gives a full `recommended_action` (fresh / update / rebuild).
The answers themselves say which of *their* files changed: when the graph records
the commit it was built from, `graphlore_fetch` flags each entry
(`file_changed_since_build`, plus `span_mismatch` when the graph's line now lands
in a different symbol), and `graphlore_locate`, `graphlore_impact` and
`graphlore_subgraph` carry a `build_drift` block listing the changed files the
answer rests on. The comparison is the build commit against the working tree
(committed, staged and unstaged changes and untracked files, two git calls per
answer); a file that was already dirty when the graph was built reads as
changed. Without a recorded build commit or git, `build_drift.known` is false and
says why. To stop thinking about it, regenerate on every commit with a git **post-commit
hook** — the recommended first-class auto-update flow:

```sh
# .git/hooks/post-commit   (then: chmod +x .git/hooks/post-commit)
#!/bin/sh
# incremental (only changed files are parsed), backgrounded so the commit returns immediately
python -m graphlore_engine build . --incremental --commit "$(git rev-parse HEAD)" >/dev/null 2>&1 &
```

The native update drops the nodes of deleted files itself. On a graph graphify
built (`graphify . --update --no-viz`), an incremental update only re-extracts
changed files — it can't *drop* nodes for deleted/renamed code on its own.
`graphlore_prune` closes that gap: it surgically
removes the phantom nodes (and their edges) for source files that are gone from the
working tree, so after a delete/rename you can `graphlore_prune` (preview with
`dry_run=True`) + `graphlore_build(update=True)` instead of a full rebuild.
`graphlore_freshness` knows about this — it only steers to a rebuild while phantom
nodes for the removed files still linger, and reports them in `phantom_files`. An
agent can also just call `graphlore_build(update=True)` when `graph_age` /
`graphlore_freshness` says the graph drifted.

There's also an opt-in filesystem watcher (`GRAPHLORE_WATCH=1`, the `[watch]`
extra): it re-syncs the graph on structural source changes, ignores cosmetic
edits and non-source churn (VCS internals, virtualenvs, its own output), and
debounces via `GRAPHLORE_WATCH_DEBOUNCE`.

## Search bridge (built-in index or semble)

`graphlore_locate` joins code search to the graph in one call. The graph gives
**structure** (how code connects); search gives **retrieval** (which code is
relevant to a question) — they're complementary.

`graphlore_locate("how does retry backoff work")`:
1. the search finds the most relevant code and resolves the top hit to its
   enclosing graph node (better than label matching).
2. returns the token-budgeted subgraph around it (**structure**).
3. runs `find_related` on the top hit and cross-checks: a cousin that is similar
   but **not** within the seed's structural neighborhood is flagged as a
   `hidden_link` (with its hop distance) — a duplication / missing-abstraction /
   implicit-coupling candidate that neither tool surfaces alone.

**Which search answers** is `GRAPHLORE_SEMANTIC_BACKEND`, and `backend` in every
reply says what did:

| Setting | Backend |
|---|---|
| unset / `auto` (default), `builtin` | the built-in index, whether or not semble is installed |
| `semble` | [semble](https://github.com/MinishLab/semble) only; without the extra, `graphlore_locate` returns an install hint |
| `module.path:Factory` | your own backend implementing `search` / `find_related`; a failure is an error, never answered by another backend |

### The built-in index

graphlore's own index (`search.py`) needs no extra, so `graphlore_locate` works
in a default install. It is built on what graphlore already has and a
general-purpose search does not:

- **One chunk per definition.** Chunks are the span engine's functions, methods
  and classes (a class holds only its own lines, not its methods'), so every hit
  joins to its graph node exactly. Module-level code between definitions is a
  low-weight `module` chunk; a file the span engine cannot read (another language
  without `[treesitter]`) is cut into 40-line windows, counted in
  `window_chunked_files`.
- **Identifier-aware BM25F.** `getUserName`, `get_user_name` and `GetUserName`
  share a term, and so do their parts (`user`, `name`); the definition's name and
  qualified name, the file path and the body are scored as separate fields, with
  the name weighted highest. A query that is a symbol (`Client._send`) gets an
  exact-name boost; a question loses its stopwords.
- **A graph overlay.** A node's docstring and the names of what it calls and what
  calls it are a second, small index, so a definition can be found by the words
  its neighbours use.
- **A prior on where code lives.** Test code, examples and type stubs rank below
  the implementation unless the query asks for tests, and each further chunk of
  one file counts for less, so a question with two homes shows both.
- **Static embeddings, optional.** With the `[embed]` extra
  (`pip install "graphlore[embed]"`), each chunk also gets a
  [model2vec](https://github.com/MinishLab/model2vec) static embedding
  (`minishlab/potion-code-16M-v2` by default, `GRAPHLORE_EMBED_MODEL`), and the
  two rankings are fused by weighted reciprocal rank: names lean on BM25,
  questions on the embedding. `mode` in the reply is `hybrid`; without the extra
  it is `bm25`, and `dense_unavailable` says why. A BM25 index matches the code's
  own words: "where are failed requests retried" finds `retry`, not code that
  says `attempt` and `backoff`; that is what the embedding adds.

The index is built on the first call and kept for the session, one per project:
every later call stats the project's files and re-reads only those whose
modification time or size changed. Embedding vectors are kept on disk by text
hash under `GRAPHLORE_CACHE_DIR` (default `~/.cache/graphlore`, never inside the
project), so a restart or an edit re-embeds only the definitions whose text
changed. Hidden directories, dependency and build trees, vendored or minified
files, files over 1 MB and binaries are skipped, and `backend.skipped` counts
them; past 20,000 files or 200,000 chunks the index stops and says so.

How it compares with semble, on semble's own query set (NDCG@10, index time,
query latency): [benchmark](benchmark.md#search-quality-built-in-index-and-semble).

The tokenizer and the BM25F loop sit behind a small kernel interface with a
pure-Python reference. With the native engine installed (graphlore-engine
0.5.80 or later), its port of the two (`engine/src/search.rs`) answers instead;
`tests/fixtures/search_golden.json` holds the terms and bit-exact scores both
must give, and on the 45 audit repositories the two agree on every file. The
port tokenizes about 2.5× and scores about 1.4× faster, which a whole index
build or query does not show: reading definitions dominates the build, and a
query takes 0.3–2.5 ms either way. Spans stay with `spans.py`'s reader, which
on those repositories is as fast as the engine's.

### semble

With the `[semble]` extra and `GRAPHLORE_SEMANTIC_BACKEND=semble`, semble
answers: static embeddings plus BM25 and its own reranking. It is not the
default (until graphlore 0.7 it was, when installed): on semble's own query set
it ranks a little better (NDCG@10 0.852 against the built-in index's 0.814,
[benchmark](benchmark.md#search-quality-built-in-index-and-semble)), but its
hits join to the graph through a chunk of lines rather than a definition, and
its first call cannot answer without the network. **The first call downloads a model.** semble, like
the built-in dense tier, fetches `minishlab/potion-code-16M-v2` from the Hugging
Face Hub on first use and caches it; later calls need no network. To fetch it
ahead of time:

```bash
python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-code-16M-v2')"
```

For a machine without network access, copy `~/.cache/huggingface` over from one
that has run the command, or point `SEMBLE_MODEL_NAME` (semble) /
`GRAPHLORE_EMBED_MODEL` (built-in) at a local copy of the model. With
`GRAPHLORE_OFFLINE=1` and no cached model, the built-in index answers with BM25
alone. graphlore also pairs well with running semble's own MCP server
alongside it.

The chunk→node join and the freshness cosmetic-vs-structural check work
**across languages**: Python uses the stdlib `ast` (no extra deps), and every
other language (JS/TS, Go, Rust, Java, Kotlin, Scala, C#, Swift, Ruby, PHP,
C/C++, …) is handled by an optional **tree-sitter** backend —
`pip install "graphlore[treesitter]"`; when the native engine is installed, its
compiled-in grammars give the spans instead. Without either, non-Python files
fall back to nearest-line matching. A `.h` file that reads as
C++ (a `class`, a `namespace`, a `template<`) is parsed as C++.
[What was measured per language](benchmark-polyglot.md), on real repositories.

## Shared state and resources

Two functions can affect each other without either calling the other: one
writes a module variable, a field, an environment variable, a file or a table
row, and the other reads it. A call graph does not show that. On a native
graph each such piece of state is a node — a variable, field or constant
(`kind: variable | property | constant`), or a resource outside the program
(`type: resource`, `resource: env | file | table | event | key`) — and the
definitions that touch it are linked to it by `reads` / `writes`. Writing is
read from the syntax (an assignment, `+=`, `x[k] = v`, `del`, `++`) and is
`EXTRACTED`; a mutating method on a collection (`cache.clear()`) or a call
through an ORM (`User.objects.create()`) is `INFERRED`, since only a name says
so. A resource becomes a node only when two definitions touch it.

The tools use it in four places:
- `graphlore_neighbors` and `graphlore_subgraph` show the edges as they are:
  a variable's neighbours are its readers and writers.
- `graphlore_impact` leaves them out by default, so a blast radius stays a
  call and import graph, and counts what it passed by in
  `state_nodes_skipped`; `shared_state=True` follows them from a writer through
  what it writes to its readers.
- `graphlore_surprises` lists, beside the edges, the state written in one file
  and read or written in another (`shared_state`), strongest first. Each row's
  `pairs` say, per writer and other definition touching the state, whether a
  call path joins them (`call_path`: either way, over calls, dispatch and HTTP,
  within 6 hops) and whether their files change together (`co_change`, below).
  A hidden pair, one with no call path, is what a call graph cannot show; it
  raises the score, and more when the two files also change together. The
  native engine works the pairs out when it builds and writes them on the
  state node (`coupling`); on any other graph graphlore looks for the paths
  itself, with the same result.
- `graphlore_node_details` says what a definition reads and writes and which
  other definitions share that state with it.

A hidden pair is also an edge of a native graph: `couples_with`, from the
definition that sees the state to the one that writes it, with the state in
`via` (at most 10 per piece of state). `graphlore_neighbors` and
`graphlore_subgraph` show it; it is never counted as a dependency of its own
(no reference, no surprise, no cycle), and `graphlore_impact` follows it only
with `shared_state=True` or `relations="state"`. Test code is left out of the
pairs: test files, and what the engine marks as test code inside a source
file (a Rust `#[cfg(test)]` module).

`graphlore_cycles` ignores reads and writes: two functions sharing a counter
do not depend on each other in a cycle.

## Files that change together

Two files that keep changing in the same commits are coupled whether or not the
code says so: a websocket client and its server, a model and the schema it
maps to, two implementations of one interface kept in step. graphlore reads
this from the project's git history when a tool asks, with one local
`git log` (no network): the last 1000 commits that are not merges, renames
followed to the file's current name, and no commit of more than 30 files (a
reformat or a mass rename says nothing about which files belong together).
The measure is code-maat's: a pair's `degree` is the commits it shares over the
mean of the two files' commits, and a pair counts from 3 shared commits and a
degree of 0.3. The result is cached on `HEAD`; it works on graphify and native
graphs alike, and nothing is written to `graph.json`.

- `graphlore_surprises` lists in `co_change` the source files that change
  together with no edge of the graph between them (tests left out: a test
  changes with the code it tests by design), and raises the score of a
  shared-state pair whose files change together.
- `graphlore_node_details` names the files a node's file changes with most,
  and whether the graph joins them (`linked`).

`history` in the reply says how much was read. A shallow clone (CI checkouts
often are: `git fetch --deepen=1000` fetches more) or fewer than 10 commits is
named as such, so an empty list is never taken for "nothing changes together".

## HTTP routes and requests

A browser's `fetch('/api/v1/users/42')` and the handler that serves it are in
one repository but joined by nothing a parser sees. On a native graph every
route is a node (`type: route`, with its `method`, its full `pattern` and a
normalized `key` such as `GET /api/v1/users/{}`), a `handles` edge leads from
it to its handler, and an `http_calls` edge leads to it from each definition
whose request reaches it. The full pattern is joined across files: a FastAPI
router's prefix and the `include_router` prefix above it, an Express
`app.use('/api', …)`, a gin group passed to a function, Laravel's
`routes/api.php` prefix. Requests are matched segment by segment, parameters of
every syntax (`{id}`, `:id`, `<int:id>`, `${id}`) as one wildcard; an exact
match with a known method is `EXTRACTED`, a value against a parameter or a
guess between candidates `INFERRED`. Requests in test code are counted in the
build report, never linked: a test's client calls the routes it sets up
itself. [What is read, per language](native-engine.md#http-routes-and-requests).

The tools use it in three places:
- `graphlore_routes` lists the route nodes, with each route's handler and the
  code that requests it (`called_by`, `callers`); on a graphify graph it reads
  the source instead, file by file.
- `graphlore_impact` walks both relations like calls, so a handler's
  dependents reach the client code that requests it, across languages;
  `relations="http"` keeps only them.
- `graphlore_node_details` says which routes a handler serves and who requests
  them, which routes a client definition requests, and a route's handlers and
  callers.

## Communities

A community is a group of nodes that belong together: `graphlore_communities`
lists them, `graphlore_skeleton(community=…)` shows one's code, and
`graphlore_surprises` counts an edge between two of them as a reason to look.
graphify finds them with Leiden on the graph as it is. The native engine runs
Leiden too, but on edges weighted by what they say (a containment or a call binds
tighter than a type named in passing, a base class tighter still), by how sure
they are (a guess by name counts half) and by where their ends live (the same
directory binds tighter, other top-level directories and test-to-production
edges looser), plus a weak tie between the files of a directory. Its
communities sit more in one directory and keep tests apart from the code they
test; [native-engine.md](native-engine.md#communities) has the rules and the
measurements. A native rebuild starts from the previous graph's communities:
a one-file change leaves almost every node where it was, the ids stay, and so
do the names saved for them, which are dropped only when their community
changed beyond recognition (the build says how many).

## Naming communities without an API key

The Leiden clustering is keyless, and so is naming what it found.
`graphlore_label_communities` has three ways:

1. **Derived** (`auto`, the default, or `derived`) — no model anywhere. A
   native graph carries the engine's own name on every node
   (`community_name`): the member with the most edges, after the directory
   most members share (`httpx/_transports: HTTPTransport`). On a graphify
   graph graphlore applies the same rule itself; `tests/test_engine.py` holds
   the two to the same names. Free, the same on every call, and it needs no
   session, so it works over stateless HTTP too.
2. **Host-LLM sampling** (`sampling`) — the server asks the *connected client*
   to run the completion via MCP `sampling/createMessage`; **the server holds
   no API key**. All communities are named in one batched request, carried
   over whichever transport the negotiated protocol allows (the legacy
   back-channel, or input-required rounds on MCP 2026-07-28+). The 2026-07-28
   revision deprecates sampling (SEP-2577), so it is asked for, never chosen
   by `auto`; `graphlore_sampling_status` says whether the client supports it.
3. **Placeholders** (`placeholder`) — `Community N`.

graphlore no longer runs graphify's own naming (`method="cli"`, a backend API
key): it is refused.

None of these saves anything. For names a reader would choose, the assistant
in the loop reads `graphlore_communities` and pushes names back via
**`graphlore_set_labels({"0": "Authentication", ...})`** — no key, no
sampling, works in any client. The names persist to
`.graphify_labels.json` and go into `graph.html`.
