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
- **Shared state.** Variables, fields and constants are nodes
  (`kind: variable | property | constant`), and so are the environment
  variables, files, tables, events and cache keys code uses by a literal key
  (`type: resource`); `reads` / `writes` link the definitions that touch them
  ([below](#shared-state-and-resources)).
- **Routes and HTTP requests.** A route is a node (`type: route`, its
  method, full path and framework) with a `handles` edge to its handler, the
  path joined across files; a request the code makes to one of the project's
  own routes is an `http_calls` edge from the requesting definition to it
  ([below](#http-routes-and-requests)).
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
  `graph.engine: graphlore` and `schema_version: 5`, and graphlore then trusts
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
   type has, and in a file the caller imports, one a module it imports passes
   on, or its directory). That is `INFERRED`. A name with several candidates gets no edge; the build reply
   counts the unresolved uses per language.

An external module's contents are unknown, so a wildcard import of one
(`#include <stdio.h>`, `using System;`) names nothing.

What the lookup takes from each language's own rules:

- **Extension functions and methods** (Kotlin `fun KmProperty.toSpec()`, C#
  `static bool IsBlank(this string s)`) are members to their callers: a call
  whose last name is no member of the receiver's type, or whose receiver the
  walk cannot type, is looked up among the extensions the file can see (its
  own, those it imports, its directory, its package or a namespace it uses) —
  `EXTRACTED` when the receiver's type is the one the extension is written
  for, `INFERRED` when one extension has the name. `self.x()` in a Rust `impl
  Trait for` a type of another crate is the method beside it. The
  receiver's type counts with its bases (`client.BuildUri(r)` on a
  `RestClient`, written for `IRestClient`), and several overloads written
  for it are one extension, chosen among by their arguments. The members of
  a C# 14 `extension(IRestClient client) { … }` block are written for the
  block's parameter (the grammar reads the block as a constructor named
  `extension` and its members as local functions; they stay nodes under
  that name). A field or property of a type the project does not define is
  answered by the extension written for the type it declares
  (`WireMockServer _server` then `_server.ConfigureBodyCapturer(…)`, `string
  Name` then `request.Name.IsNotEmpty()`), and a local by the type its
  value constructs (`var message = new HttpRequestMessage()`). A C#
  namespace sees the extensions of the namespaces around it (`RestSharp.Tests`
  those of `RestSharp`). On a receiver that is no type of the project, a name
  .NET's own strings, lists and types answer to (`ForEach`, `Split`,
  `GetProperties`, `Contains`) is theirs: no edge.
- **C# partial types.** The declarations of a `partial` type (the
  definitions of one fully qualified name with as many type parameters)
  are one type: a member, a field or a base that one declaration writes is
  found from any of them (`class RestClient : IRestClient` in one file,
  `RestClient.Async.cs` in another).
- **The words for the object and its base are each language's own.** `this`,
  `self`, `super`, C# `base`, PHP `parent::` and Python's `cls` mean the
  object a method is called on or its base only in the language that says
  so. Elsewhere they are names: a Go parameter `parent` (`parent.addChild(c)`),
  a Python parameter `base`, a Java parameter `cls`. `self` stays the object
  everywhere: where it is no keyword it is the alias of one (JavaScript
  `var self = this`).
- **A guess never links a definition to itself.** `inner.evaluate()` inside
  `evaluate()`, on a receiver the walk cannot type, is a wrapper handing on
  to what it wraps: that the project has one `evaluate` says the caller is
  it, not the callee. A typed receiver, `self` and a tie between overloads
  are not guesses and keep their edge.
- **Small cases the source states.** A Swift implicit member is typed by
  more places: the closure that initializes a property (its declared
  type), the label of a memberwise initializer (the stored property), a
  library type's static member that a project extension adds, and a
  property extensions add to library types when every such property of the
  name holds one project type (`request.headers` on a `URLRequest`,
  `INFERRED`). An enum case or entry is a value of its enum (`Path.status(…)`,
  `KModifier.EXTERNAL`). Java Lombok: `getX()` / `isX()` / `setX(…)` on a
  type (or a base) annotated `@Data`, `@Value`, `@Getter` or `@Setter`, or
  whose field is, reads or writes the field `x`. Laravel's `auth()->user()`
  is the one PHP class that extends `Authenticatable` (`INFERRED`). A Rust
  pattern binding the walk cannot type (`Err(e)`) is an untyped local, and
  a module star-imported passes on what its `pub use` names (`use
  crate::prelude::*`). A parameter type the project defines is that type
  even when its name is a language one's (`enum Type` is no `Class`). And a
  guess by name never takes a name Rust's or C++'s own types answer to
  (`lock`, `clone`, `c_str`), as for JavaScript's.
- **What the source writes at a declaration's value.** A cast is the type
  it writes, as surely as a declaration (`var a = (Box)o`, `o as Box`,
  TypeScript `<Cup>o`, Kotlin `o as? Jar`). A C# primary constructor's
  parameters are typed names of the class (`class Handler(ConduitContext
  context)`). A name taken out of the object a function returns is the
  function it names: `const { show, hide: close } = useToast()` beside
  `return { show, hide: close }` (`INFERRED`, like a part of a returned
  tuple). A Python `@property` read with more after it is what it returns
  (`self.cookies.extract_cookies(…)`). And in a C# top-level program a
  variable that names nothing in sight, called by the name of an extension
  the file can see, is that extension's receiver (`builder.Services.AddJwt()`,
  `INFERRED`); a capital head is a library's type making a static call
  (`HttpUtility.UrlEncode(s)`), never.
