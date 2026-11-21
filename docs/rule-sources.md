# Where the resolution rules come from

The native engine's name-resolution rules were found the slow way: build the
graph of a repository, compare it with graphify's or with a compiler's
answers, read the differences, write a rule. That found real rules, and it
found them one repository at a time. Engine 0.5.60 closed gaps a reading of
the languages' own tools would have listed up front: the types of a Rust
tuple struct's fields were never read, a Go function's second result type was
never read, a C# namespace written as a block did not see what a file-scoped
one saw.

This document is the plan for turning that around. **A rule is read out of
the language's own tools first** (its compiler or interpreter, its
specification, its grammar), written down with where it was read and which
versions of the language it holds for, made a case of the checklist, and only
then implemented. Running on repositories stays, as the test that the rules
were read right, not as the way to find them.

- [`docs/resolution-rules.md`](resolution-rules.md) is the checklist as it
  stands (510 cases); [`docs/native-engine.md`](native-engine.md) has the
  measurements.
- `benchmarks/rule_sources.py` fetches what this document names.

## Three kinds of rule, three sources

What the engine has been missing falls into three kinds, and each has a
place it can be read from without running anything on a repository.

| Kind | Example found on a repository | Where it can be read instead |
|---|---|---|
| **A position never read**: a place a type or a name can be written that the reader does not look at | `struct Override(Gitignore);`, `([]Completion, Directive)`, `const FLAGS: &[&dyn Flag]` | The grammar. Each tree-sitter grammar ships `node-types.json`: every node kind, its fields, and what can stand in each. The places a type or an expression can appear are a finite list. |
| **A binding rule**: which declaration a name written at a position means | block namespaces and extensions, a partial type's constructors, an `impl` of the file's own type | The compiler's binder and the specification: scopes, imports, member lookup, overload resolution. |
| **Inference**: what a name means only once types are inferred | `.map(\|worker\| worker.run())` | The type checker. Not a rule to port: a design decision per language on how far the engine goes (it has none today beyond declared and constructed types). |

The first kind is mechanical and comes first (phase 1). The second is the
bulk of the reading (phase 2). The third is decided per language after the
second, with numbers: `benchmarks/unresolved.py` and the compilers' "missed"
lists say how many edges each step of inference would buy.

## What is read, per language

`python benchmarks/rule_sources.py --root DIR` makes a sparse, blobless,
depth-1 checkout of each repository below with only these directories: 17
repositories, about 300 MB and under a minute, where the full clones are
tens of gigabytes. `DIR/sources.lock.json` records the commit of each, so a
rule can cite `repository@commit:path`. Every path below was checked against
those checkouts (`--check`).

