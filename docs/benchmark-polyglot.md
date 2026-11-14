# graphlore on 14 languages: every tool, on real repositories

`benchmark.md` measures one tool, `graphlore_locate`, on six HTTP-client
libraries. This is the other half: **all 33 tools, the resources and the
prompts**, run on 20 real repositories in 14 languages, to find
what breaks, what answers wrongly and what stays silent — and what was fixed as
a result.

Back to the [README](../README.md) · [Benchmark](benchmark.md) ·
[Tool reference](tools.md) · [Concepts](concepts.md)

Measured 2026-11-14 with graphlore 0.5.1 (unreleased), graphify
0.9.108, tree-sitter-language-pack 1.25.0, semble 0.7.3,
Python 3.12.3. Results: [`benchmarks/results-polyglot.json`](../benchmarks/results-polyglot.json).

## In short

- Before the fixes the audit reported **448 findings** (a finding is
  one problem in one repository, tool and language); after them **91**.
  No crash is left; what is left is coverage gaps, oddities of the graph
  graphify builds, and the open items at the end.
- The span engine, which `fetch`, `skeleton`, `locate`, labels and the dead-code
  and duplication analyses all stand on, had only ever been measured on six
  languages. **Kotlin had no spans at all**; PHP, TSX, Scala, Rust, Swift and C
  had spans for things that are not definitions, and those won over the real
  ones.
- JS/TS **imports through a barrel file were rejected as false**, so the
  import-cycle, layer and surprise tools worked on a thinner graph.
- Fifteen tools cut their output without saying how to get the rest, against
  the project's own convention.
- What remains is coverage, not correctness: route and package-API extraction
  read five languages, and say so now when they meet another.

## What was run

`benchmarks/polyglot.py` needs no ground truth per repository. Every check is
one of three kinds:

1. **An invariant the tools promise**: `as_json=True` parses on every path,
   hostile arguments included (`limit=-1`, `hops=0`, `../../etc/passwd`); a
   result that reports `truncated` carries a count and a `hint`; a reported
   `file:line` exists; a miss answers `code: "not_found"` with a suggestion;
   nothing is written to stdout; the read-only tools leave `graph.json` and the
   working tree untouched.
2. **Agreement between two tools**: `neighbors` against the edges, `subgraph`
   against `neighbors`, `fetch`'s code against the file slice it names, the
   skeleton header against the symbol's name, `impact`'s counts against its
   rows, a layer rule against the import edges that break it.
3. **A crude per-language oracle** — a regex that knows what a test, an import
   or a route looks like in that language — which turns a silently empty answer
   into a finding.

Everything that writes runs on a throwaway copy: a comment and a rename are
applied to one file per language and `graphlore_freshness` / `graphlore_diff`
must call the first cosmetic and the second structural; a file is deleted and
`graphlore_prune` must remove its nodes and leave no dangling edge;
`graphlore_build`, `graphlore_set_labels` and `graphlore_label_communities` are
run there too. The protocol surface (structured results, `isError`, resources,
prompts) is driven over an in-memory MCP session.

```bash
python benchmarks/polyglot.py --setup --root /tmp/polyglot      # clone the pinned commits, build the graphs
python benchmarks/polyglot.py --root /tmp/polyglot --json out.json --baseline benchmarks/results-polyglot.json
```

Graphs are built with `graphify update .`: local AST extraction, clustering
included, no API key.

