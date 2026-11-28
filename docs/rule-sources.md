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
| C# | `roslyn` `Errors/MessageID.cs` | 191 `IDS_Feature…` entries, each mapped to a `LanguageVersion` (`CSharp12`, `CSharp14`, …) | `<LangVersion>`, else the `<TargetFramework>`'s default (`.csproj`, `Directory.Build.props`) |
| Java | `jdk` `javac/code/Source.java` | the `Feature` enum: 31 features with the release each arrived in (`MODULES(JDK9, …)`) | `maven.compiler.release` / `source` (`pom.xml`), `sourceCompatibility`, `toolchain` (Gradle) |
| Kotlin | `kotlin` `LanguageVersionSettings.kt` | the `LanguageFeature` enum with `sinceVersion` (`ContextParameters(sinceVersion = KOTLIN_2_4)`) | `languageVersion`, else the Kotlin plugin's version (Gradle, Maven) |
| Swift | `swift` `include/swift/Basic/Features.def` | 264 entries (baseline, language, upcoming, experimental); an upcoming one names the language mode that turns it on | `// swift-tools-version:` and `swiftLanguageModes` (`Package.swift`) |
| Rust | `rust` `rustc_span/src/edition.rs`; uses in `rustc_resolve` | editions 2015, 2018, 2021, 2024, asked at 30 places of the resolver (`is_rust_2015()` in `ident.rs`); `rustc_feature/src/accepted.rs` has the 213 stabilised features with their release | `edition`, `rust-version` (`Cargo.toml`) |
| Go | `go` `src/go/types/version.go` | 13 `go1_N` constants, asked at 48 places of the checker | the `go` line of `go.mod`, a file's `//go:build go1.N` |
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
- `found`: how the case came to be written: from the `specification`, shown
  missing by a `compiler` (its test suite, or its answers on repositories),
  or by `comparison` with graphify's graph. The number of cases with a
  `source` is how far this plan has got.

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
| Compilers | Go 1.24, Rust 1.97 (rustup), Node 22 + `tsc` 6.0, Python 3.13 (uv), Java 21 (javac), GCC 13, Ruby 3.3, PHP 8.3 | Swift 6.1 (2.8 GB); by the environment's setup script: .NET SDK 8, rust-analyzer, kotlinc 2.2, Scala 3.3 | clang (only the one inside the Swift toolchain) |
| Sources | – | `rule_sources.py` (300 MB, under a minute) | – |
| Corpus | – | the 45 audit repositories (1.8 GB), graphify's graphs | – |

- **Disk is the constraint**: a session has a fixed allowance and this one
  ran out at 9.5 GB of scratch (45 graph sets of 167 MB each were the cause).
  The sources are small; the Swift toolchain is not. Graph sets are kept two
  at a time from now on.
- **A setup script** on the environment installs what every session needs
  and is small: `dotnet-sdk-8.0`, `rust-analyzer`, and the Kotlin and Scala
  compilers from their release archives on GitHub (120 MB, under
  `/opt/langtools`). Swift stays on demand: 2.8 GB, and only
  `verify_cases.py` and one oracle use it.
- **Network**: github.com for the sources; `download.swift.org` is already
  allowed. Nothing else is needed for phases 0 to 2.

## Where the work stands

Phase 0 is done (engine 0.5.61; no rule changed, and the 45 audit graphs
have the same edges as before):

- **The cases say how they were found**: 425 from the specification, 71
  shown by a compiler, 14 by comparison (and, since, 26 from the grammar). None has a `source` yet: that
  number is what phase 2 raises.
- **Every case is compiled by its own language**: 510 of 510, Scala's 28
  included now that a Scala compiler runs here.
- **The compilers say which version each case needs.**
  `verify_cases.py --min-version` asks, for the seven languages whose
  compiler takes a version as a flag, for the oldest version that accepts
  each case, and records it (`benchmarks/results-case-versions.json`, shown
  in the Versions column of `docs/resolution-rules.md`). Of 247 cases 18
  need more than the oldest version asked (Java 9, 10 and 16; C# 9, 10 and
  12; C++11; C99), and one is accepted up to a version only: a mutable
  `static var` is an error in Swift 6.
- **The version tables are read out of the compilers**
  (`benchmarks/versions.py` writes `benchmarks/versions/<language>.json`):

| Language | versions | features | places the resolver asks | read from |
|---|--:|--:|--:|---|
| csharp | 19 | 191 | 49 | `dotnet/roslyn@da9878a16b49:src/Compilers/CSharp/Portable/Errors/MessageID.cs` |
| java | 15 | 31 | 31 | `openjdk/jdk@78bf06f425a9:src/jdk.compiler/share/classes/com/sun/tools/javac/code/Source.java` |
| kotlin | 16 | 464 | 80 | `JetBrains/kotlin@04f3c969a5c6:core/language.version-settings/src/org/jetbrains/kotlin/config/LanguageVersionSettings.kt` |
| swift | 1 | 264 | – | `swiftlang/swift@1176ade30d65:include/swift/Basic/Features.def` |
| rust | 4 | 213 | 30 | `rust-lang/rust@c7d5ba59d8e1:compiler/rustc_feature/src/accepted.rs` |
| go | 13 | 0 | 48 | `golang/go@2dbf8b413af3:src/go/types/version.go` |
| c-cpp | 45 | 45 | – | `llvm/llvm-project@87764a902a9b:clang/include/clang/Basic/LangStandards.def` |
| scala | 14 | 0 | 35 | `scala/scala3@1615177b58f6:compiler/src/dotty/tools/dotc/config/SourceVersion.scala` |

  "Places the resolver asks" are the lines of the name binder that test the
  language version (file, line and text are in the JSON): the reading list
  of phase 2 for the rules where one source has two meanings.
- **The engine reads the version a project states** (`engine/src/versions.rs`,
  from `go.mod`, `Cargo.toml`, `.csproj` and `Directory.Build.props`,
  `pyproject.toml`, `package.json`, `pom.xml`, Gradle builds and version
  catalogs, `Package.swift`, `build.sbt`, `composer.json`, a Gemfile or
  gemspec, `CMakeLists.txt`) and writes it to the graph's header
  (`graph.language_versions`: language, version, files). On the 45 audit
  repositories 4,349 of the 4,610 source files that are not JavaScript have
  one; the six gaps are named by the audit (two Scala builds without a
  literal `scalaVersion`, a Kotlin and a Ruby project, cJSON). No rule reads
  it yet. Not followed: a C# `<LangVersion>` set in an imported `.props`
  file of another name (RestSharp), the second framework of a multi-target
  project, and a version set on the command line.

Phase 1 is done for the places a type is written. `benchmarks/positions.py`
parses the audit's repositories with each grammar, takes every name of a type
the repository itself defines written where a type is, names the place by the
node that holds it (`type_spec.type`, `const_spec.type`, `throws.-`) and asks
the engine whether it read a use there. Over 13 languages (JavaScript and Ruby
write no types) the first run found 88.9% of the names read and 55 places read
less than nine times in ten. Engine 0.5.62 reads 99.6% of 47,906 names, and
five places are left under nine in ten.

| Language | places | names of the repository's own types | read with 0.5.61 | read with 0.5.62 |
|---|--:|--:|--:|--:|
| c | 6 | 108 | 49.1% | 108 (100.0%) |
| c/c++ header | 26 | 2,547 | 82.3% | 2,526 (99.2%) |
| cpp | 16 | 948 | 77.2% | 939 (99.1%) |
| csharp | 22 | 4,830 | 98.4% | 4,814 (99.7%) |
| go | 16 | 3,163 | 61.8% | 3,163 (100.0%) |
| java | 20 | 15,701 | 91.3% | 15,700 (100.0%) |
| kotlin | 19 | 3,743 | 93.9% | 3,701 (98.9%) |
| php | 10 | 1,205 | 90.3% | 1,205 (100.0%) |
| python | 4 | 704 | 100.0% | 704 (100.0%) |
| rust | 18 | 4,737 | 99.5% | 4,734 (99.9%) |
| scala | 18 | 676 | 78.3% | 668 (98.8%) |
| swift | 11 | 3,326 | 87.5% | 3,303 (99.3%) |
| tsx | 6 | 98 | 100.0% | 98 (100.0%) |
| typescript | 31 | 6,120 | 86.6% | 6,070 (99.2%) |

What 0.5.62 reads, none of which a repository comparison had pointed at
(twenty cases of the checklist, marked `grammar`):

