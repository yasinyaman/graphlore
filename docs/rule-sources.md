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