| Language | Scopes and names (binder) | Members and overloads | Modules and imports | Specification |
|---|---|---|---|---|
| Go | `golang/go` `src/go/types/resolver.go` | `src/go/types/lookup.go` | `resolver.go` (packages), `go.mod` | `doc/go_spec.html` |
| Rust | `rust-lang/rust` `compiler/rustc_resolve/src/ident.rs` (and `late.rs`) | `compiler/rustc_hir_typeck/src/method/probe.rs` | `compiler/rustc_resolve/src/imports.rs` | `rust-lang/reference` `src/names/` |
| C# | `dotnet/roslyn` `src/Compilers/CSharp/Portable/Binder/Binder_Lookup.cs` | `Binder/Semantics/OverloadResolution/` | `Binder/Imports.cs` | `dotnet/csharpstandard` `standard/` |
| TypeScript | `microsoft/TypeScript` `tsc/internal/binder/nameresolver.go` | `tsc/internal/checker/checker.go` | `tsc/internal/module/resolver.go` | (the compiler is the specification) |
| JavaScript | TypeScript's binder reads JS too | – | `nodejs/node` `lib/internal/modules/` | ECMA-262; `doc/api/modules.md`, `esm.md`, `packages.md` |
| Python | `python/cpython` `Python/symtable.c` | (dynamic; the data model) | `Lib/importlib/_bootstrap.py` | `Doc/reference/executionmodel.rst`, `import.rst` |
| Java | `openjdk/jdk` `…/javac/comp/Resolve.java` | `…/javac/comp/Attr.java` | `…/javac/comp/TypeEnter.java` | JLS chapters 6 and 15.12 (not in a repository) |
| Kotlin | `JetBrains/kotlin` `compiler/fir/resolve/…/calls/tower/FirTowerResolver.kt` | `compiler/fir/resolve/…/calls/` | (in the tower: implicit and explicit imports) | `Kotlin/kotlin-spec` `docs/src/md/kotlin.core/` |
| Swift | `swiftlang/swift` `lib/AST/UnqualifiedLookup.cpp` | `lib/Sema/TypeCheckNameLookup.cpp` | `lib/AST/NameLookup.cpp` | The Swift Programming Language (not in this repository) |
| Scala | `scala/scala3` `compiler/src/dotty/tools/dotc/typer/Typer.scala` | `typer/Applications.scala`, `Implicits.scala` | `typer/ImportInfo.scala` | `docs/_docs/reference/` |
| C, C++ | `llvm/llvm-project` `clang/lib/Sema/SemaLookup.cpp` | `clang/lib/Sema/SemaOverload.cpp` | (the preprocessor; `compile_commands.json`) | ISO C and C++ `[basic.lookup]`, `[over]` (not in a repository) |
| Ruby | `ruby/ruby` `vm_insnhelper.c` (constant lookup, cref) | `vm_method.c` | `load.c` | `doc/syntax/` |
| PHP | `php/php-src` `Zend/zend_compile.c` | `Zend/zend_inheritance.c` | (Composer's PSR-4 map: `composer.json`) | `php/php-langspec` `spec/18-namespaces.md` |

Two of these are dynamic languages where the interpreter binds at run time
(Ruby, Python members). For them the interpreter's source says what the
lookup order is, and the static tools their communities use (Sorbet, RBS,
mypy, pyright) say what can be known without running; those are read as
secondary sources where the interpreter leaves a choice open.

## Language versions

The engine reads one language today: the newest. It reads no version from a
project. Most of the time that is harmless, because a newer feature is new
syntax, and a project on an older version simply does not write it. It is
wrong in the places where **the same source means something else in another
version**, and it is blind to which features a project can use at all.

### Each compiler has the table

The compilers keep, in one file each, which version has which feature. These
are read into one table per language (phase 0), not re-derived:

| Language | The table | What it holds (checked in the checkout) | A project says its version in |
|---|---|---|---|
| C# | `roslyn` `Errors/MessageID.cs` | 374 `IDS_Feature…` entries, each mapped to a `LanguageVersion` (`CSharp12`, `CSharp14`, …) | `<LangVersion>`, else the `<TargetFramework>`'s default (`.csproj`, `Directory.Build.props`) |
| Java | `jdk` `javac/code/Source.java` | the `Feature` enum: 31 features with the release each arrived in (`MODULES(JDK9, …)`) | `maven.compiler.release` / `source` (`pom.xml`), `sourceCompatibility`, `toolchain` (Gradle) |
| Kotlin | `kotlin` `LanguageVersionSettings.kt` | the `LanguageFeature` enum with `sinceVersion` (`ContextParameters(sinceVersion = KOTLIN_2_4)`) | `languageVersion`, else the Kotlin plugin's version (Gradle, Maven) |
| Swift | `swift` `include/swift/Basic/Features.def` | 75 language, upcoming and experimental features with the language mode that turns each on | `// swift-tools-version:` and `swiftLanguageModes` (`Package.swift`) |
| Rust | `rust` `rustc_span/src/edition.rs`; uses in `rustc_resolve` | editions 2015, 2018, 2021, 2024, and the resolver asking for them (`is_rust_2015()` in `ident.rs`) | `edition`, `rust-version` (`Cargo.toml`) |
| Go | `go` `src/go/types/version.go` | `go1_9` … constants, asked at 17 places of the checker | the `go` line of `go.mod`, a file's `//go:build go1.N` |
| C, C++ | `llvm-project` `clang/Basic/LangStandards.def`, `LangOptions.def` | 45 standards and what each turns on | `-std=` in `compile_commands.json`, `CMAKE_CXX_STANDARD` |
| Scala | `scala3` `config/SourceVersion.scala`, `Feature.scala` | source versions `3.0` … with their `-migration` twins | `scalaVersion` (`build.sbt`), `-source` |
| TypeScript | `tsc/internal/core/compileroptions.go` | no feature-by-version table; `module` / `moduleResolution` / `target` decide how an import is resolved | `tsconfig.json`, the `typescript` version in `package.json` |
| Python | `cpython` `Doc/whatsnew/*.rst`, `Grammar/python.gram` | prose per release, and the grammar | `requires-python` (`pyproject.toml`), `python_requires`, `.python-version` |
| PHP | `php-src` `UPGRADING` (per release branch) | prose per release | `require.php` (`composer.json`) |
| Ruby | `ruby` `doc/NEWS/NEWS-*.md` | prose per release | `.ruby-version`, `required_ruby_version` (gemspec) |

For the last three the table is built by hand from the release notes; for
TypeScript the axis that matters is the module-resolution mode, not the
compiler's version.

### What the engine needs

1. **The version of each file's project** (`FileFacts::lang_version`, read by
   `ModuleSystems` from the manifest nearest the file, like the crate root
   and the namespace are today). No manifest, or none that says: the newest,
   as now.
