# The native engine

`engine/` is graphlore's own extractor: a Rust library, shipped as the Python
package `graphlore-engine`, that walks a project and writes a
graphify-compatible `graph.json`. It exists so that graphlore can stop depending
on the graphify CLI. Today it is experimental and opt-in
(`GRAPHLORE_ENGINE=native`, see [configuration](configuration.md#the-native-engine));
this page says what it does, how it compares with graphify on real code, and
what is left before it can be the default.

## What it extracts

- **Files.** It reads everything under the project root that git would not
  ignore (and `.graphifyignore` / `.graphloreignore`), skipping the output
  directory, `node_modules`, virtualenvs and files over 2 MB. Source files with
  an extension no module reads (`.lua`, `.zig`, `.ex`, …) are counted in the
  build reply, not dropped silently.
- **Definitions in 14 languages.** Python, JavaScript, TypeScript, TSX, Go,
  Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Ruby and PHP, with tree-sitter
  grammars compiled into the wheel: nothing is downloaded and nothing reaches
  the network. Each definition node carries its span (`region_start` with its
  doc comments and decorators, `end_line`, `source_location` as `L13-L15`),
  `kind`, `qualname` and `language`; Rust `#[cfg(test)]` / `#[test]` items are
  marked `test`. Properties (C#, Swift, Kotlin, TS/JS class fields) and
  module-level constants (TS/JS `const`, Rust `const` / `static`) are nodes too,
  with `kind: property` / `constant`.
- **Containment.** `contains` from a file or an enclosing definition, `method`
  from a class-like definition to its callables (including Go receivers, Rust
  `impl` blocks, Swift extensions and C++ `Class::method` defined outside the
  class, when the type is in the same file), `defines` to a type's properties,
  `case_of` to enum members.
- **Imports.** `imports` / `imports_from` from a file to the file its statement
  names, and to each definition it imports by name; `re_exports` for JS/TS
  `export … from`; `dynamic_import` for `import()`. Every import edge carries
  `runs`: `module-level`, `deferred` (inside a function) or `type-checking`
  (`if TYPE_CHECKING:`, `import type`).
- **References.** `calls` (constructions included, JSX components too),
  `indirect_call` (a function or method passed as a value), `inherits` /
  `implements` / `mixes_in` / `embeds`, and `references` for the types a
  signature, a field, a variable or a type argument names, decorators and
  annotations (`context: decorator`) and property reads (`context:
  property_read`). Each carries the `source_file` and `source_location` of its
  site.
- **External names.** A module, package or base the project does not define
  is a node with `type: external`, `source_file: ""` and its `ecosystem`
  (`py`, `npm`, `go`, `jvm`, `dotnet`, `crate`, `php`, `gem`, `swift`, `c`).
- **Documents.** A page node per Markdown / reStructuredText file and a node per
  heading, nested by level (`code_only` skips them).
- **Communities.** Every node has a `community` and a `community_name`, from
  Leiden on edges weighted by kind, confidence and directory ([below](#communities));
  the graph header carries their count, modularity and a signature per
  community, which the next build uses to keep ids and saved names.
- **Incremental updates.** `update=True` parses only the files that changed
  since the last build ([below](#incremental-updates)).
- **graphify's conventions.** Node ids are path slugs (`core/src/lib.rs` +
  `Fixed.acquire` → `core_src_lib_fixed_acquire`), labels are `name()` /
  `.name()` / bare, and the node-link layout is graphify's, so every graphlore
  tool — and graphify's own `query` / `path` / `explain` — reads the graph
  unchanged. `built_at_commit` is recorded for freshness. The graph says
  `graph.engine: graphlore` and `schema_version: 3`, and graphlore then trusts
  its edges: `ImportChecker` reads `runs` instead of re-checking an import
  against the source, and the edges `resolve.py` adds to graphify's graphs are
  already there.

### How definitions are found

`engine/src/generic.rs` is a port of the rule in graphlore's `spans.py`: a
node is a definition when its type carries a hint word (`function`, `class`,
`struct`, …) and it names itself — a `name` field, a C/C++ declarator chain, an
identifier child — so a grammar no module knows still yields its definitions.
Each language module in `engine/src/lang/` overrides only what its grammar does
differently: Go struct vs interface types, Kotlin's and Swift's single
`class_declaration`, Rust test regions, the C++ reading of a `.h` file, Python's
decorator-only regions.

Checked against `spans.py` on the files of this machine (Go, JS, TS, Ruby,
Rust, Java, C#, PHP, Kotlin, Swift, Scala, C and C++ from package caches), the
native engine returned the same `(region_start, end_line, def_line, qualname)`
for every one of about 95,000 definitions, except in C/C++ headers (17,830 of
18,280, 97.5%), where the Rust grammar crates and the Python language pack
parse macros and the most vexing parse differently. On 30,522 Python
definitions it returns the same spans as the stdlib `ast`.

### How edges are resolved

The same walk records, per file, the import statements, the uses (calls,
constructions, bases, types in signatures and bodies, decorators, attribute
reads) and the names it can type (typed parameters and fields, `x = Foo()`,
`Foo x = new Foo()`, `let x = Foo::new()`, `x := pkg.New()` through the
function's declared result). Then, for the whole project
(`engine/src/resolve/`):

1. **Modules** are resolved the way each language resolves them
   (`resolve/modules.rs`): Python with graphlore's own root rules (packages,
   `src/` layouts, relative imports); JS/TS by path with extensions and
   `index` files, `tsconfig.json` `paths` / `baseUrl` and workspace packages;
   Go through `go.mod`; Java, Kotlin, Scala, C# and PHP through the packages
   and namespaces files declare; Rust through `Cargo.toml` crates and the module
   tree (`crate::`, `self::`, `super::`); Ruby `require_relative` / `require`;
   Swift through SwiftPM targets; C/C++ `#include` by path, then by unique path
   suffix.
2. **A name** is looked up as the language looks it up: enclosing definitions
   (and the class and its bases where methods see members without `this`), the
   file, its imports (following re-exports), the package / namespace / module it
   shares, its wildcard imports. A receiver is typed through what the walk knows
   of it, and a chain (`builder().a().b()`) through each method's declared
   result. All of that is `EXTRACTED`.
3. **Only then** is a name looked up across the project: a unique function or
   type of the same language, or a method only one type has (not a name every
   type has, and in a file the caller imports or its directory). That is
   `INFERRED`. A name with several candidates gets no edge; the build reply
   counts the unresolved uses per language.

An external module's contents are unknown, so a wildcard import of one
(`#include <stdio.h>`, `using System;`) names nothing.

### Communities

`engine/src/community/` runs Leiden (local moving, refinement, aggregation, so
every community is connected) to maximise modularity, on a graph of the
internal nodes whose edges are weighted:

| | weight |
|---|--:|
| `contains`, `method`, `defines`, `case_of`, `calls` | 1.0 |
| `inherits`, `implements`, `mixes_in`, `embeds` | 1.5 |
| an import of a file / of a definition | 0.7 / 0.4 |
| `references`, `uses`, `indirect_call` and the rest | 0.5 |
| × an `INFERRED` edge | 0.5 |
| × ends in the same directory / in different top-level directories | 1.25 / 0.6 |
| × test code to production code | 0.1 |

plus a tie of total weight 0.1 between each file and the other files of its
directory. External nodes take no part, and join the community most of their
neighbours are in. Then graphify's rules: an isolated node is its own
community, one over a quarter of the graph (at least 10 nodes) or one of 50 and
more with a cohesion under 0.05 is split by a second pass, ids are by size, and
a community is named after its most connected member, prefixed with the
directory when most members share one (`httpx: Response`).

Nodes are visited in the order of a hash of their id, so adding a node does
not reorder the rest, and the same graph gives the same communities on any
machine. A build starts from the previous graph's communities and repeats the
partition until a pass returns its own start (when graphify's splits make two
states alternate, it keeps the one closest to where it began, which a rebuild
from it keeps too); a first build settles the same way. The previous ids are
kept by overlap, and a saved name stays while its community's members are the
same or a strict majority of both its old and new members; others are dropped
from `.graphify_labels.json`, and the build says how many.

On the 31 audit repositories, against graphify's communities on its own graphs
(`benchmarks/compare_engines.py REPO --communities`, averages per repository):

| | graphify | native |
|---|--:|--:|
| communities | 90.6 | 90.5 |
| largest, % of the nodes | 10.0 | 12.4 |
| singletons, % of the nodes | 1.1 | 0.1 |
| median size | 8.3 | 10.8 |
| modularity on the plain graph | 0.67 | 0.60 |
| members in the community's main directory | 82.8% | 87.1% |
| test nodes in a mostly-production community | 20.9% | 8.0% |

The two partitions agree with a normalised mutual information of 0.78 (0.62 to
1.0). The lower plain modularity is the price of the weights: the native
communities follow directories more closely (in 20 of the 31 repositories) and
keep tests apart from the code they test. The largest communities that the
size rule leaves whole are single files: express's `History.md` (298 headings
under one title) and express-mongoose-demo's minified jQuery (225 functions).

After a one-file change (two functions added) and `update=True`, 99.8–100% of
the nodes keep their community id on eight of the repositories (httpx,
retrofit, alamofire, guzzle, kotlinpoet, got, restsharp, cpr), and 99% to all
of the names saved for every community stay; before the settling above, 86–92%
of the ids stayed. The community step takes at most 173 ms on the audit
repositories, and 3–5 s on the 270,000 nodes of the Rust crates in
`~/.cargo/registry`.

### Incremental updates

Every build writes `<out dir>/cache/graphlore-native.bin`: each file's facts
with its size, modification time and content hash, for this engine version and
these build options. `update=True` reuses the facts of a file whose size and
modification time are unchanged (and older than the cache by two seconds), or
whose content hashes the same, and parses the rest; resolution and communities
run over the whole project, so an update writes the graph a full build would
(a test checks), and the nodes of deleted files go. A cache from another
version or other options, or one that cannot be read, means a full parse.
`verbose=True` shows the time per phase.

| | full build | update, nothing changed | update, one file changed |
|---|--:|--:|--:|
| httpx (86 files) | 0.26 s | 0.11 s | 0.10 s |
| kotlinpoet (163) | 0.58 s | 0.34 s | 0.30 s |
| guzzle (168) | 0.86 s | 0.21 s | 0.26 s |
| retrofit (392) | 2.18 s | 0.68 s | 0.69 s |
| `~/.cargo/registry` (2,466 files, 270,000 nodes) | 19.2 s | 8.7 s | – |

What an update still spends is the whole-project part. On the crates that is
assembling and resolving (1.8 s and 1.6 s), communities (3.0 s; 5.0 s on a
first build, which settles from scratch) and writing a 250 MB graph.json
(1.3 s), while parsing drops from 8.5 s to 0.2 s.

## Compared with graphify on 31 repositories

`benchmarks/compare_engines.py` builds both graphs of a repository, pairs
graphify's code definitions with the native ones (same file, definition line
and bare name), and compares the edges between paired definitions (imports as
file pairs). On the 31 repositories of the [polyglot audit](benchmark-polyglot.md)
(graphify 0.9.111, engine 0.2.0, measured 2026-11-16), as native / graphify
edges and the share of graphify's that the native graph has too:

| Language | same definition | imports (file pairs) | calls | bases | type references |
|---|--:|--:|--:|--:|--:|
| Python | 100% | 212 / 219 (96%) | 2,113 / 834 (93%) | 67 / 62 (100%) | 709 / 494 (68%) |
| JavaScript | 97.3% | 190 / 305 (59%) | 890 / 2,061 (34%)¹ | 1 / 1 | 3 / 0 |
| TypeScript | 99.6% | 527 / 634 (83%) | 627 / 688 (79%) | 25 / 20 (100%) | 693 / 156 (88%) |
| TSX | 99.7% | 253 / 316 (80%) | 422 / 456 (93%) | – | 38 / 4 |
| Go | 100% | 538 / 950 (47%)² | 5,893 / 3,167 (98%) | 19 / 14 (100%) | 672 / 355 (100%) |
| Java | 100% | 1,100 / 1,010 (100%) | 5,082 / 5,123 (80%) | 207 / 168 (94%) | 3,908 / 1,930 (96%) |
| Kotlin | 99.4% | 359 / 333 (95%) | 3,376 / 2,108 (61%) | 87 / 56 (88%) | 1,003 / 673 (75%) |
| Scala | 100% | 2 / 0 | 33 / 20 (35%) | 21 / 22 (95%) | 62 / 32 (91%) |
| C# | 92.7% | 334 / 280 (28%)³ | 2,172 / 2,450 (76%) | 87 / 86 (98%) | 1,388 / 806 (97%) |
| Rust | 93.3% | 323 / 370 (75%) | 1,714 / 853 (76%) | 50 / 41 (93%) | 1,164 / 895 (81%) |
| Swift | 95.9% | 145 / 166 (–)³ | 3,247 / 2,141 (77%) | 170 / 164 (100%) | 934 / 580 (51%) |
| C | 100% | 89 / 77 (99%) | 864 / 791 (100%) | – | 253 / 130 (97%) |
| C++ | 92.1% | 215 / 228 (93%) | 285 / 280 (25%)⁴ | 30 / 5 (100%) | 206 / 214 (71%) |
| C/C++ headers | 68.2% | 164 / 172 (95%) | 337 / 342 (83%) | 11 / 12 (92%) | 380 / 285 (72%) |
| Ruby | 99.5% | 42 / 0 | 298 / 374 (60%) | 36 / 15 (93%) | 48 / 0 |
| PHP | 80.6% | 429 / 505 (84%) | 5,715 / 6,140 (81%) | 65 / 60 (98%) | 263 / 213 (95%) |
| **All** | **93.3%** | **4,922 / 5,565 (75%)** | **33,068 / 27,828 (77%)** | **876 / 726 (96%)** | **11,724 / 6,767 (85%)** |

Of the native calls, 5,067 (15%) are `INFERRED`, against 47% of graphify's.
Reading a sample of each side's edges the other lacks, per language:

- **What only graphify has** is mostly its own guesses and artefacts.
  ¹ 1,235 of graphify's 2,061 JavaScript calls sit in one generated Dokka
  bundle (`website/…/scripts/main.js` in retrofit) and most of the rest in
  minified jQuery and Bootstrap; without the bundle and the minified files the
  native graph has 97% of them. Its import edges to `package.json` (an `import x from 'pkg'` linked
  to the manifest that declares `pkg`, which graphlore's import check has
  always rejected) are most of the JS/TS import gap. ² A Go import goes to
  every file of the package in graphify's graph, `_test.go` files included;
  the native graph links the files the importer actually uses. ³ C# and
  Swift imports go to namespace and module nodes that graphify pins to an
  arbitrary file of the namespace (`UIKit` lands on `MasterViewController.swift`).
  Name guesses that the source contradicts: `unquote` imported from
  `urllib.parse` linked to a project `unquote`, `toString()` linked to a test
  helper, Ruby's `@article.destroy` linked to the controller's own `destroy`.
  ⁴ C++ calls: graphify links a call to the declaration in the header; the
  native graph to the definition, whose body is what `fetch` and `impact` read.
- **What only the native graph has** is mostly what a name lookup by itself
  cannot see: calls through a module object (`httpx.Response(...)`), through a
  typed local or field (`client = httpx.Client(); client.get()`,
  `self.repo.find()`), through builder chains (`new Retrofit.Builder().baseUrl()`)
  and Go functions' declared results, type references in the same file, and
  `this` inside functions assigned to an object (`res.send = function () {
  this.get() }` in express).

**Definitions.** What only graphify has is PHP closures (`{closure#N}()`, 894
of PHP's 895), a node for a Rust `impl` or Swift `extension` target defined in
another file, and C/C++ forward declarations and macro-wrapped declarations in
headers. What only the native engine has is what `spans.py` treats as
definitions: nested functions, anonymous functions bound to an object key or a
class field, overloads graphify folds into one node, C++ methods declared in
their class.

**Speed.** With resolution and communities, the native builds are 5–8 times
faster than graphify's (`--code-only --no-viz`, four cores, starting Python
included): retrofit 2.2 s against 13.9 s, guzzle 0.9 s / 5.8 s, alamofire
1.2 s / 5.7 s, got 0.7 s / 5.3 s, kotlinpoet 0.6 s / 3.7 s; an update after a
one-file change takes a third of that ([above](#incremental-updates)).

### The polyglot audit on native graphs

`benchmarks/polyglot.py --engine native` runs every tool on the native graphs
of the same 31 repositories (built into `graphlore-native/`, semantic tools
skipped), and the same run on graphify's graphs is the baseline. Findings, per
id, summed over the repositories (measured 2026-11-16):

| Finding | graphify graphs | native graphs |
|---|--:|--:|
| `FETCH-SHADOW-SPAN` (high: fetch returns another definition's span) | 7 | 0 |
| `FETCH-NO-SPAN` (fetch falls back to a line window) | 94 | 0 |
| `FETCH-ENCLOSING-SPAN` (fetch returns the definition around the node) | 9 | 1 |
| `SLOW` (a call over the time limit) | 11 | 0 |
| `SKELETON-EMPTY` (a file with definitions yields no symbols) | 6 | 5 |
| `TEST-PATH-MISSED` (a file holding tests is not seen as a test file) | 1 | 2 |
| `REPLY-TOO-LARGE` (a reply over 90 KB with default arguments) | 0 | 5 |
| `ROUTES-SILENT-EMPTY`, `IMPORTS-UNCLASSIFIED`, `UNREFERENCED-LISTS-A-TEST` | 3 | 1 |
| `GRAPH-ORPHAN-NODES` (info) | 26 | 20 |

Every tool answers on every native graph as it does on graphify's,
`communities` and `label_communities` included since the engine detects
communities (engine 0.3.0; an `AMBIGUOUS-LABELS` that run found, six
`.filter()` object-key functions in one file listed alike, was a display bug
of graphlore's, now fixed). The run takes 134 s of tool time against 216 s on
graphify's graphs. What the native graphs still raise:

- **`REPLY-TOO-LARGE`** comes from their denser edges, not from wrong ones:
  more edges make other nodes the hubs the audit picks, and those are big.
  guzzle's `CurlFactory` is one class of 3,100 lines that `fetch` returns
  whole (it always returns the first node it is asked for), retrofit's `Call`
  has a 98 KB neighbor list, and cpr's cross-language `same_names` is long.
- **`SKELETON-EMPTY`** is a file that holds only constants
  (`docs/docusaurus.config.ts`): they are nodes, but not spans, so `skeleton`
  lists nothing. `fetch` reads such a node's own lines (`region_start` to
  `end_line`).
- **`TEST-PATH-MISSED`**: ureq's `src/run.rs` and aspnetcore-realworld's
  `build/Program.cs` hold tests that no path rule recognises.

The first native run also found three bugs that had nothing to do with the
engine, and they are now fixed for both engines. A Go function type in a map
type (`map[string]func() hash.Hash`) was taken for a function named `string`,
in `spans.py` as well as in `generic.rs`. `neighbors` listed one row where
two nodes reference each other. `path` and `explain` read the CLI's default
`graphify-out/` when `GRAPHLORE_OUT_DIR` named another directory.

## Roadmap

1. **Phase 1: definitions, spans, containment, headings.** Done.
2. **Phase 2: imports and references in all 14 languages.** Done (this page).
3. **Communities and incremental updates.** Done (this page).
4. **No CLI behind the CLI tools.** `query`, `path` and `explain` answered over
   graph.json in Python; community naming by MCP sampling only; the route and
   package-API extractors moved into the language modules.
5. **Native by default.** The polyglot audit passes on native graphs (above),
   so what is left is publishing the wheels (below) and making `auto` the
   default; then
   graphify, `graphlore_add` and `mode="deep"` go, and the output directory gets
   graphlore's own name (still reading `graphify-out/`).

Publishing `graphlore-engine` wheels (Linux, macOS and Windows, x86-64 and
ARM) comes before the plugin can install it; until then it is built from a
checkout.