- **The type arguments a call writes** (C#, Java). `client.Get<User>(id)`
  is of a callable that declares as many type parameters; one that
  declares none, or another number, cannot be meant. A call that writes
  none says nothing by that (they may be inferred), but between a callable
  and its generic twin of the very same parameters, as written, the plain
  one is called: that is the languages' own tie-break, and the twin is the
  one called with its argument written. `Send<T>(IRequest<T>)` beside
  `Send(IRequest)` are no twins.
- **The type a declaration writes comes first.** A variable, field or
  property declared with a type is of that type whatever it is assigned:
  Go `var rooms *Manager` set in an `init`, Kotlin `val kind: Kind =
  builder.kind`, C++ `static HttpServer* server = new HttpServer();`. What
  it is assigned types it only where no type is written (`INFERRED`).
- **The overload a chain asks for.** A call in the middle of a chain writes
  no arguments the walk keeps, so of several callables of one name
  (`Result.response()` beside the static `response(Response)`) the one
  whose result has the member asked next is followed, when exactly one
  has it (`INFERRED`: the chain chose, not the arguments).
- **A field before a function of its name**, when a member is asked of it
  and it is not called: Kotlin `constructor(builder: Builder, tags: TagMap
  = builder.buildTagMap())` beside a companion's `fun builder(…)`, Swift
  `eventMonitor?.request(…)` beside `func eventMonitor(…)`.
- **C# names are looked up in every namespace around the file's**:
  `Users.UserHelpers.Create()` from `App.Features.Articles` is
  `App.Features.Users.UserHelpers`, and a base written `:
  Interceptors.Interceptor` is the project's class.
- **Names bound with a type the source states.** A loop variable written
  with its type (Java `for (Pet pet : pets)`, C# `foreach (Pet pet in
  pets)`) and a type pattern (Java `o instanceof Pet p`, C# `o is Pet p`,
  `case Pet p:`) are locals of that type. A name bound to what a wrapper
  holds is a local of the value's path, opened: Rust `if let Some(p) =
  self.pet()`, `while let`, `let Some(p) = e else { … }` and a `match`
  arm's `Some(p)` / `Ok(p)`; the fields of an enum variant an arm takes
  apart (`Slot::Full(inner)`); Swift `if let p = load()` and `guard let q =
  cat`. `Err(e)` holds no type the source names, and a binding that only
  shadows its own name (`if let pet = pet`, `guard let self`) goes on
  meaning what it meant. A local assigned a field or a variable (`let q =
  cat`) is typed by what that holds. A test that writes no name (`x is
  Foo`, Kotlin's smart cast) binds nothing: narrowing by control flow is
  not read.
- **A closure's parameters are names of the closure only**: `r` in
  `func(w http.ResponseWriter, r *http.Request) { … }` is the request there
  and whatever `r` was outside, and an untyped block parameter hides the
  outer name inside the block, not after it.
- **Ruby.** A bare name that is no local is a call (`render json:
  article_params`, `items.each { … }`, the value of a pair), and so is the
  method behind `self.class.from(x)`. A module or class is every file that
  opens it: `Utils::Headers` is found from any `module Faraday`, and `class
  Far::Compact` is `Compact` in `Far`. A method at the top level of another
  file is in scope only for the files that require it (two specs each
  defining `response` are not each other's).
- **Constructions.** `new Foo(…)` (`Foo(…)`, `Foo.new`) is a call of the
  type and of the constructor the type declares (`__init__`, `constructor`,
  `initialize`, `__construct`, a Java / C# / C++ / Swift constructor). Of
  several overloads the one taking as many arguments as the site passes is
  named; when that picks none or several, only the type is. A Kotlin class's
  primary constructor is no definition, so a secondary one that fits is
  `INFERRED`.
- **A construction typed by its declaration**: C# `Box b = new(1, 2)` (a
  local, a field, a property) and C++ `Buf b(3)` / `Buf b{3}` construct the
  type the declaration names.
- **What a call gives.** A function that declares its result gives that type;
  one that declares none gives what its `return`s and expression body
  construct, when they agree (`fun builder(n) = Builder(n)`, `return Conn()`
  beside `return None`, `return self`, the last expression of a Ruby method),
  and what is reached through such a result is `INFERRED`. A closure's `return`
  is the closure's. A local keeps the whole chain it is assigned
  (`val b = builder(x).tag(y)`, `res := c.R().SetBody(b)`) and is typed by
  following each call's result; in Kotlin the value sits beside the
  declaration (`val c = Writer(out)`). Inside its own initializer a name is
  still the outer one (`val builder = builder(x)`, `let tls =
  tls::Config::builder()`). In Ruby a name called without parentheses is
  followed the same way (`stubs.match(env)`, `s = stubs`).
- **Lambdas with a receiver** (Kotlin). Inside `x.apply { … }`, `x.run { … }`,
  `with(x) { … }` and a lambda passed to a parameter typed `T.() -> R`
  (`buildCodeBlock { indent() }`), a bare name is a member of the receiver, or
  an extension written for its type, before it is a name of the class around;
  nested lambdas are looked through from the innermost. When the class around
  has a callable of that name too, the receiver's is taken (`INFERRED`: types
  would decide) unless it takes fewer parameters than the call passes or the
  call hands over `this` (`buildString { emit(this) }` gives the receiver to
  the outer `emit`). `also` and `let` pass the value as `it` and have no
  receiver.
- **A receiver of no known type** — a parameter or a local the source gives no
  type and no typeable value — is an unknown receiver like the result of an
  unknown call: a method only one type of the project has, in a file the
  caller imports or its directory, is `INFERRED`. In Ruby and Swift, where
  every type of the module is in scope and no import says which, only when no
  other type answers to the name at all: no property or field of that name,
  no Ruby attribute declared without a `def` (`attr_reader :status`,
  `Struct.new(:headers)`, `def_delegators`), and not a name the language's own
  strings and collections have (`shift`, `gsub`, `sorted`). Not on a local
  whose type is one the project does not define: there only an extension
  written for that type can be meant (in Rust, a method of an `impl Trait for`
  that type; in Kotlin, of several declarations alike but for their source
  set — `expect` / `actual` — the one nearest the caller).
- **Overloads** (Java, Kotlin, Scala, C#, Swift, C++). Of several callables
  of one name written side by side, the call is of the one that can take it:
  the count of arguments against the parameters (defaults and varargs
  counted), the name an argument is passed under (Kotlin `named(b = "x")`, C#
  `retries: 3`, Swift's labels, which also tell `init(frame:)` from
  `init(name:)`), and the type of each argument the site shows — a literal's
  kind against `String` / `Int` / `Boolean`, a local's or a construction's
  type against a parameter typed by a class of the project (a subclass
  fits), the type the source writes for a name against the parameter's. An
  overload ruled out by any of these is not the one; of those left the best
  match is taken, one not marked `@Deprecated` / `[Obsolete]` before one that
  is, and the one found first when nothing decides. A constructor is named
  only when one is left (a Kotlin class's primary constructor, written in its
  header, competes with the secondary ones and is the class itself). The same
  comparison decides between a receiver's method and the class's around a
  lambda.
- **Arguments the site types.** For the choice among overloads an argument
  is: a literal (string, number, flag), a lambda (against a parameter of a
  function type, `Runnable`, `Action`), a class literal (against `Class` /
  `KClass`), a concatenation with a string, a comparison, `x.toString()`, a
  cast's type; a local, by its type or the type the source writes for it; a
  field, property or constant, by its declared type or its value's. When
  several overloads still fit alike, the one that takes exactly the arguments
  passed comes before one that needs a default or a vararg; overloads a base
  class adds under the name compete; and when nothing decides, **each
  overload that fits is linked and every one of those edges is `INFERRED`**:
  the call is of one of them (at most four are named).
- **Untyped receivers, by what is asked of them.** A parameter or local with
  no type and no typeable value is the one type of the project that answers
  every member used on it in its definition — a method or field of its own or
  of a base, in Ruby an attribute too — when at least two names that are not
  the language's own (`map`, `get`, `to_s`) are used and one type fits (or
  one that all the others derive from). `def call(env)` using `env.status`,
  `env.body` and `env.parallel?` is an `Env`; a name called must be callable
  (not in Ruby, where reading an attribute is a call). `INFERRED`: duck
  typing writes no type, and a library's type could answer too.
- **Elements of typed collections.** A local, parameter or field declared as
  a collection (`List<Foo>`, `Foo[]`, `[]Foo`, `[Foo]`, `list[Foo]`), or a
  function that declares it returns one, gives its element type to the
  variable of a loop over it (`for (x in xs)`, `for x in xs`, `for _, x :=
  range xs`, Java `for (var x : xs)`), to the first parameter of a lambda
  given to an iteration method (`xs.forEach { x -> … }`, `xs.map(x => …)`,
  Kotlin's `it`; `x.let { … }` has `x` itself), to `xs[0]` and to
  `xs.first()` / `xs.get(i)`. Go's `v, err := f()` takes `f`'s first result.
- **Chains of calls on a collection.** The walk along a path knows when what
  it holds is a collection of a type, and the name of the next method says
  what it gives: one that keeps the collection (`filter`, `sortedBy`,
  `toList`, `stream()`, `iter()`, `Where`, `OrderBy`, `ToList`, Ruby
  `select`) leaves it a collection of the same elements, one that takes an
  element (`first()`, `get(i)`, `find { … }`, `FirstOrDefault()`, Swift's
  `xs.first`) gives the element, and what an optional is unwrapped with
  (`unwrap()`, `orElseThrow()`, `.get()` after `findFirst()`) changes
  nothing. A mapping whose function is one expression is the collection of
  what that expression gives for an element: `xs.map { it.owner }`,
  `xs.map(x => x.owner)`, `items.stream().map(i -> i.boss())`,
  `dishes.Select(d => d.Chef)`, `planes.map { $0.pilot }`, Ruby
  `xs.map(&:owner)`, Python `[j.chief for j in jobs]`; `flatMap { it.kids() }`
  is the collection of the elements of what each gives. So
  `xs.filter { it.ok }.map { it.owner }.forEach { it.run() }` calls
  `Owner.run`, `items.stream().filter(…).findFirst().get().end()` calls
  `Item.end`, and a local assigned such a chain (`val owners = xs.map { …
  }`) is a collection to the loop over it. A mapping whose function gives
  something the walk cannot name (`xs.map { it.toString() }`, a block of
  several statements) is a collection of nothing known, never of the same
  elements; `first()` called on a collection is the library's method and no
  edge to the element's type. The element is the first parameter of the
  lambda, the second for Kotlin's `forEachIndexed` / `mapIndexed`; a Swift
  closure's parameters (`{ p in … }`, `$0`) and a C# `d => …` are names of
  the closure like every other language's.
- **What a wrapper holds.** A wrapper that answers for what it holds (`Box`,
  `Arc`, `Rc`, `Readonly`) is read as that type, as before. One that holds
  its value closed — `Option`, `Optional`, `Mutex`, `RwLock`, `RefCell`,
  `Promise`, `Future`, `CompletableFuture`, `Deferred`, and Rust's `Result`
  and C#'s `Task` / `ValueTask` (in those languages only: retrofit has a
  `Result<T>` of its own) — is the wrapper until it is opened: by `await`,
  Rust's `?`, an unwrapper (`unwrap()`, `expect()`, `get()`,
  `orElseThrow()`, `lock()`, `borrow()`, `.Result`, `.Value`), or the
  parameter of a lambda given to it (`opt.ifPresent { it.run() }`, `opt.map
  { it.owner }`). A method that keeps the wrapper (`map_err`, `ok_or`,
  `filter`, `ConfigureAwait`) leaves it closed; anything else called on a
  closed wrapper is the wrapper's own method (`is_some()`, `isPresent()`,
  `then()`, `Wait()`) and no edge. So `self.pool.lock().unwrap().get()` on
  an `Arc<Mutex<Pool>>` calls `Pool.get`, `open()?.stop()` on `fn open() ->
  Result<Conn, E>` calls `Conn.stop`, and `(await fetchDoc()).load()` on a
  `Promise<Doc>` calls `Doc.load`. A function's declared result is read
  through a wrapper too (`-> Option<Foo>` returns a `Foo`, closed), which
  it was not before. A local assigned a call of such a function is closed
  unless the value was opened where it is assigned (`let c = open();` is,
  `let c = open()?;` is not).
- **Maps and pairs.** A map declared as one (`Map<K, V>`, `HashMap`,
  `Dictionary`, `dict[K, V]`, `Record`, Go `map[K]V`, Swift `[K: V]`) has its
  values' type as its element, and records that it is a map: `m[k]`,
  `m.get(k)`, `getValue`, `getOrDefault`, `getOrPut` give a value,
  `m.values` / `.Values` is the collection of them, and `entries`,
  `entrySet`, `items` keep the map. Iterating a map gives its entries (in
  Python its keys), so a single loop variable over one, a lambda's one
  parameter, and `m.first()` are nothing known; a pair taken apart is its
  key and value (`for ((k, v) in m)`, `for k, v in d.items()`, `for (k, v)
  in &m`, `for (const [k, v] of m)`, `foreach (var (k, v) in m)`, `m.forEach
  { (k, v) -> … }`, Java's `m.forEach((k, v) -> …)`), and Go's `range` gives
  the value second. The same pair beside an index gives the element
  (`xs.withIndex()`, `xs.iter().enumerate()`, `xs.enumerated()`). The keys'
  type is recorded too: `m.keys` / `keySet()` is a collection of it, and the
  first of a pair taken apart (`for ((k, v) in m)`, `m.forEach((k, v) ->
  …)`) is a key; the first of a list's pair is the element (`forEach((x,
  i) => …)`), beside an index nothing. `xs.partition { … }` taken apart
  (`val (ok, bad) = …`) gives two collections of the elements, `xs.zip(ys)`
  (and Python's `zip(xs, ys)`) taken apart gives one of each, `xs.fold(init)
  { acc, x -> … }` (`Aggregate(seed, …)`, `inject(init)`, JS `reduce(f,
  init)`) is what `init` is and so is the accumulator, and
  `xs.associateWith { it.owner }` is a map whose values are what the lambda
  gives. A part of a tuple a callable returns as a literal of names (`return
  [copiedText, copy]`) is the definition of that name inside it when its
  type names no class, and a local holding it calls it (`copy(id)`,
  `INFERRED`).
- **What a declaration's type arguments bind.** A local, parameter, field
  or result written with type arguments (`Response<Contact> r`, `b:
  Crate<Owner>`, `fun execute(): Response<T>`) binds the type parameters of
  the generic type it names (`class Response<T>`): `r.body()` with `fun
  body(): T` is a `Contact`, and so is `r.value` with `T value`, an element
  of `r.all()` with `List<T> all()`, and a constructor's `val item: T`. A
  result written with the class's own parameter passes the binding on:
  `call.execute().body().getName()` on a `Call<Contact>` reaches
  `Contact.getName` through `Response<T> execute()`. Only a member of the
  class the bindings are for takes them (a member a base class declares
  does not, nor a method's own type parameter); a wrapper's argument is the
  type itself, as before. A local typed by its value carries the bindings
  its value's walk ends with (`val r = call.execute()` on a `Call<Contact>`:
  `r.body()` is a `Contact`). A type argument that is a collection binds
  its element too (`Call<List<Contributor>>`: `call.execute().body()` is a
  list of `Contributor`, `.get(0).login` reaches it). A member a generic
  base declares takes the bindings the base is written with: `class People :
  Store<Person>` makes `people.find()` (`fun find(): T`) a `Person`, through
  intermediate bases too (`class Crowd : Mid<Person>`, `class Mid<T> :
  Store<T>`), in Java, Kotlin, C#, TypeScript and Swift. And a loop over a
  local assigned a call that returns a collection (`val xs = repo.all();
  for (x in xs)`) has the element: the local's path ends at the callable,
  which the element step did not look through.
- **What a mapping's function gives, and lists of lists.** A lambda of
  several statements gives its last expression where the language makes
  that the value (Kotlin, Ruby, Rust, Swift) or what its `return`s give
  when they agree; a local the lambda declares is what it is assigned
  (`xs.map { x -> val o = x.owner; o }`). A function given by reference maps
  too: `map(Item::owner)`, `map(Item::getBoss)`, `map(::Item)`,
  `map(Item::new)`, Rust `map(Part::head)`, Swift `map(\.pilot)`. The walk
  counts lists of lists: `chunked`, `windowed`, `chunks`, `each_slice` give
  lists of the elements, `groupBy` / `GroupBy` / `group_by` a map of them,
  `flatten` / `SelectMany` takes one level off, so `xs.chunked(2).forEach {
  c -> c.forEach { it.run() } }` and `xs.groupBy { it.id }.values.forEach {
  g -> g.first().run() }` reach the element's `run`. `associateBy` /
  `ToDictionary` / `keyBy` key the elements (a map of them), `reduce` /
  `Aggregate` / `inject` give one. A closure inside another names the outer
  closure's parameter as the outer closure's (the inner parameter's type
  was looked up under the bare name).
- **Swift's implicit members.** `session.request(.status(301))` writes no
  type: `.status` is a static member of whatever the place asks for. The
  place types it: the parameter the argument is for (by label or position,
  on the overload the call is of), the type a declaration writes (`let e:
  Endpoint = .made(1)`), or the result of the function it is returned from.
  No type, no edge. It also says which overload a call is of: an argument
  `.status(…)` fits a parameter whose type has a static `status`, not one
  whose type has none (a protocol may get one from a conforming type, so it
  is neither). For that the overloads of a Swift method include the ones an
  extension of its type adds (`extension Session { func request(_ endpoint:
  Endpoint) }` beside `Session.request(_: any URLConvertible)`), the
  parameter's very type scores over one of its bases, and a trailing
  closure goes to the parameter that asks for no label, of a function type
  or of a type alias, before a labelled one.
- **The arguments of a Rust macro.** The grammar gives what a macro is
  called with as tokens, with no call in them, so `assert_eq!(handle_count(
  &pool), 2)`, `assert!(pool.count() > 0)`, `vec![make_pool(), make_pool()]`
  and `format!("{}", c.name())` named nothing. The arguments in `( … )` or
  `[ … ]` are parsed again as an expression, on their own, and the calls and
  constructions in them are placed where they stand in the file, a macro
  inside a macro too. Arguments that are no expressions (a `{ … }` body,
  `a => b` arms, a pattern) parse with an error and are left out, and so is
  the pattern of `matches!(value, Pattern)`.
- **A CommonJS module is what it assigns to its exports.** `require` gives
  `module.exports`, so `const Foo = require('./foo'); new Foo()` constructs
  the class `foo.js` assigns there, `make()` calls the function, and
  `Foo.create()` is that class's member; `exports = module.exports =
  createApplication` and a named function expression count the same. A
  module that assigns another one (`module.exports = require('./lib/app')`,
  a package's `index.js`) is that module: the import is a `re_exports`
  edge, and its names and what it is are looked up there. `exports.name =
  bound` passes a name on (`exports.authCallback = login`, `exports.response
  = res`). A module that only names its exports is called by none of this.
- **A test file's receivers.** `it('…', function () { app.use(function (req,
  res) { res.sendFile(…) }) })` runs at file level: `res` is a closure's
  parameter and `app` a module variable holding what `express()` returns,
  which no walk can type. Both are receivers the walk cannot name, as an
  untyped local is, and only a distinctive method name says what is called
  (`INFERRED`): the one callable of that name that is a method, or a
  function assigned to an object (`res.sendFile = function sendFile() {}`;
  one assigned to `exports` is the module's function, not a method), in a
  file in sight. In sight now includes what an imported module passes on:
  a test requires the package's entry, the entry re-exports `lib/express.js`,
  and that exports `response.js` under a name. Never on a variable holding a
  library's value (`http.createServer()` then `server.listen()`), and never
  by a name JavaScript's own arrays, strings and promises or Node's and the
  DOM's emitters and streams answer to (`once`, `pipe`, `concat`, `abort`):
  beside a project method of that name they say nothing.
- **Tuples taken apart.** A callable's result that is a tuple — `-> (A, B)`
  (Rust, Swift), `(A, B) Method()` (C#), `[A, B]` (TypeScript), `Pair<A, B>`
  / `Triple` (Kotlin), `tuple[A, B]` (Python), a Go function's results — is
  recorded part by part (`Def::rets`), and a declaration that takes it
  apart gives each name its part: `let (agent, clock) = setup()`, `var
  (client, probe) = Setup()`, `val (l, r) = two()`, `let (t, b) = split()`,
  `const [door, lock] = parts()`, `a, b = two()`, Go `a, b := both()`. A
  tuple is no one type: the result of such a function names nothing by
  itself (it named one of its parts, and the first name of a declaration
  that took it apart was the whole), except in Go, where `v, err := f()`
  takes `f`'s first result as before. A part written as a type alias
  (`type CopyFn = (text: string) => …`) is no class, so its name is a local
  of no type. A numbered field is a part too: a Rust tuple struct's
  (`struct Script(Arc<Mutex<State>>)`: `self.0.lock().unwrap().bump()`,
  `struct Tunnel(Box<dyn Transport>)`: `self.0.buffers()`), and one of the
  tuple a local holds (`let pair = setup(); pair.0.go()`, Swift `both.1`,
  C# `pair.Item2`, Kotlin `p.second` on a `Pair`).
- **Results that are type parameters.** A function that returns its own type
  parameter gives the type of the argument that binds it: `fun <T> pick(x:
  T): T` the argument's, `fun <T> head(xs: List<T>): T` its element's. A method that returns a type
  parameter its type binds to itself (`fun tag(x: Any): T` in `interface
  Taggable<T : Taggable<T>>`, Java `B<T extends B<T>>`) returns the type it
  was called on, so a builder chain keeps its type. A local assigned a call
  that is handed a type — one type argument (`mock<Foo>()`,
  `sp.GetRequiredService<IFoo>()`) or one class literal
  (`retrofit.create(Service::class.java)`, `typeof(Bar)`) — is that type
  (`INFERRED`), when the function called returns its own type parameter, or
  is a library's and is not named like something that wraps what it is given
  (`listOf<Foo>()`, `Task.FromResult<Foo>(x)`).
- **Anonymous classes** (Java `new Callback<T>() { … }`, Kotlin `object :
  Listener { … }`) are definitions that go by the type they implement and
  answer to no name: the method around constructs the type, the class
  `implements` / `inherits` it, and the names inside are looked up through the
  class and method around.

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
| communities | 90.6 | 94.6 |
| largest, % of the nodes | 10.0 | 11.1 |
| singletons, % of the nodes | 1.1 | 0.1 |
| median size | 8.3 | 11.6 |
| modularity on the plain graph | 0.67 | 0.60 |
| members in the community's main directory | 82.8% | 88.8% |
| test nodes in a mostly-production community | 20.9% | 9.4% |

(Engine 0.4.0, with the shared-state edges; 0.3.0 had 90.5 communities, 87.1%
in the main directory and 8.0% of test nodes mixed in.) The two partitions
agree with a normalised mutual information of 0.78 (0.62 to 1.0). The lower plain modularity is the price of the weights: the native
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

### Shared state and resources

Two definitions that never call each other still affect each other when one
writes what the other reads. The fact walk records, for every name and member
access, whether it is read or written (`engine/src/state.rs`); each language
says what its state is (`LanguageModule::state_defs`), and resolution keeps the
uses that name it:

| | state nodes |
|---|---|
| module / package level | Python assignments (capitals and literals: constants), JS/TS `let` / `var` / `const` (not `require` bindings), Go `var` / `const`, Rust `static` / `const`, C/C++ globals (not `extern`), Kotlin / Swift / Scala top level, Ruby `$x` and constants, PHP class constants |
| class level | Python class attributes, Java / C# / C++ / PHP static fields, Ruby `@@x`, Scala `object` members, C# / Swift / Kotlin / TS properties |
| instance | Python `self.x` and JS `this.x` first assigned in a method, Ruby `@x`, Java / C# / C++ / PHP / Scala / Rust / Go fields |

A write is an assignment, `+=` and the like, `x[k] = v`, `del x`, `x++`
(`EXTRACTED`), or a call of a mutating method (`append`, `set`, `clear`,
`push_back`, …, through `lock()` / `unwrap()` chains) on a variable that holds
a collection or a builder (`INFERRED`: only the name says so). A local hides a
variable of its name — a parameter, a declared local, and in Python and Ruby any
name assigned without `global` — but `local.field` is the field of the object
the local holds. A constructor setting its own object's fields builds it and is
left out.

Resources (`engine/src/resources.rs`) are what lies outside the program, by a
key written as a string literal: environment variables (`os.getenv`,
`os.environ[…]`, `process.env.X`, `System.getenv`, `env::var`, `ENV[…]`,
`$_ENV`, `getenv`), files (`open(p, mode)`, `fs.writeFileSync`, `os.ReadFile`,
`File.WriteAllText`, `fs::write`, `fopen`, …), tables in SQL strings (`FROM` /
`JOIN` read; `INSERT INTO`, `UPDATE … SET`, `DELETE FROM` write; names a
`WITH` defines left out), event names (`emit` / `publish` write, `on` /
`subscribe` read; only when the project does both, and not for names every
stream or page emits) and cache keys (`localStorage`, Redis-like stores).
A data model names its table (`__tablename__`, Django `Meta.db_table`, `@Table`
/ `@Entity`, `[Table]`, Laravel `$table`, Rails `table_name`, an ORM base class,
EF `DbSet<T>`, `mongoose.model`), and a call through it (`User.objects.filter`,
`Post.create`, `user.save()`, `db.Items.Add`) reads or writes that table
(`INFERRED`). A resource is a node (`res_<kind>_<key>`) only when two
definitions or more touch it; the rest are counted in the build reply.

On the 31 audit repositories:

| | |
|---|--:|
| variables / constants / fields | 492 / 1,798 / 6,478 |
| `reads` (inferred) | 16,555 (691) |
| `writes` (inferred) | 2,333 (459) |
| resources shared by two definitions or more | env 21, table 8, event 10, key 2 |
| state written in one place and read or written in another | 1,095 |
| edges added | +16% |

Read by hand, 30 sampled reads were all right, 24 of 25 writes (the 25th a
C# object initializer setting a property of the class it names), and 29 of 30
edges to instance fields (the miss, a Go struct literal's key, is now left
out). The first samples found the rules above: a method named `delete` on an
HTTP client (`got.delete(url)`) is no write, a chain does not carry a write
past an unrelated call (`req.getHeader(…).replace(…)`), a constructor's own
fields are construction, `_` in Go is no variable, a `"Update own password"`
message is no SQL, and `data` / `close` / `click` are no shared events.

Builds take 15% longer (median of eight repositories against 0.3.0, −1% to
+27%): the walk keeps a stack of ancestors for these rules, as tree-sitter's
`parent()` walks down from the root again and cost twice that.

### HTTP routes and requests

A frontend's `fetch('/api/v1/users/42')` and the FastAPI handler it reaches
are joined by nothing in the code: no call, no import. The engine reads both
ends and links them. Each language module reads its frameworks' routes
(`engine/src/http/`), after the rules of graphlore's `routes.py`:

| | routes | requests |
|---|---|---|
| Python | FastAPI, Flask, Sanic, Quart, Litestar, Django | `requests`, `httpx`, `aiohttp` (and clients with a `base_url`) |
| JS / TS | Express, Fastify, Koa, Restify, Polka, NestJS | `fetch`, axios (and `axios.create({ baseURL })`), generated SDKs (`client.get({ url })`), jQuery, Angular `HttpClient`, `EventSource`, supertest |
| Java / Kotlin | Spring, JAX-RS, Ktor | Feign, `@FeignClient` / `@HttpExchange` / `@RegisterRestClient` interfaces, Retrofit, `RestTemplate`, `WebClient` / `RestClient`, MockMvc, RestAssured, the Ktor client |
| C# | ASP.NET Core controllers, minimal APIs | `HttpClient` |
| Go | gin, echo, chi, gorilla, net/http | `http.Get`, `http.NewRequest`, resty |
| Rust | axum, actix-web | reqwest, ureq |
| Ruby | Rails `routes.draw`, Sinatra | HTTParty, Faraday, rack-test |
| PHP | Laravel, Symfony | Guzzle, Laravel `Http::`, Laravel test requests |

Unlike `routes.py`, the engine joins a route's path across files. A router has
a prefix of its own (`APIRouter(prefix="/users")`, `Blueprint(url_prefix=…)`,
`express.Router()`), is mounted under another with one (`include_router(r,
prefix=settings.API_V1_STR)`, `app.use('/api', require('./users'))`,
`register_blueprint`, Fastify `register`), or under a global prefix
(`app.setGlobalPrefix('api')`). A prefix written as a name is read through
resolution: `settings.API_V1_STR` is the value of the class attribute of the
`Settings()` that `settings` holds. Routers can also be groups bound in a
function (`v1 := router.Group("/v1")`), a function's parameter that each call
mounts (`addUserRoutes(v1)` with `func addUserRoutes(rg *gin.RouterGroup)`), the
router a function builds (axum `.nest("/api", users::router())`, actix
`.configure(routes)`), or a route file (Laravel's
`Route::prefix('api')->group(base_path('routes/api.php'))`). A prefix whose
value is unknown is written as a parameter (`/{API_PREFIX}/users`) and the
route says `prefix_unresolved`.

Handlers: a decorated function or an annotated method is its own handler; a
name (`users.login`, `[UserController::class, 'store']`, Rails
`"articles#index"`) is resolved, from the scopes only; a lambda that makes one
call (`post { users.register(ctx) }`, `(req, res) => ctrl.show(req, res)`) is the
method it calls; any other inline function is the definition around it.

A request is linked when its URL is the project's own: a path (`/api/users`),
a relative one (`"todos"` through an `HttpClient` whose base address the code
does not say), or `localhost`; an unknown leading part (`${API_URL}/users`) is
taken for the base. Paths are compared segment by segment, every parameter
form (`{id}`, `:id`, `<int:id>`, `[id]`, `*`) one wildcard. The best candidate
has the most segments that match as text, then parameter against parameter
(a template hole `${id}` counts as a parameter); a tie goes to the route
nearest the request in the tree. An exact match with a known method is
`EXTRACTED`; a value against a parameter (`/users/7`), an unknown method or
base, and several candidates are `INFERRED`. Requests in test code are not
linked, only counted (`report.http.test_calls`), as are those to another host
(`external`) and those whose URL the source does not spell out (`dynamic`).

On the 31 audit repositories:

| | |
|---|--:|
| route nodes | 830 |
| `graphlore_routes` rows found as routes | 2,375 of 2,376 |
| … with a longer path (a prefix from another file or a group) | 69 |
| … with a closure in a variable for a handler (named by the function around it) | 60 |
| production requests / linked | 67 / 37 |
| requests in tests (counted) | 2,797 |

The links, read one by one: fastapi-template's generated SDK, 23 requests to
the 23 handlers; kotlin-spring-realworld's six Feign methods to their Spring
handlers; todoapi's Blazor and BFF `HttpClient`s, 7 of 9 (the other two reach
routes `MapIdentityApi` adds at run time); gin-examples' browser
`EventSource('/stream/' + id)` to the Go handler in the same example. Builds
take 7% longer than with engine 0.4.0 (median of ten repositories, best of
three each; −6% to +30%, the +30% of express not seen again in its per-phase
times). Outside
the engine's sight: prefixes set in configuration (ASP.NET
`ApiRoutePrefixConvention`, Spring `server.servlet.context-path`), routes a
framework adds (`MapIdentityApi`, `devise_for`), named-route helpers (Rails
`*_path`, Symfony `path('name')`), template links (`href`, `action`), and
GraphQL / gRPC.

## Compared with graphify on 31 repositories

`benchmarks/compare_engines.py` builds both graphs of a repository, pairs
graphify's code definitions with the native ones (same file, definition line
and bare name), and compares the edges between paired definitions (imports as
file pairs). The native graph has definitions graphify folds into the one
around them (a function nested in a method, the setter beside a getter of the
same name); an edge from one of those is compared as an edge from the paired
definition it belongs to, as graphify records it. On the 31 repositories of the
[polyglot audit](benchmark-polyglot.md) (graphify 0.9.111, engine 0.5.37,
measured 2026-11-18), as native / graphify edges and the share of graphify's
that the native graph has too:

| Language | same definition | imports (file pairs) | calls | bases | type references |
|---|--:|--:|--:|--:|--:|
| Python | 100% | 212 / 219 (96%) | 2,970 / 834 (94%) | 73 / 62 (100%) | 440 / 494 (68%) |
| JavaScript | 93.5%⁵ | 190 / 305 (59%) | 1,386 / 2,058 (43%)¹ | 1 / 1 | 3 / 0 |
| TypeScript | 99.4% | 527 / 634 (83%) | 805 / 687 (98%) | 25 / 20 (100%) | 740 / 156 (88%) |
| TSX | 99.7% | 253 / 316 (80%) | 435 / 456 (95%) | – | 42 / 4 |
| Go | 100% | 538 / 950 (47%)² | 6,723 / 3,167 (99%) | 19 / 14 (100%) | 679 / 355 (99%) |
| Java | 100% | 1,105 / 1,010 (100%) | 5,718 / 5,123 (85%) | 415 / 168 (94%) | 3,989 / 1,930 (98%) |
| Kotlin | 99.4% | 368 / 333 (95%) | 4,786 / 2,108 (71%) | 96 / 56 (93%) | 1,079 / 673 (91%) |
| Scala | 100% | 2 / 0 | 46 / 20 (45%) | 21 / 22 (95%) | 75 / 32 (100%) |
| C# | 92.7% | 347 / 280 (30%)³ | 2,860 / 2,450 (96%) | 87 / 86 (98%) | 1,248 / 806 (99%) |
| Rust | 93.3% | 314 / 370 (75%) | 2,016 / 853 (86%) | 53 / 41 (93%) | 1,245 / 895 (81%) |
| Swift | 95.9% | 166 / 166 (–)³ | 5,494 / 2,141 (83%) | 190 / 164 (100%) | 1,091 / 580 (62%) |
| C | 100% | 89 / 77 (99%) | 864 / 791 (100%) | – | 253 / 130 (97%) |
| C++ | 98.7% | 215 / 228 (93%) | 765 / 281 (29%)⁴ | 30 / 5 (100%) | 350 / 234 (88%) |
| C/C++ headers | 91.4% | 164 / 172 (95%) | 372 / 348 (84%) | 12 / 12 (92%) | 563 / 312 (90%) |
| Ruby | 99.7% | 42 / 0 | 443 / 375 (69%) | 46 / 15 (93%) | 28 / 0 |
| PHP | 80.6% | 429 / 505 (84%) | 8,873 / 6,140 (90%) | 65 / 60 (98%) | 648 / 213 (95%) |
| **All** | **94.4%** | **4,962 / 5,565 (75%)** | **44,556 / 27,832 (85%)** | **1,137 / 726 (97%)** | **12,473 / 6,814 (89%)** |

Of the native calls, 12,772 (29%) are `INFERRED`, against 47% of graphify's.
That share was 20% before engine 0.5.5: a call of an overloaded name that the
arguments do not decide used to be an `EXTRACTED` edge to the first overload,
and is now an `INFERRED` edge to each overload that fits (999 calls, with
the edges to the other overloads). The share of graphify's calls found has
moved little since 0.5.2 (84.1% to 84.3%) while the native graph gained some
3,700 edges: most of what these versions add — a call through a Kotlin `val`
typed by its value, through a chain's results, through an element of a typed
collection, to the constructor the arguments are for — graphify does not have
either. Nor does this table see a call that moved from one overload to
another of the same name: 0.5.4 moved 1,175 of them (742 in Kotlin, 288 in
Java, 109 in Swift, 33 in C#).
Engine 0.5.6 (chains of calls on a collection, a property typed where it is
declared, closure parameters in Swift and C#) gives the 31 graphs 144 more
calls (most in ktor-realworld's tests, through `appRule.http.createArticle()`,
in kotlinpoet and in alamofire), and 479 more `reads` and 46 more `writes`
of fields reached through an element or a typed property
(`response.error.code`, `x => x.TargetId`); 12 edges are gone. Those were
read: ten named a method or property of alamofire's `Request` where the name
was a closure's parameter (`{ response, completionHandler in
handler(response) }`), two moved to the extension function the call is of.
The chains themselves are a small part of it: these repositories mostly
loop over a collection or call one method on an element, which 0.5.5
already followed.
Engine 0.5.7 (what a wrapper holds) adds 191 edges and drops 2: most are
C#, where `var x = await client.ExecuteAsync(r)` was never typed (a C#
declarator names its value with no field, and only a call was looked for)
and a generic class whose base is the plain one of its name
(`RestResponse<T> : RestResponse`) reached no base at all: 141 more `reads`
in restsharp, 31 more `reads` / `writes` in aspnetcore-realworld. The Rust
unwrap chains of these repositories are mostly on library types
(`uri.authority().unwrap()`, `res.headers().get(…).unwrap()`), so they add
14 calls in all. Of the two edges gone, one was a guess on a name
(`ret.extensions_mut()` on an `http::Response`, linked to ureq's own
`Request`) and one moved to the definition the call is of.
Engine 0.5.8 (maps and pairs) changes 10 edges: kotlinpoet's
`constructorProperties[param.name].emit(…)` calls `PropertySpec.emit`
where a guess by name linked `ParameterSpec`'s, ureq's `proxy.map(|p|
p.username())` moves from a function of `util.rs` to `Proxy.username`, and
two edges of got are gone that named a function defined in another loop
for a destructured loop variable of the same name. The maps of these
repositories that hold project types are mostly looked up by key once, or
not walked at all.
Engine 0.5.41 is Ruby's: a class opened in several files has, in each of
them, what any opening mixes in or extends (`reopened_base_member`), and a
class written as a constant assigned `Struct.new(…) do … end` mixes in what
the block says. faraday gets its eight `Faraday::Request.register_middleware(…)`
/ `Faraday::Response.…` calls (`EXTRACTED`, through `extend
MiddlewareRegistry`) and three `mixes_in` edges; nothing else changes.
Engine 0.5.40 gives a closure no definition holds a scope of its own
(`Def::block`: no node, no name). A test file is mostly such closures —
`test('…', async t => { const request = … })`, `it '…' do conn = … end` —
and a local declared in one was no local at all: the name went on to the
file, the imports and the project, so `request` in one test was a `request`
function of another file, `EXTRACTED`, and `assert.strictEqual(body, 'ok')`
passed another test file's `body`. Each closure is now a definition to the
resolver (its parent the closure around it), its locals are typed like a
function's, and a definition written in it is its child. In the 31
repositories' own code that adds 210 edges (got 96, faraday 59, express 41,
alamofire 13, nestjs-realworld 1) and removes 140 (got 95, express 22,
faraday 13, alamofire 9, express-mongoose-demo 1). Read at their sites, the
removed ones are a local or a callback's parameter taken for a definition of
its name somewhere else, but for two kinds: ten guesses by a method's name
on what is now an untyped local of a block in faraday's specs, four of them
right (`b.adapter :test`, `options.each_key`; an untyped local is asked only
a name no other type answers to), and typeahead.js in alamofire's
documentation assets, a module written `var Dataset = (function () {
function Dataset() {} return Dataset })()`, whose `new Dataset()` outside
that function no longer resolves (5 edges). The vendored and minified bundles (jQuery, Bootstrap,
retrofit's generated Dokka `main.js`), which the analysis scope leaves out,
lose 2,587 edges and gain 1,347: their one-letter names were file-wide.
The 31 builds take as long as before (15.5 s).
Engine 0.5.39 asks one more thing of a call the walk could not follow: the
one extension in sight written for a type the project does not define
(`library_extension_call`, `INFERRED`). The receiver of such a call is a
library's member or a chain of them, which the walk cannot type:
`e.Name.LocalName.RemoveUnderscoresAndDashes()` (an `XElement`'s name, an
extension for `string`), `user.email.isEmailValid()`,
`NotificationCenter.default.postNotification(…)`,
`request.headers().has_content_type()` (`impl HeaderMapExt for HeaderMap`).
It adds 79 calls (kotlinpoet 30, restsharp 23, alamofire 13, ureq 7,
aspnetcore-realworld 3, ktor-realworld 3), each read at its site. The first
version of the rule added 96, and the 17 wrong ones made the rule what it
is: a local whose type the source names was already asked with that type
(`call.is_chunked()` on a `ureq_proto` `Call` is not `HeaderMap`'s), a name
that is also a member of a project type says nothing of an unknown receiver
(`self.0.connect()` beside `impl Connector for ()`), a static call on a
library's type is the library's (`HttpUtility.UrlEncode(s)`), and an
extension that wraps the library's method of its own name does not call
itself (`FileManager.default.createDirectory(…)` inside
`createDirectory`). The same version parses a C# `#if` written inside an
expression once (its first branch): with both branches in the tree the
grammar lost the class around, and RestSharp's `HttpResponseExtensions` was
no node (13 node ids of restsharp change; its three extension methods are
methods again, called `EXTRACTED`).
Engine 0.5.38 fixes one walk: a member read without a call, with more after
it, is the field when the type also has a method of that name. ureq's
`LazyBuffers` holds `input: ConsumeBuf` and implements a trait's `fn
input()`, so `self.input.unconsumed()` took the method and named nothing;
the same for `self.current_time.now()` and `self.der.as_ref()`. ureq gets 42
calls (`EXTRACTED` but one), and no other edge of the 31 graphs changes.
Engine 0.5.36 (JavaScript receivers) reads four things the source states. A
`this` in a function expression no definition holds is what the function
is attached to (`RefFact::this_of`): `app[method] = function () {
this.route() }` calls `app.route`. In an ES5 constructor `function View()
{ this.lookup() }` it is the functions assigned to `View.prototype`. A
callback a Mongoose schema is handed (`UserSchema.path('name').validate(function
() { this.skipValidation() })`) runs on a document, whose methods are the
file's `UserSchema.methods = { … }` (an object literal assigned to a member
is that member's object now, as `obj.name = function` was), `INFERRED`; so
is a local constructing a model (`new Article(…)`, `Article` a
`mongoose.model('Article')`, registered with `mongoose.model('Article',
ArticleSchema)`). And a project that builds a framework's request or
response object itself, as express does (`var req =
Object.create(http.IncomingMessage.prototype)`, `Def::created_from`), types
a handler's untyped `req` / `res` by it and by what an app adds to
`app.response`, `INFERRED`: express's tests and examples get 158 calls
(`res.send` 99, `res.render` 18, `res.json` 17), the Mongoose demo 49. A
project that builds no such object leaves `req` the framework's
(`req.logout()` is passport's). The object-literal rule also makes jQuery's
`jQuery.fn = { ready: function () {…} }` methods to the guess by method
name, so vendored bundles (jQuery, Prism, Epoch, a Dokka `main.js`) get
some 60 calls inside themselves; the analysis scope leaves those files out.
JavaScript's unmarked unresolved rows: 55 → 18.
Engine 0.5.37 reads one more convention, the document a Mongoose model's
static hands its callback (`User.load(options, function (err, user) {
user.authenticate() })`, the second parameter, `FileFacts::callbacks`), and
says why each of the 17 JavaScript rows left is no gap (graph unchanged
otherwise): a call of the caller's own local or parameter, a callback it
was handed (`done(err)`; 42 rows in all languages); the one candidate in
vendored or minified code (`app.param` beside jQuery's); a member that is a
field assigned a value (`this.engine = opts.engines[ext]`); a receiver that
is a property defined at run time (`Object.defineProperty(this, 'router',
…)`, an `Object.create` descriptor, `FileFacts::runtime_props`); a
receiver holding a library's value (`const app = express()`; 36 in all,
most of them got's `server.listen`); a property middleware attaches to a
request (`req.article = …`, `FileFacts::request_props`); and the request
a framework hands a function where the project builds none (passport's
`req.logout()`). JavaScript's unmarked rows: 18 → 0; in all, 926 → 801.
Engine 0.5.35 (C function-pointer slots) reads a struct of function pointers
as an interface and what is put in its slots as the implementations: an
initializer of a variable, by name (`.async_read = redisAsyncRead`) or by
position among the struct's fields, data fields counted (`static dictType
callbackDict = { callbackHash, NULL, callbackValDup, … }`), and an
assignment of a function's bare name to a member (`ac->ev.addRead =
redisLibuvAddRead;`, `c->dataCleanup = asCleanup;`). Each is a
`dispatches_to` edge from the slot to the function (`EXTRACTED`), the
relation graphlore's `impact`, `calls` preset and call paths already follow
from an interface method to its implementations, so `r->fn->createString(…)`
now leads to `createStringObject`. `NULL`, a library's function and a data
field give none. A field of an anonymous struct type (`struct { void *data;
void (*addRead)(void *); } ev;`) answers for the members its body declares,
which the grammar gives to the struct around (`Def::inline_body`): the
adapters' `ac->ev.addRead = …` and reads through `c->tcp.host`. hiredis gets
88 `dispatches_to` edges (62 of them the 11 event-loop adapters filling
`ac->ev`, 10 `redisContextFuncs` for TCP and TLS, 7 reply-object functions,
5 `dictType` callbacks, 4 allocators in the tests) and 81 reads and writes
through anonymous structs. A member of an anonymous struct named like one
of the struct around (`ev.data` beside `data`) is not a definition of its
own, so a use of it names nothing rather than the outer one.
Engine 0.5.34 (C `typedef struct`) reads `typedef struct redisReader { … }
redisReader;` as the struct written inside it: the alias is a definition
whose child is the struct, and its members are the struct's (a local
`redisReader *r` typed by the alias found none, so `r->fn` named nothing).
A bodiless `typedef struct conn conn_t;` names `conn`; the target 0.5.32
wrote for an alias with a body was the first type of the body (`redisReader`
named `redisReplyObjectFunctions`) and named nothing only because it was
an alias in turn. Three gaps behind it went with it: a function returning a
pointer (`void *createStringObject(…)`) had no parameters typed (its
`function_declarator` sits under a `pointer_declarator`); `extern
hiredisAllocFuncs hiredisAllocFns;` declared nothing, so a header's inline
functions had no such name in scope (it is a declaration of the definition
now, `decl_only`, no node, as a prototype is); and a file-level variable
inside an `#if` group or a header's `extern "C" { … }` was none (a
declaration the grammar recovers from macro words, `ffc_internal
ffc_inline` above a function, with no `;` of its own, is still none).
hiredis gets 34 calls (`r->fn->createString(…)`, the slot of the function
pointer, as an interface method is the call's target), 836 reads, 324
writes and 312 uses of the structs passed (`redisContext`, `redisReply`),
and 16 file-level variables (`FFC_POWERS_OF_FIVE`, `ossl_locks`); a
`Q_OBJECT` read as a field `friend` is gone. C's unmarked unresolved rows:
33 → 0.
Engine 0.5.33 (C++ preprocessor branches) gives the parser an `#if … #else
… #endif` once when every branch leaves the same number of braces open
(`if (error_code) {` under `#if` and again under `#else`, closed once after
`#endif`): the other branches and the directive lines are blanked in place,
so bytes and lines keep their places. The parser saw two open braces and
read the rest of the file into the function around: in cpr's
`multiperform.cpp` the 25 methods after `DoMultiPerform` were nested in it.
`spans.py` blanks the same way (`_blank_duplicate_branches`), so the spans
stay equal; of the 187 C and C++ files of the audit repositories, 2 are
blanked (`multiperform.cpp`, hiredis's `adapters/libuv.h`). Calls to those
methods now reach their definitions, not the header's prototypes. The drop
of C++ calls in the table above (801 to 765, 34% to 29%) is graphify's: it
parses the file the old way, and its 24 calls from `DoMultiPerform` pair
with nothing now. 10 calls are lost: `session->PrepareGet()` on the
structured binding `for (const auto& [session, method] : sessions_)`, which
had an edge only because `session` named another method's parameter while
everything was one function; a pair taken apart from a `std::vector` of
`std::pair`s is not read yet.
Engine 0.5.32 (C++ aliases and specializations) reads `using AsyncResponse =
AsyncWrapper<Response>;` as a definition (graphify has these nodes too;
since 0.5.33 it is a span of `spans.py`'s rule as well, by its node type
`alias_declaration`, which has no hint word) and follows an alias or a
`typedef` to the type it names. A specialization (`class AsyncWrapper<T,
true>`) also answers to its template's name, and the arguments an alias
writes pick it: `<Response, true>` fits `<T, true>`, not `<T, false>`. A
bare `AsyncWrapper` (a local `AsyncWrapper w{…}` whose deduction guide picks
the specialization) does not say which one: a member exactly one
specialization answers to is that one's, `INFERRED`, and a name several
declare (`wait`) gets no edge. A deduction guide (`AsyncWrapper(std::future<T>&&)
-> AsyncWrapper<T, false>;`) declares no function: it was the base `AsyncWrapper<T,
true>` resolved to, and calls went to it. C++'s standard sequences and maps
(`std::vector<Foo>`, `std::map<K, V>`) are collections: `events[0].data`
reads the field of the element. cpr: 50 calls, 23 reads and 98 references
(`Header`, `SecureString`, `AsyncResponse`), 34 alias nodes; 5 edges moved
(the guide's).
Engine 0.5.31 (C++ namespaces) looks a namespace's member up in every block
that opens the namespace, not only the one the caller's file opens: a
header's `namespace cpr { struct File … }` and a test's `namespace cpr {
… }` are one namespace, so `cpr::File(…)`, `cpr::Url{…}` and `cpr::Get(…)`
reach their definitions wherever the caller's file opens a block of its
own. A method defined for a type inside such a block (`Response
Session::Get() { … }` in `namespace cpr { }`) is the type's, not a name of
the namespace. A name called is the function of that name and a name with
`::` after it the type (`cpr::async(…)` beside `class async`,
`cpr::async::cleanup()`). cpr gets 273 `EXTRACTED` calls (`cpr::Get` 116,
`cpr::Post` 47, `cpr::ThreadPool` and its constructor 40, `cpr::Url` 21);
nothing is gone.
Engine 0.5.30 (C++ locals) reads `Session session;` as a local of its type
(a declaration without a value had none: only `T x = …`, `T x(1)` and `T
x{…}` were read), and `cpr::ThreadPool pool(context);` as a variable
constructed from the local `context`: the grammar reads a declaration of a
function `pool` taking a `context`, and the language makes it a variable
because the local hides any type of that name. Such a declaration keeps its
span (`spans.py` reads it the same way) and gets no node and answers no name
(`Def::misread`). cpr gets 538 `EXTRACTED` calls and 140 `INFERRED` ones
(`session.SetUrl(…)` 136 times, `multiperform.AddSession(…)` 46; the
`INFERRED` ones are calls of overloads the arguments do not decide, such as
`session.SetPayload(…)`), and 13 nodes of misread declarations are gone
(`std::istringstream stream(headers)` was a function `stream` of
`parseHeader`, with a call to it); hiredis gets 10 reads and writes of a
struct's fields (`redisOptions options; options.push_cb = …`).
Engine 0.5.28 (RSpec) reads `let(:conn) { … }`, `let!`, `subject(:request)
{ … }` and a nameless `subject { … }` as methods of the block they are
written in: a name of that `describe` / `context` only, the innermost of
several of one name around a use, and none outside them (a use in
`shared_examples`, or in an outer `subject` whose `conn` an inner context
declares, names nothing: which one runs is the example's). What the block's
last expression gives is the method's result, so `conn.get(…)` reaches
`Faraday::Connection#get` through `let(:conn) { Faraday::Connection.new }`.
A module `RSpec.configure`'s `config.include M` puts in every example
answers a name a spec calls that nothing else answers to
(`httpbin_connection`, `EXTRACTED`: a spec's bare call runs only if it is
included). In faraday, the one repository of the 31 with RSpec lets, that is
374 new definitions and 484 calls (442 `EXTRACTED`), 68 gone: 52 moved
into the `let` they are written in (`Faraday::Adapter::Test::Stubs.new`
from the file to `stubs()`), and 9 were `INFERRED` guesses on a block
parameter (`Faraday.new { |b| b.adapter(:test) }`) that, inside a `let`, is
an untyped local like any method's, which Ruby's rule types only by a name
no other type answers to (`adapter` is also a delegator of `Connection`).
The table above barely moves (Ruby 442 → 443 calls): graphify has no
definition for a `let`, so its edges pair with nothing.
Engine 0.5.27 (the small cases the diagnostics left: Swift's implicit
members, Lombok, Laravel's user, Rust's `Err(e)`, a type named like a
primitive) adds 264 calls (183 `EXTRACTED`) and 302 reads and writes, and
takes 252 calls away, 227 of them kotlinpoet's moving to another overload. alamofire gets 30 calls of implicit members
whose place gave no type: a `return .userAgent(…)` in the closure that
initializes `static let defaultUserAgent: HTTPHeader`, a static member an
extension of a library type adds (`.streamCompletionQueue(forRequestID:)`
for a `DispatchQueue` parameter), the label of a struct's memberwise
initializer (`Endpoint(path: .delay(…))`, the stored `var path =
Path.method(.get)`) and `request.headers.update(.contentType(…))` through
the `headers` property extensions add to `URLRequest` and
`HTTPURLResponse`. quarkus-realworld gets 301 reads and writes from Lombok
accessors (`filter.getTags()` reads the field `tags` of a `@Data` class);
laravel-realworld 3 calls through `auth()->user()`, the one class that
extends `Authenticatable`; actix-realworld 18 calls of `e.error_response()`
in `Err(e)` arms, `INFERRED`: `e` is a local of no written type, and
`Error` is in sight through `use crate::prelude::*` and the prelude's `pub
use crate::error::Error`. kotlinpoet's 171 `EXTRACTED` calls that moved go
to another overload of the same name: `addModifiers(KModifier.EXTERNAL)`
is `addModifiers(vararg KModifier)` now that an enum entry is a value of
its enum, not the overload taking an `Iterable`. Of the 252 gone, 22 were
`INFERRED` guesses by a name Rust's or C++'s own types answer to
(`state.lock()` on a `Mutex`, `header_string.c_str()` on a `std::string`),
227 kotlinpoet's moved overloads (171 `EXTRACTED`, 56 `INFERRED`), and 3
restsharp's ties now decided; one right guess goes with
the 22 (`key.kind()` on a `PrivateKey` taken out of a tuple). retrofit's
`new PhoneNumber("867-5309", Type.MOBILE)` keeps its constructor: its
`enum Type` is the project's, not a language `Class`.
Engine 0.5.25 (from what the 0.5.24 diagnostics left: C# primary
constructors, a cast, an object a function returns, a Python `@property`,
C#'s top-level programs) adds 83 calls (60 `EXTRACTED`) and 157 reads,
writes, uses and references, turns 11 edges `EXTRACTED` and takes 8 away.
aspnetcore-realworld gets most of it: its handlers take what they use in a
primary constructor (`class QueryHandler(ConduitContext context, …)`), and
`context.SaveChangesAsync()` or `mapper.PersonToUser(person)` had no edge;
its `Program.cs` reaches `builder.Services.AddConduit()` (`INFERRED`: the
receiver is a top-level variable of a library's value). fastapi-template's
components get 13 calls of the functions a hook returns (`const {
showSuccessToast } = useCustomToast()`), httpx 8 through its properties
(`self.cookies.extract_cookies(…)`), and casts type a local in C#, Java,
TypeScript and Kotlin (`var attribute = (DeserializeAsAttribute)…`, `o as
Jar`). Of the 8 gone, 4 were wrong: two `indirect_call`s of alamofire
from `self.didCancelTask(task)` to the method `task()`, where `task` is the
local `guard let task = … as? URLSessionDownloadTask` binds, a call of got's
tests to an unrelated `close()` that a destructured local now holds, and
one inside a minified bundle. kotlinpoet's `memberName.emit(this)` goes to
`MemberName.emit` (`EXTRACTED`) instead of a guess. The other three are
reads of alamofire's example app that a later binding now hides (`guard
let request` beside `if let request = request as? DataRequest` in the same
function): locals are the function's, not the block's.
Engine 0.5.23 (the type arguments a call writes) changes restsharp only,
and only which of several overloads a call is linked to: 81 targets are
gone and 27 calls that are left with one are `EXTRACTED`.
`_client.GetAsync<TestClass>(new RestRequest())` was linked to
`GetAsync(RestRequest, …)` as well as to `GetAsync<T>(RestRequest, …)`,
`client.ExecutePostAsync(request, cancellationToken)` to the generic one
too, and `client.PostJsonAsync<TRequest, TResponse>(resource, request)` to
the overload with one type parameter. The table above
pairs edges by the labels of their ends, which overloads share, so it does
not move. aspnetcore-realworld's `SendAsync<TResponse>(IRequest<TResponse>)`
beside `SendAsync(IRequest)` stays a tie: the two take different things,
and only the argument's type would say which.
Engine 0.5.22 (the type a declaration writes, the overload a chain asks
for, a field before a function of its name, C# namespaces around) adds
249 calls (142 `EXTRACTED`) and 150 reads and writes, turns 513 calls from
`INFERRED` to `EXTRACTED` and takes 9 calls away. 509 of the 513 are
cpr's: its test files declare `static HttpServer* server = new
HttpServer();`, and a call like `server->GetBaseUrl()` was typed by what
the variable is assigned, a guess; its declaration says it, and C++'s `INFERRED` calls
fall from 692 to 189. The same reading types Go's package variables (`var
roomManager *Manager`) and Kotlin's properties (`val kind: Kind =
builder.kind`); kotlinpoet gets 75 new calls from that and from its
constructor parameters. retrofit gets 46 calls, most through
`result.response().isSuccessful()`: `Result` has a static `response(Response)`
beside `response()`, and the walk followed the first; alamofire 51, 48 of
them `eventMonitor?.request(self, didSuspendTask: task)`, where a method
`eventMonitor(…)` hid the property. The 9 gone are kotlinpoet's and were
ties: `WildcardTypeName.producerOf(ANY.copy(nullable = true))` was linked to
three overloads, and now that `ANY` is a `ClassName` by its declaration two
of them cannot take it; `FunSpec.builder("foo").contextReceivers(genericType)`
goes to the `vararg` overload its interface declares, not to the one taking
an `Iterable`. In restsharp and aspnetcore-realworld four bases written
through a namespace (`: Interceptors.Interceptor`) are the project's class
now, where they were an external name.
Engine 0.5.21 (names bound with a type the source states) adds 29 calls
(19 `EXTRACTED`) and 27 reads, uses and writes, and takes 24 edges away.
New are the calls on a loop variable written with its type (Java `for
(RecordingSubscriber<?> subscriber : subscribers) subscriber.assertNoEvents()`
in retrofit, `for (Pet pet : getPets()) pet.getName()` in spring-petclinic),
on what `if let Some(proxy) = details.config.proxy()` binds in ureq, and on
what a C# pattern binds (`response.Request is not RestXmlRequest
xmlRequest`, then `xmlRequest.XmlNamespace.IsNotEmpty()` in restsharp). The 24 gone are alamofire's, and were
wrong: after `guard let task = mutableState.tasks.last`, `task` was the
class's method `task()` handed on as a value (8 `indirect_call`), after
`guard let index = headers.index(of: name)` it was `index()`, and after `if
let request = state.requests.last` a read of the property `request` (11
`reads`). They are locals now; five calls through such a local are
`INFERRED` where the property of the same name had made them `EXTRACTED`.
Engine 0.5.20 (each language's own words for the object and its base,
and no guess from a definition to itself) is a correction more than a
gain: 14 calls, 3 reads and 4 writes are new, 38 calls are gone. New are
the calls on a Go variable named `parent` (`parent.SetProxy(…)`,
`parent.replaceChild(…)` in resty and chi), which was read as a base class
that Go does not have. Gone are 13 `EXTRACTED` calls from a C# method to
itself: `base.SaveChangesAsync(…)`, `base.Dispose(disposing)` were read as
`this.…`; the base is a library's there, and `base.GetValueFromXml(…)`
now reaches the base class of the project. Gone too are 25 `INFERRED`
calls from a definition to itself that a name alone had made:
`evaluator.evaluate(trust, forHost: host)` inside `evaluate`,
`clazz.getDeclaredField(name)`, `declaredType.asElement()` — 18 of them a
call of another object's method of that name, 3 inside a minified bundle,
and 4 a real recursion on another object
(`extendsBound?.recursiveParameterizedTypesTo(types)`), which is what the
rule costs. graphify's graph has some of these loops too, which is why
Kotlin's and Ruby's share drops a point.
Engine 0.5.19 (C# extension blocks, extensions through bases and on
library types, partial types) adds 618 calls (197 `EXTRACTED`, 421
`INFERRED`), turns 51 `INFERRED` ones `EXTRACTED` and takes 7 away.
restsharp gets 570 of them: its extensions are written in more than 30 C#
14 `extension(…)` blocks that named nothing (`client.BuildUri(request)`,
`request.AddHeader(…)`, `new RestRequest().AddObjectStatic(data)`), on an
`IRestClient` that `RestClient` implements in another declaration of the
partial class. Of its 392 new `INFERRED` calls, 290 are a call of an
overloaded extension the arguments do not decide (`client.GetAsync(request)`
beside `GetAsync<T>(…)`: each that fits is linked), and the 22 read of the
other 102 are right, `INFERRED` because the receiver is a local typed by
its value. Unifying partial types also gives it 121 reads, uses and
references of members another declaration holds, and 91 `references` edges
are gone in restsharp and kotlinpoet: they named the type a call started
from when the call itself named nothing (`new RestRequest(…).AddJsonBody(…)`
was a reference to `RestRequest`; the construction still calls it).
kotlinpoet gets 34 new calls, most of extensions on properties of library
types (`parameters.toImmutableList()`, `arguments.joinToCode(…)`), and 27 of its
`INFERRED` calls of overloaded extensions (`annotated`, `parameterizedBy`)
are `EXTRACTED`; aspnetcore-realworld gets `article.Title.GenerateSlug()`
and seven `services.AddJwt(…)`; ureq, actix-realworld and alamofire one to
four each (`details.uri.host_port()`). Gone: three self-loops (a polyfill
`Split` calling `string.Split`, and two tests named like the extension they
call, which now call it), `typeof(T).GetProperties()` (reflection, linked
to the project's `GetProperties(this object)`), and three right ones the
same guard costs (`obj.GetProperties(…)` on a type parameter twice,
`contentHeaders.ForEach(…)`).
Engine 0.5.18 (CommonJS modules as what they export, and the receivers of
a test file) adds 329 calls and takes 12 away. express gets 234: 110
`EXTRACTED` calls of `createApplication` from the tests and examples that
start with `express()` (through `index.js`, which is `lib/express.js`), and
124 `INFERRED` ones from a test or example to the method it exercises
(`app.listen`, `res.status`, `app.enable`, `res.sendFile`, `req.accepts`, …;
the 44 read are right) — before, only `test/utils.js` had an edge into
`lib/`, in either graph. Its `app.use`, `app.set` and `res.set` stay without
one: a name of three letters is no evidence. express-mongoose-demo's five
OAuth routes are handled by `users.login`, the function `exports.authCallback
= login` names, not by the function of the routes file. Of the other 95 new
calls, 94 are inside minified bundles (jQuery, Bootstrap, epoch, a
documentation script), from a function to one assigned to an object in the
same file; the analysis scope leaves those files out by default. got loses 12 `INFERRED` calls of
its own `pipe`, `unpipe` and `abort` on receivers the walk cannot type:
those names are Node's streams' (`body.pipe(request)`). The table above
counts edges between definitions both graphs have, so it shows 26 of these.
Engine 0.5.17 (Swift's implicit members, and the overloads an extension
adds) changes alamofire only: 158 calls of an implicit member it had no
edge for (`.status(401)`, `.endpoints(…)`, `.payloads(2)`, `.retrier { … }`,
all read), and 783 calls that go to another definition of the same name
than before, none to nothing — `session.request(.get)` in a test is the
`request(_ endpoint: Endpoint)` its test helpers add to `Session`, not the
`request(_: any URLConvertible)` of the library, and `self.cURLDescription()`
is the overload with no parameter, not the one it is called from. The
call sites that tie between overloads go from 800 targets to 671.
Engine 0.5.16 (the arguments of Rust macros) adds 171 edges, none gone: 164
calls in ureq (80 `EXTRACTED`, 84 through a local the walk types by its
value or not at all, as outside a macro), 5 in axum-realworld and 2 in
actix-realworld. Nearly all are a test's assertions (`assert_eq!(request(
&agent, a.clone()), "1")`), so what they add is the tests of a function.
The share of graphify's Rust calls the native graph has goes from 81% to
86%.
Engine 0.5.14 (keys, partition, zip, fold, associateWith, returned
functions) adds one edge: `copy(id)` in fastapi-template's columns, the
right guess 0.5.11 had lost, now from what `useCopyToClipboard` returns.
The rest does not occur in these repositories with project types.
Engine 0.5.13 (generics through values, nested arguments, generic bases)
changes three edges: a loop variable over a call's collection is typed in
kotlinpoet, and `addAnnotation(fileAnnotation)` goes to the one overload
that takes an `AnnotationSpec` where it went to three of four, `INFERRED`.
None of the other patterns occurs in these repositories with project types
on both sides (they are held by `tests/edges_golden.rs`).
Engine 0.5.12 (numbered fields) changes 28 of ureq's calls: 14 that were
guesses by name are `EXTRACTED`, and 13 move from a method of that name in
another file (`self.0.lock()` linked to `cookies.rs`) to the trait or type
the field holds (`TunnelTransport(Box<dyn Transport>)`'s `self.0.is_open()`
is `Transport::is_open`). Most of ureq's numbered fields hold library types
(`Receiver`, `Uri`, `HeaderMap`) or a type parameter
(`ChainedConnector<In, First, Second>(First, Second, …)`), which name nothing.
Engine 0.5.11 (tuples taken apart) adds 38 edges and drops 18: restsharp's
interceptor tests (`var (client, interceptor) = SetupClient()`, 25 reads)
and ureq's (`let (agent, state) = setup(config)`, 13 calls). Of the 18
gone, 14 were in minified bootstrap bundles (a destructured name taken
for a function of the bundle), two linked a Go file to a test file's `err`
through `x, err := f()`, one linked React's `setTheme` setter to the
property of that name around it, and one was a right guess by name
(`copy(id)` after `const [copiedText, copy] = useCopyToClipboard()`, whose
part is a type alias).
Engine 0.5.10 (mapping functions, references, groups, lists of lists) adds 5
edges and drops none: a function passed by reference (`filter_map(
NoProxyEntry::try_parse)` in ureq, `compactMap(\.self)` in alamofire) and a
field read through a lambda local. These repositories map almost always
with a one-expression lambda, which 0.5.6 already read.
Engine 0.5.9 (type arguments) adds 19 edges and drops none, and 12 of
retrofit's calls that were guesses by name are now `EXTRACTED`: retrofit's
tests take a `Response<T>` or `Result<T>` out of a recording observer
(`observer.takeValue().code()`, `result.response()`), and its samples call
a service through a `BehaviorDelegate<T>`. A method of a generic Rust impl
or C++ template (`impl<T> Holder<T>`, `CurlContainer<T>::Add`) now finds
its type (the owner was looked up with its parameters in the name): three
more reads and writes of a field in cpr.
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
  A call by name alone to a method of the same file is most of the rest:
  `funSpec.toString()` linked to a test's own `toString()` (262 of Kotlin's 716
  in kotlinpoet), `Truth.assertThat()` to a project `assertThat` (653 in
  retrofit), `@handlers.insert(i, h)` to the builder's `insert`, `server.enqueue`,
  `Unirest.delete`. `Article.find_by(…)` is a `calls` edge to the model there,
  a `references` edge here, and a method that builds an anonymous class
  (`new ToStringConverterFactory() { … }`) calls, in graphify's graph, the
  first anonymous class of that name in the file.
- **What the source does not say**, and the native graph therefore leaves
  open rather than lacks: which overload a call is of when the argument that
  would decide is the result of a library call (`baseUrl(server.url("/"))`:
  `String`, `URL` or `HttpUrl`?) — each overload that fits is linked,
  `INFERRED`; and what an untyped parameter is when only one member is asked
  of it and several types have it (Ruby `x.status`), or when two types answer
  everything asked — no edge. Generic results are followed for a type
  parameter bound to the type itself, handed explicitly, or bound by an
  argument or its elements, and through a chain of collection methods
  whose names say what they give (`xs.filter { … }.map { it.owner
  }.first()`, `groupBy`, `chunked`, `associateBy`, `reduce`, `fold`,
  `partition`, `zip`, `associateWith`); not through type arguments nested
  more than one level (`Map<K, List<V>>`'s values are lists of an unknown
  type), a Rust trait's type arguments (`impl Repo<User> for UserRepo`),
  a Python dictionary iterated for its keys (`for k in d`), or a lambda
  given to `zip` that takes the pair apart. Nor does it say what an
  object built at run time is (`var app = express()`, where the
  application is a function that methods are mixed into): a distinctive
  method name is guessed by, a short or common one (`app.use`, `app.set`)
  is left; whether `items.ForEach(…)` is `List<T>`'s own or a project
  extension for `IEnumerable<T>` when `items` is a library type; and which
  of a generic and a plain overload a call without type arguments is when
  the two take different things (`SendAsync(query)` beside
  `SendAsync<T>(IRequest<T>)` and `SendAsync(IRequest)`): each is linked,
  `INFERRED`.
- **What only the native graph has** is mostly what a name lookup by itself
  cannot see: calls through a module object (`httpx.Response(...)`), through a
  typed local or field (`client = httpx.Client(); client.get()`,
  `self.repo.find()`), through builder chains (`new Retrofit.Builder().baseUrl()`)
  and Go functions' declared results, type references in the same file, and
  `this` inside functions assigned to an object (`res.send = function () {
  this.get() }` in express).

**Definitions.** What only graphify has is PHP closures (`{closure#N}()`, 894
of PHP's 895), a node for a Rust `impl` or Swift `extension` target defined in
another file, C/C++ forward declarations and macro-wrapped declarations in
headers, and ⁵ a node for each name bound to an import (`const mongoose =
require('mongoose')`), which the native graph treats as the import it is. What only the native engine has is what `spans.py` treats as
definitions: nested functions, anonymous functions bound to an object key or a
class field, overloads graphify folds into one node, C++ methods declared in
their class.

**Speed.** With resolution, shared state, routes and communities, the native
builds take 13.3 s for the 31 repositories against graphify's 81.3 s
(graphify 0.9.112 `update .`, four cores, starting Python included, the
faster of two clean builds each): 8.7 times faster at the median, from 2.1
(todoapi, whose bundled JavaScript the native engine reads in full) to 17.2
times; retrofit 2.9 s against 13.1 s, guzzle 0.9 s / 6.1 s, alamofire 1.2 s /
5.4 s, got 0.9 s / 4.7 s (engine 0.5.1; with all that 0.5.27 resolves the same
31 builds take 16.3 to 18.9 s through Python, 15.8 to 18.1 s in the engine alone, retrofit 3.7 to 4.1 s). An update after a one-file change takes a third of
a build ([above](#incremental-updates)).

### What is left without an edge

The comparison above measures the native graph against graphify's. What the
engine itself leaves out is measured by `benchmarks/unresolved.py`: `python -m
graphlore_engine build ROOT --unresolved PATH` writes every call that got no
edge although its name is the one callable of that name in the project, with
the reason the walk stopped (graph.json is the same with or without it). A
name a library also has (`setHeader`, PSR-7's `getHeaderLine`, PHPUnit's
`fail`) is counted too, so the table is an upper bound and its samples
(`--samples N`) say which rows are real (the split below says how many). On the 31 repositories, without
vendored and minified files (engine 0.5.15, 2026-11-18): 6,796 calls.

| Reason the walk stopped | Calls | of them at file level |
|---|--:|--:|
| the head name is nothing the file can see | 1,572 | 579 |
| a field or variable whose type names nothing | 1,332 | 1,071 |
| a closure's parameter outside any definition | 696 | 600 |
| a library's name | 557 | 280 |
| `self`: no such member on the type around | 517 | 17 |
| a local of a library type | 435 | 0 |
| a local the walk cannot type | 376 | 0 |
| a bare name not in scope | 311 | 163 |
| an untyped local or parameter | 218 | 0 |
| a function whose result names nothing | 180 | 65 |
| a typed local, a step of the path names nothing | 174 | 0 |
| a receiver the walk cannot name | 149 | 15 |
| a type with no such member | 122 | 10 |
| a typed local with no such member on its type | 128 | 0 |
| other | 29 | 0 |

By language: JavaScript 1,892, PHP 988, Ruby 726, TypeScript 602, Java 566,
C# 470, Rust 372, Go 345, Kotlin 262, C++ 228, Python 153, Swift 97, Scala
43, C 33, TSX 19. More than a third of the rows (2,515) are at file level,
outside any definition: the callbacks of JavaScript, TypeScript and Ruby
test DSLs (`it('…', function () { app.use(…); res.download(…) })`), where a
variable or a closure's parameter has no definition to be a local of. The
calls inside a Rust macro were not in this table at all (no call was read
there); since 0.5.16 they are read, 171 of them got an edge, and the 52
that did not are counted (6,848 rows, Rust 424). The same goes for Swift's
implicit members since 0.5.17: 39 whose place gives no type are rows now
(6,887). Engine 0.5.18 takes 402 rows off (6,485; JavaScript 1,473): the
closure parameters and variables of express's tests. What is left at file
level there is `app.use` and `app.set` (842 rows of express), names too
short and too common to guess by. Engine 0.5.19 takes 123 more off (6,362;
C# 349 from 470; 6,360 with 0.5.20, 6,348 with 0.5.21, 6,285 with 0.5.22, 6,237 with 0.5.25, 6,206 with 0.5.27, 6,166 with 0.5.28, 6,156 with 0.5.30, 6,140 with 0.5.31, 6,132 with 0.5.32, 6,126 with 0.5.33, 6,093 with 0.5.34, 6,047 with 0.5.36, 6,046 with 0.5.37, 6,012 with 0.5.38, 5,928 with 0.5.39, 5,908 with 0.5.40, 5,898 with 0.5.41). Most of what C# has left is not the project's: `Assert.
NotNull` beside its own `Ensure.NotNull` (77), `WireMockServer.Start` (23).

By then most rows were of that kind: a library's name the project happens
to define once. Since engine 0.5.24 each row says whether it is, and why
(`library_like`), and the script prints both totals (`--real` shows only
the rest). Of the 6,285 rows (engine 0.5.24, 2026-11-18):

| | Calls |
|---|--:|
| a name the language's own types answer to (`each`, `clone`, `once`, `add`, `Split`) | 2,626 |
| the receiver is a library's (`MockResponse().setBody`, a local of a library type) | 1,002 |
| `self` / `super` in a type with a base the project does not define (PHPUnit's `$this->fail()`) | 329 |
| the one definition is test code and the call is not (PSR-7's `getHeaderLine` beside a test double) | 214 |
| **none of these: what an edge is still plausible for** | **2,114** |

(0.5.25 takes 48 of those 2,114 off: C# 196 → 166, TSX 19 → 6, Python 20
→ 17, Kotlin 79 → 77; 2,066 are left. Since 0.5.26 two more kinds are
marked: a capital head that is no name of the project and no local, a
library's type or global (`Date.now()`, `Object.freeze(…)`,
`Assert.NotNull(…)`, 476 rows), and the methods of Rust's and C++'s
standard types (`lock`, `clone`, `kind`, `c_str`): of the 6,237 rows of
0.5.25, 1,610 are left unmarked — PHP 396, TypeScript 315, Ruby 298, Rust
119, Swift 93, C# 79, Kotlin 67, JavaScript 55, C++ 50, Java 44, Go 34, C
33, Python 17, TSX 6, Scala 4. 0.5.27 leaves 1,545: Swift 61 from 93, Java
32 from 44, Rust 101 from 119. 0.5.28 leaves 1,486: Ruby 239 from 298, with
99 fewer "bare name: not in scope" rows — faraday's spec lets. Since 0.5.29
one more kind is marked: the one definition is a member of a type that
implements a library type declaring members (`class UnvalidatedUriRequest
implements RequestInterface`, `class Http2ClientRequest extends Writable`)
and the receiver has no type the source gives (an untyped parameter or
local, a closure's parameter, a `$variable`, a field of no named type), so
it may be any implementation: guzzle's `$request->getHeaderLine()` and got's
`response.setHeader()` in a test server's callback. A marker base
(`Sendable`, `JsonSerializable`, `Closeable`, an exception root) does not
count, nor a Ruby base or mix-in (a class's own methods stay its own), nor a
member extension function. 477 rows: got 191, guzzle 253, the rest JVM and
C# tests; 1,009 are left unmarked — Ruby 239, PHP 137, TypeScript 124, Rust
100, C# 76, Swift 61, JavaScript 55, C++ 50, Kotlin 47, Go 34, C 33, Java 26,
Python 17, TSX 6, Scala 4. The mark does not know which members the library
type declares: symfony-demo's `$user->getFullName()` is marked beside the
`getRoles()` `UserInterface` does declare. 0.5.30 marks the C and C++ calls
on `std` (`std::async(…)`, and a member of a field or local declared with a
`std::` type, as written, `Def::written_head` / `Local::written_head`: the
readers of types drop a C++ namespace), and reads C++ locals; C++ is left
with 28 unmarked rows, from 50, with 12 after 0.5.31's namespaces 6 after 0.5.32's aliases and none after 0.5.33's preprocessor branches.
Engine 0.5.38 marks four more kinds, found by reading the 801 rows 0.5.37
left unmarked: the path reaches a field, variable or function written with a
library's type, or a local constructed from a type the project does not
define (`$easy->response->getHeaderLine()` with `?ResponseInterface
$response`, `self.builder.header()` on an `http::request::Builder`, `new
\ReflectionProperty(…)`; never when the one definition is an extension, which
may be written for that very type); a bare call where a method is not in
scope by its bare name (PHP `\rewind($h)`, `\getenv(…)`); a bare name whose
one definition is an RSpec `let` of another block of the file (`uri =
conn.build_exclusive_url(…)` in an `it` beside a `let(:uri)`); and Ruby's
`dup`. A `let` is no candidate for a member call either. 576 are left
unmarked — Ruby 175, TypeScript 87, C# 75, Rust 64, Swift 61, Kotlin 42, PHP
29, Go 14, Java 12, Python 9, TSX 4, Scala 4. Read by hand, what is left is
mostly four things: extension methods called on a library's type (C#,
Kotlin, Swift), locals of a file-level callback (`test('…', () => { const
request = … })`), the members of a Ruby `Struct`, and a library's generic
holding a project type (`http::Response<Body>`). One of the C# rows is a
parse: an `#if` inside an expression costs
`HttpResponseExtensions` its class. 0.5.39 fixes that parse and links
the extensions for a library's type: 509 are left unmarked — Ruby 175,
TypeScript 87, Rust 64, Swift 49, C# 40, PHP 29, Kotlin 22, Go 14, Java 12,
Python 9, TSX 4, Scala 4. 0.5.40's block scopes leave 501: TypeScript 39
from 87, and Ruby 215 from 175 — a receiver that was no name at all
(`head name not found`) is an untyped local of its block now, and the
members of faraday's `Struct` options asked of it are the rows. 0.5.41
marks those: a member an untyped receiver is asked, of a name a type
declares with no `def` (a `Struct` member, `attr_accessor`,
`def_delegators`), may be that type's as well as the one `def`'s — 131
rows, all Ruby (`adapter` 26, `response_headers` 23, `response_body` 22,
`headers` 15, `status` 14). 383 are left unmarked: Ruby 97, Rust 64, Swift
49, C# 40, TypeScript 39, PHP 29, Kotlin 22, Go 14, Java 12, Python 9, TSX
4, Scala 4. Ruby's largest group left is a `let` shared through
`shared_examples` (`request_stub`, 20 rows of faraday's adapter examples),
which nothing reads yet.) The 2,114 by language: PHP 445, TypeScript 405, Ruby 304, C# 196, Rust 192,
C++ 169, Swift 94, Kotlin 79, JavaScript 71, Java 49, Go 34, C 33, Python
20, TSX 19, Scala 4. They are still an upper bound: the marks are
conservative, and PHP's and TypeScript's rows are mostly PSR-7 and Node
names on parameters the source gives no type (`$request->getUri()`,
`response.setHeader(…)`).

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

Rerun with engine 0.4.0 and its shared state, the audit raises nothing new
but one convention finding, fixed since: `graphlore_surprises(limit=0)` cut its
`shared_state` list without saying how much (`shared_state_left_out` does now).
`SKELETON-EMPTY` went from 5 to 0, and the tool time stayed at 131 s.

Rerun on graphs built by engine 0.5.3, and again by 0.5.4 through 0.5.27
(2026-11-18), the audit raises nothing on the native graphs but its
`GRAPH-ORPHAN-NODES` note on 19 of them; 115 to 172 s of tool time (on the 0.5.7 run, retrofit's build through `graphlore_build` took 5.1 s and was reported `SLOW` for crossing 5 s). What the table above still listed is
gone:

- **`REPLY-TOO-LARGE`**: `graphlore_neighbors` cuts a hub at `limit` and says
  how its neighbors split by relation, `graphlore_fetch` cuts one definition
  over 8,000 tokens at a line and names the budget that returns all of it,
  and `graphlore_same_names` lists the first 25 definitions of a name
  ([tools](tools.md)). As JSON, the 752 neighbors of retrofit's `Call` went
  from 99 KB to 25 KB, guzzle's 3,100-line `CurlFactory` from 124 KB to 29 KB,
  cpr's 647 definitions named `TEST` from 134 KB to 6 KB.
- **`ROUTES-SILENT-EMPTY`**: express's examples take the framework by
  `require('../../')`; a relative import of the directory whose `package.json`
  names a server framework now counts as importing it (139 route nodes).
- **`TEST-PATH-MISSED`** and a `FETCH-SHADOW-SPAN` this run raised were the
  audit's own: `[Test],` as an element of a list, `#[cfg(test)]` on a `let`,
  and Ruby's `class ActiveSupport::TestCase` compared by its whole name.

An earlier version of this page reported a rerun "with engine 0.5.2" that had
in fact read graphs engine 0.5.1 built: the audit kept a native graph it found
on disk. It now rebuilds one built by another version of the engine and says
so.

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
4. **Shared state and resources.** Done (this page): what two definitions
   couple through without a call.
5. **HTTP routes and requests.** Done (this page): route nodes with full
   paths in 10 languages, and the requests that reach them. Git co-change and
   the call-path check of shared-state pairs are done on graphlore's side
   ([concepts](concepts.md#files-that-change-together)), for both engines.
6. **No CLI behind the CLI tools.** `query`, `path` and `explain` answered over
   graph.json in Python; community naming by MCP sampling only; the
   package-API extractors moved into the language modules.
7. **Native by default.** The polyglot audit passes on native graphs (above),
   so what is left is publishing the wheels (below) and making `auto` the
   default; then
   graphify, `graphlore_add` and `mode="deep"` go, and the output directory gets
   graphlore's own name (still reading `graphify-out/`).

Publishing `graphlore-engine` wheels (Linux, macOS and Windows, x86-64 and
ARM) comes before the plugin can install it; until then it is built from a
checkout.