2. **A rule that depends on the version asks it**, the way a rule that is one
   language's asks which language it is reading (`parse::current()`,
   `Family`). The neutrality check (`benchmarks/neutrality.py`) is the model
   for the test: the same small project under two versions, and the edge
   that must differ.
3. **A case of the checklist carries its versions** (`since`, `until`), and
   `benchmarks/verify_cases.py` gives it to the compiler in that version:
   `javac --release N`, `kotlinc -language-version`, `swiftc -swift-version`,
   `rustc --edition`, `-std=`, Roslyn's `LanguageVersion` parse option, the
   `go` line of the case's `go.mod`, `scalac -source`. One installed compiler
   per language covers its older versions this way; only Python, Ruby and PHP
   need several interpreters (`uv python install`, and for the other two a
   decision in phase 0).

The first reading task of phase 2, per language, is the short list of places
where the binder itself asks for the version: those are the rules where one
source has two meanings. Everything else in the feature table is syntax that
is either read or not, which phase 1 settles.

## A rule, written down

A case of `benchmarks/conformance.py` already has the language, an id, the
rule in a sentence, a reference and the small project with the edges that
must and must not exist. It gains three fields:

- `source`: where the rule was read, as `repository@commit:path` plus the
  function or section (`dotnet/roslyn@da9878a:…/Binder_Lookup.cs
  LookupAllExtensions`, or `csharpstandard §12.8.10.3`). Today's `where`
  is one string per language.
- `since` / `until`: the language versions it holds for (absent: all).
- `found`: `source` (read from the tools first) or `repository` (found by
  measurement, source looked up afterwards). The share of `source` is how
  far this plan has got.

`docs/resolution-rules.md` is generated from the cases and shows them.

## Languages without open sources

All fourteen languages the engine reads today have an open compiler or
interpreter. The method has to hold for one that does not (a vendor's
language with a closed compiler: ABAP, Apex, PL/SQL, MATLAB, a COBOL
dialect), and for a closed tool of an open language:

1. **The grammar first.** A tree-sitter grammar (or the vendor's published
   grammar) gives the positions, as for any other language.
2. **The vendor's language reference is the specification.** A rule cites
   the document, its version and the section, in the same `source` field.
   Where the reference is silent the rule is not written; it is listed as
   unknown.
3. **Whatever the vendor ships that answers "what is this name" is the
   oracle**: a language server (LSP `textDocument/definition` over a corpus
   is the same comparison `oracle_go.py` makes), a compiler's cross-reference
   or symbol-table listing, a published conformance suite.
4. **With no oracle, a rule is `INFERRED`**, whatever it resolves through:
   `EXTRACTED` means a compiler would agree, and nobody has asked one.
5. Documentation is read under its licence: rules and citations are written
   in our own words, and no vendor text or test program is copied into the
   repository.

## The environment

What a session has, and what it needs (measured in the cloud environment
`graphlore`, 2026-11-21):

