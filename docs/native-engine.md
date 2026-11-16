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
- **graphify's conventions.** Node ids are path slugs (`core/src/lib.rs` +
  `Fixed.acquire` → `core_src_lib_fixed_acquire`), labels are `name()` /
  `.name()` / bare, and the node-link layout is graphify's, so every graphlore
  tool — and graphify's own `query` / `path` / `explain` — reads the graph
  unchanged. `built_at_commit` is recorded for freshness. The graph says
  `graph.engine: graphlore` and `schema_version: 2`, and graphlore then trusts
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

**Speed.** With resolution, the native builds are 3–6 times faster than
graphify's (`--code-only --no-viz`, four cores, starting Python included):
retrofit 2.5 s against 13.9 s, guzzle 1.4 s / 5.8 s, alamofire 1.8 s / 5.7 s,
got 1.4 s / 5.3 s, kotlinpoet 0.8 s / 3.7 s. The Rust crates in
`~/.cargo/registry` (2,300 files, 88,000 nodes, 203,000 edges) take 11.5 s.

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

Every tool answers on every native graph as it does on graphify's, except
`communities` and `label_communities`, which have no communities to show yet.
The run takes 122 s of tool time against 216 s on graphify's graphs. What
the native graphs still raise:

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
3. **Communities and incremental updates.** A deterministic community detection
   in the engine (stable ids, since saved names are keyed by them), and a
   per-file facts cache keyed by content hash, so `update=True` re-parses only
   what changed and node-carried spans can be trusted without re-parsing.
4. **No CLI behind the CLI tools.** `query`, `path` and `explain` answered over
   graph.json in Python; community naming by MCP sampling only; the route and
   package-API extractors moved into the language modules.
5. **Native by default.** `auto` becomes the default once communities are in
   (the polyglot audit already passes on native graphs otherwise, above); then
   graphify, `graphlore_add` and `mode="deep"` go, and the output directory gets
   graphlore's own name (still reading `graphify-out/`).

Publishing `graphlore-engine` wheels (Linux, macOS and Windows, x86-64 and
ARM) comes before the plugin can install it; until then it is built from a
checkout.