- every language: a closure's own parameters and result (`func(next Handler)
  Handler { … }`, `function (Cookie $c): Result { … }`);
- Go: what a defined type is defined as (`type Key Opt`, `type Mid
  []func(Handler) Handler`), a constant's type, the types a slice or map
  literal is made of, the type handed to `make`; a package's `const` and
  `var` are the source of their own uses, as a Rust item is;
- C and C++: the type of a declaration (`buffer p = { 0 };`) and what a
  `typedef` names;
- Java: `throws` clauses, `T.class`, the type of a for-each variable, of a
  varargs parameter and of an interface's constant;
- Swift: `throws(E)`, a protocol property's type, `(any P).self`, and
  `Box<T>(…)` as a construction of `Box`;
- C#: `new T[n]`; Scala: a `val`'s declared type, `new Outer.Inner(…)` and
  `new p.T[A](…)`; Rust: the struct a pattern takes apart;
- C++: `T{…}` as a construction (cpr's whole request API is written that
  way: 660 calls), the result type of a member function declared in its
  class, explicit template arguments of a call, a trailing result type, a
  `friend`;
- TypeScript: the parameters of a function type name their types and no
  longer their names (`(err: Err) => void` was read as a type `err`).

On the 45 audit repositories that is 4,300 more reference edges and
950 more calls, and the compilers agree with them: Go 12,273 of
12,278 edges where `go/types` has an answer (five wrong, as before),
rust-analyzer 98.5%, Roslyn 97.9%, the Scala compiler's files 97.3% (96.7%),
TypeScript's 99.5%; the floor of graphify's confirmed edges holds, and the
audit of every tool on the native graphs has no new finding. (C and C++ have no compiler to
ask yet: their part, cpr's brace constructions above all, is checked by the
cases and by reading samples only.)

Ten places are left out on purpose and say why (`DECIDED` in the script: the
type an `impl` is of, a Go method's receiver, a Kotlin extension's receiver,
a declaration's own name). What is left under nine in ten is small: a C++
specialization that inherits from its own template, a Kotlin type annotation
inside a function type, two Swift shapes.

Twenty-six cases of the checklist are marked `grammar`. One mistake of the
measure was found on the way: it compared `$`-prefixed TypeScript names
without their `$`, and reported zod's `$ZodType` family as unread.

**The places a value is written.** `positions.py --values` does the same
for every name of the repository's own functions, methods, constants,
variables, properties and enum members written where a value is (a call's
callee, a member read, an argument), in all 15 report languages. A name that
is also a local or a parameter in the file is left out (the measure cannot
tell which is meant), and the engine records a use once per definition, so a
name counts as read when a use of it is recorded in the definition it is
written in. Engine 0.5.63 reads 97.7% of 162,839 names:

| Language | names of the repository's own values | read |
|---|--:|--:|
| c | 5,387 | 5,342 (99.2%) |
| c/c++ header | 48 | 47 (97.9%) |
| cpp | 9,152 | 9,013 (98.5%) |
| csharp | 6,612 | 6,485 (98.1%) |
| go | 18,971 | 18,914 (99.7%) |
| java | 8,788 | 8,723 (99.3%) |
| javascript | 3,506 | 3,266 (93.2%) |
| kotlin | 16,691 | 16,420 (98.4%) |
| php | 9,999 | 9,995 (100.0%) |
| python | 8,210 | 8,148 (99.2%) |
| ruby | 8,241 | 8,065 (97.9%) |
| rust | 21,298 | 20,805 (97.7%) |
| scala | 2,444 | 2,368 (96.9%) |
| swift | 15,274 | 14,114 (92.4%) |
| tsx | 896 | 888 (99.1%) |
| typescript | 27,322 | 26,457 (96.8%) |

The list it gave, and what came of it:

- **Read now.** A member read or written through a local or a parameter, in
  the languages where a name from another file needs an import (Python,
  JavaScript, TypeScript, Rust): `ctx.headers`, `request._noPipe = true`,
  `args.crlf`. The state reader dropped every such use whose head was not a
  name the file declares or imports, and a local is neither; a name a block
  declares counted for a bare use only. Swift `.case` beside a value whose
  type gives it (`h.section == .header`, `x.section = .body`, `case .body:`)
  is a reference to that type's case; only a called one (`.status(301)`) was
  read. A Rust macro's arguments are read for their member reads and writes
  too, not only for calls (`assert_eq!(false, args.crlf)`). A Scala or Rust
  call written with type arguments (`opt[URI]("x")`, `parse::<T>()`) is a
  call, not a reference. A local assigned a bare name holds what the name
  does (`const request = first;`).
- **Left out on purpose** (`DECIDED`, each with its reason): an object
  literal's keys and a struct literal's field names, argument labels and
  named arguments, a macro's own name, the arguments of a Swift attribute, a
  C++ constructor's member initializers (a constructor setting its own fields
  couples nothing), export lists.
- **Left**: TypeScript test files whose callbacks take untyped parameters
  (`(context) => context.command`), Swift `.case` in places the reader does
  not type (an array literal, a call whose parameter the walk cannot find),
  Ruby `@x += 1` on a field nothing declares, Java `case NULL:` labels.

On the 45 audit repositories that is about 1,900 `reads`, 1,100
`references`, 220 `writes` and 150 calls more. The compilers agree: Go 12,279
of 12,284, rust-analyzer 98.5% (recall 78.8% → 83.4%), TypeScript's 99.5%,
Kotlin's 99.4%, Swift's 94.0%. Many of the new `reads` are `INFERRED`: a
member of a local typed by what a call returns, or by the one type that has
every member the local is used with (`structural_type`); the second is a
guess, and it is wrong where a library type the project does not define has
the same members (got's `response.body` is read as `Options.body`).


### Phase 2: C#

The C# standard (`dotnet/csharpstandard`, `standard/`) and Roslyn's binder
(`src/Compilers/CSharp/Portable/Binder/`) read section by section: §7.8.1
namespace and type names (`LookupSymbolsSimpleName`, `InContainerBinder`,
`WithUsingNamespacesAndTypesBinder`, `WithExternAndUsingAliasesBinder`,
`BindQualifiedName`), §12.8.4 simple names, §12.5 member lookup
(`LookupMembersInClass`), §12.8.7 member access and the `Color Color` rule
(`BindLeftOfPotentialColorColorMemberAccess`), §12.8.10.3 extension methods
(`LookupAllExtensions`), §12.6.4 overload resolution (`BetterFunctionMember`)
and §14.5 using directives. `benchmarks/rule_inventory.py` holds the list:
every rule with the cases that hold it, or why the engine does not follow it
(54 rules, 47 held by a case, 7 not followed; 18 of 70 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| csharpstandard §7.8.1; Roslyn LookupSymbolsSimpleName | a method's type parameter is the name first | `method-type-parameter-first` |
| csharpstandard §7.8.1 | then a nested type of the type around, its own or a base's | `nested-type`, `nested-type-of-a-base` |
| csharpstandard §7.8.1; Roslyn InContainerBinder | then a type of each namespace around, innermost first | `enclosing-namespace` |
| csharpstandard §7.8.1 | a type of the namespace comes before one a `using` imports | `namespace-type-before-using` |
| csharpstandard §7.8.1; Roslyn WithExternAndUsingAliasesBinder | a using alias comes before the namespaces `using` imports | `using-alias-before-using-namespace`, `using-alias` |
| csharpstandard §7.8.1; Roslyn WithUsingNamespacesAndTypesBinder | a `using` namespace imports its types, not its nested namespaces | `using` |
| csharpstandard §14.5.1 | a `using` holds in the namespace body (or file) that writes it | `using-inside-a-namespace-block` |
| csharpstandard §14.5.1 | `global using` holds in every file of the compilation | `global-using` |
| csharpstandard §14.5.4 | `using static` imports a type's static members and nested types | `using-static` |
| csharpstandard §14.2 | a file-scoped namespace is the namespace of the whole file | `file-scoped-namespace` |
| csharpstandard §7.8.1; Roslyn BindQualifiedName | `N.T`, `T.Nested`: qualified names | `qualified-nested-type` |
| csharpstandard §7.8.1 | a generic name is looked up by its number of type arguments | `type-by-arity`, `generic-namesake`, `generic-use-is-no-plain-type` |
| csharpstandard §7.8.1 | two `using` namespaces giving the same name is an ambiguity error | not followed: an error is no binding; a graph of code that compiles never has one |
| csharpstandard §14.4 extern alias | `extern alias` names an assembly's root namespace | not followed: assemblies are not read: an aliased name is external |
| csharpstandard §12.8.4 | a local or a parameter hides a member of its name | `local-hides-member` |
| csharpstandard §12.8.4 | a member of the class or a base by its bare name (implicit `this`) | `implicit-this`, `inherited`, `inherited-from-a-grandparent` |
| csharpstandard §12.8.4 | a local function is a name of its block | `local-function` |
| csharpstandard §12.8.4 | a primary constructor's parameter is a name in the type's body | `primary-constructor` |
| csharpstandard §12.8.4 | a name declared later in the block is an error before its declaration | not followed: an error is no binding |
| csharpstandard §12.5; Roslyn LookupMembersInClass | a member declared in a derived type hides the base's of its name | `derived-member-hides-base` |
| csharpstandard §12.5 | a member of a base is a member of the type | `base`, `inherited` |
| csharpstandard §12.5 | an interface's members are members of a value typed by it | `interface-typed` |
| csharpstandard §15.2.7 | a partial type is every declaration of it | `partial`, `partial-type-constructor` |
| csharpstandard §12.5 | an inaccessible member is no candidate (`private` in another type) | not followed: accessibility is not modelled; code that compiles uses only what it can reach, so it seldom decides |
| csharpstandard §12.8.7.2; Roslyn BindLeftOfPotentialColorColorMemberAccess | `Color Color`: a member named like its type is the value or the type, by what follows | `color-color` |
| csharpstandard §12.8.7 | a static member through the type's name | `static-via-class`, `static-property` |
| csharpstandard §12.8.7 | an enum member through its enum | `enum-member`, `enum-member-value` |
| csharpstandard §12.8.8 | `a?.b` is a member of `a` | `null-conditional` |
| csharpstandard §12.8.14 | `this.m` and `base.m` | `this`, `base` |
| csharpstandard §12.8.10.3 | an instance method comes before an extension of the name | `instance-method-before-extension` |
| csharpstandard §12.8.10.3; Roslyn LookupAllExtensions | extensions are searched namespace by namespace, closest first | `closest-extension-namespace` |
| csharpstandard §12.8.10.3 | an extension is in sight only from a namespace around or one a `using` imports | `extension-needs-its-namespace`, `extension`, `block-namespace-sees-outer-extension`, `using-block-namespace` |
| csharpstandard §12.8.10.3 | an extension of an interface applies to its implementations | `extension-on-interface` |
| csharpstandard §12.8.10.3 | an extension's type arguments are inferred or written | `extension-by-type-arguments` |
| csharpstandard §12.6.4.2 | a candidate takes the call's number of arguments | `overload-arity` |
| csharpstandard §12.6.4.2 | a named argument picks the overload with a parameter of that name | `overload-by-argument-name` |
| csharpstandard §12.6.4.3; Roslyn BetterFunctionMember | a better conversion wins: an exact type over `object` | `overload-exact-type` |
| csharpstandard §12.6.4.3 | a candidate needing no default arguments wins | `overload-without-defaults` |
| csharpstandard §12.6.4.3 | a `params` candidate in normal form wins over its expanded form | `overload-normal-before-params` |
| csharpstandard §12.6.4.5 | better conversion between numeric types (`int` → `long` over `double`) | not followed: literal kinds only: the walk does not know an expression's numeric type |
| csharpstandard §12.6.4.3 | a non-generic candidate wins over a generic one alike | `overload-non-generic-first` |
| csharpstandard §13.6.2 | `var x = new T()`: x is a T | `var-new` |
| csharpstandard §13.6.2 | a declared local's type | `local-declared-type` |
| csharpstandard §12.8.17.2 | target-typed `new()` is a construction of the declared type | `target-typed-new` |
| csharpstandard §13.9.5 | `foreach` gives the element type | `foreach-typed`, `foreach-var` |
| csharpstandard §12.12.12 | `is T x` and `as T` bind and convert | `is-pattern`, `as-cast`, `is-as-catch` |
| csharpstandard §12.9.7 | a cast is a value of its type | `cast` |
| csharpstandard §12.9.8 | `await` gives what a `Task<T>` holds | `await-task` |
| csharpstandard §15.7 | a property's type, and the members its body uses | `property-type`, `property-body-is-the-property` |
| csharpstandard §12.8.4 | a method group passed as a value | `method-group` |
| csharpstandard §15.11.2 | `: base(…)` / `: this(…)` call a constructor | `constructor-initializer` |
| csharpstandard §15.16 | a record is a class | `record` |
| csharpstandard §12.8.4 | `dynamic` binds at run time | not followed: no static answer: a member of a `dynamic` value names nothing |
| csharpstandard §12.20 | a lambda's parameter typed by the delegate it is converted to | not followed: phase 3: inference from the target type |

18 cases were written from this reading (`found_by("source")`, each with the
section and the Roslyn function it was read from), all accepted by the C#
compiler. Four did not hold, and engine 0.5.64 follows them:

- **Overloads by a predefined type.** A parameter written `int`, `string` or
  `object` had no type path, so every overload of `Write("x")` scored alike
  and each was an `INFERRED` edge. The written type is the type now, for a
  literal argument (a value of a project type can still convert through an
  `implicit operator`, so a path argument is not ruled out by it).
- **`params` in normal form first** (§12.6.4.3): a candidate applicable only
  with its `params` expanded ranks below one that takes the arguments as
  written. This holds for Java's and Kotlin's varargs too: Kotlin's
  `ClassName("p", "A", "B")` is the `(String, String, vararg String)`
  constructor, not `(String, vararg String)`.
- **The closest namespace's extensions** (§12.8.10.3): of extensions written
  for the type, those of the innermost namespace around the use.
- **A `using` inside a namespace block** (§14.5.1): a type of another
  namespace block of the same file is not a name of the block that uses it.

Measured with engine 0.5.64: Roslyn's precision on the four C# audit repositories is 98.0% (10,706 of 10,930 edges it has an answer for; 10,543 of 10,767 with 0.5.63: 163 edges more, none more wrong), its recall 79.9%; the Kotlin and Swift compilers' tests give 99.4% and 94.0% as before, and `verify_graphify.py` lacks 44 confirmed edges, as before. On the 45 audit repositories about 190 calls change: an `INFERRED` tie among overloads becomes the one overload (Retrofit's `buildRequest(…)`, kotlinpoet's `ClassName(…)`, RestSharp's `AddHeader(…)`), and a construction whose constructors all tied is linked to the one it calls (RestSharp's `new RestRequest("…")`, 160 sites).

### Phase 2: Rust

The Rust Reference (`rust-lang/reference`, `src/names/`, `src/paths.md`,
`src/items/use-declarations.md`, `src/expressions/method-call-expr.md`) states
its rules with identifiers (`r[names.scopes.items.nested-modules]`), which the
cases cite. It leaves primary and type-relative resolution as placeholders:
for those the source is rustc's resolver (`compiler/rustc_resolve`) and its
method probe (`compiler/rustc_hir_typeck/src/method/probe.rs`, inherent
candidates before trait candidates). `benchmarks/rule_inventory.py` lists
every rule (38 rules, 31 held by a case, 7 not followed; 15 of 70 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| r[names.scopes.items.module] | an item of a module is a name in the whole module | `use-path`, `nested-module-path` |
| r[names.scopes.items.statement] | an item declared in a block is a name of the block | `item-in-a-block` |
| r[names.scopes.items.nested-modules] | the items of a module around are no names of a nested module | `outer-module-items-not-in-scope` |
| r[names.scopes.associated-items.scope] | associated items are named by a path from their type or trait, never bare | `associated-items-are-not-scoped`, `associated-function`, `self-type` |
| r[names.scopes.pattern-bindings.let] | a `let` binding is a name from after it to the end of its block | `let-shadows-from-where-it-is`, `block-scope`, `shadowing` |
| r[names.scopes.pattern-bindings.parameter] | a parameter is a name of the function's body | `reference-parameter`, `method-on-local` |
| r[names.scopes.pattern-bindings.closure] | a closure's parameter is a name of the closure's body | `closure-body` |
| r[names.scopes.pattern-bindings.loop] | a `for` binding is a name of the loop body | `vec-iteration` |
| r[names.scopes.pattern-bindings.let-chains] | an `if let` / `while let` binding is a name of the conditions after it and the block | `if-let-some` |
| r[names.scopes.pattern-bindings.match-arm] | a `match` arm's binding is a name of that arm | `match-arm-binding` |
| r[names.scopes.pattern-bindings.shadow] | a binding shadows a function of its name | `untyped-let-hides-a-function`, `let-shadows-from-where-it-is` |
| r[names.scopes.generic-parameters.param-list] | a generic parameter is a name in the item it is declared on | `generic-parameter-hides-a-type`, `generic-bound`, `generic-bound-where` |
| r[names.scopes.lifetimes] | lifetimes and loop labels | not followed: they name no definition of the graph |
| r[names.scopes.prelude.layers] | the preludes, which a module's own names shadow | `option-unwrap`, `question-mark`, `smart-pointer` |
| r[names.scopes.macro_rules] | `macro_rules!` textual and path scopes | not followed: macros are not expanded: a macro's arguments are read as expressions (`rust/macro-arguments`), its definition names nothing |
| r[names.scopes.derive] | derive helper attributes | not followed: attributes are macro input: a name in `#[derive(…)]` is no value read |
| r[names.scopes.self.def-scope] | `Self` in a struct, enum, trait or impl is the type | `self-type-in-a-struct`, `self-in-a-trait`, `result-of-self` |
| r[names.scopes.self.impl-scope] | `Self(…)` / `Self { … }` constructs the implementing type | `self-type` |
| r[names.namespaces.kinds] | types, values and macros are separate namespaces: `mod log` beside `fn log` | `module-and-function-namespaces`, `tuple-struct-constructor`, `enum-variant` |
| r[names.namespaces.without.fields] | a field is named only by a field expression on its type | `field-not-method`, `field-beside-a-method`, `field-type` |
| r[names.resolution.expansion.imports.shadowing] | a `use` of an inner scope shadows one of an outer scope | `inner-use-shadows-outer` |
| r[items.use.glob.shadowing] | an item or a named import shadows what a glob import brings | `glob-shadowed-by-an-item`, `named-import-shadows-a-glob` |
| r[items.use.forms-glob] | `use m::*;` brings m's public names | `glob-import`, `use-super-glob` |
| r[items.use.forms-as] | `use m::f as g;` binds g | `use-alias` |
| r[items.use.self] | `use a::{self, f};` binds the module and the name | `use-self-in-braces` |
| r[items.use.visibility] | `pub use` re-exports | `re-export` |
| r[items.use.path.edition2018] | since 2018 a `use` path starts at a name in scope, or a crate | `uniform-paths`, `another-crates-name` |
| r[names.resolution.expansion.imports.ambiguity] | ambiguous glob imports are errors | not followed: an error is no binding |
| r[paths.qualifiers] | `crate::`, `self::`, `super::` paths | `qualified-path`, `super`, `self-path`, `crate-root-named-by-the-manifest` |
| r[paths.qualified] | `<T as Trait>::f` qualified paths | not followed: not read yet: the trait named in the path is not taken as the receiver's |
| r[expr.method.candidate-search] | a method of the type's own `impl` before a trait's | `inherent-method-first` |
| r[expr.method.candidate-search] | a value of a type parameter has the methods of the traits that bound it | `generic-bound`, `generic-bound-where`, `dyn-trait` |
| r[expr.method.candidate-receivers] | the receiver is dereferenced: `&T`, `Box<T>`, `Arc<T>` find T's methods | `smart-pointer`, `reference-parameter` |
| r[expr.method.candidate-search] | a trait's method is found only when the trait is in scope | not followed: code that compiles has the trait in scope; the engine takes it as given |
| r[expr.method.candidate-search] | a trait's default method is a method of every implementor | `trait-default-method`, `trait-method` |
| r[items.impl.inherent] | an `impl` may be written in another module than its type | `impl-in-another-file`, `impl-of-the-files-own-type` |
| r[expr.field] | `x.0` of a tuple struct | `tuple-struct-field`, `tuple-field-types` |
| r[type.closure] / typeck | a closure's parameter typed by the iterator or option it is called on | not followed: phase 3: inference from the call it is passed to |