| | In the image | Installed by hand each session | Missing |
|---|---|---|---|
| Compilers | Go 1.24, Rust 1.97 (rustup), Node 22 + `tsc` 6.0, Python 3.13 (uv), Java 21 (javac), GCC 13, Ruby 3.3, PHP 8.3 | .NET SDK 8 (`apt`), rust-analyzer (`rustup component`), kotlinc (83 MB), Swift 6.1 (2.8 GB) | Scala 3, clang (only the one inside the Swift toolchain) |
| Sources | – | `rule_sources.py` (300 MB, under a minute) | – |
| Corpus | – | the 45 audit repositories (1.8 GB), graphify's graphs | – |

- **Disk is the constraint**: a session has a fixed allowance and this one
  ran out at 9.5 GB of scratch (45 graph sets of 167 MB each were the cause).
  The sources are small; the Swift toolchain is not. Graph sets are kept two
  at a time from now on.
- **A setup script** on the environment installs the two small tools every
  session needs (`dotnet-sdk-8.0`, `rust-analyzer`). kotlinc and Swift stay
  on demand: they are large, and only `verify_cases.py` and the two oracles
  use them.
- **Scala** has no compiler here: installing one through its launcher was
  refused by the session's safety check, and that is the owner's decision
  to make (a release archive from `scala/scala3`, as kotlinc was installed,
  is the alternative).
- **Network**: github.com for the sources; `download.swift.org` is already
  allowed. Nothing else is needed for phases 0 to 2.

## Phases

**Phase 0: the frame** (no rule changes).
`source` / `since` / `until` / `found` on the cases, shown in
`docs/resolution-rules.md`. The version tables read out of the six compilers
that have one into `benchmarks/versions/<language>.json`, with the script
that regenerates each from the checkout. `lang_version` read from manifests
into the facts (unused by any rule yet), with a golden per manifest kind.
`verify_cases.py` passing a case's version to the compiler.
*Done when*: every existing case has a `source`, and the audit repositories
each report a version per language.

**Phase 1: positions, from the grammars.**
`benchmarks/positions.py`: for each of the 14 grammars, every node kind of
`node-types.json` with a field that can hold a type or an expression, against
what the reader visits; the output is the list of positions not read, per
language. Each is then decided: read it (a case, then code) or leave it out
on purpose (written down with the reason: a struct literal's field names).
*Done when*: the list is empty or every entry has a decision. This would
have listed the tuple fields, the `const` types and the Go results.

**Phase 2: binding rules, from the binders**, one language at a time:
read the binder and the specification section by section; list the rules;
mark each as held by an existing case, missing, or read differently; write
the missing ones as cases with their `source`; implement; then run the
oracle and `verify_graphify.py` as the test. Order, by what the measurements
say is left and by how directly the compiler can check the result:

1. **C#** (28 confirmed edges still missing; Roslyn runs here; the feature
   table is the largest)
2. **Rust** (editions change path resolution; rust-analyzer runs here)
3. **Go** (smallest binder, already whole on the floor: the cheapest full
   reading, and the template for the others)
4. **TypeScript / JavaScript** (module resolution modes)
5. **Java**, then **Kotlin** (one platform; `--release` and
   `-language-version` give every version from one compiler)
6. **Swift**, **C / C++** (need the large toolchains)
7. **Python**, **PHP**, **Ruby** (interpreters; the static tools as second
   source)
8. **Scala** (when a compiler is available)

*Done per language when*: every section of the binder's lookup is either a
case or a written "not followed, because", and the share of cases marked
`found: source` is reported.

**Phase 3: inference, by the numbers.** Per language, what one step of
inference would add (a closure parameter typed by the collection it is
called on, a pattern binding), from the lists the oracles already produce,
and the decision to build it or to leave those edges out.

**Phase 4: closed languages**, when one is asked for: the method above.

## What stays as it is

The oracles, `verify_graphify.py` and its floor, the audit and the
neutrality check keep running after every resolver change. They are how a
rule read from a compiler's source is shown to have been read right, and how
a regression is caught. What changes is the direction of the work: from the
tools to the rule to the test, not from the failing test to the rule.
