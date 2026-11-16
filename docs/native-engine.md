# The native engine

`engine/` is graphlore's own extractor: a Rust library, shipped as the Python
package `graphlore-engine`, that walks a project and writes a
graphify-compatible `graph.json`. It exists so that graphlore can stop depending
on the graphify CLI. Today it is experimental and opt-in
(`GRAPHLORE_ENGINE=native`, see [configuration](configuration.md#the-native-engine));
this page says what it does, how it compares with graphify on real code, and
what is left before it can be the default.

## What it does (phase 1)

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
  marked `test`.
- **Containment.** `contains` from a file or an enclosing definition, `method`
  from a class-like definition to its callables (including Go receivers, Rust
  `impl` blocks, Swift extensions and C++ `Class::method` defined outside the
  class, when the type is in the same file), `case_of` to enum members.
- **Documents.** A page node per Markdown / reStructuredText file and a node per
  heading, nested by level (`code_only` skips them).
- **graphify's conventions.** Node ids are path slugs (`core/src/lib.rs` +
  `Fixed.acquire` → `core_src_lib_fixed_acquire`), labels are `name()` /
  `.name()` / bare, and the node-link layout is graphify's, so every graphlore
  tool — and graphify's own `query` / `path` / `explain` — reads the graph
  unchanged. `built_at_commit` is recorded for freshness.

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

## Compared with graphify on 31 repositories

`benchmarks/compare_engines.py` builds both graphs of a repository and pairs
graphify's code definitions with the native ones (same file, definition line
and bare name). On the 31 repositories of the [polyglot audit](benchmark-polyglot.md)
(graphify 0.9.111, engine 0.1.0, measured 2026-11-16):

| Language | graphify | native | same definition | parent agrees |
|---|--:|--:|--:|--:|
| Go | 2,048 | 2,069 | 100% | 98% |
| Java | 4,290 | 4,821 | 100% | 100% |
| Python | 1,384 | 1,406 | 100% | 98% |
| Scala | 144 | 161 | 100% | 92% |
| C | 364 | 428 | 100% | 84% |
| Ruby | 616 | 644 | 99.5% | 96% |
| Kotlin | 2,497 | 2,897 | 99.4% | 98% |
| C++ | 554 | 1,324 | 92.1% | 88% |
| Rust | 1,652 | 1,644 | 91.3% | 96% |
| JavaScript | 1,115 | 7,024 | 89.4% | 66% |
| TypeScript | 1,391 | 2,176 | 88.0% | 99% |
| Swift | 2,636 | 2,718 | 83.5% | 95% |
| PHP | 4,603 | 3,715 | 80.6% | 99% |
| TSX | 291 | 313 | 78.0% | 100% |
| C/C++ headers | 1,378 | 1,752 | 68.2% | 97% |
| C# | 3,095 | 2,404 | 67.9% | 92% |
| **All** | **28,058** | **35,496** | **88.2%** | |

"Parent agrees" is over the matched definitions that graphify gives a parent.
The builds are 10–17 times faster: retrofit 13.9 s with graphify against 0.93 s
native, guzzle 5.8 s / 0.52 s, alamofire 5.7 s / 0.59 s, got 5.3 s / 0.32 s,
kotlinpoet 3.7 s / 0.21 s (`--code-only --no-viz`; four cores; the native time
includes starting Python).

What only graphify has is mostly node kinds the native engine does not make
yet, not definitions it misses:

- **Properties and variables**: C# properties (`{ get; set; }`, most of the C#
  gap), Swift stored and computed properties, module-level constants and
  variables in TypeScript, JavaScript and Rust (`const PWD_ALGORITHM`).
- **Closures**: PHP's `{closure#N}()` nodes (894 of the 895 PHP gaps).
- **Types defined elsewhere**: a node for the target of a Rust `impl` or a
  Swift `extension` whose type lives in another file (`Target<'a>`,
  `extension String`); the native engine hangs those methods off the file.
- **Forward declarations and macros in C/C++ headers** (`class Interceptor;`).

What only the native engine has is mostly what `spans.py` already treats as
definitions: functions nested in functions, anonymous functions bound to an
object key or a class field (`onSuccess: () => …`), overloads that graphify
folds into one node (Swift's `tableView(_:…)` delegate methods), C++ methods
declared in their class, and minified or
vendored JavaScript that graphify skips (graphlore's analysis scope leaves those
out at read time either way). JavaScript's parent gap is
`View.prototype.render = function` (graphify makes it a method of `View()`; the
native engine hangs it off the file).

## Roadmap

1. **Phase 1 (this release): definitions, spans, containment, headings.**
2. **Imports and references.** Language modules record import statements (with
   when they run: module-level, deferred, type-checking) and references (calls,
   type uses, inheritance) during the same walk; a project symbol table and a
   resolver per language turn them into `imports`, `imports_from`, `calls`,
   `inherits`, `implements` edges with a confidence. On a native graph
   `ImportChecker` and `resolve.py` then have nothing left to correct. The
   missing node kinds above (properties, constants, closures, external impl
   targets) land here too, since references need them.
3. **Communities and incremental updates.** A deterministic community detection
   in the engine (stable ids, since saved names are keyed by them), and a
   per-file facts cache keyed by content hash, so `update=True` re-parses only
   what changed and node-carried spans can be trusted without re-parsing.
4. **No CLI behind the CLI tools.** `query`, `path` and `explain` answered over
   graph.json in Python; community naming by MCP sampling only; the route and
   package-API extractors moved into the language modules.
5. **Native by default.** `auto` becomes the default once the polyglot audit
   passes on native graphs; then graphify, `graphlore_add` and `mode="deep"`
   go, and the output directory gets graphlore's own name (still reading
   `graphify-out/`).

Publishing `graphlore-engine` wheels (Linux, macOS and Windows, x86-64 and
ARM) comes before the plugin can install it; until then it is built from a
checkout.