15 cases were written from this reading, all accepted by rustc. Six did not
hold, and engine 0.5.65 follows them:

- **A `match` arm's binding is the arm's**
  (`r[names.scopes.pattern-bindings.match-arm]`): it was a local of the whole
  block, so `Some(size) => size, None => size()` hid the function in the
  other arm.
- **`Self` in a struct's own fields** (`next: Option<Box<Self>>`) is the
  struct.
- **A module and a function of one name** are in two namespaces: `log::write()`
  is the module's, `log()` the function.
- **A `use` in a block holds in the block** and shadows the module's `use` of
  the name; the files it names count as imported.
- **A glob import brings names the file never writes**: a capitalized name
  of a file with a `use m::*;` is kept for resolution (it was dropped as
  undeclared), and the module's own item still shadows the glob's.
- **2018 paths**: `use m::f;` inside `mod a { … }` is `a`'s own `m` first;
  an item of an inline module is no name outside it (an `impl`'s type is
  looked up from where the `impl` is written).

Reading the audit repositories' changes found two more things, fixed with
them: a name in `#[derive(…)]` is macro input, never a value read (`Debug`
was linked to a project struct named `Debug`), and the `use` inside a test
function (`use grep_regex::RegexMatcherBuilder;`) picks that crate's type
where two crates of the workspace define one.

Measured with engine 0.5.65: rust-analyzer's precision on the four Rust audit repositories is 98.5% (12,612 of 12,802), recall 83.4%, as before; `verify_graphify.py` lacks 44 confirmed edges, as before. On the 45 audit repositories 13 edges change, all in ripgrep and ureq: the `use` in a function picks the type it imports (`Separator::new` is `FieldContextSeparator::new` there), `std::os::unix::fs::symlink` imported in a test is no longer a call of the test's own `symlink`, and a `match` arm's `v` is no longer read as the parameter `v`.

### Phase 2: Go