| Language | Repository | Commit | Nodes | Edges | Definitions |
|---|---|---|--:|--:|--:|
| Python | [encode/httpx](https://github.com/encode/httpx) | `b5addb64f0` | 2,339 | 4,534 | 1,219 |
| Python + TSX | [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | `aabb901269` | 2,059 | 4,391 | 740 |
| JavaScript | [expressjs/express](https://github.com/expressjs/express) | `43fca52d28` | 707 | 1,122 | 170 |
| JavaScript | [madhums/node-express-mongoose-demo](https://github.com/madhums/node-express-mongoose-demo) | `8a134ab5de` | 372 | 482 | 138 |
| TypeScript | [sindresorhus/got](https://github.com/sindresorhus/got) | `6b5aafc1cd` | 1,713 | 3,833 | 1,016 |
| TypeScript | [lujakob/nestjs-realworld-example-app](https://github.com/lujakob/nestjs-realworld-example-app) | `c1c2cc4e44` | 340 | 688 | 110 |
| Go | [go-resty/resty](https://github.com/go-resty/resty) | `716987e2b6` | 1,614 | 5,699 | 1,362 |
| Go | [go-chi/chi](https://github.com/go-chi/chi) | `c64ed7d663` | 816 | 2,973 | 524 |
| Go | [gin-gonic/examples](https://github.com/gin-gonic/examples) | `2661f002ee` | 720 | 1,170 | 277 |
| Java | [square/retrofit](https://github.com/square/retrofit) | `7b6300dda7` | 5,306 | 16,635 | 4,014 |
| Java | [spring-projects/spring-petclinic](https://github.com/spring-projects/spring-petclinic) | `500158f732` | 697 | 1,560 | 245 |
| Kotlin | [square/kotlinpoet](https://github.com/square/kotlinpoet) | `5a120729f7` | 3,236 | 8,169 | 2,035 |
| Scala | [com-lihaoyi/requests-scala](https://github.com/com-lihaoyi/requests-scala) | `e3619c19ef` | 338 | 505 | 144 |
| C# | [restsharp/RestSharp](https://github.com/restsharp/RestSharp) | `466e5f8782` | 4,025 | 9,328 | 1,576 |
| Rust | [algesten/ureq](https://github.com/algesten/ureq) | `51bb001154` | 1,992 | 4,375 | 1,215 |
| Swift | [Alamofire/Alamofire](https://github.com/Alamofire/Alamofire) | `dea4bab917` | 4,336 | 9,959 | 2,276 |
| C | [redis/hiredis](https://github.com/redis/hiredis) | `5bb7f030f6` | 1,330 | 3,154 | 689 |
| C++ | [libcpr/cpr](https://github.com/libcpr/cpr) | `34e18e3f70` | 2,932 | 5,058 | 1,063 |
| Ruby | [lostisland/faraday](https://github.com/lostisland/faraday) | `565a149d40` | 1,188 | 1,471 | 528 |
| PHP | [guzzle/guzzle](https://github.com/guzzle/guzzle) | `f8aca40edf` | 5,146 | 13,849 | 4,150 |

41,206 nodes in all. The repositories were picked to give every extractor
a positive case (a FastAPI, an Express, a NestJS, a gin, a chi and a Spring
application) and every language its usual test layout (`src/test/java`,
`jvmTest`, `Foo.Tests/`, `spec/`, Rust's inline `#[cfg(test)]`).

## What works in each language

A definition "has its own span" when the span picked for its graph node starts
on the node's line and carries its name; up to 60 definitions per language and
repository were fetched. *Before* is the same harness on the code before the
fixes (commit `7e0ddb8`).

| Language | Own span, before | Own span, after | Skeleton headers without the name, before → after | Freshness / diff |
|---|--:|--:|--:|---|
| Python | 120 / 120 (100%) | 120 / 120 (100%) | 0 → 0 of 406 | correct (2 files edited) |
| JavaScript | 190 / 284 (67%) | 217 / 284 (76%) | 0 → 0 of 592 | correct (6 files edited) |
| TypeScript | 174 / 183 (95%) | 174 / 183 (95%) | 0 → 0 of 774 | correct (3 files edited) |
| TSX | 48 / 66 (73%) | 64 / 66 (97%) | 0 → 0 of 66 | correct (2 files edited) |
| Go | 180 / 180 (100%) | 180 / 180 (100%) | 0 → 0 of 691 | correct (3 files edited) |
| Java | 144 / 145 (99%) | 144 / 145 (99%) | 680 → 0 of 950 | correct (2 files edited) |
| Kotlin | 0 / 121 (0%) | 117 / 121 (97%) | 0 → 0 of 718 | correct (3 files edited) |
| Scala | 46 / 60 (77%) | 60 / 60 (100%) | 4 → 0 of 138 | correct (1 file edited) |
| C# | 53 / 60 (88%) | 60 / 60 (100%) | 137 → 0 of 218 | correct (1 file edited) |
| Rust | 55 / 60 (92%) | 58 / 60 (97%) | 0 → 0 of 310 | correct (1 file edited) |
| Swift | 54 / 60 (90%) | 60 / 60 (100%) | 87 → 0 of 394 | correct (1 file edited) |
| C | 52 / 60 (87%) | 60 / 60 (100%) | 1 → 0 of 250 | correct (1 file edited) |
| C++ | 45 / 123 (37%) | 123 / 123 (100%) | 0 → 0 of 355 | correct (2 files edited) |
| C / C++ headers | 63 / 120 (52%) | 89 / 120 (74%) | 16 → 1 of 776 | correct (2 files edited) |
| Ruby | 33 / 60 (55%) | 58 / 60 (97%) | 0 → 0 of 127 | correct (1 file edited) |
| PHP | 49 / 60 (82%) | 60 / 60 (100%) | 0 → 0 of 1344 | correct (1 file edited) |

"Correct" means a trailing comment was reported as a cosmetic change and a
renamed definition as a structural one, by both `graphlore_freshness` (working
tree) and `graphlore_diff` (two commits).

What the import-reading tools (`cycles`, `layers`, `surprises`) and the
extractors make of each language:

| Language | Import edges | How they are classified | `package_apis` | `routes` |
|---|--:|---|---|---|
| Python | 430 | 409 module-level, 13 type-checking, 7 unclassified, 1 deferred, 8 rejected | read: 87 of 88 importing files | read |
| JavaScript | 216 | 216 module-level | read: 149 of 162 importing files | read |
| TypeScript | 1,328 | 973 module-level, 351 type-checking, 4 deferred | read: 134 of 146 importing files | read |
| TSX | 612 | 596 module-level, 16 type-checking | read: 67 of 72 importing files | read |
| Go | 950 | 950 module-level | read: 187 of 187 importing files | read |
| Java | 866 | 866 module-level | read: 328 of 328 importing files | read |
| Kotlin | 243 | 243 module-level | not read, and says so (`not_read`) | not read, and says so |
| Scala | 0 | — | not read, and says so (`not_read`) | not read, and says so |
| C# | 132 | 132 module-level | not read, and says so (`not_read`) | not read, and says so |
| Rust | 407 | 407 module-level | not read, and says so (`not_read`) | not read, and says so |
| Swift | 167 | 167 module-level | not read, and says so (`not_read`) | not read, and says so |
| C | 77 | 77 module-level | not read, and says so (`not_read`) | not read, and says so |
| C++ | 236 | 236 module-level | not read, and says so (`not_read`) | not read, and says so |
| C / C++ headers | 174 | 174 module-level | not read, and says so (`not_read`) | not read, and says so |
| Ruby | 0 | — | not read, and says so (`not_read`) | not read, and says so |
| PHP | 396 | 396 unclassified | not read, and says so (`not_read`) | not read, and says so |

Routes, on the applications picked for them. "Idiom hits" is the oracle's count
of route registrations in non-test source; "rows" is what `graphlore_routes`
returned for that language:

| Repository | Framework | Idiom hits | Rows | Found as |
|---|---|--:|--:|---|
| fastapi-template | FastAPI | 23 | 23 | fastapi 23 |
| express | Express (its own examples) | 61 | 0 | nothing |
| express-mongoose-demo | Express | 26 | 0 | nothing |
| nestjs-realworld | NestJS | 21 | 21 | nestjs 21 |
| chi | chi | 60 | 252 | net-http 132, chi 120 |
| gin-examples | gin | 65 | 66 | gin 66 |
| spring-petclinic | Spring | 19 | 17 | spring 17 |

Not in the table: 536 JS/TS edges graphify draws from `import x from 'pkg'`
to the `package.json` that declares `pkg`. The import check rejects those, which
is the check doing its job.

Test files, as `graphlore_tests_for` and the production-before-tests ordering
see them. A file "holds tests" when it contains the language's test marker
(`@Test`, `[Fact]`, `#[test]`, `func Test…`, `RSpec.describe`, `import XCTest`…):

| Language | Files that hold tests | Recognised by path, before | Recognised by path, after |
|---|--:|--:|--:|
| Python | 30 | 30 | 30 |
| JavaScript | 91 | 91 | 91 |
| TypeScript | 50 | 50 | 50 |
| Go | 60 | 60 | 60 |
| Java | 121 | 115 | 121 |
| Kotlin | 50 | 15 | 50 |
| Scala | 5 | 5 | 5 |
| C# | 70 | 70 | 70 |
| Rust | 30 | 2 | 4 |
| Swift | 35 | 35 | 35 |
| C++ | 36 | 36 | 36 |
| Ruby | 36 | 35 | 36 |
| PHP | 60 | 60 | 60 |

Rust is the one that stays low: its tests live in the source file, inside
`#[cfg(test)] mod tests`, and no path rule can see that (see the open items).

## Before and after

The same harness, on the code before the fixes and after them. A count is the
number of times the check fired.

| Finding | Kind | Before | After |
|---|---|--:|--:|
| `CRASH` — ValueError: k should be >= 1, is now 0 | high / bug | 20 | 0 |
| `FETCH-ONE-LINE-SPAN` — a multi-line definition was fetched as its first line only | high / bug | 25 | 0 |
| `FETCH-SHADOW-SPAN` — a span that starts on the node's line belongs to another symbol | high / bug | 112 | 6 |
| `ROUTES-EXPECTED-MISSING` — no express routes found in an application built on express | high / gap | 1 | 1 |
| `APIS-SILENT-EMPTY` — files of this language import packages; the reply finds none and never says the language is not read | medium / bug | 13 | 0 |
| `EXT-TABLE-GAP` — an extension graphify indexes is missing from graphlore's tables | medium / bug | 2 | 0 |
| `FETCH-NO-SPAN` — a definition has no span: fetch falls back to a line window | medium / gap | 274 | 99 |
| `IMPORTS-REJECTED` — over 5% of this language's import edges are rejected as contradicted by the source | medium / bug | 3 | 0 |
| `REPLY-TOO-LARGE` — a reply over 90 KB with default-sized arguments (some 25,000 tokens and up) | medium / bug | 34 | 0 |
| `ROUTES-SILENT-EMPTY` — the source registers routes in an idiom the extractor does not read; the reply lists none | medium / gap | 2 | 2 |
| `SET-LABELS-INVISIBLE` — a name written by set_labels shows up in neither graphlore_communities nor graphlore_overview | medium / bug | 20 | 0 |
| `SKELETON-EMPTY` — a file with definition nodes yields no symbols | medium / gap | 13 | 4 |
| `SKELETON-HEADER-NO-NAME` — a symbol's header does not show its name | medium / bug | 925 | 1 |
| `TEST-PATH-MISSED` — files that contain tests are not recognised as test files | medium / gap | 5 | 1 |
| `TRUNCATED-NO-HINT` — `truncated` without a `hint` on how to get the rest | medium / convention | 607 | 0 |
| `UNREFERENCED-LISTS-A-TEST` — lists a test the runner collects as dead code | medium / bug | 190 | 1 |
| `AMBIGUOUS-LABELS` — member list repeats a bare label; rows are not qualified | low / convention | 14 | 0 |
| `CLI-AMBIGUOUS-NAME` — a bare name several nodes share is refused by the graphify CLI; graphlore passes the refusal through instead of resolving the name | low / gap | 6 | 7 |
| `EMPTY-TYPE-FIELD` — every row carries `type: ""` (graphify >= 0.9 writes file_type) | low / bug | 20 | 0 |
| `EXT-TABLES-DISAGREE` — an extension is known to some source-extension tables and not others | low / bug | 11 | 0 |
| `FETCH-ENCLOSING-SPAN` — the node has no span of its own: fetch returns the definition around it | low / gap | 70 | 13 |
| `IMPORTS-UNCLASSIFIED` — every import edge of this language is unclassified | low / gap | 6 | 2 |
| `NEIGHBORS-COUNT` — row count differs from the edges touching the node | low / bug | 3 | 0 |
| `RELATION-UNKNOWN` — graphify emits a relation no graphlore relation set names (relations="code" skips it) | low / gap | 24 | 0 |
| `SLOW` — took over 5000 ms | low / perf | 3 | 4 |
| `APIS-LANGUAGE-NOT-READ` — no package-API extractor for this language (the reply says so) | info / gap | 0 | 13 |
| `BUILD-DEFAULT-NEEDS-KEY` — graphlore_build() with its defaults fails without an LLM key when the repository holds docs; code_only=True builds | info / upstream | 20 | 20 |
| `GRAPH-ORPHAN-NODES` — the graph graphify built has orphan_nodes | info / upstream | 17 | 17 |

Tool status over the 20 repositories × 32 tools:
before 578 ok, 42 empty, 20 crashed; after 600 ok,
40 empty, 0 crashed. "Empty" is a valid answer with nothing in it
(`routes` on a library, `package_apis` on a language it does not read, `impact`
on a node nothing depends on). `graphlore_add` is not counted: it fetches a URL,
so it is only called with one it must refuse. The `SLOW` rows are
3 `graphlore_build` calls (graphify's own extraction time) and 1 `graphlore_duplication_scan` call.

`benchmarks/multilang.py`, re-run before and after on the six repositories it
covers: span-join precision is unchanged for Python, JS/TS, Go, Java and Rust,
and rises from 32 to 39 of 54 hits for C++ (hits with no span at all fall from
19 to 7).

## What was wrong, and is fixed

Each entry names the repository that showed it. The regression tests in
`tests/test_tools.py` hold the same cases cut down to a few lines.

### Spans

- **Kotlin had no spans.** Its grammar names a declaration through an
  identifier child, not a `name` field, and the engine only looked for the
  field. In kotlinpoet none of 2,009 definitions had a span: `graphlore_fetch`
  returned one line, `graphlore_skeleton` an empty file, no annotation was ever
  read. 1,997 have one now.
- **A type written in a signature was a definition.** Any node with a hint word
  in its type and a `name` field became a span: TypeScript
  `React.ComponentProps` (fastapi-template), Rust `io::Result` and the struct
  literal `Pool { … }` (ureq), Swift type annotations (1,412 of them in
  Alamofire), Scala constructor parameters (requests-scala), C
  `struct config config` in a parameter list (hiredis) — and every PHP
  `$parameter` in guzzle, because `simple_parameter` contains the hint "impl".
  These one-line spans sat on the definition's own line and, being the
  innermost, won: `graphlore_fetch("FormMessage")` returned the first line of
  the function under the name `FormMessage.ComponentProps`.
- **A `.h` file was always C.** cpr's headers are C++; the C grammar read their
  classes as errors, and 146 of 558 definitions had a span. A header with a
  `class`, a `namespace` or a `template<` at the start of a line is parsed as
  C++ now: 532. Function prototypes, methods declared in their class, typedefs,
  operators and functions behind a reference return type
  (`const std::string& Cookie::GetDomain() const`) are spans too.
- C# and Java `record`s, Swift `protocol`s, `init` and `deinit`, and a JS
  function assigned to a member (`res.send = function () {…}` — how all of
  express is written) had no span.
- **Skeleton headers stopped before the name.** A Java, C# or Swift
  declaration starts at its first annotation, and GNU-style C puts the return
  type on its own line, so the header of 925 symbols read `@Override`, `[Fact]`
  or `static int` and nothing else (retrofit, RestSharp, Alamofire,
  spring-petclinic, hiredis).
- Several spans can start on one line (`enum Color { RED, GREEN }`); `fetch`,
  `skeleton(node=)` and qualified labels now take the one that carries the
  node's name. A node with no span of its own — a closure, a member declared
  without a body — is labelled by where it lives (`sendDigest.{closure#3}()`)
  instead of taking the enclosing function's name: guzzle showed three closures
  as three `AuthMiddleware.sendDigest()` rows.
- Names scoped with `::` (Ruby `Faraday::Utils::Headers`, C++ / PHP
  `Session::SetOption()`) resolve by their last part and as a qualified key;
  they used to fall through to the substring match.

### Imports and relations

- **JS/TS imports through a barrel were rejected.** graphify links
  `import { CreateDto } from './dto'` to the file that defines `CreateDto`. The
  import check compared the specifier with that file, found `dto/index.ts`
  instead, and dropped the edge as "contradicted by the source": 113 of got's
  797 import edges, 96 of express's 170, 39 in fastapi-template, 9 in the NestJS
  application. `cycles`, `layers` and `surprises` ran on what was left.
  Re-exports are followed now (`export … from`, `module.exports = require(…)`,
  up to three files); a file the entry merely imports is still not accepted.
- C, C++ and Objective-C `#include`s were `unclassified`; they are
  module-level, like the imports of the other compiled languages.
- graphify 0.9 writes relations the code compared against literals did not
  know. `defines` (a class's field) and `case_of` (an enum's case) are
  containment: they could close an "other" cycle and counted as a reference.
  `dispatches_to`, `references_constant` and `uses_static_prop` are code
  relations that `graphlore_impact(relations="code")` skipped.
- `.mts` / `.cts` were source for the import reader and not for freshness or
  dead-code analysis; `.swift`, `.kts` and `.hxx` had no language family, so
  `graphlore_same_names` compared a Swift class with every other family-less
  language.

### Tests and dead code

- Test files of Gradle source sets (`jvmTest`, `androidTest` — 35 files in
  kotlinpoet), .NET test projects (`Foo.Tests/`), `FooTests.java`, Ruby
  `foo_spec.rb` and `*_tests.cpp` were production code to `tests_for` and to
  the production-before-tests order.
- `graphlore_unreferenced` listed collected tests as dead code — Go
  `Benchmark…` functions, googletest `TEST`, xUnit `…Tests` classes and
  `setUp` fixtures — and never read an annotation that starts the declaration
  itself (Java / Kotlin `@Test`), nor a C# `[Fact]` or a Rust `#[test]`.
  In spring-petclinic 117 definitions are now "registered by a decorator"
  (`@Test` ×80, `@GetMapping` ×11, …) instead of candidates.
- It also examined what no caller names: 866 of guzzle's roughly 900 rows
  were closures (`{closure#3}`); cpr listed operators and constructors;
  faraday's `success?` and `body=` could not be found by a text scan for an
  identifier. guzzle is down to 14 rows.

### Conventions and replies

- **Fifteen tools cut silently.** `truncated` and a count were there, the
  `hint` the project's convention asks for was not: `god_nodes`, `surprises`,
  `search`, `subgraph`, `tests_for`, `locate`, `duplication_scan`,
  `freshness`, `diff`, `cycles`, `unreferenced`, `layers`, `same_names`,
  `package_apis`, `routes`.
- `graphlore_package_apis` and `graphlore_routes` answered a Rust, C#, Kotlin,
  Swift, Scala, Ruby, PHP or C project exactly like one with nothing to find.
  They count the files they could not read in `not_read`.
- A name written by `graphlore_set_labels` showed up in no tool;
  `graphlore_communities` shows it, and the names graphify itself builds.
- `type` was `""` on every row of `god_nodes`, `search` and `node_details`:
  graphify 0.9 writes `file_type`.
- `graphlore_locate(top_k=0)` raised `ValueError` out of the semantic backend.
- With semble installed and its model download blocked, `locate` said
  "needs the optional 'semble' extra. Install with: pip install …".
- `graphlore_neighbors` listed a self-loop twice.
- A negative `limit` was used as a slice end: `limit=-1` meant "all rows but
  the last", 471 KB from `graphlore_search` on guzzle.
- `graphlore_communities` listed every member of every community: 280 KB on
  guzzle and 277 KB on retrofit, in the second step of the onboarding flow. It
  returns the `limit` largest with their `members` most connected nodes.

## What is still missing

In the order I would take them.

1. **Express routes when the routes file does not import the framework.** The
   extractor only reads `app.get('/x', h)` in a file that imports express.
   express-mongoose-demo registers every route in `config/routes.js`, which
   exports `function (app, passport) {…}` and imports no framework: 0 of 26
   routes found. The gate exists to keep `axios.get('/users')` out; a project-level
   gate (some file imports a server framework) plus a receiver that is a
   parameter or is named like a router would keep that and find these. The
   opposite case is in got: 1,354 rows, all of them routes its tests register
   on a throwaway server.
2. **`graphlore_routes`, `graphlore_package_apis`, `graphlore_layers` and
   `graphlore_tests_for` take no `scope`.** The convention says a tool that
   ranks or audits the graph does; these four read vendored and test files
   alike (the 1,354 rows above).
3. **Routes and package APIs for the other languages.** Nothing is read for
   Kotlin (Spring, Ktor), C# (ASP.NET attributes and `MapGet`), Rust (axum,
   actix), Ruby (Rails, Sinatra), PHP (Laravel, Symfony), Scala, Swift or
   C / C++; Java reads Spring and not JAX-RS. The replies say so now; the
   `apis.py` extractors are the model for adding one.
4. **Rust tests are invisible to the path rule.** In ureq 26 of 48 source
   files hold tests inside `#[cfg(test)] mod tests`. `tests_for` treats them as
   production callers, and the production-before-tests order cannot separate
   them. The span engine could mark a `mod` under `#[cfg(test)]`.
5. **PHP and Ruby imports are unclassified** (396 edges in guzzle). A PHP `use`
   cannot be deferred and could join the compile-time languages; Ruby
   `require` inside a method can.
6. **A bare name is refused by the CLI tools.** graphify 0.9 answers
   `graphlore_explain("create")` with "Ambiguous: matches 3 nodes … retry with
   the full node id" and graphlore passes that through. The other tools resolve
   the same name with `_pick_node` and say which node they used.
7. **`graphlore_build()` with its defaults fails on any repository that has a
   README and no LLM key** (graphify 0.9: "no LLM API key found"). graphlore
   now names `code_only=True` in that error; whether `code_only` should be the
   default without a key is a product decision.
8. **Spans still missing**: a JS function wrapped in a call
   (`exports.create = async(function* () {…})` — 44 definitions in
   express-mongoose-demo), a class made by assignment (Ruby `Struct.new`, a
   TypeScript `const schema = z.object(…)`), C declarations behind unknown
   macros (hiredis `ffc.h`: `ffc_internal ffc_inline` before the return type),
   anonymous Java classes, and nodes graphify makes of a forward declaration
   (`class ProxyAuthentication;`). For a typedef'd
   anonymous struct the skeleton header is `typedef struct {`, the name being
   on the closing line.
9. **Dead-code heuristics that remain language-shaped**: C# extension classes
   (never named at the call site), Scala case classes next to a companion of
   the same name, and JSDoc tags written without a leading `*` (`@default`),
   which read as decorators.
10. **The lockfile pins graphify 0.8.39; `uvx` gives plugin users 0.9.x.**
    Most of what this audit found in the relation and field vocabulary
    (`file_type`, `defines`, `case_of`, `dispatches_to`, closure nodes,
    `package.json` import targets) is 0.9 behaviour that the 5-node fixture
    graph cannot show. A fixture graph built by 0.9 would.

### From the graph, not from graphlore

Reported so they are not mistaken for the above.

- Orphan nodes in 17 of 20 graphs; self-loop `contains` edges (a Go type
  containing itself).
- A node placed on another definition's line: express's `res` object sits on
  the line of `res.status = function status(code)`, so `graphlore_fetch("res")`
  returns `status`. The six remaining `FETCH-SHADOW-SPAN` rows are this.
- A label that carries more than the name
  (`ffc_adjusted_mantissa ffc_digit_comp()`).
- `graphify . --code-only` refuses to overwrite a graph that `graphify update`
  built when its dedup would shrink it.

### About the environment the audit ran in

semble's default model download goes through Hugging Face's xet backend, which
needs `cas-server.xethub.hf.co` besides `huggingface.co`. Behind a proxy that
allows only the latter, `HF_HUB_DISABLE_XET=1` falls back to a plain download
(which is served from `us.aws.cdn.hf.co`).
