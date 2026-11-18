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
[polyglot audit](benchmark-polyglot.md) (graphify 0.9.111, engine 0.5.19,
measured 2026-11-18), as native / graphify edges and the share of graphify's
that the native graph has too:

| Language | same definition | imports (file pairs) | calls | bases | type references |
|---|--:|--:|--:|--:|--:|
| Python | 100% | 212 / 219 (96%) | 2,963 / 834 (94%) | 73 / 62 (100%) | 439 / 494 (68%) |
| JavaScript | 93.5%⁵ | 190 / 305 (59%) | 1,341 / 2,058 (43%)¹ | 1 / 1 | 3 / 0 |
| TypeScript | 99.4% | 527 / 634 (83%) | 805 / 687 (98%) | 25 / 20 (100%) | 737 / 156 (88%) |
| TSX | 99.7% | 253 / 316 (80%) | 435 / 456 (95%) | – | 42 / 4 |
| Go | 100% | 538 / 950 (47%)² | 6,702 / 3,167 (99%) | 19 / 14 (100%) | 678 / 355 (99%) |
| Java | 100% | 1,105 / 1,010 (100%) | 5,680 / 5,123 (85%) | 415 / 168 (94%) | 3,989 / 1,930 (98%) |
| Kotlin | 99.4% | 368 / 333 (95%) | 4,705 / 2,108 (71%) | 96 / 56 (93%) | 1,079 / 673 (91%) |
| Scala | 100% | 2 / 0 | 46 / 20 (45%) | 21 / 22 (95%) | 75 / 32 (100%) |
| C# | 92.7% | 347 / 280 (30%)³ | 2,811 / 2,450 (94%) | 87 / 86 (98%) | 1,216 / 806 (99%) |
| Rust | 93.3% | 314 / 370 (75%) | 2,009 / 853 (86%) | 53 / 41 (93%) | 1,245 / 895 (81%) |
| Swift | 95.9% | 166 / 166 (–)³ | 5,445 / 2,141 (82%) | 190 / 164 (100%) | 1,091 / 580 (62%) |
| C | 100% | 89 / 77 (99%) | 864 / 791 (100%) | – | 253 / 130 (97%) |
| C++ | 97.8% | 215 / 228 (93%) | 631 / 281 (33%)⁴ | 30 / 5 (100%) | 326 / 218 (88%) |
| C/C++ headers | 90.6% | 164 / 172 (95%) | 361 / 348 (85%) | 11 / 12 (92%) | 515 / 289 (87%) |
| Ruby | 99.7% | 42 / 0 | 445 / 375 (70%) | 46 / 15 (93%) | 28 / 0 |
| PHP | 80.6% | 429 / 505 (84%) | 8,870 / 6,140 (90%) | 65 / 60 (98%) | 648 / 213 (95%) |
| **All** | **94.4%** | **4,961 / 5,565 (75%)** | **44,115 / 27,832 (85%)** | **1,132 / 726 (97%)** | **12,364 / 6,775 (89%)** |

Of the native calls, 13,015 (30%) are `INFERRED`, against 47% of graphify's.
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
  of a generic and a plain overload a call without type arguments is
  (`client.GetAsync(request)` beside `GetAsync<T>(…)`): each is linked,
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
5.4 s, got 0.9 s / 4.7 s (engine 0.5.1; with all that 0.5.19 resolves the same
31 builds take 16.3 to 18.9 s through Python, 16.1 to 18.1 s in the engine alone, retrofit 3.7 to 4.1 s). An update after a one-file change takes a third of
a build ([above](#incremental-updates)).

### What is left without an edge

The comparison above measures the native graph against graphify's. What the
engine itself leaves out is measured by `benchmarks/unresolved.py`: `python -m
graphlore_engine build ROOT --unresolved PATH` writes every call that got no
edge although its name is the one callable of that name in the project, with
the reason the walk stopped (graph.json is the same with or without it). A
name a library also has (`setHeader`, PSR-7's `getHeaderLine`, PHPUnit's
`fail`) is counted too, so the table is an upper bound and its samples
(`--samples N`) say which rows are real. On the 31 repositories, without
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
C# 349 from 470). Most of what C# has left is not the project's: `Assert.
NotNull` beside its own `Ensure.NotNull` (77), `WireMockServer.Start` (23).

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

Rerun on graphs built by engine 0.5.3, and again by 0.5.4 through 0.5.19
(2026-11-18), the audit raises nothing on the native graphs but its
`GRAPH-ORPHAN-NODES` note on 19 of them; 115 to 159 s of tool time (on the 0.5.7 run, retrofit's build through `graphlore_build` took 5.1 s and was reported `SLOW` for crossing 5 s). What the table above still listed is
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