The Go specification (`doc/go_spec.html`: Blocks, Declarations and scope,
Selectors, Import declarations, Method expressions and values) and go/types
(`lookup.go` `LookupFieldOrMethod`, `scope.go` `LookupParent`, `resolver.go`).
`benchmarks/rule_inventory.py` lists every rule (29 rules, 23 held by a case, 6 not followed; 15 of 53 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| spec Declarations_and_scope | a top-level constant, type, variable or function is a name of the package block, in every file | `package-scope`, `test-file` |
| spec Declarations_and_scope | a method is no name of the package block | `method-is-no-package-name` |
| spec Declarations_and_scope | an imported package's name is a name of the file block of the importing file | `imported-package`, `import-alias` |
| spec Declarations_and_scope | a receiver, parameter or result is a name of the function body | `parameter-hides-a-function`, `value-receiver` |
| spec Declarations_and_scope | a type parameter of a function is a name from its name to the end of its body | `type-parameter-hides-a-type`, `generic-constraint` |
| spec Declarations_and_scope | a variable or constant of a function is a name from the end of its declaration to the end of the block | `short-var-scope-starts-after`, `local-hides-function`, `short-var-hides-package-name` |
| spec Declarations_and_scope | a type of a function is a name from its identifier to the end of the block | `local-type-hides-a-type` |
| spec Declarations_and_scope | a name redeclared in an inner block denotes the inner one only there | `block-redeclaration-ends-with-the-block` |
| spec Blocks | `if`, `for`, `switch` and each clause of a `switch` or `select` are blocks | `switch-clause-is-a-block` |
| spec Label_scopes | labels are function-wide and not block scoped | not followed: labels name no definition of the graph |
| spec Predeclared_identifiers | the universe block: `len`, `append`, `error`, … | not followed: predeclared names are no project definitions; a project's own `len` shadows them as any package name does |
| spec Exported_identifiers | only an exported name of another package is reachable | not followed: code that compiles uses only exported names: the engine does not check |
| spec Import_declarations | an import's name defaults to the imported package's clause | `package-name-from-the-clause` |
| spec Import_declarations | `import . "p"` declares p's exported names in the file block | `dot-import` |
| spec Import_declarations | `import _ "p"` binds nothing | not followed: it binds no name to resolve |
| spec Selectors; go/types LookupFieldOrMethod | `x.f` is the field or method at the shallowest depth | `shallowest-depth`, `shallower-embedding-wins` |
| spec Selectors | an embedded type's fields and methods are promoted | `embedded-struct`, `promoted-field`, `embedded-interface` |
| spec Selectors | a method of an interface value is the interface's method | `interface-method` |
| spec Calls | a `*T` method is called on an addressable `T` | `pointer-method-on-a-value` |
| spec Method_expressions | `T.M` is a use of the method | `method-expression` |
| spec Method_values | `x.M` without a call is a use of the method | `method-value`, `method-value-argument` |
| spec Selectors | two fields or methods at the same shallowest depth make the selector illegal | not followed: an error is no binding |
| spec Short_variable_declarations | `x, err := f()` gives each name its result's type | `multiple-results`, `every-result-type` |
| spec For_range | a `range` value is the element type | `range-slice` |
| spec Index_expressions | an element of a slice or map | `slice-index`, `map-value` |
| spec Type_assertions | `x.(T)` is a T; a type switch's `case T` names T | `type-assertion`, `type-assertion-and-switch` |
| spec Type_declarations | a defined type's methods, and what it is defined as | `named-slice-type`, `defined-type-names-its-type` |
| spec Function_literals | a function literal sees the package and names its signature's types | `closure`, `function-literal-signature` |
| go/types infer.go | a function argument's type inferred for a generic call | not followed: phase 3: type inference is not modelled |

15 cases were written from this reading, all accepted by the Go compiler.
Two did not hold, and engine 0.5.66 follows them:

- **The shallowest depth** (Selectors): of two embedded types with a method
  of one name, the one embedded less deep is meant; the lookup went depth
  first and found the deeper one. It now goes level by level, as
  `lookupFieldOrMethodImpl` does.
- **Each `switch` clause is a block** (Blocks): `helper := 2` in one `case`
  hid the function `helper` in the next.

Measured with engine 0.5.66: `go/types` gives 12,279 of 12,284 (as before),
recall 84.0%; `verify_graphify.py` lacks 44 confirmed edges, as before; the
45 audit repositories' own-code edges do not change. Go was already the
closest to whole: the reading found two rules missing, and neither occurs in
the four Go audit repositories.

### Phase 2: TypeScript and JavaScript

TypeScript has no current specification, so the source is the compiler: the
name resolver of its Go port (`tsc/internal/binder/nameresolver.go`,
`NameResolver.Resolve`, which walks the scopes from the use outwards with a
`meaning` of type, value or namespace), module resolution
(`tsc/internal/module`), and ECMAScript's own rules for `var`, function
expressions and `catch`. `benchmarks/rule_inventory.py` lists every rule
(31 rules, 26 held by a case, 5 not followed; 7 of 58 cases found in the language's own tools). The TypeScript compiler's test baselines had already been the
oracle for engine 0.5.52–0.5.54, so most rules were held before this reading.

| Source | Rule | Held by / why not |
|---|---|---|
| nameresolver.go Resolve: locals | a block's `let` / `const` / class is a name of the block | `block-scope`, `block-scope-branches`, `block-scope-file-level` |
| nameresolver.go Resolve: function-like | a function's type parameters are names of its whole declaration | `type-parameter-hides`, `type-parameter-bound` |
| nameresolver.go Resolve: function-like | a parameter is a name of the body and the parameter list | `parameter-called`, `parameter-hides-import` |
| nameresolver.go Resolve: function-like | an expression in a parameter list does not see the body's declarations | not followed: tsc rejects the program that would tell (TS2373); nothing to bind differently |
| nameresolver.go Resolve: KindSourceFile / KindModuleDeclaration | a module's exports are names inside it; an export specifier is not | `namespace-member-inside`, `namespace` |
| nameresolver.go Resolve: KindEnumDeclaration | an enum's members are names inside it | `enum-member`, `enum-member-value` |
| nameresolver.go Resolve: KindClassDeclaration | a class's members are no names inside its methods; only its type parameters are | `class-members-are-not-names`, `this-method` |
| nameresolver.go Resolve: KindExpressionWithTypeArguments | a class's heritage sees its own type parameters | `generic-argument` |
| nameresolver.go Resolve: KindDecorator | a decorator is resolved at the class declaration | `decorator-resolved-at-the-class` |
| nameresolver.go Resolve: KindCatchClause | a `catch` binding is the catch block's | `catch-binding-is-the-blocks` |
| nameresolver.go Resolve: KindInferType | an `infer T` is a name of the true branch of the conditional type | `type-alias-body` |
| nameresolver.go Resolve: meaning | a lookup for a type passes over values of the name, and for a value over types | `type-meaning-skips-values`, `type-and-value`, `typeof-type` |
| nameresolver.go Resolve: lookup / merging | declarations of one name merge: interfaces, a function or class with a namespace | `declaration-merging`, `function-namespace-merge` |
| nameresolver.go Resolve: KindExportSpecifier | `export { x as y }` binds y for importers only | `re-export`, `default-reexport` |
| nameresolver.go Resolve: globals | the global scope and `declare global` augmentations | not followed: library globals name no project definition; a project's own `declare global` is not read |
| checker getPropertyOfType | a member of a type, through what it extends and implements | `inherited-method`, `interface-extends`, `implements`, `abstract-base` |
| checker getTypeOfSymbol | a declared type before the value's | `local-typed-by-declaration`, `typed-field`, `readonly-field` |
| checker getReturnTypeOfSignature | a call's declared result | `return-type`, `await-promise` |
| checker narrowing | `as`, `!`, `instanceof` and optional chaining keep or set the type | `as-cast`, `non-null`, `as-and-instanceof`, `optional-chaining` |
| checker overload resolution | a call of an overloaded function picks the signature its arguments fit | not followed: TS overloads are signatures of one implementation: the edge is to the function either way |
| checker contextual typing | a callback's parameter typed by the parameter it is passed for | not followed: phase 3: inference from the call it is passed to |
| module resolver | a relative import with or without its extension, a directory's `index` | `barrel`, `named-import` |
| module resolver | a directory's `package.json` `types` / `main` | `package-json-types-entry` |
| module resolver tryLoadModuleUsingPathsIfEligible | `paths` in `tsconfig.json` | `tsconfig-paths` |
| module resolver | `import x = require()`, `export =` | `import-equals-require`, `export-assignment` |
| module resolver | `node_modules` packages | not followed: a library's sources are not read: its names are external |
| ECMAScript FunctionDeclarationInstantiation | a function declaration is hoisted to its scope | `hoisting` |
| ECMAScript VarDeclaredNames | a `var` is the function's, whatever block it is written in | `var-is-the-functions` |
| ECMAScript InstantiateOrdinaryFunctionExpression | a function expression's name is its own body's | `named-function-expression` |
| ECMAScript CommonJS (Node) | `require`, `module.exports`, `exports.x` | `commonjs-destructure`, `commonjs-default`, `commonjs-member` |
| ECMAScript prototypes | `F.prototype.m = function` is a method of `new F()` | `prototype-method` |

10 cases were written (one more, a parameter default that would see the
body's declarations, was dropped: tsc rejects the program, TS2373). Three did
not hold, and engine 0.5.67 follows them:

- **A type position passes over values** (the resolver's `meaning`):
  `const Repo = 1` beside `interface Repo` no longer hides the interface in
  `const other: Repo = r`. On zod, the only audit repository it changes,
  403 `EXTRACTED` references come and 61 `INFERRED` ones go: a type argument
  (`util.assertEqual<t1, …>`) was linked through the value `t1` to the
  schema class it was built with, and is now the type alias `t1` the test
  declares; `core.$constructor<ZodType>` names the interface, not the
  `const` of that name.
- **`typeof x` in a type names the value** `x`, as before; it is resolved
  with a value meaning now that type positions have one of their own.
- **Interfaces of one name merge** (declaration merging): a member of either
  declaration is a member of the interface.

The other cases (`var` is the function's, a function expression's name is
its own, a `catch` binding, a decorator resolved at the class, class members
are no bare names, `paths`, a directory's `package.json`) held already.

Measured with engine 0.5.67: the TypeScript compiler's baselines give 99.5%,
recall 86.4%, as before; `verify_graphify.py` lacks 44 confirmed edges, as
before.

### Phase 2: Java

The JLS (6.3 scope, 6.4 shadowing and obscuring, 7.5 imports, 15.12 method
invocation) and javac's resolver (`com/sun/tools/javac/comp/Resolve.java`:
`findVar` looks in each environment's scope and then the class's fields,
inherited ones included, before going out a class; `findFun` the same for
methods; `findType` type variables, member types, then the file's imports
and package; `findMethod` in three phases, then `mostSpecific`).
`benchmarks/rule_inventory.py` lists every rule (30 rules, 26 held by a case, 4 not followed; 14 of 62 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| JLS 6.3; Resolve findVar | a local or parameter is a name from its declaration to the end of its block | `local-hides-field`, `for-each`, `try-with-resources`, `instanceof-pattern` |
| JLS 15.12.1; Resolve findFun | a bare method name is a method of the innermost class that has one of the name, inherited ones counted | `implicit-this`, `inherited`, `inherited-before-enclosing`, `outer-method` |
| JLS 6.4.1; Resolve findVar | a bare field name: the class's own and inherited fields before the class around | `inherited-field-before-enclosing`, `this-field` |
| JLS 9.3, 8.3; Resolve findField | a constant of an implemented interface is inherited | `inherited-interface-constant`, `interface-constant-type` |
| JLS 6.4.1; Resolve findFun | a method of the class shadows a static import of the name | `own-method-before-static-import`, `static-import` |
| JLS 6.4.1; Resolve findType | type variables, then member types (own and inherited) of each class around, then the file's | `method-type-parameter-hides-a-class`, `member-type-before-import`, `inherited-member-type`, `nested-static-class` |
| JLS 6.3 | a local class is a name of the rest of its block | `local-class` |
| JLS 6.4.1; Resolve findType | a single-type import shadows a type of the package | `single-type-import-before-package`, `single-type-import` |
| JLS 7.5.2; Resolve findType | a type of the package shadows an on-demand import | `package-before-on-demand-import`, `same-package`, `on-demand-import` |
| JLS 7.4.3 | a class of another package is not in reach without an import or a qualified name | `other-package-not-in-reach`, `fully-qualified` |
| JLS 6.4.2 obscuring | a variable obscures a type, a type obscures a package of its name | not followed: code that compiles is written around it; no case tells a variable from a type there yet |
| JLS 15.8.4 | `Outer.this` is the object of the class named | `qualified-this` |
| JLS 15.11.2 | `super.m()`, `super(…)`, `this(…)` | `super`, `explicit-constructor-call` |
| JLS 15.12.2.1 | a candidate takes the call's number of arguments | `overload-arity` |
| JLS 15.12.2.2; Resolve findMethod | phase 1: applicable without boxing | `no-boxing-before-boxing` |
| JLS 15.12.2.4 | phase 3: variable arity last | `fixed-arity-before-varargs` |
| JLS 15.12.2.5; Resolve mostSpecific | the most specific of the applicable: a subtype, a narrower primitive | `most-specific-reference-type`, `overload-by-type` |
| JLS 15.12.2.5 | a floating literal's `float` / `double` overload | not followed: the facts tell an integer literal from another number, not `1.0f` from `1.0`: such ties are INFERRED |
| JLS 15.12.2.7 | a generic method's type arguments inferred from the call | not followed: phase 3: inference |
| JLS 8.4.8 | an interface's default method is inherited | `interface-default`, `interface-typed` |
| JLS 15.12.3 | a static method through its class or interface | `static-via-class`, `interface-static` |
| JLS 14.4.1 | `var` is the type of its initializer | `var` |
| JLS 15.16 | a cast is a value of its type | `cast`, `instanceof-cast-catch` |
| JLS 15.13 | a method reference is a use of the method | `method-reference` |
| JLS 15.27 | a lambda's body sees the method around | `lambda-body` |
| JLS 15.9.5 | an anonymous class sees the class around | `anonymous-class` |
| JLS 8.9 | an enum constant has the enum's methods and is a use of it | `enum-method`, `enum-constant-value` |
| JLS 8.10 | a record is a class with its components | `record` |
| JLS 4.5 | a member of a parameterized type with its arguments | `generic-argument`, `list-element`, `chained-result` |
| JLS 6.6 | access control (`private`, package-private) | not followed: code that compiles reaches only what it may: the engine does not check |

14 cases were written from this reading, all accepted by javac. Three did not
hold, and engine 0.5.68 follows them:

- **`Outer.this`** (15.8.4): `A.this.size()` in an inner class is `A`'s
  method; it was read as a member `self` of the class, and named nothing.
- **An interface's fields are constants** (9.3): the reader took state only
  from `field_declaration`, so `int MAX = 10;` in an interface was no
  definition and `MAX` in an implementing class read nothing.
- **Phase 1 and specificity for numbers** (15.12.2.2, 15.12.2.5): a number
  literal fits a primitive parameter before a boxed one (`put(long)` over
  `put(Integer)`), and of the primitives an integer literal widens to the
  narrowest is the most specific (`int`, `long`, `float`, `double`). Java's
  primitive parameter types have no type path, as C#'s predefined ones, so
  `fit` reads their written text for a literal. The facts now tell an
  integer literal (`Arg::Int`) from another number.

On the 45 audit repositories only gson and retrofit change: 43 `EXTRACTED`
calls of gson's `JsonWriter.value(…)` that went to the `Number` overload now
go to `long` for an integer literal, or are `INFERRED` ties among `float` and
`double` for `1.0` / `1.0f` (the facts do not tell those apart);
`ParameterHandler.this.apply(…)` is a call; `ReflectionAccessFilter.BLOCK_ALL_JAVA`
is read. `verify_graphify.py` lacks 44 confirmed edges, as before.

### Phase 2: Kotlin

The Kotlin specification (`docs/src/md/kotlin.core/overload-resolution.md`:
'Building the overload candidate set' gives, for a call with and without an
explicit receiver, the ordered sets of candidates, and the first set with an
applicable one wins; `scoping.md`; `operators.md`). The K2 resolver
(`compiler/fir`) follows the same order. `benchmarks/rule_inventory.py` lists
every rule (24 rules, 20 held by a case, 4 not followed; 13 of 53 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| scoping.md 'Linked scopes' | a declaration of an inner scope shadows an outer one | `function-beside-parameter`, `local-typed-by-value` |
| overload-resolution.md 'Call without an explicit receiver' 1 | local functions first | `local-function-before-member` |
| overload-resolution.md 'Call without an explicit receiver' 2 | then members of the implicit receivers, innermost first | `member-before-top-level`, `inherited`, `receiver-lambda`, `with` |
| overload-resolution.md 'Call without an explicit receiver' 3 | then top-level: imported by name, same package, star-imported, default imports | `explicit-import-before-package`, `package-before-star-import`, `same-package`, `import` |
| overload-resolution.md 'Call with an explicit receiver' 1 | members of the receiver's type before any extension | `member-wins`, `member` |
| overload-resolution.md 'Call with an explicit receiver' 2–5 | extensions: local, imported by name, package, star-imported | `local-extension-first`, `imported-extension-before-package`, `extension` |
| overload-resolution.md 'Call with an explicit receiver' (member extensions of implicit receivers) | an extension declared as a member of a class in scope | not followed: a member extension is read as a member of its class only |
| overload-resolution.md 'Call with an explicit type receiver' | `T.f()`: static members, then the companion object's | `companion`, `object` |
| overload-resolution.md 'Call with an explicit super-form receiver' | `super.f()`, `super<A>.f()` | `super`, `super-of-a-named-supertype` |
| overload-resolution.md 'Infix function call' | `a foo b` is `a.foo(b)` of an `infix fun` | `infix-call` |
| overload-resolution.md 'Operator call'; operators.md | `a + b`, `a[i]` are the operator functions of a's type | `operator-call`, `index-operator` |
| overload-resolution.md 'Operator call' | `+=`, `in`, `..`, comparison and `invoke` conventions, delegated properties | not followed: only the arithmetic and indexing conventions are read yet |
| overload-resolution.md 'Callables and invoke convention' | a property of a function type called is the property | `property-invoke` |
| overload-resolution.md 'Call with named parameters' | a named argument is a parameter of the callee | `named-argument-label` |
| overload-resolution.md 'Choosing the most specific candidate' | the candidate with the more specific parameter types | `most-specific-type` |
| overload-resolution.md 'Algorithm of MSC selection' | a candidate without `vararg` expansion or defaults wins a tie | `non-vararg-before-vararg` |
| overload-resolution.md 'Using lambda return type to refine function applicability' | overloads told apart by a lambda's result | not followed: phase 3: the lambda's result type is not inferred |
| overload-resolution.md 'Resolving callable references' | `::f` is a use of f | `function-reference` |
| overload-resolution.md 'Resolving property access' | a property through its receiver, a constructor `val` | `constructor-property`, `constructor-property-read`, `property-type` |
| type-inference.md / cdfa.md | smart casts after `is`, `as`, safe calls | `is-and-as`, `when-is`, `cast`, `safe-call` |
| declarations.md 'Type alias' | a typealias names its type | `typealias`, `typealias-body` |
| declarations.md 'Enum class declaration' | an enum entry has the enum's members | `enum-entry` |
| builtins / standard library | scope functions: `let`, `apply`, `also`, `with`, `run` | `let`, `apply-also`, `it` |
| declarations.md 'Visibility' | `private`, `internal`, `protected` | not followed: code that compiles reaches only what it may: the engine does not check |

13 cases were written from this reading, all accepted by kotlinc. Six did not
hold, and engine 0.5.69 follows them:

- **Operator and infix calls** ('Operator call', 'Infix function call'):
  `a + b` is `a.plus(b)`, `a[i]` is `a.get(i)`, `r upTo 10` is `r.upTo(10)`.
  The reader read none of them as calls; it now writes a `Call` ref with the
  context `operator`, kept only when a's type gives the function (never a
  guess by its name).
- **`super<A>.f()`** is A's member; `super<A>` was read as plain `super`.
- **The order of extensions**: one declared in a function around the use,
  then one imported by name, then one of the file's package, then a
  star-imported one (`kotlin_extension_order`).

Measured with engine 0.5.69: the Kotlin compiler's resolver tests give
99.4%, recall 88.7%, as before; `verify_graphify.py` lacks 44 confirmed
edges, as before. On the 45 audit repositories kotlinpoet gains three
`get` calls through `typeParamResolver[name]`, and clikt loses four
`INFERRED` reads on lines written with Kotest's infix `shouldBe`.

### Phase 2: Swift, C and C++

Swift: the compiler's unqualified lookup (`lib/AST/UnqualifiedLookup.cpp`,
`ASTScopeLookup.cpp`: local scopes, then the members of each type around,
then the module and what it imports) and its ranking of overloads
(`lib/Sema/CSRanking.cpp`). C and C++: the standards' lookup rules (C 6.2.1–
6.2.3; C++ [basic.lookup], [class.member.lookup], [namespace.*],
[over.match.best], [over.ics.rank]) and clang (`SemaLookup.cpp`,
`SemaOverload.cpp`). `benchmarks/rule_inventory.py` lists every rule (Swift:
16 rules, 13 held by a case, 3 not followed; 9 of 52 cases found in the language's own tools; C and C++: 21 rules, 17 held by a case, 4 not followed; 9 of 45 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| ASTScopeLookup | local declarations (a function, a `let`, a closure's parameters) first | `local-function-before-member`, `nested-function`, `closure-parameter-hides-a-function`, `own-value-is-outer` |
| UnqualifiedLookup lookInMembers | then members of each type around (implicit `self`) | `member-before-global`, `implicit-self`, `inherited` |
| UnqualifiedLookup module scope | then the module's top-level names, then imported modules' | `method-on-parameter` |
| TSPL 'Extensions' | an extension's members are the type's | `extension`, `conformance-in-extension` |
| TSPL 'Protocols' | a protocol's requirements and extensions | `protocol-typed`, `protocol-extension`, `generic-bound` |
| CSRanking isDeclAsSpecializedAs | a concrete type's member over a protocol extension's | `concrete-before-protocol-extension` |
| TSPL 'Functions' | argument labels are part of a function's name | `overload-by-label` |
| CSRanking isDeclAsSpecializedAs | a more specific parameter type; a non-generic candidate | `overload-specific-type`, `non-generic-before-generic` |
| CSRanking compareSolutions | non-variadic and fewer default arguments | `non-variadic-before-variadic`, `fewer-defaults` |
| TSPL 'Implicit Member Expression' | `.name` where a type is expected | `implicit-member`, `implicit-member-beside-a-value` |
| TSPL 'Optional Chaining', 'if let' | unwrapping keeps the type | `optional-chaining`, `if-let`, `guard-let` |
| TSPL 'Type Casting' | `as`, `as?`, `is`, `catch … as` | `as-cast`, `is-and-as` |
| TSPL 'Initialization' | `T(…)` and `Self()` call an initializer | `initializer`, `self-constructs`, `generic-construction` |
| TSPL 'Access Control' | `private`, `fileprivate`, `internal` | not followed: code that compiles reaches only what it may |
| UnqualifiedLookup (weak self) | `guard let self` rebinding in closures | not followed: `self` names the type around either way |
| CSGen / CSSolver | a closure's parameter typed by the parameter it is passed for | not followed: phase 3: inference |

| Source | Rule | Held by / why not |
|---|---|---|
| C 6.2.1 | an identifier is in scope from its declaration; a block's hides the file's | `same-file`, `local-hides-function`, `forward-declaration` |
| C 6.2.2 | linkage: `static` is the file's, `extern` another file's | `static-wins`, `external-linkage`, `extern-variable` |
| C 6.2.3; SemaLookup LookupTagName | struct tags are a name space of their own | `tag-name-space`, `typedef-struct` |
| [basic.lookup.unqual] | block, then class (and bases), then namespaces outwards | `implicit-this`, `inherited`, `member-before-namespace`, `enclosing-namespace` |
| [class.mem] | a member function's body sees every member of its class | `member-declared-later` |
| [class.member.lookup] | a derived class's member hides the base's of its name | `derived-member-hides-base` |
| [namespace.udecl] | `using Base::f;` and `using ns::f;` | `using-declaration-of-a-base-member`, `using-declaration` |
| [namespace.udir] | `using namespace ns;` | `using-namespace` |
| [namespace.qual] | `ns::f`, `::f` | `namespace-qualified`, `global-scope-operator` |
| [namespace.def] | inline and unnamed namespaces | `inline-namespace`, `anonymous-namespace` |
| [namespace.alias] | `namespace u = util;` | `namespace-alias` |
| [basic.lookup.argdep] | argument-dependent lookup | `argument-dependent-lookup` |
| [over.match.viable] | a candidate takes the call's number of arguments | `overload-arity` |
| [over.ics.rank]; SemaOverload CompareStandardConversionSequences | an exact match over a conversion | `exact-match-before-conversion` |
| [over.match.best]; SemaOverload isBetterOverloadCandidate | a non-template over a template specialization | `non-template-before-template` |
| [over.match.oper] | `a + b` is `a.operator+(b)` | `operator-call` |
| [over.match.oper] | a free `operator+(a, b)` | not followed: only member operators are read yet |
| [dcl.ambig.res] | `T x(f());` declares a variable when f() is no type | `vexing-parse`, `constructor-arguments` |
| [temp.deduct] | template arguments deduced from a call | not followed: phase 3: deduction is not modelled; `Page<Item>` written out is read |
| [class.access] | `private`, `protected` | not followed: code that compiles reaches only what it may |
| preprocessor | macros and conditional compilation | not followed: macros are not expanded; `#if` branches that agree are blanked to one (parse::blank_duplicate_branches) |

19 cases were written (9 Swift, 10 C / C++), all accepted by swiftc, cc and
c++. Swift held all but one; C and C++ six of ten. Engine 0.5.71 follows the
seven:

- **A local function's overloads are its own scope's** (Swift): a function
  declared in a method tied with the type's method of its name, so
  `h()` was an `INFERRED` call of both. Overloads are now of one scope (one
  parent) in every language, which also keeps `::Helper` and
  `app::Helper` apart.
- **C struct tags are a name space of their own** (C 6.2.3), and in C++ a
  function hides the class of its name: `stat(x)` beside `struct stat` is
  the function, `struct stat s` the struct (`rust_namespace_pick` now serves
  C and C++ too; an out-of-class constructor is no function beside the
  class).
- **`::f()`** is the global namespace's `f`; the reader dropped the `::`.
- **An inline namespace's members** are the enclosing namespace's
  (`FileFacts::inline_namespaces`).
- **`a + b`** is `a.operator+(b)` when a's class declares it (the Kotlin
  mechanism, context `operator`, for C++ member operators).
- **Overload ranking**: an integer literal matches an `int` parameter
  exactly and a string literal a `const char*`, before a conversion; of
  candidates that fit alike, one that is no template (C++) or not generic
  (Swift), only where every argument is a literal and the generic one's
  parameters are its type parameters themselves (`Show<T>(T x)`): an array
  literal or a value the walk cannot type may be the very `Sequence` or
  closure the generic one takes (Alamofire's `write { … }`, swift-argument-parser's
  `self.init([arg])`), and those stay `INFERRED` ties.

Reading the audit repositories' changes found one more: in C and C++ `p + 1`
may be a pointer moved along, so its kind is unknown, not a number (it had
picked leveldb's `Slice(const Slice&)` for `Slice(sep + 1)`).

Measured with engine 0.5.71: the Swift index tests give 94.0%, the Kotlin
tests 99.4%, Roslyn 98.0%, all as before; `verify_graphify.py` lacks 44
confirmed edges, as before. On the 45 audit repositories leveldb's twelve
`Slice("…")` constructions go from `Slice(const std::string&)` to
`Slice(const char*)`, cpr's thirteen `Redirect(false)` / `Redirect(0L)` are
linked to the constructor of that type, and three edges into libraries
(`::crc32c::Extend`, Windows' `::LockFile` / `::UnlockFile`) no longer name a
project function of the name.

### Phase 2: Python, PHP and Ruby

These have interpreters, not binders that check a program before it runs, so
the sources are the languages' own references, with the interpreter's
symbol table where there is one: Python's Language Reference ('Naming and
binding', 'The import statement') and CPython's `Python/symtable.c`; PHP's
language specification (`spec/18-namespaces.md`, `14-classes.md`,
`16-traits.md`); Ruby's documentation and its method and constant tables
(`class.c` `include_modules_at`, `vm_insnhelper.c` `vm_get_ev_const`).
`benchmarks/rule_inventory.py` lists every rule (Python: 15 rules, 14 held by a case, 1 not followed; 8 of 46 cases found in the language's own tools; PHP: 12 rules, 10 held by a case, 2 not followed; 3 of 37 cases found in the language's own tools;
Ruby: 10 rules, 9 held by a case, 1 not followed; 5 of 34 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| 4.2.2; symtable analyze_name | a name bound in a block is local to all of it | `local-hides-global`, `bound-later-is-local` |
| 4.2.2 | free names: the enclosing functions, then the module, then builtins | `enclosing-scope`, `module-function` |
| 4.2.2 | a class body's names are no names of its methods | `class-scope-not-in-methods`, `class-body-call` |
| 4.2.2; symtable DEF_GLOBAL / DEF_NONLOCAL | `global` / `nonlocal` make an assignment bind the outer name | `global-statement` |
| 4.2.1 | a lambda's and a comprehension's names are their own | `lambda-parameter-hides`, `comprehension` |
| 4.2.2 'Annotation scopes' | PEP 695 type parameters | `pep695-type-parameter` |
| 7.11 'The import statement' | `import a.b`, `from m import f as g`, relative imports | `from-import`, `import-alias`, `relative-import`, `module-attribute`, `module-alias`, `submodule-import` |
| 7.11 | `from m import *`: `__all__`, else the names without `_` | `star-import`, `star-import-respects-all`, `star-import-skips-underscore` |
| 7.11 | an import inside a function binds there | `function-local-import` |
| 5.4 packages | a name a package's `__init__` imports is the package's | `package-reexport` |
| 3.3.2 / MRO | attributes through the C3 method resolution order | `inherited-method`, `mro-first-base`, `mro-second-base`, `super` |
| 3.2 'Custom classes' | an instance attribute, else the class's | `self-method`, `class-attribute-through-self`, `attribute-assigned-in-init` |
| descriptors | `@property`, `@classmethod`, `@staticmethod` | `property`, `classmethod-via-class`, `staticmethod-via-self` |
| typing (PEP 484) | annotations type parameters, results and attributes | `annotated-parameter`, `return-annotation`, `class-attribute-annotation` |
| runtime | `getattr`, `__getattr__`, monkey-patching, `exec` | not followed: no static answer |

| Source | Rule | Held by / why not |
|---|---|---|
| 18-namespaces 'Name Lookup' | `use` imports, aliases, groups, `use function` | `use`, `use-alias`, `group-use`, `use-function` |
| 18-namespaces 'Name Lookup' | `\N\C` fully qualified, `Sub\C` relative | `fully-qualified`, `qualified-relative`, `global-class-is-not-a-namespaced-one` |
| 18-namespaces 'Name Lookup' | an unqualified class is the current namespace's: no fallback | `same-namespace`, `class-name-has-no-global-fallback` |
| 18-namespaces 'Name Lookup' | an unqualified function: the namespace's, else the global one | `namespaced-function`, `function-fallback` |
| 14-classes | `$this`, `self::`, `static::`, `parent::` | `this`, `self-static`, `parent`, `parent-constructor`, `static-return` |
| 14-classes | members through the parent class and interfaces | `inherited`, `interface-typed`, `abstract-method` |
| 16-traits | a trait's methods; the class's own over the trait's over the parent's | `trait`, `trait-before-inherited` |
| 16-traits | `insteadof` and `as` resolve trait conflicts | `trait-insteadof` |
| 14-classes | typed properties, promoted parameters, return types | `typed-property`, `constructor-promotion`, `return-type`, `nullable-parameter` |
| 10-expressions | `new`, `::class`, `instanceof`, `?->`, closures | `new`, `class-constant-fetch`, `instanceof-and-catch`, `nullsafe`, `closure-use`, `closure-signature` |
| 07-variables | a variable is the function's: no outer variable without `global` | not followed: variables name no definition of the graph |
| runtime | variable functions and methods (`$f()`, `$o->$m()`), `__call` | not followed: no static answer; `[$this, 'm']` is read |

| Source | Rule | Held by / why not |
|---|---|---|
| doc 'Assignment' | a local exists from its first assignment on; before it a bare name is a call | `local-only-after-assignment`, `local-hides-method`, `implicit-self` |
| doc 'Blocks' | a block's parameters are its own | `block-parameter-hides-a-method`, `block-body` |
| class.c Module#ancestors | the class, its modules from the last included, then the superclass | `class-before-included-module`, `last-include-first`, `include`, `superclass` |
| class.c rb_prepend_module | a prepended module before the class | `prepend` |
| class.c | `extend`, `def self.m`, `class << self`, `module_function` | `extend`, `class-method`, `singleton-class`, `module-function` |
| vm_insnhelper.c vm_get_ev_const | a constant: the lexical nesting, then the ancestors, then the top level | `lexical-constant`, `lexical-constant-before-ancestor`, `nested-constant` |
| doc 'Modules and Classes' | a class reopened in another file is one class | `reopened-class` |
| doc 'Calling Methods' | `super`, `&.`, predicate names, private calls | `super`, `super-with-arguments`, `safe-navigation`, `predicate-name`, `private-method` |
| Kernel#require | a required file's names | `top-level-method`, `include-across-files` |
| runtime | `method_missing`, `define_method`, `send`, refinements | not followed: no static answer |

19 cases were written (8 Python, 3 PHP, 8 Ruby), all accepted by their
interpreters. Nine did not hold, and engine 0.5.73 follows them:

- **Python `global x` / `nonlocal x`**: an assignment to x in the function
  binds the module's (or the enclosing function's) name; it was a local that
  hid the module variable. click's `_getchar` and httpx's
  `HTTPCORE_EXC_MAP` are now written and read by the functions that say
  `global`.
- **PEP 695 type parameters** (`def f[T](x: T)`, `class C[U]`) hide a class of
  their name.
- **The order members are looked up in**: Ruby's ancestors are the class, its
  modules from the last included, then the superclass; a PHP trait's methods
  come before the parent class's (monolog's `$this->getFormatter()` is now the
  `FormattableHandlerTrait`'s, not the interface's declaration).
- **A Ruby local exists from its first assignment on**: a bare name above it
  is a method call.
- **A Ruby constant** is looked up in the lexical nesting before the
  ancestors of the class.
- **PHP class names do not fall back to the global namespace** (only
  functions and constants do), and **`\Name` is the global one**: the reader
  keeps the `\` (`["\\", "Name"]`) and the resolver takes the name from the
  global namespace, or nothing. guzzle's 128 `catch (\InvalidArgumentException
  $e)` and `instanceof \CurlShareHandle` were linked to the project's own
  `GuzzleHttp\Exception\InvalidArgumentException` and to a phpstan stub; its
  test stub `curl_setopt` no longer calls itself through `\curl_setopt`.

The other cases (a class body's names are no names of its methods, a name
bound later in a function is local to all of it, `__all__` and `_` names for
`import *`, a lambda's parameters, PHP `insteadof`, a Ruby block's
parameters, the class's method before a module's) held already.
`verify_graphify.py` lacks 44 confirmed edges, as before.

### Phase 2: Scala

The Scala 3 typer gives the binding precedences outright
(`compiler/src/dotty/tools/dotc/typer/Typer.scala`, `BindingPrec`: a
definition of the compilation unit, then a named import, then a wildcard
import, then a member of the package that another compilation unit defines),
and `findRef` walks them scope by scope. `benchmarks/rule_inventory.py` lists
every rule (12 rules, 9 held by a case, 3 not followed; 5 of 37 cases found in the language's own tools).

| Source | Rule | Held by / why not |
|---|---|---|
| Typer BindingPrec | a definition of the compilation unit, local or inherited, first | `definition-before-import`, `implicit-this`, `trait-method` |
| Typer BindingPrec | then a named import, then a wildcard import | `named-import-before-wildcard`, `member-import`, `import-rename`, `wildcard-import` |
| Typer BindingPrec | then a member of the package another compilation unit defines | `wildcard-import-before-other-unit`, `same-package` |
| Typer checkNewOrShadowed | a name both inherited and defined in a scope around is ambiguous | not followed: an error is no binding (scalac E049) |
| SLS 2 | a type of another package needs an import or its path | `other-package-not-in-reach`, `nested-type-not-in-reach`, `type-through-package` |
| Scala 3 'Extension Methods' | a member before an extension of the name | `member-before-extension`, `extension-type-parameters` |
| Scala 3 'Export Clauses' | `export p.m` makes m a member of the object writing it | `export-clause` |
| SLS 6.6 / apply | `C(…)` is the companion's `apply`, or a case class built | `companion-apply`, `case-class` |
| SLS 8 patterns | `case r: T` and constructor patterns | `pattern-typed`, `pattern-types` |
| SLS 4.6 | a method called without parentheses, generic calls | `no-parentheses`, `generic-call`, `generic-method-parameters` |
| Scala 3 'Given Instances' | `given` / `using` contextual parameters | not followed: implicit resolution is not modelled |
| Scala 3 'Implicit Conversions' | a member found through an implicit conversion | not followed: implicit resolution is not modelled |

5 cases were written (a sixth, an inherited member beside a definition of the
scope around, was dropped: scalac rejects it as ambiguous, E049). Two did
not hold, and engine 0.5.74 follows them:

- **A wildcard import beats another unit's package member**: `import lib._`
  gives `lib.Repo` over `app.Repo` of another file of the package; it is the
  other way round in Java, and was here too.
- **`export p.print`** makes `print` a member of the object that writes it
  (`FileFacts::forwards`).

Measured with engine 0.5.74: the Scala compiler's SemanticDB expect files
give 97.4%, recall 74.9%, as before; no edge of the 45 audit repositories
changes.

### Phase 2, done

Every language of the plan has been read. What the reading left:

| Language | rules | held by a case | not followed | cases read from its own tools |
|---|--:|--:|--:|--:|
| csharp | 54 | 47 | 7 | 18 of 70 |
| rust | 38 | 31 | 7 | 15 of 70 |
| go | 29 | 23 | 6 | 15 of 53 |
| typescript | 31 | 26 | 5 | 7 of 58 |
| java | 30 | 26 | 4 | 14 of 62 |
| kotlin | 24 | 20 | 4 | 13 of 53 |
| swift | 16 | 13 | 3 | 9 of 52 |
| cpp | 21 | 17 | 4 | 9 of 45 |
| python | 15 | 14 | 1 | 8 of 46 |
| php | 12 | 10 | 2 | 3 of 37 |
| ruby | 10 | 9 | 1 | 5 of 34 |
| scala | 12 | 9 | 3 | 5 of 37 |
| **all** | **292** | **245** | **47** | |

The 47 rules not followed are of four kinds, each written down in the
inventory with its reason: errors (an ambiguity is no binding), access
control (code that compiles reaches only what it may), what has no static
answer (`dynamic`, `method_missing`, `getattr`), and inference (a closure's
parameter typed by the call it is passed to, generic arguments deduced from
a call) — the last is phase 3.

### Phase 3: inference, by the numbers

`benchmarks/inference_gaps.py` reads every oracle's missed uses (`--json`)
and sorts each by what stands before the name: a closure's parameter
(`x => x.m()`, `|r| r.m()`, `{ it.m() }`), a call's result, a name the walk
did not type, a member named in an initializer or a struct literal, a type
a member is reached through (`Proxy::new()`), another type position, a bare
name, an import. Each cell is the rows of the kind and their share of every
use the compiler binds (engine 0.5.74):

| language | uses | missed | closure-parameter | call-result | bare-name | other-receiver | initializer | type-before-member | type | bare | import |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| csharp | 17318 | 3475 | 154 (0.9%) | 200 (1.2%) | 509 (2.9%) | 122 (0.7%) | 1302 (7.5%) | 1004 (5.8%) | 49 (0.3%) | 101 (0.6%) | 34 (0.2%) |
| rust | 31105 | 5165 | 111 (0.4%) | 385 (1.2%) | 659 (2.1%) | 303 (1.0%) | 921 (3.0%) | 737 (2.4%) | 1041 (3.3%) | 501 (1.6%) | 507 (1.6%) |
| go | 24077 | 3862 | 6 (0.0%) | 62 (0.3%) | 568 (2.4%) | 30 (0.1%) | 2693 (11.2%) | 0 (0.0%) | 438 (1.8%) | 60 (0.2%) | 5 (0.0%) |
| typescript | 19760 | 2682 | 260 (1.3%) | 57 (0.3%) | 1605 (8.1%) | 80 (0.4%) | 50 (0.3%) | 64 (0.3%) | 101 (0.5%) | 435 (2.2%) | 30 (0.2%) |
| kotlin | 1771 | 200 | 0 (0.0%) | 1 (0.1%) | 23 (1.3%) | 6 (0.3%) | 0 (0.0%) | 85 (4.8%) | 61 (3.4%) | 24 (1.4%) | 0 (0.0%) |
| swift | 140 | 44 | 0 (0.0%) | 8 (5.7%) | 7 (5.0%) | 1 (0.7%) | 0 (0.0%) | 8 (5.7%) | 15 (10.7%) | 5 (3.6%) | 0 (0.0%) |
| scala | 439 | 110 | 0 (0.0%) | 3 (0.7%) | 23 (5.2%) | 5 (1.1%) | 0 (0.0%) | 15 (3.4%) | 27 (6.2%) | 28 (6.4%) | 9 (2.1%) |

What it says:

- **A closure's parameter typed by the call it is passed to is at most 1.3%
  of the uses** (TypeScript 260, C# 154, Rust 111; Go, Kotlin, Swift and
  Scala none). Most of those closures are passed to a library's method
  (`db.Todos.Where(t => t.OwnerId …)`, `.map(|c| c.is_tls())`) whose
  parameter types the engine does not read, and a closure passed over a
  project collection is already typed by its element (`ITERATION_METHODS`).
  **Decision: not built.** What it would take (the parameter types of
  library methods) is more than the 1% it adds.
- **The largest kind is no inference**: a member named in an initializer or
  a struct literal (`new T { Name = … }`, `&Command{Use: "child"}`; Go
  11.2%, C# 7.5%, Rust 3.0%). The compiler binds the name to the field; the
  reader leaves it out on purpose (`positions.py`'s `DECIDED`: a value built
  with its fields couples the builder to the type, which the construction
  edge already says). **Decision: kept out**, with the number written here.
- **A type a member is reached through** (`Proxy::new()`, `Token.Name()`,
  `C.foo()`; C# 5.8%, Kotlin 4.8%, Rust 2.4%): the engine links the member
  and writes no `references` edge to its type, which the oracles count as a
  use of its own. **Decision: kept**, as a convention: the edge to the
  member names its type through containment.
- **What is left** (a name the walk did not type, a call's result: 1–8%) is
  typing, not inference: the TypeScript rows are the compiler tests' own
  corner cases (`z.bar()` through an alias of a merged module, `this.cars`
  in an ES5 class), read case by case as the oracles are, not by a phase.

So phase 3 ends without a change to the engine: the numbers say the
inference left is small, and the larger gaps are positions left out by
decision or a convention of the graph's.

One thing the cases showed that is not a position: a Swift protocol's
property requirement is a definition of its own named `var handler`, of kind
interface (the span rule takes `protocol_property_declaration` for a
protocol). It is a node today; whether it should be is a span decision, and
`spans.py` and the engine make it together.

## Phases

**Phase 0: the frame** (no rule changes).
`source` / `since` / `until` / `found` on the cases, shown in
`docs/resolution-rules.md`. The version tables read out of the six compilers
that have one into `benchmarks/versions/<language>.json`, with the script
that regenerates each from the checkout. `lang_version` read from manifests
into the facts (unused by any rule yet), with a golden per manifest kind.
`verify_cases.py` passing a case's version to the compiler.
*Done when*: every existing case says how it was found, and the audit
repositories each report a version per language. (Done: see above. A
`source` per case is phase 2's work, language by language.)

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
   table is the largest): read (engine 0.5.64, `rule_inventory.py`: 54 rules, 47 held
   by a case, 7 not followed with the reason)
2. **Rust** (editions change path resolution; rust-analyzer runs here): read (engine 0.5.65, 38 rules, 31 held by a case, 7 not followed)
3. **Go** (smallest binder, already whole on the floor: the cheapest full
   reading, and the template for the others): read (engine 0.5.66, 29 rules, 23 held by a case, 6 not followed)
4. **TypeScript / JavaScript** (module resolution modes): read (engine 0.5.67, 31 rules, 26 held by a case, 5 not followed)
5. **Java**, then **Kotlin** (one platform; `--release` and
   `-language-version` give every version from one compiler): Java read (engine 0.5.68, 30 rules, 26 held by a case, 4 not followed); Kotlin read (engine 0.5.69, 24 rules, 20 held by a case, 4 not followed)
6. **Swift**, **C / C++** (need the large toolchains): read (engine 0.5.71; Swift 16 rules, 13 held by a case, 3 not followed, C and C++ 21 rules, 17 held by a case, 4 not followed)
7. **Python**, **PHP**, **Ruby** (interpreters; the static tools as second
   source): read (engine 0.5.73; Python 15 rules, 14 held by a case, 1 not followed, PHP 12 rules, 10 held by a case, 2 not followed, Ruby 10 rules, 9 held by a case, 1 not followed)
8. **Scala** (when a compiler is available): read (engine 0.5.74, 12 rules, 9 held by a case, 3 not followed)

*Done per language when*: every section of the binder's lookup is either a
case or a written "not followed, because", and the share of cases marked
`found: source` is reported.

**Phase 2b: the sweep.** Phase 2 read each language once, and the
inventory it left (10 to 54 rules a language) said nothing of the sections it
had not read: each round of measurements on repositories then found rules
missing. A sweep reads every section to the end and splits the work by
model. A smaller model reads (`.claude/workflows/rule-sweep.js`: one reader
per group of `SECTIONS[lang]` in `benchmarks/rule_inventory.py`, then a second
per group that looks for what the first left out, at most two rounds), and
returns rows (a rule held by a case, a new case, a rule not followed with the
reason, a section with no rule); it writes nothing into the repository. The
strongest model checks every new case with `benchmarks/sweep_check.py`
(compiled by the language's own compiler; asked of the type checker where one
answers, `verify_cases.py --binding`; built by the engine), reads every
reason, writes the cases under `found_by("sweep")` and the rows into the
inventory, and fixes the engine for the broken ones.
*Done per language when*: `rule_inventory.py --lang L --sections` lists no
section as `not read`.

Go, the first: 156 sections of the specification and of `go/types` (23 with
no rule of name binding, settled by hand; 18 read before). 21 readers (7
groups, two rounds of gap hunting each, and the second round still found rows
in every group but one) returned 417 rows: 257 new cases, 87 held by a case,
47 not followed, 26 sections with no rule. Of the 241 distinct new cases all
compiled; the binding check first flagged 27 edges, of which 14 were its own
blind spots (12 absent edges that only name a relation, 2 a type and its
method declared on one line; both fixed in `verify_cases.py`) and 13, in 8
cases, names the case left ambiguous (`helper` both a function and a field),
qualified by hand. The engine held 153 of the 241: the 88 it did not are
locals typed by their expression (`new(T)`, `&T{}`, `T(x)`, comma-ok, `<-ch`,
`range` over channels, maps, integers and functions, type-switch clauses),
aliases, types defined from other types, explicit instantiation and the
scope of `if` / `for` / `switch` / `select` initializers. The first fixes
they led to (engine 0.5.80: Go's comma-ok forms are no tuple, `<-ch` and a
`chan` type give the element, `new(T)` and `&xs[i]` type a local, a composite
literal is a value of its type in a chain, a conversion is a `references` of
its type and gives a value of it, and a name declared in a statement's init
ends with the statement) hold 16 more: 169 of the 241, and on cobra, chi,
resty and gin-examples `go/types` finds 12,336 of 12,342 judged edges right
(12,311 of 12,317 before) and 85.5% of the uses with an edge (85.4%).

Go, finished as far as one pass of the engine goes: a third round of gap
hunting (7 readers; generics found nothing new, the other groups 2 to 6 rows
each, against 4 to 14 in the second) added 18 cases, all checked as above
(317 Go cases, 425 rules, 156 of 156 sections). The fixes that followed
(aliases and defined types, a type switch's variable per clause, `range`'s
key, `select` bindings, slices, `make` / `append`, explicit instantiation,
constants of defined types, package variables in a path, bounds written with
type arguments, a receiver's type parameters, array lengths and map keys
read as values) bring the engine to 299 of the 317. On cobra, chi, resty and
gin-examples `go/types` finds 12,419 of 12,426 judged edges right and 86.0%
of the uses with an edge (12,311 of 12,317 and 85.4% before the sweep).
Open: the bindings of a generic instance (`type ItemBox Box[Item]`, an
explicit type argument as a result, the instance's method behind a bound),
core types (`S ~[]E`), range over function iterators, anonymous struct
types, unexported names of two packages, a name an `else if` declares
again (the two blocks end at one byte, and the facts do not say where a
block starts), a short variable's own value (tried: it cost 11 uses on chi)
and a variable holding a function literal. Two engine changes tried for
these were taken back after measuring (keeping every Go declaration of a
name broke `v, ok := f()` redeclaring `v`; the own-value rule cost 11 uses).

C#, the second: 190 clauses of the standard and Roslyn's binder files in 9
groups (64 with no rule of name binding, settled by hand). 34 readers (three
rounds of gap hunting; types, statements and interfaces found nothing in
their last round, classes, expressions and invocation still 8 to 12 rows in
the third) returned 561 rows: 338 new cases, 110 held, 88 not followed, 25
sections with no rule. Of the 325 distinct new cases all compiled but one (a
C# 9 pattern marked 8.0); Roslyn, asked through `verify_cases.py --binding`
as go/types is for Go, contradicted two (a name two types end with, and a
rule read backwards: `x is B` is the type pattern beside a constant `B`),
and each was fixed by hand. The check itself learnt four things on the way:
Roslyn lists the constructor a `new` or `: base(…)` chose, a use is the
innermost definition's (but a field's type is its type's), a name the
compiler wrote nothing of is no answer, and two definitions on one line say
nothing of whose a use is. The inventory has 587 rules (492 held by a case,
95 not followed) and 188 of the 190 sections read (`Binder_Operators.cs` and
`Binder_Deconstruct.cs` got no row). The engine held 207 of the 397 cases.
The first fixes (block scopes, as in Go; `[Cache]` as `CacheAttribute`;
user-defined operators; an object initializer writing the created type's
members; the class a static member is reached through) brought it to 236;
the second round (indexers; the calls the language makes by name:
`GetEnumerator`, `Deconstruct`, `GetAwaiter`, a collection initializer's
`Add`; `with`; an extension when the type's own method does not take the
arguments) to 264, and the third (a lambda's parameters typed by its
delegate, an overload taken by a lambda's parameter count, `out var`;
user-defined operators by both operands, `checked`, `operator true` /
`false` and conversions; query expressions) to 294. On todoapi, restsharp, commandline and aspnetcore-realworld Roslyn
finds 12,355 of 12,575 judged edges right (10,731 of 10,944 before, 98.3%)
and 89.9% of the uses with an edge (80.0%); query expressions alone found
55 uses it had missed, with no wrong edge. The third round's nine new "wrong"
rows are the check's own: eight `o.RedirectOptions = new RedirectOptions
{ … }` lines where Roslyn answers for the class only (the property written
is the one the graph names), and one property whose attributes put its
name five lines below its first line, past the check's slack; the one
real miss is `messages.Concat(msgs)`, the library's `Enumerable.Concat`
where the graph names the project's extension. (Roslyn's check reads names,
so operators are judged by the cases alone.) Open: the predefined types
extensions are written for (`await 3`), aliases and `global::`, attribute
targets, accessibility, nullable value types, and the finer points of
overload resolution.

Rust, the third: the Reference file by file (one section per file, a rule
id going to the file of its longest prefix) and rustc's resolver
(`rustc_resolve`'s `ident.rs`, `late.rs`, `imports.rs`,
`build_reduced_graph.rs`, `macros.rs`) and method probe (`probe.rs`,
`confirm.rs`, `mod.rs`), 121 sections in 8 groups (44 with no rule of name
binding, settled by hand). 24 readers (two rounds of gap hunting; only names
found nothing in its second) returned 508 rows: 216 new cases, 116 held, 139
not followed, 37 sections with no rule. rust-analyzer answers
`verify_cases.py --binding` for Rust (`oracle_rust.py --dump`, its LSIF index
of each case's project); of the 209 distinct cases one did not compile (a
generic parameter of an outer item, E0401) and the binding check first
flagged 15. Its blind spots were two, and the check learnt both: rust-analyzer
names the function of the `impl Trait for X` block a trait's method call runs
where the graph names the trait's (a use bound to a function the trait
dispatches to, or one of an `impl` of that trait, counts as the trait's), and
absent edges named by a bare name that a field and a function share
(qualified by line by hand). The inventory has 509 rules (362 held, 147 not
followed) and 121 of 121 sections read. The engine holds 100 of the 208 new
cases (177 of the 285), precise mode 253. Open, by size: pattern bindings
typed by what they take apart (`for (a, b)`, `while let`, struct, tuple
struct, slice and `@` patterns, a parameter pattern), operators as calls of
their traits' impls (`+`, `==`, `!`, `?`, `[]`, `*`), globs that skip private
names, type aliases, `Deref` targets, `where` bounds and associated types,
edition 2015 paths and `#[path]`.

**Phase 3: inference, by the numbers.** (Measured: see 'Phase 3: inference, by the numbers' above; nothing built.) Per language, what one step of
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
