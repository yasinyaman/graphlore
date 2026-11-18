# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Files that change together.** graphlore reads the project's git history
  (one local `git log`: the last 1000 commits that are not merges, renames
  followed, commits of more than 30 files skipped) and measures, per pair of
  files, code-maat's degree of change coupling; cached on `HEAD`, for graphify
  and native graphs alike, nothing written to `graph.json`.
  `graphlore_surprises` lists in `co_change` the source files that change
  together with no edge of the graph between them (tests left out), and
  `graphlore_node_details` the files a node's file changes with most and
  whether the graph joins them (`linked`). Both say how much history was read
  (`history`), and a shallow clone or a history under 10 commits is named
  rather than shown as an empty list. On nine audit repositories deepened to
  their last 1000 commits, 239 such pairs, read by hand all real: a websocket
  client and its server, Rails routes and models with `schema.rb`, parallel
  implementations kept in step; `git log` took at most 230 ms.
- **Shared state, pair by pair.** Each `shared_state` row of
  `graphlore_surprises` lists its `pairs` (a writer and another definition
  touching the state), whether a call path joins them (`call_path`, either
  way, over calls, dispatch and HTTP, within 6 hops) and whether their files
  change together (`co_change`). The score counts hidden pairs (no call path)
  instead of files that import neither the other, and a hidden pair whose
  files change together raises it again; a row whose pairs are all joined by
  calls says so (`every pair is joined by a call path as well`).
- **Routes and HTTP requests in the native engine** (graph schema 5, engine
  0.5.0), in Python, JS/TS, Java, Kotlin, C#, Go, Rust, Ruby and PHP: every
  route is a `type: route` node with its
  `method`, its full `pattern` and a normalized `key` (`GET /api/v1/users/{}`),
  joined to its handler by a `handles` edge. The full path is composed across
  files, which `graphlore_routes` cannot do: FastAPI `APIRouter(prefix=…)` and
  `include_router(…, prefix=settings.API_V1_STR)` (a constant's value is
  read), Flask blueprints, Express `app.use('/api', router)` and
  `app.use('/x', require('./x'))`, Fastify `register(…, { prefix })`, NestJS
  `setGlobalPrefix`; within a file, Spring and JAX-RS class prefixes, Ktor's
  nested `route("p") { … }` and ASP.NET `[Route]` controllers and `MapGroup`
  groups (kept per method, so two `var group = …` do not mix), chi `Route`
  lambdas, actix scopes, the Rails `routes.draw` DSL (`resources`,
  `namespace`, `scope`) and Laravel `prefix()->group()`; across files, gin /
  echo groups (`v1 := router.Group("/v1")`) and the router passed to a
  function (`addUserRoutes(v1)`), axum `.nest("/api", users::router())` /
  `.merge(…)` and actix `.configure(routes)`, and Laravel's route-file prefix
  (`Route::prefix('api')->group(base_path('routes/api.php'))`, Laravel 11's
  `withRouting(api: …)`). A Rails `"articles#index"` or Laravel
  `[UserController::class, 'store']` handler is the controller's method (a
  singular `resource :user` is `UsersController`'s). A handler
  written as a lambda that makes one call (`post { users.register(ctx) }`,
  `(req, res) => ctrl.show(req, res)`) is the method it calls. A prefix whose value is unknown is written as a parameter
  (`/{API_PREFIX}/users`) and the node says `prefix_unresolved`. Requests the
  code makes (`fetch`, `axios` and clients made by `axios.create({ baseURL })`,
  generated SDKs such as `client.get({ url: '/api/v1/items/' })`, jQuery,
  Angular `HttpClient`, `EventSource`, `requests`, `httpx`, `aiohttp`, Feign
  `@RequestLine`, the mappings of a `@FeignClient` / `@HttpExchange` /
  `@RegisterRestClient` interface and Retrofit's `@GET` (requests, not
  routes, unlike `graphlore_routes`), `RestTemplate`, `WebClient` /
  `RestClient`, MockMvc, RestAssured, the Ktor client, C# `HttpClient` with
  a relative URL such as `$"todos/{id}"`, Go `http.Get` / `http.NewRequest`,
  reqwest / ureq, HTTParty / Faraday, Guzzle and Laravel `Http::`, and in
  tests rack-test and Laravel's `$this->getJson(…)`) are
  linked to the route they reach by `http_calls`: `EXTRACTED` when the method
  is known and every segment matches (a parameter or a template hole against
  a parameter), `INFERRED` for a value against a parameter, an unknown method
  or base, or several candidates. A path several routes take goes to the
  nearest. Only production code is linked: requests in tests are counted
  (`report.http.test_calls`), as are those to another host and those whose URL
  the source does not spell out. `graphlore_impact` follows both relations, so
  a handler's dependents reach the client code that requests it, and
  `relations="http"` names them. In fastapi-template all 23 requests of the
  generated SDK reach their 23 handlers, in kotlin-spring-realworld the 6
  Feign requests theirs, and in todoapi 7 of the 9 `HttpClient` requests (the
  other two go to routes `MapIdentityApi` adds at run time), and in
  gin-examples a browser `EventSource('/stream/' + id)` the Go handler of
  `GET /stream/:roomid`. Across the 31 audit repositories, 2375 of
  `graphlore_routes`' 2376 rows are routes of the native graph (69 with a
  longer path; 60 with a closure kept in a variable for a handler, which the
  native graph names by the function around it). Builds take 7% longer
  (median of ten repositories against engine 0.4.0).
- On a native graph (schema 5), `graphlore_routes` lists the graph's route
  nodes (`source: "graph"`): the full path, the handler, and `called_by` /
  `callers`, the code whose requests reach the route; on a graphify graph it
  reads the source as before (`source: "source"`). `graphlore_node_details`
  has an `http` section: the routes a handler serves and who requests them,
  the routes a client definition requests, or a route's handlers and callers.
- **Shared state in the native engine**: variables, constants and class-level
  fields are nodes in all 14 languages (`kind: variable | constant |
  property`), and the definitions that read or write them are linked by
  `reads` / `writes` edges: assignments, `+=`, `x[k] = v`, `del`, `++`, and a
  mutating method (`append`, `set`, `clear`, …) on a collection or a builder,
  `INFERRED` since only its name says so. Locals, parameters and Python names
  assigned without `global` are left out; a constructor setting its own
  object's fields builds it and couples nothing. Two definitions touching one
  such node affect each other without either calling the other. Member reads
  of a property or constant that were `references` are `reads` now. Fields of
  instances count too (`self.x`, `this.x`, `@x`, declared fields), and so do
  **resources**, by a literal key: environment variables (`os.getenv`,
  `process.env.X`, `ENV[...]`, …), files (`open(p, "w")`, `fs.writeFileSync`,
  …), tables named in SQL strings or reached through an ORM model
  (`User.objects.filter`, `Post.create`, `db.Items.Add`; the model's table
  from `__tablename__`, `Meta.db_table`, `@Table`, `$table`, `DbSet<T>`, …),
  events a project both emits and listens for, and cache keys
  (`localStorage`, Redis-like stores). A resource is a `type: resource` node
  only when two definitions touch it. On the 31 audit repositories this adds
  16% edges; builds take 15% longer (median). Schema 4, engine 0.4.0.
- **`graphlore_impact(shared_state=True)`** walks shared state: the
  dependents of a writer include the code that reads what it writes, the
  dependencies of a reader the code that writes what it reads (a `written_by`
  step). By default reads and writes are not walked and are counted in
  `state_nodes_skipped`; `relations="state"` names them.
- **`graphlore_surprises` lists shared state** (`shared_state`): variables,
  fields and resources written in one file and read or written in another,
  scored by whether the files import each other, their top-level directories
  and their communities.
- **`graphlore_node_details` says what a definition reads and writes**, and
  which other definitions share that state with it, and how.
- **A native extraction engine, `engine/`** (experimental, opt-in): graphlore's
  own extractor, written in Rust and built with maturin + PyO3 into a separate
  package, `graphlore-engine`, with tree-sitter grammars for Python,
  JavaScript, TypeScript, TSX, Go, Java, Kotlin, Scala, C#, Rust, Swift, C, C++,
  Ruby and PHP compiled in — nothing is downloaded and nothing reaches the
  network. It writes a graphify-compatible `graph.json` with a node per file,
  per definition and per Markdown / reStructuredText heading, and the
  `contains` / `method` / `case_of` edges between them. Each definition node
  carries its real span (`region_start`, `end_line`, `source_location` as
  `L13-L15`), `kind`, `qualname` and `language`; Rust `#[cfg(test)]` and
  `#[test]` items are marked `test`. Ids and labels follow graphify's scheme
  (25 of the 26 code definitions in the polyglot fixture get the same id and
  parent from both engines). The definition rule is a port of `spans.py`'s: on
  ~95,000 definitions in real Go, JS, TS, Rust, Ruby, Java, C#, PHP, Kotlin,
  Swift, Scala and C/C++ files it returns the same spans as `spans.py`, apart
  from 2.5% of C/C++ headers where the grammar versions differ, and on 30,000
  Python definitions the same spans as the stdlib `ast`. Per language, a module
  overrides only what its grammar does differently. No communities yet; every
  native build says so.
- **Imports, calls, bases and type references in the native engine**, in all
  14 languages. The engine reads import statements, calls and constructions,
  base lists, types in signatures, fields, variables and type arguments,
  decorators and attribute reads, and resolves them the way each language
  does: module paths per language (Python's root rules, JS/TS relative paths
  with `tsconfig.json` paths and workspace packages, `go.mod`, JVM / C# / PHP
  packages and namespaces, Cargo crates and the module tree, Ruby
  `require_relative`, SwiftPM targets, C/C++ includes), names through the
  enclosing scopes, the file, its imports and re-exports, its package or
  namespace, and receivers through typed parameters, fields, assigned
  constructions and declared return types (builder chains included). What that
  finds is `EXTRACTED`; a unique name across the project, with import
  evidence for a method, is `INFERRED`; anything else gets no edge and is
  counted in the build reply. Import edges carry `runs` (module-level,
  deferred, type-checking); external modules and bases are `type: external`
  nodes with an `ecosystem`. Properties (C#, Swift, Kotlin, TS/JS class
  fields) and module-level constants are nodes. graph.json schema 2. Compared
  with graphify on the 31 audit repositories, the native graph has 77% of
  graphify's call edges, 75% of its file-level imports, 96% of its bases and
  85% of its type references, while 15% of its calls are inferred against
  47% of graphify's; most of the rest is graphify's name guesses and artefacts
  (`docs/native-engine.md` has the per-language table and samples). Builds stay
  3–6 times faster than graphify's.
- **Communities in the native engine** (`engine/src/community/`): Leiden on the
  internal nodes, with edges weighted by what they say (containment and calls
  1.0, bases 1.5, imports 0.7 / 0.4, type references 0.5), by confidence
  (`INFERRED` × 0.5) and by where their ends live (same directory × 1.25, other
  top-level directory × 0.6, test to production × 0.1), plus a weak tie between
  the files of a directory; external nodes join their neighbours' community.
  Then graphify's rules (isolated nodes alone, oversized and sparse
  communities split, ids by size) and names after the most connected member,
  prefixed with the directory most members share. Nodes are visited in the
  order of a hash of their id and a build starts from the previous graph's
  communities and settles, so after a one-file change 99.8–100% of nodes keep
  their community; ids are kept by overlap, and a name saved for a community
  stays while its members mostly do (stale ones are dropped from
  `.graphify_labels.json`, and the build says how many). On the 31 audit
  repositories there are as many communities as graphify finds (90.5 against
  90.6 per repository), with 87% of a community's members in one directory
  (graphify: 83%) and 8% of test nodes in a production community (21%).
  graph.json schema 3: `community` / `community_name` on every node and
  `graph.communities` in the header. `python -m graphlore_engine cluster GRAPH`
  detects them again.
- **Incremental native updates**: every native build writes a facts cache
  (`graphify-out/cache/graphlore-native.bin`), and `graphlore_build(update=True)`
  reuses the facts of the files whose size and modification time, or content,
  are unchanged, and parses the rest; resolution and communities run over the
  whole project, so an update writes the graph a full build writes. retrofit:
  2.6 s for a full build, 1.0 s for an update after one file changed. The build
  reply says how many files were parsed and reused; `verbose=True` adds the
  time per phase.
- **`benchmarks/compare_engines.py --communities`** compares the two engines'
  communities: count, sizes, modularity, directory purity, test mixing and
  their agreement (NMI).
- **`graphlore_build` on a native graph trusts its edges**: `ImportChecker`
  takes an edge's `runs` as when the import runs and rejects nothing, and the
  calls and imports `resolve.py` adds to graphify's graphs are not added to a
  native schema-2 graph (`graph._engine_resolved`).
- **`benchmarks/compare_engines.py` compares edges** (imports as file pairs;
  calls, bases and type references over the definitions both engines have),
  and **`benchmarks/polyglot.py --engine native`** runs the whole audit on
  native graphs (built into `graphlore-native/`). On the 31 repositories it
  raises no high finding (7 on graphify's graphs) and no `FETCH-NO-SPAN` (94),
  and every tool but the community ones answers as on graphify's graphs.
- **`GRAPHLORE_ENGINE`** picks the extractor `graphlore_build` uses: `graphify`
  (the default, unchanged), `native` (an error naming the install command when
  the engine is missing; `mode` and `cluster_only` refused; `update` rebuilds
  the whole graph) or `auto` (native when installed, graphify for what native
  cannot do and for updates of a graph graphify built). The native build runs
  in a child process bounded by `GRAPHLORE_TIMEOUT`, and records
  `built_at_commit` for freshness. With the native engine chosen, a file whose
  Python tree-sitter grammar is missing (no `[treesitter]` extra, or offline)
  gets its spans from the engine's compiled-in grammars.
- **`benchmarks/compare_engines.py`**: the native graph of a repository against
  graphify's, per language — exact, near and same-file matches, what only one
  engine has, and whether matched definitions share a parent and relation.

### Changed
- **Three replies that could run past 90 KB are cut, and say what they left
  out** (the polyglot audit's `REPLY-TOO-LARGE` on native graphs, five to
  none). `graphlore_neighbors` takes `limit` (200): past it the result carries
  `total`, `truncated` and `counts` by relation and direction (retrofit's
  `Call`, 752 neighbors: 99 KB to 25 KB as JSON). `graphlore_fetch` returns one
  definition whole up to 8,000 tokens or `budget_tokens`, whichever is larger;
  a longer one is cut at a line with `cut` (`lines_shown`, `lines_total`,
  `tokens_total`), `lines_left_out` and a hint naming `graphlore_skeleton` and the budget that
  returns all of it (guzzle's 3,100-line `CurlFactory`: 124 KB to 29 KB).
  `graphlore_same_names` lists the first 25 definitions of a name and counts
  them all in `definitions_total` (cpr's 647 definitions named `TEST`: 134 KB to 6 KB).
- With the native engine, `graphlore_build(cluster_only=True)` detects the
  communities of the graph on disk again (it was refused), and `update=True`
  is incremental (it rebuilt everything); under `GRAPHLORE_ENGINE=auto` both
  still go to graphify when the graph on disk is graphify's.
- On a native graph built before the engine detected communities,
  `graphlore_communities` says so and names `graphlore_build(cluster_only=True)`.
- `graphlore_fetch` returns the lines a native property or constant node
  carries (`region_start`..`end_line`) when the span engine has no span for it
  and the file is unchanged since the build, instead of a line window.

### Changed
- **A call of an overloaded name that the arguments do not decide is an
  `INFERRED` edge to each overload that fits** (native engine 0.5.5; Java,
  Kotlin, Scala, C#, Swift, C++), where it was an `EXTRACTED` edge to the
  first. `baseUrl(server.url("/"))` may be `baseUrl(String)`, `baseUrl(URL)`
  or `baseUrl(HttpUrl)`: all three are callers' targets now, so
  `graphlore_impact` on any of them lists the call, and `strict=True` (only
  extracted edges) leaves it out. 999 calls of the 31 audit repositories
  changed this way; the share of `INFERRED` calls goes from 20% to 29%.

### Fixed
- Native engine 0.5.5, what 0.5.4 could not decide. 759 more edges on the 31
  audit repositories and 32 gone (most of those a loop variable taken for a
  function of its name: `for (const hook of hooks) hook(x)`):
  - **Untyped receivers, by what is asked of them.** A parameter or local
    with no type is the one type of the project that answers every member
    used on it in its definition, when two names or more that are not the
    language's own are used: Ruby `def call(env)` with `env.status`,
    `env.body` and `env.parallel?` is an `Env`, where `env.status` alone named
    a struct and a class. Python, JavaScript and PHP parameters, and locals
    the walk cannot type in any language, are typed the same way.
  - **More arguments typed for the choice among overloads**: a field,
    property or constant by its declared type or its value's; a lambda
    against a function-typed parameter; a class literal against `Class` /
    `KClass`; a concatenation, a comparison, `x.toString()`, a cast. An
    overload that takes exactly the arguments passed comes before one that
    needs a default; overloads a base class adds compete.
  - **Elements of typed collections.** The variable of a loop over a
    `List<Foo>`, `Foo[]`, `[]Foo` or `list[Foo]` (or over a call that
    declares it returns one), the parameter of a lambda given to `forEach` /
    `map` / `filter` (Kotlin's `it`), `xs[0]` and `xs.first()` are `Foo`.
  - **Type parameters bound by an argument**: `fun <T> pick(x: T): T` gives
    the argument's type, `fun <T> head(xs: List<T>): T` its element's.
  - Go `resp, err := client.Get(url)` takes the first of the function's
    results (it took the last, `error`): 235 calls in resty. Java `var x =
    …` and `for (var x : xs)` are typed by the value; `var` was read as the
    name of a type.
  - A local that holds a value and is called (`handler(x)`, `builder::add`)
    is no longer a construction of its type.
  - A local assigned a call of its own name whose arguments name another
    local (`let session = session(using: c)`) no longer sends the resolver
    round in circles (a crash on four files of alamofire while this was
    being written, caught by the comparison run; a golden holds it).

  The audit on graphs rebuilt by 0.5.5 raises nothing but its note on orphan
  nodes; the 31 builds take 16.1 to 16.6 s (14.6 to 15 s with 0.5.4).
- Native engine 0.5.4, the three things 0.5.3 still named as missing. 1,088
  more edges on the 31 audit repositories and 71 gone, and 1,175 calls moved
  from one overload to another of the same name:
  - **Overloads** (Java, Kotlin, Scala, C#, Swift, C++). A call was linked to
    the first definition of a name; it is now the one that can take the call:
    by the count of arguments (defaults and varargs counted), the name an
    argument is passed under (`named(b = "x")`, `retries: 3`, Swift's labels),
    and the type of each argument the site shows — a literal against `String`
    / `Int` / `Boolean`, a typed local or a construction against a parameter
    typed by a class of the project. `addParameter("value", String::class)`
    no longer lands on `addParameter(parameterSpec)`. Constructors are chosen
    the same way: Swift `View(frame: f)` and `View(name: "x")` are two
    constructors (462 calls in alamofire, where the site decided nothing
    before); a Kotlin primary constructor competes with the secondary ones;
    an overload marked `@Deprecated` / `[Obsolete]` gives way to one that is
    not. Kotlin annotations with arguments (`@Deprecated("old")`) are read as
    the decorators they are (303 references).
  - **The receiver or the class around.** Where a lambda's receiver and the
    class around both have a method of the name, the arguments' types decide
    (`buildCodeString { emit(this) }` passes a `CodeWriter`, which only the
    outer `emit` takes); the guess remains for the calls they do not.
  - **Results that are type parameters.** A method returning the type
    parameter its type binds to itself (`tag(x): T` in `Taggable<T :
    Taggable<T>>`) returns the type it was called on. A local assigned a call
    handed a type (`retrofit.create(Service::class.java)`, `mock<Foo>()`,
    `sp.GetRequiredService<IFoo>()`) is that type, when the function returns
    its own type parameter or is a library's and not named like a wrapper
    (`listOf<Foo>()`).
  - **Kotlin `expect` / `actual`**: of the declarations of one extension, one
    per source set, the nearest to the caller, where several candidates used
    to mean none.
  - **Untyped receivers in Ruby and Swift.** A method named on a parameter or
    untyped local is linked (`INFERRED`) when no other type answers to the
    name: no property or field of it, no `attr_reader` / `Struct.new` member /
    delegator, and not a name of the core library (`shift`, `gsub`,
    `sorted`). `env.needs_body?` is found; `env.status`, which a struct and a
    class both answer, is not guessed.

  The audit on graphs rebuilt by 0.5.4 raises nothing but its note on orphan
  nodes; the 31 builds take 14.6 to 15 s (14.2 s with 0.5.3).
- Native engine 0.5.3, what 0.5.2 still lacked. 1,859 more edges on the 31
  audit repositories (calls 41,063 to 42,408), 121 gone, most of those a
  local taken for a function of its name:
  - **Kotlin locals were typed only when the declaration wrote a type.** In
    `val c = Writer(out)` the value sits beside the declaration, where the
    walk did not look: `c.close()` reached nothing, or a method of that name
    elsewhere. kotlinpoet gains 747 calls, such as `spec.toString()` to the
    right class's `toString`.
  - **A local keeps the chain it is assigned** (`val b = builder(x).tag(y)`,
    Go `res := c.R().SetBody(b)`): it was dropped as untypeable, and is now
    typed by following what each call returns (528 calls in resty). Inside
    its own initializer a name is still the outer one (`val builder =
    builder(x)`, Rust `let tls = tls::Config::builder()`).
  - **Results read from the body.** A function that declares no result
    returns what its `return`s and expression body construct, when they agree
    (`fun builder(n) = Builder(n)`, Python `return Conn()` beside `return
    None`, `return self`, the last expression of a Ruby method); a closure's
    `return` is not the function's. Calls through such a result are
    `INFERRED`. In Ruby a name called without parentheses is followed the same
    way (`stubs.match(env)`, `s = stubs; s.match(env)`), and a local, typed or
    not, is no longer read as a call of the type it holds.
  - **Kotlin lambdas with a receiver.** In `x.apply { … }`, `x.run { … }`,
    `with(x) { … }` and a lambda passed to a parameter typed `T.() -> R`
    (`buildCodeBlock { indent() }`), a bare name is the receiver's member
    before it is the class's around. `out.apply { emit("x") }` inside an
    `emit` was a call of itself. Where both have the name the receiver's is
    taken as a guess, unless it takes fewer parameters than the call passes or
    the call hands over `this` (`buildCodeString { emit(this) }`).
  - **Constructions typed by their declaration**: C# `Box b = new(1, 2)` and
    C++ `Buf b(3)` / `Buf b{3}` call the type and its constructor (98 calls in
    cpr, 28 in the C# repositories).
  - **A parameter or local of no known type is an unknown receiver**: a method
    only one type of the project has, near the caller, is `INFERRED` (as it
    already was on the result of an unknown call). Not in Ruby and Swift,
    which have no import to say which type is meant. In Rust, a method of an
    `impl Trait for` a type of another crate is found on a local of that type
    (38 calls in ureq).
  - **Routes in a framework's own repository.** A file that takes the
    framework by a relative path (`require('../../')` in express's examples)
    imports it: the directory's `package.json` names it. Both `graphlore_routes`
    and the native engine read such files now (express: no route to 53, and
    139 route nodes with its tests' in the native graph).
- `benchmarks/polyglot.py --engine native` read whatever native graph was on
  disk: after an engine change it audited the previous engine's graphs, and
  the rerun reported for engine 0.5.2 had read 0.5.1's. It now rebuilds a
  graph built by another version of the engine and says so. It also no longer
  reports `[Test],` (an element of a list) or `#[cfg(test)]` on a `let` as a
  test the path rules miss, nor Ruby's `class ActiveSupport::TestCase` as a
  span of another symbol. On graphs built by 0.5.3 the audit raises nothing
  but its note on orphan nodes.
- Native engine 0.5.2, the gaps the 0.5.1 comparison left (`calls` recall
  against graphify 82% to 84% on the 31 audit repositories; C# 80% to 93%,
  Ruby 60% to 66%, Kotlin 64% to 70%, Java 81% to 85%):
  - **Extension functions and methods** are members to their callers: Kotlin
    `property.toPropertySpec()` (`fun KmProperty.toPropertySpec()`), C#
    `realm.IsBlank()` (`static bool IsBlank(this string s)`, from a file that
    uses its namespace), and `self.userinfo()` in a Rust `impl Trait for` a
    type of another crate. `EXTRACTED` when the receiver's type is the one the
    extension is written for, `INFERRED` when one visible extension has the
    name.
  - **A closure's parameters are scoped to the closure.** They were locals of
    the whole function: in Go, `r` of `func(w http.ResponseWriter, r
    *http.Request)` hid the `r := chi.NewRouter()` around it, and an untyped
    block parameter could name a function elsewhere (`done` in a test, linked
    to a library's `done()`).
  - **Ruby.** A bare name passed as an argument or as a pair's value
    (`Article.new(article_params)`, `status: status_code`) and the receiver of
    a call (`current_user.follow(@user)`) are calls of the method, no longer an
    `indirect_call` or nothing; `self.class.from(x)` is the class's method. A
    module or class reopened in another file is one scope
    (`Utils::Headers.new`, `Faraday::ParsingError.new` and `class Far::Compact`
    resolve). A method at the top level of a file the caller does not require
    is no longer in scope, and the name in `def headers=(hash)` is no call.
  - **Anonymous classes.** `new Callback<String>() { … }` (Java) and `object :
    Listener<T> { … }` (Kotlin): the method around constructs the type, the
    anonymous class `implements` / `inherits` it (also when the type is
    external), and no name lookup lands on the anonymous class itself.
  - **Constructors.** A construction is a call of the constructor the type
    declares as well as of the type (`new RestRequest(url)` → `RestRequest`
    and its `.RestRequest()`; Python `__init__`, JS/TS `constructor`, Ruby
    `initialize`, PHP `__construct`, Swift `init`), so `graphlore_impact` on a
    constructor lists the code that builds the type. Of several overloads the
    one taking as many arguments as the site passes is named; when that picks
    none or several, only the type is.
- Native engine 0.5.1, from comparing its graphs with graphify's edge by
  edge on the 31 audit repositories:
  - A call in a field assignment inside a method (`self._uri = urlparse(url)`
    in `__init__`, `this.x = make()` in a constructor) was recorded as the
    class's call, not the method's, so `graphlore_impact` on the method missed
    it. Python's `calls` recall against graphify goes from 92% to 94%.
  - PHP variables were never typed: `$factory = new CurlFactory();
    $factory->create()` and a typed parameter's `$given->release()` now reach
    the class's method (PHP 81% to 90%; `$x` stays a variable and is never
    resolved as a function or class named `x`).
  - A name bound to a required module or one of its members (`var parse =
    require('./utils').parse`, `const mongoose = require('mongoose')`) is an
    import, not a variable or constant node: `parse(v)` is a call of the
    function again, and the binding is no longer listed as state.
  - A local whose value the walk cannot type hid nothing: `let mut pool =
    self.pool.lock().unwrap(); pool.get(…)` was an `EXTRACTED` call of an
    unrelated top-level `get`, and a parameter named like a function elsewhere
    (`token_headers: dict`, a minified bundle's `t`) was that function passed
    as a value. Such a local now hides the outer name (1,079 `indirect_call`
    edges gone, nearly all in minified bundles); a bare call beside a typed
    local (`baseUrl(…)` beside `String baseUrl`), a local assigned from the
    function of its own name (`let agent = agent();`) and a field assigned in
    a method keep the outer meaning.
  - In Rust, `use super::*` inside `mod tests { … }` was read as an import of
    the parent module's file, which put that file's names in scope and added
    an import edge to it.
  - In Swift, an `extension Session { … }` in a module that only extends the
    type hid the type it imports: `Session()` and everything typed through it
    was unresolved in such a module (666 calls gained in alamofire's tests).
- `benchmarks/compare_engines.py` compares an edge from a definition only the
  native graph has (a function nested in a method, the setter beside a
  getter) as an edge from the paired definition it belongs to; TypeScript's
  `calls` recall read 78% where 96% of graphify's calls were in the graph.
- `graphlore_unreferenced` and `graphlore_same_names` no longer take a native
  module-level constant for a class (it hangs off its file by `contains`, as a
  class does); the native `kind` decides.
- `graphlore_neighbors` lists both directions when two nodes link each other
  by the same relation (A references B and B references A); it dropped one.
- `graphlore_path` and `graphlore_explain` pass `--graph` to the CLI, as
  `graphlore_query` did, so with `GRAPHLORE_OUT_DIR` set they read the graph
  the other tools read instead of the CLI's default `graphify-out/`.
- Ambiguous `.name()` labels of functions bound to an object key
  (`{ filter: () => … }` in six test callbacks) are qualified by `file:line`
  in member and neighbor lists; the bare `filter()` they got from their span
  told them apart no better than the label did.
- A Go function type written inside a map type (`map[string]func() hash.Hash`)
  is no longer taken for a function named `string`, in `spans.py` and in the
  native engine.

## [0.6.0] - 2026-11-15

Every tool was run on 31 real repositories in 14 languages
(`benchmarks/polyglot.py`, `docs/benchmark-polyglot.md`). The changes below are
what that turned up: package APIs read in all fourteen languages, routes in
nine, a span engine checked on real code in each, and the conventions applied
everywhere. What is still missing is listed at the end of the report.

### Added
- **Package APIs for Kotlin, Scala, C#, Rust, Swift, C / C++, Ruby and PHP**,
  read from each file's imports (`import a.b.C`, `using A.B;`, `use a::b::{c}`,
  `#include <curl/curl.h>`, `require 'faraday/utils'`, `use A\B\C;`). The
  project's own packages are left out per language: the packages and namespaces
  its JVM, .NET and PHP files declare (an exact match, so guzzle's
  `GuzzleHttp\Promise` dependency is not mistaken for guzzle), its crates
  (`Cargo.toml`, plus `crate::` / `self::` / `super::` and modules declared in
  the same file), gems, Swift targets and header directories. Java gained the
  same rule: retrofit's own `retrofit2.*` packages were listed as external.
- **Routes for Kotlin, C#, Rust, Ruby and PHP, and JAX-RS for Java**: Spring
  and Ktor in Kotlin; ASP.NET Core controllers (`[Route("api/[controller]")]`,
  `[HttpGet("{id}")]`) and minimal APIs (`app.MapGet`, prefixes through
  `var g = app.MapGroup("/api")`); axum (`.route("/x", get(h).post(h2))`,
  `.nest`) and actix-web (`#[get("/x")]`, `web::scope` / `resource`); Sinatra
  and the Rails `routes.draw` DSL (`resources` with `only:` / `except:` /
  `param:` and nesting, `namespace`, `scope`, `root`); Laravel `Route::` chains
  (`prefix`, `group`, `resource`, `apiResource`) and Symfony `#[Route]`. Each
  is gated on its framework, and Java's JAX-RS on a `javax.ws.rs` /
  `jakarta.ws.rs` import so Retrofit's client `@GET` stays out. On eleven
  RealWorld-style sample applications every route of the spec is found
  (`docs/benchmark-polyglot.md`). `config/routes.rb` and `routes/*.php` are
  read even when the graph holds no symbol of them.
- `scope` on `graphlore_routes`, `graphlore_package_apis`, `graphlore_layers`
  and `graphlore_tests_for`, the four tools that read the whole graph without
  one. Their results carry the `scope` payload and say what was left out.
- `tests/fixtures/polyglot/`: five languages and the graph graphify 0.9 built of
  them, so the suite runs on `file_type`, `community_name`, `case_of`,
  closures, a Rust test module and a JS barrel — what the 5-node fixture
  cannot show.
- `benchmarks/polyglot.py`: runs all 33 tools, the resources and the prompts on
  pinned commits of real repositories in Python, JavaScript, TypeScript/TSX, Go,
  Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Ruby and PHP, and reports what
  breaks an invariant (parseable JSON on every path, a cut that says what it
  left out, a `file:line` that exists), what two tools disagree on, and where a
  language gets an empty answer its source contradicts. `--baseline` marks
  findings new or gone against an earlier run. Results:
  `benchmarks/results-polyglot.json`; report: `docs/benchmark-polyglot.md`.
- Every result that reports `truncated` now carries a `hint` naming the
  argument to raise or the filter to add. Fifteen tools reported the cut and a
  count but not how to get the rest: `god_nodes`, `surprises`, `search`,
  `subgraph`, `tests_for`, `locate`, `duplication_scan`, `freshness`, `diff`,
  `cycles`, `unreferenced`, `layers`, `same_names`, `package_apis`, `routes`.
- `graphlore_package_apis` and `graphlore_routes` count the source files they
  have no extractor for in `not_read` (`{".rs": 48}`) and say so in the text.
  A Rust or Kotlin project used to get "no routes" in the same words as a
  project that has none.
- `graphlore_communities` shows each community's `name`: the one set with
  `graphlore_set_labels` / `graphlore_label_communities`, else the one graphify
  wrote on the nodes. A name written by `graphlore_set_labels` was visible in
  no tool. Members that share a label are qualified.
- `graphlore_communities(limit=30, members=15)`: the largest communities, each
  with its most connected members first, `size` and `members_left_out`. It
  used to list every member of every community — a 280 KB reply on guzzle, in
  the second step of the onboarding flow.
- `server.json`, the manifest for the official MCP Registry
  (`io.github.yasinyaman/graphlore`, PyPI package, stdio, run as
  `uvx --with graphifyy graphlore`), and the `mcp-name:` line in the README
  that the registry uses to verify PyPI ownership. Publishing there is a manual
  step after the first PyPI release; `CONTRIBUTING.md` has the commands.

### Changed
- `graphlore_routes` leaves test code out by default: got's tests register
  1,354 routes on throwaway servers. `scope="+tests"` brings them back.
- An Express-style row is labelled by the framework the file imports
  (`fastify`, `koa`, `restify`, `polka`), not always `express`.
- `graphlore_build()` with no LLM key on a folder that holds docs: graphify 0.9
  refuses it, so it runs again with `--code-only` and says the docs were
  skipped. A build with `mode=` asked for is not turned into another one.
- `graphlore_explain` and `graphlore_path` resolve a name before graphify sees
  it, and pass graphify the node id. graphify refuses a name several nodes
  share ("Ambiguous: matches 3 nodes"); the reply now says which node was used.
- Dependencies upgraded. `uv.lock` moves to graphifyy 0.9.109 (from 0.8.39:
  what plugin users already got through `uvx`), mcp 2.3, semble 0.7,
  tree-sitter-language-pack 1.25, ruff 0.17, mypy 2.4 and pytest 9.2. The
  floors in `pyproject.toml` are now the oldest versions the suite passes with
  on Python 3.10: `tree-sitter>=0.23` and `tree-sitter-language-pack>=1.11`
  (1.9 and 1.10 return a `Language` that `tree_sitter.Parser` rejects, so the
  span engine had no grammar with them), and `mypy>=1.12` for development (1.11
  crashes on mcp's sources). CI uses `actions/checkout@v7`,
  `actions/setup-python@v7`, `actions/upload-artifact@v7` and
  `actions/download-artifact@v8` (node 24).
- The README is a short landing page: what graphlore is, quick start, the 12
  `lean` tools, the benchmark headline and an FAQ. The detail moved, unchanged,
  to `docs/tools.md` (all 33 tools, resources, prompts), `docs/concepts.md`,
  `docs/configuration.md` (toolsets, transports, the network table,
  environment variables) and `docs/benchmark.md`. README links into the
  repository are absolute, so they work on PyPI.
- Package and plugin metadata describe what the server is for and carry more
  search keywords (`mcp-server`, `model-context-protocol`, `code-navigation`,
  `impact-analysis`, ...); `pyproject.toml` gains Documentation and Changelog
  URLs.
- The server instructions, the docs and the shipped skill no longer name a
  `hidden_links` key (removed in 0.5.1), and the docs describe the fields
  0.5.0 added (`not_fetched`, `did_you_mean`, `complete` / `beyond_hops`,
  `vendored_dirs`).
- `CONTRIBUTING.md` no longer recommends `ruff format .` (CI does not check
  formatting and most files are not ruff-formatted), and its project layout
  lists every module.

### Fixed
- **Express routes in a file that imports no framework.** An Express app's
  `config/routes.js` (`module.exports = function (app) { app.get(...) }`)
  imports nothing, so none of express-mongoose-demo's 26 routes were found;
  all are now. Without a framework import in the file, a call counts when its
  receiver is named like a router or is a parameter of the function around it,
  it has a handler argument, and some file of the project imports a server
  framework — `axios.get('/x', {…})` still does not. `router.route('/x')
  .get(h).post(h2)` chains are read too.
- **Rust tests inside a source file are tests.** `#[cfg(test)] mod tests` and
  `#[test]` functions (26 of ureq's 48 source files hold them) were production
  code to `tests_for`, the production-before-tests order, `-tests` scopes and
  `unreferenced`'s `count_test_references`. A Rust `mod { … }` is a span, so a
  test's qualname reads `tests.it_works`.
- PHP imports are module-level instead of `unclassified` (a `use` cannot be
  deferred).
- Spans for a JS function wrapped in one call (`exports.create = async(function*
  …)`, `const f = wrap(async () => …)`; 44 definitions in
  express-mongoose-demo), for anonymous Java and Kotlin classes
  (`new Callback<Void>() { … }`, `object : Callback<T> { … }`) and Ruby
  `Request = Struct.new(…) do … end`. The skeleton of a C
  `typedef struct { … } name;` shows the closing line with the name.
- `graphlore_unreferenced` does not read a doc comment's `@default` / `@example`
  as a decorator, and counts a class as used when one of its members is called
  elsewhere: C# extension classes are never named at the call site
  (`client.BuildUri(…)`), so RestSharp's were all listed. The row says
  `used_through_members`.
- **Kotlin had no spans at all.** Its grammar names a declaration through an
  identifier child, not a `name` field, so none of kotlinpoet's 2,009
  definitions had a span: `graphlore_fetch` returned one line,
  `graphlore_skeleton` nothing, and annotations were never read. Now 1,997 do.
- **A type written in a signature is no longer a definition.** The span engine
  took any node with a hint word in its type and a `name` field: TypeScript
  `React.ComponentProps`, Rust `io::Result` and struct literals, Swift type
  annotations, Scala constructor parameters, a C `struct config cfg` parameter
  — and every PHP `$parameter`, because `simple_parameter` contains "impl".
  Such a one-line span sat on the definition's own line and won:
  `graphlore_fetch` returned the first line of a function, and qualified names
  read `FormMessage.ComponentProps`.
- A `.h` file that reads as C++ is parsed as C++ (the C grammar turned cpr's
  classes into errors: 146 of 558 definitions had a span, now 532). Function
  prototypes, methods declared in their class, typedefs, operators and
  functions behind a reference return type are spans.
- C# and Java `record`s, Swift `protocol`s, `init` and `deinit`, and a JS
  function assigned to a member (`res.send = function () {…}`,
  `exports.x = …`) are spans.
- `graphlore_fetch`, `graphlore_skeleton(node=)` and qualified labels take the
  span that starts on the node's line under the node's name, not the innermost
  one: `enum Color { RED, GREEN }` no longer answers a request for `Color` with
  one of its cases.
- `graphlore_skeleton` headers run on to the line that names the symbol. A
  Java, C# or Swift declaration starts at its first annotation, and GNU-style C
  puts the return type on a line of its own, so the header of 925 symbols was
  `@Override`, `[Fact]` or `static int` and nothing else.
- Names scoped with `::` (Ruby `Faraday::Utils::Headers`, C++ and PHP
  `Session::SetOption()`) resolve by their last part and as a qualified key,
  instead of falling through to the substring match.
- **JS/TS imports through a barrel are no longer rejected.** graphify links
  `import { X } from './dto'` to the file that defines `X`; the import check
  compared the specifier with that file, found `dto/index.ts` instead and
  dropped the edge as contradicted by the source — 113 of got's 797 import
  edges, so `graphlore_cycles`, `graphlore_layers` and `graphlore_surprises`
  worked on a thinner graph. Re-exports (`export … from`,
  `module.exports = require(…)`) are followed through up to three files; a file
  the entry merely imports is still not accepted. `require('../..')` names the
  root `index.js`.
- A C, C++ or Objective-C `#include` is module-level, like the imports of the
  other compiled languages; those edges were `unclassified`.
- Test files of more layouts are recognised: Gradle source sets (`jvmTest`,
  `androidTest`), .NET test projects (`Foo.Tests/`), `FooTests.java` /
  `FooTest.php` / `FooTestCase.swift`, Ruby `foo_spec.rb`, `*_tests.cpp`.
  `graphlore_tests_for` and the production-before-tests order depend on it.
- `graphlore_unreferenced` no longer lists collected tests as dead code: Go
  `Benchmark…` / `Example…` / `Fuzz…` functions, googletest `TEST`, `…Tests` /
  `…TestCase` classes and `setUp` / `tearDown` fixtures are tests by name, and annotations that start the declaration itself
  (Java and Kotlin `@Test`, C# `[Fact]`) or use another syntax (Rust
  `#[test]`) are read as decorators. Compiler and lint attributes
  (`#[must_use]`, `#[derive]`, `[Obsolete]`, `@JvmStatic`) do not count as a
  registration.
- `graphlore_unreferenced` no longer examines what no caller names: anonymous
  functions (`{closure#3}` — 866 of guzzle's roughly 900 rows), operators (C++
  `operator[]`, Ruby `[]=`), constructors and destructors, and PHP magic
  methods (`__invoke`). Ruby `success?` / `body=` are looked up in the text as
  `success` / `body`, the way a caller writes them.
- A node with no span of its own (a closure, a member declared without a body)
  is qualified by where it lives: `sendDigest.{closure#3}()`. Three closures in
  one method were all rendered `AuthMiddleware.sendDigest()`, the name of the
  method around them.
- Relations graphify 0.9 writes are known: `defines` (a class's field) and
  `case_of` (an enum's case) are containment, so they form no cycle and are no
  reference; `dispatches_to`, `references_constant` and `uses_static_prop` are
  code relations, so `graphlore_impact(relations="code")` walks them.
- `.mts` / `.cts` files count as source in every table; `.swift`, `.kts` and
  `.hxx` have a language family, so `graphlore_same_names` no longer compares a
  Swift class with every other language that had none.
- `type` in `graphlore_god_nodes`, `graphlore_search` and
  `graphlore_node_details` falls back to graphify 0.9's `file_type`; it was
  `""` on every row.
- `graphlore_neighbors` listed a self-loop twice.
- A negative `limit` lists nothing. It was used as a slice end, so `limit=-1`
  meant "all rows but the last": 471 KB from `graphlore_search` on guzzle
  (`search`, `surprises`, `validate`, `package_apis`, `routes`,
  `duplication_scan`).
- `graphlore_locate(top_k=0)` raised `ValueError` from the semantic backend
  instead of answering.
- When semble is installed but cannot build its index (the embedding model
  could not be downloaded), the error says so and quotes the reason, instead
  of "needs the optional 'semble' extra".
- `graphlore_build` adds `code_only=True` to the error graphify 0.9 gives for a
  default build of a repository with docs and no LLM key, which names only the
  CLI flag.

## [0.5.1] - 2026-11-14

Fifth round of feedback: 0.5.0 re-run on the same three repositories.

### Changed
- Scoped results summarize the vendored/minified files they left out instead
  of listing twenty paths on every call: `scope.left_out` keeps
  `vendored_nodes` and `vendored_file_count`, gives three example paths in
  `vendored_files` and the directories holding most of them in
  `vendored_dirs`. `graphlore_validate` returns the full list
  (`vendored_files`) and counts it in its text.
- `graphlore_impact`'s `complete` is false only when the budget dropped rows
  within the hops asked for. A walk that listed everything within `hops` is
  complete even when nodes lie further out; `beyond_hops` and the hint say
  separately what `hops + 1` would add.
- `graphlore_locate` lists each semantic cousin once, in `semantic_cousins`
  with `linked`: hidden links (`linked: false`) first, nearest first, then
  the connected ones. The `hidden_links` list, which repeated the same
  entries, is replaced by `hidden_link_count`. Header chunks the answer
  skips are marked `header: true` in `semantic_hits`.

### Fixed
- `graphlore_surprises` ranked a guessed edge from a file that imports nothing
  of the project at the top: an `indirect_call` from a module importing only
  the standard library, into an unrelated class in another layer. The graph
  records no import edge for such a file, and the name-guess rule only judged
  files it had import edges for. A file without recorded imports is now
  judged from its own import statements (`ImportChecker.reaches`: Python via
  `ast`, JS/TS via tree-sitter); when none can mean the target the edge is
  dropped and counted under `inferred_unimported`. A file whose source cannot
  be read is still not judged.

## [0.5.0] - 2026-11-14

An agent's-eye review: results that say how they were reached, what they
left out and whether their files changed; real MCP structured results; an
offline switch; and a smaller default tool surface.

### Added
- Tool results carry MCP `structuredContent` and `isError`. The payload a tool
  builds for `as_json=True` is now sent as `structuredContent` on every call,
  text or JSON, and errors (node not found, missing graph, a failed CLI call,
  `graphlore_fetch` with every name missing, a failed `git status` in
  `graphlore_freshness`) set `isError: true`. Tool functions still return a
  string; `GRAPHLORE_STRUCTURED=0` restores plain-text results.
- A name that matches nothing comes back with `did_you_mean`: the closest
  node names (stdlib `difflib`, pool bounded on large graphs), in the text and
  in JSON next to `code: "not_found"`. `graphlore_fetch` lists them per
  missing name.
- Every budget or limit cut says what it left out. `graphlore_fetch` names the
  requested nodes it did not fetch (`not_fetched`); `graphlore_skeleton` names
  the files left out; `graphlore_locate` counts the seed's direct neighbors left
  out of the structure and the callees past the five in `calls_into`;
  `graphlore_search`, `graphlore_god_nodes`, `graphlore_surprises` and
  `graphlore_package_apis` report `total` and `truncated`;
  `graphlore_freshness` reports the real length of each capped list in
  `counts`; the `graphlore://community/{id}` resource says when it shows 50 of
  more boundary edges. Each adds a one-line `hint`.
- `graphlore_impact` reports `beyond_hops` (nodes one hop past `hops`) and
  `complete`, false whenever the budget or the hop limit cut the answer.
- Answers say which of their files changed since the graph was built.
  `graphlore_fetch` entries carry `file_changed_since_build` (true / false /
  null) and `span_mismatch` when the graph's line in a changed file now lands
  in a different symbol — before, fetch silently returned that other symbol.
  `graphlore_locate`, `graphlore_impact` and `graphlore_subgraph` carry
  `build_drift` (`known`, `since`, `changed_files`) and a text line naming the
  changed files. It compares the recorded `built_at_commit` with the working
  tree in two git calls; without that commit or git it reports
  `known: false` and why.
- `graphlore_neighbors`, `graphlore_subgraph` and `graphlore_locate`'s
  structure mark inferred / ambiguous edges (`confidence`) and edges graphlore
  resolved from the source (`resolved_by`), as `graphlore_impact` already did.

- `GRAPHLORE_OFFLINE=1` refuses what graphlore knows reaches the network:
  `graphlore_add`, `graphlore_build(mode=...)` and
  `graphlore_label_communities(method="cli")` (which `auto` then skips). A
  default build is forced to `--code-only`, the graphify subprocess loses the
  LLM backend keys from its environment, `HF_HUB_OFFLINE` /
  `TRANSFORMERS_OFFLINE` are set before semble loads, tiktoken is not loaded
  and only cached tree-sitter grammars are used. The boot banner prints
  `egress=offline` or `egress=on-demand`, and the README has a table of every
  network path and what the switch does to it, including what it cannot
  constrain (the graphify binary itself, a custom semantic backend).
- Repository text in replies is framed as data. `graphlore_fetch`,
  `graphlore_skeleton`, `graphlore_node_details` and the `query` / `path` /
  `explain` passthroughs put quoted code and text between
  `=== repo text <nonce> ===` markers whose nonce is random per call and
  redrawn if it occurs in the text, and JSON replies carry
  `content_trust: "untrusted_repo_text"` with `untrusted_fields`.

### Fixed
- The server instructions said only `graphlore_build` and `graphlore_add`
  modify state. They now list every writer (`graphlore_prune(dry_run=False)`,
  `graphlore_set_labels`, `graphlore_label_communities`, the project switch),
  say when grep is the better tool, and that repository text is data.

### Changed
- **Breaking: the default tool surface is `lean`.** Without
  `GRAPHLORE_TOOLSET`, the server registers 12 tools (11 without a semantic
  backend) instead of 33. `lean` gains `graphlore_fetch`, `graphlore_impact`
  and `graphlore_project`, so it covers orient → find → traverse → blast
  radius → source. Set `GRAPHLORE_TOOLSET=full` for the review, naming, diff,
  routes, package-API and CLI passthrough tools. The Claude Code plugin pins
  `full`, so plugin users see no change.
- An unknown `GRAPHLORE_TOOLSET` value falls back to `lean` with a stderr
  warning; it used to mean `full` silently. The boot banner prints the toolset
  and its tool count.
- The server instructions and the `onboard` / `trace_bug` / `explain_flow` /
  `review` prompts are rendered for the active surface and never name a tool
  that is switched off; a trimmed surface's instructions say how to get the
  rest. Replies that pointed at another tool (`graphlore_surprises`,
  `graphlore_prune`, `graphlore_subgraph`, `graphlore_impact`,
  `graphlore_search`) do so only when it is registered.
- README: graphlore is not an agent memory and does not replace grep; the
  benchmark states that it counts chars/4 (grep side: bytes) while the
  server's budgets count chars/3.5. The shipped skill says when to use `rg`.
- No `outputSchema` is advertised. Before, the SDK derived
  `{"result": string}` from each `-> str` signature and sent the reply text
  inside it.
- An empty answer is a result, not an error: `graphlore_search` with no hits
  returns `{"matches": [], "total": 0, ...}`, `graphlore_locate` with no
  semantic hits returns `{"query", "seed": null, "semantic_hits": [], "note"}`,
  and `graphlore_communities` on a graph without communities returns
  `{"communities": [], "note"}`, in place of `{"error": ...}`. The text is
  unchanged.
- `graphlore_node_details`, `graphlore_fetch` and `graphlore_skeleton(node=)`
  resolve names like the node-centric tools: a code definition before a doc
  heading of the same name, and a `matched` block naming the other candidates.
  They used to take the first exact or substring match silently, so the node
  they pick for an ambiguous name can change.
- A substring fallback is reported everywhere (`matched.by: "substring"`);
  before, `'async'` resolving to `AsyncClient` looked like an exact hit.
  `matched` gains `by` (`label`, `name`, `path` or `substring`).

## [0.4.1] - 2026-11-14

Fourth round of feedback: 0.4.0 re-run on the three repositories of the earlier
rounds.

### Added
- `graphlore_project` and `graphlore_overview` print the server version. A
  client keeps the tool schemas it loaded before a plugin update; a schema
  without `scope` next to a current version number now reads as "start a new
  session", not as "the parameter does not exist".
- `graphlore_locate` returns `calls_into`: what the seed calls in other files,
  most specific callee first. The tool that exposes a behavior is often a thin
  wrapper over the module that implements it, and the graph knows that.

### Changed
- `graphlore_locate`'s `also_matched` no longer lists hits on a file's header
  (its docstring and imports, before the first definition) — they are counted
  in `header_hits_skipped` — and names the definition each hit falls in
  (`qualname`).
- `graphlore_surprises` scores an inferred edge into a module the source file
  imports like an extracted one (the import resolves it; nobody guessed), and
  withholds the structural bonuses from a pair of directories that ten or more
  edges already join: an inbound adapter using the application layer is the
  architecture, not a finding.

### Fixed
- **Regression in 0.4.0: `graphlore_unreferenced` listed methods that a
  same-named caller calls.** A route handler `remove_example()` calling
  `review.remove_example(...)` lives in a file that defines its own
  `remove_example`, and 0.4.0 dropped every occurrence of a name in such a file.
  Only what can refer to the file's own symbol is dropped now — its `def` line
  and bare uses; an attribute access (`obj.name`, `mod.name`) still counts as a
  mention.
- An import edge graphlore resolved from the source carried no line, so
  `graphlore_impact` reported it at the importing file's line 1 as a
  definition. It now carries the line of the import statement and is reported
  as a reference site.

## [0.4.0] - 2026-11-14

Second and third rounds of feedback: the 0.3.0 review recipe run on the same
774-file repository through the MCP tools, then a side-by-side run with the
hosted Graphify connector on this repository.

### Added
- **Calls through an imported module are resolved from the source.** graphify's
  local extractor records `from pkg import f; f()` but not
  `from pkg import mod; mod.f()`, nor often the import of `mod` — so a tool that
  four tests call as `server.graphlore_unreferenced(...)` had no caller and no
  test in the graph. New `resolve.py` adds those call edges (following
  re-exports) and the missing module imports at read time, marked
  `resolved_by: "graphlore"`; graph.json is never modified. `graphlore_impact`,
  `graphlore_neighbors`, `graphlore_subgraph`, `graphlore_tests_for`,
  `graphlore_unreferenced`, `graphlore_cycles` and `graphlore_layers` read them,
  and `graphlore_validate` reports how many there are. Per language it adds
  what the extractor is seen to leave out: in Python every call through a
  module object; in JS/TS calls through a default import and through a
  dynamically imported module (namespace imports and `require` are already
  linked); in Go and Java nothing, graphify links those itself. One parse per
  Python and JS/TS file, once per process; `GRAPHLORE_RESOLVE=0` turns it off.
- **`graphlore_tests_for`** — the tests that exercise a symbol: `direct` (they
  call it, with the call line), `through_one_caller` (a production function or
  a test helper in between), and `weak` matches kept apart (a test file that
  imports its module, a test named after it).
- `graphlore_impact`, `graphlore_subgraph` and `graphlore_neighbors` take the
  `scope` argument (`-tests`, a path prefix, ...). Unlike the audit tools they
  leave nothing out by default.
- `graphlore_impact(strict=True)` walks only edges the extractor resolved; every
  row carries its edge's `confidence` (and `resolved_by` when graphlore added
  the edge), and inferred rows are marked in the text.
- `graphlore_locate` lists the other top hits under the seed (`also_matched`,
  production code before tests).
- **`graphlore_cycles` classifies import cycles by when their imports run.**
  Each import edge is read back from the importing file (new `imports.py`):
  `runtime` cycles — a loop of module-level imports, the ones that can fail at
  start-up — come first, then `deferred` (the loop needs an import inside a
  function) and `type-only` (it needs one under `if TYPE_CHECKING:`). Each cycle
  carries `import_kind` and `hops` (the kind of every import on its loop), the
  payload adds `runtime_` / `deferred_` / `type_only_import_cycle_count`, and a
  repository without a runtime cycle says so in one line. Python is read with
  `ast`; JS/TS with tree-sitter (`import()` or `require` inside a function is
  deferred, `import type` is type-only); Go, Java, Kotlin, Scala, C#, Rust and
  Swift import at compile time and are always module-level. An import in any
  other language counts as module-level — unless the graph recorded it on a
  function, which makes it deferred — and the result says so when a reported
  runtime cycle rests on one.
- **`graphlore_duplication_scan` has an exact tier, `identical_bodies`.** Every
  function and method body is hashed after normalizing whitespace, comments and
  the docstring (Python via `ast`, other languages via tree-sitter), and bodies
  that occur more than once are reported as groups with `file:line` for each
  copy. No model, and it covers the small helpers that the semantic tier's
  largest-first seeding never reaches. Without the `[semble]` extra the tool now
  returns this tier instead of an error.
- `graphlore_same_names` reports `used_in_own_file` for each copy and takes
  `across_languages`.
- `graphlore_layers` violations carry `import` (module-level / type-checking /
  deferred), and the result lists `rejected_imports`.
- `graphlore_unreferenced` rows carry `also_defined` when another definition
  shares the name.
- README: start a new session after updating the plugin — a client keeps the
  tool schemas it loaded before the update.

### Changed
- **`graphlore_impact` and `graphlore_subgraph` put production code before
  tests.** Rows at one distance were alphabetical, so a hub function's budget
  went to test helpers named `_build` and `_graph` and no production caller was
  shown. They are now ordered non-test first, then most connected, and a
  truncated result says how many rows (or direct neighbors) were dropped and how
  many of them were tests. `graphlore_neighbors` is ordered the same way.
- **An ambiguous name is no longer resolved silently** in `graphlore_impact`,
  `graphlore_subgraph`, `graphlore_neighbors` and `graphlore_tests_for`. A name
  shared by several nodes — a function and a README heading, two `Settings`
  classes — resolves to a source definition first, and the result says "Matched
  N nodes ...; using X" with the others' ids (`matched` in JSON). A bare name
  matches its callable (`graphlore_locate` finds `graphlore_locate()`), a path
  names its file's node, and `Client.send` prefers the method of that class.
- An empty answer from those tools says that no edge was indexed — not that
  the code has none — and what kinds of call the index cannot see.
- `graphlore_duplication_scan` leaves out semantic pairs scoring under 0.7 on
  the semble backend (`min_score=0` lists them) and says what the score is. On
  the reporting repository every pair between 0.49 and 0.64 was two classes
  with the same role, not copied code. A custom backend gets no default floor.
- `graphlore_same_names` compares names within one language family, and reads
  nesting from the source: a closure that graphify hangs directly on its file
  is no longer compared with top-level functions.
- `graphlore_surprises`: a *script* nothing depends on counts as a consumer even
  when its directory is depended upon (`docker/smoke/make_catalog.py`), consumer
  detection ignores resolver guesses, and anything test code uses — shared test
  fakes in another test directory included — is routine coupling. Non-test code
  that depends on test code keeps its bonuses.
- `graphlore_unreferenced` names the base in its hook note ("method of a
  subclass of NodeVisitor") and gives it only when that base can call the
  method: a base outside the project, or a project base that defines or mentions
  a method of that name.

### Fixed
- "Nothing depend on ..." now reads "Nothing depends on ...".
- **A third-party import linked to a first-party module of the same name** —
  `import yaml` (PyYAML) recorded by graphify as an import of
  `pkg/.../export/yaml.py`, or `import mcp` as an import of `mcp.json` — was
  reported as a layer violation and could close an import cycle. Import edges
  are now checked against the importing file's own statements before they are
  lifted to file level: an absolute import can only mean a module whose dotted
  path starts in a directory that is not itself a package, and a bare one-word
  import only one that starts at the project root, a source root, a
  distribution root or a directory above the importing file. The JS/TS
  equivalent: a relative specifier is resolved to its file, a bare one-word
  specifier is an npm package, and a path alias is kept. Rejected edges are
  counted and shown, in `graphlore_cycles`, `graphlore_layers` and
  `graphlore_surprises`.
- **`graphlore_unreferenced` missed a dead class when another class had its
  name.** The text cross-check counted the other class and its uses as
  mentions. A file that defines its own symbol of that name is now left out of
  the count.
- `graphlore_unreferenced` marked methods of a class whose only base is `ABC`
  as possible hook overrides. Marker and data bases (`ABC`, `Protocol`,
  `BaseModel`, exceptions) no longer trigger the note.
- A label that already carried its location was printed with it twice in
  `graphlore_duplication_scan` and `graphlore_unreferenced` rows
  (`Settings (a.py:L35) (a.py:35)`).
- An ambiguous top-level function label lost its `()` when qualified and was
  left ambiguous (`run` for both `run()`); it is now shown as
  `run() (file:Lline)`.

## [0.3.0] - 2026-11-14

Feedback from a review session on a 774-file Python + JS repository (graphify
0.9.108, 13,159 nodes): the analysis tools were dominated by code that was not
the project's own, a graph built by path could not be read, and reviewing a
codebase took hand-written scripts over `graph.json`.

### Added
- **`graphlore_project`** — shows the directory the server reads and switches it
  to another one. A second repository (or one just built with
  `graphlore_build("/abs/path")`) no longer needs a second server. Changes no
  file; refused under `GRAPHLORE_RESTRICT_PATHS`, so never available over HTTP.
- **Analysis scope.** `graphlore_god_nodes`, `graphlore_validate`,
  `graphlore_cycles`, `graphlore_surprises` and `graphlore_duplication_scan` take
  one `scope` string: path prefixes or globs to keep, `!pattern` to drop,
  `-tests`, `+external`, `+vendored`, `all`. Every result carries a `scope`
  object and a closing line saying what was left out.
- **`graphlore_unreferenced`** — dead-code candidates: definitions with no
  incoming graph reference, not registered by a decorator (routes, click/typer
  commands, validators, fixtures — read from the graph's decorator edges and
  from the source lines above the definition, in any language the span engine
  reads), and not mentioned anywhere else in the repository's text. Each with
  `file:line` and a mention count; public methods of classes other files use
  are marked "may be API".
- **`graphlore_layers`** — checks import edges against layer rules
  (`{"layer prefix": ["prefixes it may import"]}`, inline or from a JSON file in
  the repository, `.graphlore-layers.json` by default) and returns the
  violations grouped by layer with `file:line`. `strict=True` also objects to
  imports into files outside every declared layer.
- **`graphlore_same_names`** — classes and top-level functions that share a name
  across modules, with how many modules import and reference each copy. Exact,
  no embedding model.
- A **`review` prompt** and a "Reviewing a codebase" recipe in the
  `graphlore-explore` skill: validate → import cycles → layer rules →
  unreferenced code → same-named symbols → hotspots → surprises.
- `graphlore_build(verbose=True)` returns the full CLI log.
- `graphlore_duplication_scan` gains `include_tests` and `min_score`.

### Changed
- **Vendored, minified and external code is left out of the analysis tools by
  default.** Files under `vendor/` / `node_modules/` / `third_party/`, files
  named `*.min.js` or carrying a build hash (`decorate-BgofJUJ5.js`), JS/CSS whose
  lines are too long to be hand-written, and nodes standing for external
  packages or unresolved names (`typing`, `pytest`, `_`) no longer rank as god
  nodes or show up as cycles and self-loops. `graphlore_overview` ranks its god
  nodes the same way. `scope="all"` restores the old behaviour.
- **`graphlore_cycles` reports by relation kind**: import cycles between modules
  first (import edges lifted to file level; a module's import of its own package
  `__init__` is ignored), call cycles after, then cycles that only close through
  another relation. `kind="imports" | "calls" | "other"` asks for one. Each group
  now carries one concrete loop (`path`) and its `kind`; the payload adds
  `import_cycle_count` / `call_cycle_count` / `other_cycle_count`. A repository
  with no import cycle says so in one line. Previously every relation was mixed
  into one list of node groups.
- **`graphlore_build` returns the build's counts and warnings** instead of the
  raw CLI log (progress ticks and stage banners are counted, not printed). It
  also reports when the graph was written somewhere this server does not read —
  graphify writes `graphify-out/` inside the folder it is given, so a sibling
  repository *or a subfolder* leaves the read tools on the old graph or on none
  — and names the `graphlore_project` call that switches to it. Two more notes
  appear when they apply: vendored/minified files found in the graph (with the
  `.graphifyignore` hint), and a `graphify-out/` that git neither ignores nor
  tracks. It writes no `.gitignore` itself: graphify supports a committed graph,
  so whether to ignore the directory stays the repository's decision.
- **`graphlore_duplication_scan` compares definition with definition.** Seeds
  are functions, methods and classes (largest bodies first) instead of the most
  connected nodes, each anchored to the chunk holding its own source rather
  than to whatever chunk mentions its label. A neighbour is kept only if it is
  a definition of the same kind and a similar size and structurally distant.
  Pairs carry the similarity `score` and `a_site` / `b_site` (`file:line`), best
  score first; test ↔ source pairs are left out unless `include_tests=True`.
- **`graphlore_surprises`** scores a directory nothing depends on (`bench/`,
  `demo/`, `examples/`) using a package that several directories depend on as
  expected coupling, and drops an inferred edge whose source file imports neither
  the target's module nor its package (the resolver matched a name).
- **`graphlore_impact`** leaves docstring/rationale nodes (`rationale_for`) out
  of the blast radius and counts them in `rationale_nodes_skipped`; name the
  relation in `relations` to walk them.
- **`graphlore_validate`** counts a function calling itself as `recursive_calls`,
  not as a `self_loops` issue.
- The descriptions of `graphlore_locate` and `graphlore_duplication_scan`, the
  README and the semble-missing error now say that the first semantic call
  downloads semble's embedding model from the Hugging Face Hub, and give the
  command that fetches it ahead of time. The source no longer calls that path
  "offline".

### Fixed
- `graphlore_build` announced `graph.json ready` for the project's own, older
  graph after building a different folder, and said nothing at all when the
  project had no graph.
- A package entry file labelled with part of its path (`pkg/__init__.py`, as
  graphify >= 0.9 writes it) was not recognized as a whole-file node, so
  `graphlore_surprises` scored edges it should have skipped.
- README told users to `pip install graphlore`, but the package has never been
  released on PyPI, so the command failed. Installation now shows the GitHub
  form that works today
  (`pip install "graphlore @ git+https://github.com/yasinyaman/graphlore"`).
- `mypy` failed inside the `uv.lock` environment (`uv run mypy`): the pinned
  `tree-sitter-language-pack` types `get_language`'s argument as a `Literal` of
  grammar names while `detect_language_from_path` returns `str`. The one call
  site carries a targeted ignore. CI, which installs unlocked, stayed green.
- **`graphlore_surprises` was effectively dead on every AST-built graph.** It
  keyed on a `surprise`/`is_surprise` flag no graphify writer emits, and its
  only fallback needed community info those graphs also lack — so it printed an
  empty list under a header that read as "this codebase has no surprising
  couplings". It now scores cross-file edges itself (confidence, file-type /
  directory / community crossing, peripheral→hub), suppressing resolver noise
  and routine test↔source coupling, and reports a `basis` of `flagged` /
  `computed` / `none` — the last naming exactly which signals are missing
  instead of implying a clean codebase. `graphlore_overview` no longer asserts
  "0 surprise edges" when the graph simply carries no flags.
- **`graphlore_impact` walked every edge type and reported no reference site.**
  It gains `relations=` (presets `all`/`code`/`calls`/`imports`/`types`, or
  literal relation names, orthogonal to `direction`; an unmatched filter names
  the graph's actual vocabulary instead of returning nothing) and each row now
  carries the traversed edge's recorded position, marked `site_kind=reference`
  or `definition` so a definition line is never misread as a call site. When a
  filter excludes containment, the node's own members are seeded so a class's
  methods stay reachable.
- Docs corrected against an audit of the wrapped CLI: token budgeting, HTTP
  auth, multi-project serving and general graph navigation exist in Graphify's
  own embedded MCP server and are no longer presented as things graphlore adds.
  The README now states the real differentiator (the span engine and what it
  makes possible) and the honest limits (no extractor, one project per process,
  no PR tooling).
- `graphlore_package_apis` (JS/TS): clearly-internal import sources are no
  longer counted as npm packages — absolute paths (`/lib/x`, whose "package"
  was the empty string), package.json `#` subpath imports, and the common
  bundler aliases `~/x` and `@/x`. A scoped `@app/utils` is still counted:
  without reading tsconfig it is indistinguishable from a real scoped npm
  package (documented over-approximation).
- `benchmarks/multilang.py` no longer aborts the whole run (discarding every
  measured repo) when a REPOS entry's graph is missing — the row is skipped
  with a stderr note, since the documented setup only builds a subset. The
  freshness table also stops claiming "expect False" for the token-change
  fallback edit, which is a comment in the C-family (expectation unreliable)
  — the row is flagged instead.
- The CLI-backed tools (`graphlore_query`/`path`/`explain`) and `_run_cli`'s
  plumbing (arg wiring incl. `--graph`, missing-binary message, timeout,
  exit-code formatting, exec-OSError) are now covered by tests — previously
  three registered tools had zero coverage.
- Rename leftovers in the docs artifacts: `docs/benchmark.html`,
  `docs/benchmark.tr.html` and `docs/benchmark.svg` still titled/labelled the
  project `graphify-mcp`; they now say `graphlore` (matching the final rename —
  the intermediate `codegraph-mcp` title never shipped).

### Changed
- **README rewritten for the graphlore surface** — restructured around the
  locate-first workflow, tools/tables refreshed for all 28 `graphlore_*` names
  and the newer behaviors (junk-file-tolerant freshness, qualified ambiguous
  labels, pluggable semantic backend, watcher), project-layout/dev sections
  updated (mypy in CI), and the "Comparison — when to use which" section
  removed. `docs/benchmark*.html`/`.svg` retitled from `codegraph-mcp` to
  `graphlore` (measurement numbers untouched).
- **Internals: one confinement boundary, indexed lookups.** The PROJECT_DIR
  confinement check that five per-file analyzers hand-copied is now a single
  `spans._resolve_in_project()`, and the span/API/route per-file caches share
  one `_cached_file_analysis()` pipeline (same mtime-keyed, bounded-FIFO
  semantics). `_resolve_node`'s exact id/label pass and `_node_for_location`'s
  same-file lookup are served from indexes cached on the nodes-list identity
  (the `_ADJ_CACHE` scheme) instead of rescanning all N nodes per call — the
  latter ran once per semantic hit / route row / duplication pair.
- **Ambiguous node labels are qualified at render time.** graphify labels
  methods bare (`.auth_flow()`), so distinct nodes across classes/files used to
  render identically in subgraph arrows and node lists. Wherever a label is
  shared by more than one node, tools now show the span-recovered FQN
  (`DigestAuth.auth_flow()`), falling back to `label (file:Lline)` when no span
  is available; unique labels are untouched. Lazy + memoized (only ambiguous
  ids that actually render pay the parse cost) and cached per loaded graph —
  this also collapses the eleven per-tool `{id: label}` rebuilds into one
  shared `_display_labels()` helper.
- **Tool surface renamed: `graphify_*` → `graphlore_*`** (and the resources
  `graphify://…` → `graphlore://…`), completing the package rename below so
  the commands an assistant sees carry the product's own name. All 28 tools
  are affected (`graphlore_locate`, `graphlore_build`, …); the bundled
  explore skill moved to `graphlore-explore` accordingly. The `graphify-out/`
  output directory and `.graphify_labels.json` keep their names — they are
  artifacts of the wrapped Graphify CLI (`graphifyy`), not this package.
- **Environment variables renamed to `GRAPHLORE_*`, with `GRAPHIFY_*` as a
  legacy fallback.** All settings (`GRAPHLORE_PROJECT_DIR`,
  `GRAPHLORE_TOOLSET`, `GRAPHLORE_TRANSPORT`, …) now read the new spelling
  first via one `config.env()` accessor and fall back to the pre-rename
  `GRAPHIFY_*` names, so existing configs keep working; when both are set,
  `GRAPHLORE_*` wins. Server messages/docs lead with the new names; the
  bundled `mcp.json` / `claude_desktop_config.json` templates are updated.
- **Project renamed: `graphify-mcp` → `graphlore`** (before any PyPI release,
  so no published users are affected). The Python module is now `graphlore`,
  the MCP server announces itself as `graphlore`, and the console script is
  the bare `graphlore` — the collision with the `graphify-mcp` script that
  `graphifyy` ships (which forced the old `graphify-mcp-server` entry point)
  is gone. The repository moved to `github.com/yasinyaman/graphlore`; old
  GitHub URLs redirect. (An intermediate rename to `codegraph-mcp` lived for
  a day and was dropped: "codegraph" is already crowded in the MCP niche —
  codegraph-ai/CodeGraph, two GitHub repos literally named codegraph-mcp,
  plus codegraph and codegraph-mcp-server on PyPI.)
- **Upgraded to MCP Python SDK v2** (`mcp>=2.1,<3.0`, was `>=1.26,<2.0`). The
  server now builds on `mcp.server.mcpserver.MCPServer` (v1's `FastMCP`); tool
  and prompt surfaces are unchanged. Notable internals:
  - Server version is passed as `MCPServer(..., version=...)` instead of the old
    private `_mcp_server.version` override.
  - `ToolAnnotations` hints use the v2 snake_case field names
    (`read_only_hint`, `destructive_hint`).
  - HTTP transports take host/port per call (`mcp.run(transport=..., host=...,
    port=...)`, `streamable_http_app(host=...)`) — v2 removed
    `settings.host/port`. The `host=` passed to the app factories also feeds the
    SDK's DNS-rebinding protection, which auto-enables on loopback hosts.
  - Host-LLM sampling (`graphlore_label_communities`, `graphlore_sampling_status`)
    now names all communities in **one batched** `sampling/createMessage`
    request (a JSON name map) instead of one request per community, and picks
    its transport per protocol: on 2026-07-28+ (no in-call back-channel) a v2
    `Resolve`/`Sample` resolver carries the request as input-required rounds;
    on older protocols the tool body samples directly over the back-channel,
    preserving the pre-v2 graceful degradation — a failing host model yields
    placeholder names plus a note instead of erroring the call. (On the
    resolver path that degradation is impossible: `Sample` has no error arm,
    so a host-model failure surfaces as a tool error there.) Unusable or
    partial replies degrade to placeholder names with an explanatory note,
    and an empty naming batch (`limit=0`) no longer sends a request at all.
  - The test suite drives tools through the v2 in-process `Client` (the v1
    `create_connected_server_and_client_session` helper is gone).

### Added
- **Claude Code plugin + marketplace.** The repository root is now a Claude
  Code plugin (`.claude-plugin/plugin.json`) listed by its own marketplace
  (`.claude-plugin/marketplace.json`), so
  `/plugin install graphlore --marketplace yasinyaman/graphlore` adds the MCP
  server and the `graphlore-explore` skill in one step. The server is launched
  with `uvx` from the plugin's own copy of the source (extras `graphify`,
  `treesitter`, `semble`; `uv` required), so the server always matches the
  installed plugin version, and `GRAPHLORE_PROJECT_DIR` is set to the opened
  project. A test keeps the manifest version equal to `server.__version__`.
- **PyPI publish workflow** (`.github/workflows/publish.yml`): publishing a
  GitHub release builds the sdist and wheel and uploads them with PyPI Trusted
  Publishing (no stored token), refusing when the release tag and the
  `pyproject.toml` version differ. The one-time PyPI setup and the release
  steps are in CONTRIBUTING.md under "Releasing".
- `code_only` flag on `graphlore_build` — appends the CLI's `--code-only`, so a
  repo containing doc/paper/image files can be indexed via local AST with no
  LLM API key (those files otherwise demand semantic extraction and the build
  errors without a key).
- README **"Comparison — when to use which"** section: an honest side-by-side
  with [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)
  (same goal, different trade-offs: verbatim-code mega-tool vs token-budgeted
  map, structural-only vs semantic hidden_links, watcher vs git-aware
  freshness), plus one-liners on Graphify's embedded MCP server and semble's
  own MCP server as complements.
- **Benchmark call/read metrics + `--json`** — `benchmarks/multilang.py` now
  also measures the agent-efficiency axis: locate = 1 tool call / 0 file reads
  vs the naive baseline's 1 grep + N file-read calls (N = the files the same
  grep matches, so both metrics share one baseline), and `--json PATH`
  persists the full result set. The 2026-08 re-run (semble 0.5.5) is committed
  as `benchmarks/results-multilang.json` — the first persisted benchmark
  artifact — and the README / `docs/benchmark*.html` cross-language tables now
  carry a "Calls (locate vs naive)" column (89–95% fewer calls; span-join
  percentages follow the overload-family re-count above).
- **`graphlore_routes`** — framework route → handler table: which URL patterns
  hit which code, each row joined back to its graph node and qualified name
  (`GET /items/{id} -> read_item (app.py:5)`). Recognizes the common
  registration idioms per language — Python: Flask `@app.route` /
  Flask-FastAPI verb decorators (labelled by the file's imports) / Django
  `path()`-family (gated on a django import, so a local `path()` never
  registers); JS/TS: Express-style verb calls (plain-identifier receiver +
  leading-`/` literal, which keeps `map.get('key')` out) and NestJS
  `@Controller`+`@Get`; Go: gin/echo/chi verb methods, `Handle`/`HandleFunc`
  with Go 1.22 `"GET /x"` patterns split into method + path, and chi
  `Route(...)` closure nesting with prefix propagation; Java: Spring
  `@GetMapping`-family and `@RequestMapping` (class prefix joined). Scans the
  files the graph knows plus any `urls.py` the graph missed (capped, pruned
  walk). An honest lower bound: dynamic registration, chained builders
  (`router.route().get()`, gorilla `.Methods()`) and variable-bound group
  prefixes are invisible. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `routes.py` engine module with a bounded
  per-(path, mtime) cache and a public `routes_for_source(src, rel)` seam,
  mirroring `apis.py`.
- **`GRAPHIFY_TOOLSET=locate`** — a minimal, mega-tool-style surface of five
  tools: `graphlore_locate` (the one-call orient), `graphlore_fetch` (map → code),
  plus `graphlore_overview` / `graphlore_build` / `graphlore_freshness` to stay
  oriented and in sync. Inspired by single-tool code-graph servers where a
  smaller surface measurably improves tool selection. Needs a semantic backend
  (the `[semble]` extra or `GRAPHIFY_SEMANTIC_BACKEND`); without one the server
  falls back to the lean surface with a stderr warning instead of advertising a
  locate tool that can only return an install hint. `graphlore_overview` now
  suggests `graphlore_locate(...)` first whenever it is on the active surface, so
  the trimmed mode keeps a non-empty `suggested_next`.
- `GRAPHIFY_ALLOWED_HOSTS` — Host-header allowlist for the HTTP transports'
  DNS-rebinding protection (comma-separated, `:*` port wildcards; `*` disables
  it). The MCP v2 SDK auto-enables that protection with a loopback-only
  allowlist when the server binds a loopback host, which rejects
  reverse-proxied requests whose `Host` wasn't rewritten; this variable is the
  escape hatch (v1 enforced no Host check).
- `graphlore_package_apis` — symbol-level external API surface: which names each
  external package is actually used for ("uses `Depends`, `APIRouter` from
  fastapi", not just "imports fastapi"), the input a version-upgrade audit needs.
  Captures from-imports and attribute access through import aliases
  (`np.array(...)` → `numpy: array`) plus qualified paths (`numpy.linalg.norm`)
  from real use sites; an honest lower bound (dynamic import / `getattr` /
  `import *` / wrapper indirection are invisible). First-party packages are
  detected and excluded. Python via stdlib `ast`; JS/TS, Go and Java via the
  optional `[treesitter]` extra. New `apis.py` engine module with a bounded
  per-(path, mtime) cache like the span index.
- `api_uses_for_source(src, rel)` — public, stable entry point for external
  consumers (importable from the package root): the same extraction on a source
  blob you already hold, with no file IO / caching / project-dir confinement.
  The underscore-prefixed extractors behind it remain internal.
- Seven new analysis tools: `graphlore_impact` (reverse-dependency blast radius,
  ordered by hop distance), `graphlore_duplication_scan` (repo-wide hidden-link
  audit — semantically similar but structurally distant pairs),
  `graphlore_fetch` (hydrate a node's source, token-capped), `graphlore_skeleton`
  (def/class signatures with bodies stripped), `graphlore_diff` (file-level
  structural changeset between two git refs, cosmetic-only changes separated),
  `graphlore_prune` (drop phantom nodes for deleted/renamed files — the surgical
  alternative to a rebuild, `dry_run` preview), and `graphlore_cycles`
  (Tarjan-SCC dependency cycles).
- Pluggable semantic backend: `GRAPHIFY_SEMANTIC_BACKEND` selects `semble`
  (offline default) or any `module.path:Factory` implementing the
  `SemanticIndex` protocol (`search`/`find_related`); `graphlore_locate` and
  `graphlore_duplication_scan` dispatch through it.
- Opt-in filesystem watcher: `GRAPHIFY_WATCH=1` re-syncs the graph on
  structural source changes (cosmetic edits ignored), debounced via
  `GRAPHIFY_WATCH_DEBOUNCE`; ships as the optional `[watch]` extra (watchdog).

## [0.2.0] - 2026-06-22

### Added
- `graphify-mcp-server` console script and `python -m graphify_mcp` entry point,
  both collision-free with the `graphify-mcp` script that `graphifyy` also ships.
- Boot banner on stderr at startup — `graphify-mcp vX.Y.Z | transport=… |
  toolset=… | project=…` — so it's immediately clear which server (and project
  dir) a client connected to, even when `graphifyy`'s same-named script is around.
- Lightweight `graph_age` field on `graphify_overview` and `graphify_subgraph`
  (e.g. "built 3 commits ago" / "built at HEAD" / "built at an unreachable
  commit") so staleness is visible without a separate `graphify_freshness` call.
  Git-only and cheap; `null` when there's no recorded build commit or no git repo.
  README now documents a first-class **post-commit-hook auto-update** flow to keep
  the graph fresh automatically.
- `graphify_label_communities` — names Leiden communities via **host-LLM MCP
  sampling** (no server API key), a backend key (`method="cli"`), or placeholders.
- `graphify_sampling_status` — capability test reporting whether the client
  supports sampling, whether a backend key is set, and the preferred method.

### Removed
- The bare `graphify-mcp` console script (shipped in 0.1.0). `graphifyy` ships a
  script of the same name, so a bare `graphify-mcp` resolved to whichever package
  installed last and could silently launch the wrong server. **Breaking:** invoke
  the server via `graphify-mcp-server` or `python -m graphify_mcp` instead.

### Fixed
- `graphify_node_details` now reads the source line from graphify's real
  `source_location` field (e.g. `"L295"`), not just `line`/`lineno`/`start_line`,
  so `file:line` references resolve against actual graph output.
- Server now reports its own version over MCP instead of the `mcp` library's.
- `graphify_subgraph` no longer re-serializes the whole edge list on every edge
  during the budget check — a running counter replaces the O(n²) `json.dumps`.
- `graphify_freshness` now detects newly-added **untracked** files (via
  `git status --porcelain`), compares against graphify's `built_at_commit`
  (robust across checkouts where mtime resets), and ignores its own
  `graphify-out/` output.
- `graphify_overview` and `graphify_surprises` now share one surprise-edge
  definition (`_is_surprise_edge`); an INFERRED *confidence* is no longer
  miscounted as a surprise, and the two tools agree.
- Span extraction no longer mistakes a **call/invocation** for a definition. A
  Java `method_invocation` (and C# `invocation_expression`, Ruby `method_call`,
  PHP `function_call_expression`, …) exposes the callee under a `name` field, so a
  call like `get(u)` inside a method body used to leak in as a phantom symbol
  (`Class.method.get`) and pull chunk resolution to it. Caught by the new Java
  golden span test.

### Changed
- Internal refactor (no behaviour change): the 2,000-line `server.py` is split into
  layered modules — `config.py` (shared `PROJECT_DIR`), `graph.py` (graph.json load +
  node/edge/traversal helpers), `spans.py` (the tree-sitter/ast span + structural-diff
  engine) — leaving `server.py` as the MCP surface (tools/resources/prompts). Same
  108 tests, same benchmark numbers.
- `_load_graph` caches the parsed graph by path + mtime, so a multi-MB
  `graph.json` isn't re-parsed on every tool call.
- Community-naming sampling `max_tokens` raised 16 → 24 to avoid clipped names.
- Token budgeting is now conservative: `~3.5` chars/token (vs the old `4.0`) plus
  a JSON-envelope allowance, so `approx_tokens` and `budget_tokens` reflect the
  whole returned payload and stop systematically under-reporting. Documented as an
  estimate (±~20%). Optional **exact** counting via the `[tiktoken]` extra +
  `GRAPHIFY_TOKENIZER=tiktoken` (the budget cap stays heuristic, so it's fast
  either way; the reported `approx_tokens` becomes exact).
- Tightened the `mcp` dependency bound from `>=1.2.0` to `>=1.26,<2.0`. The server
  uses streamable-HTTP, tool annotations and host-LLM sampling, which the old floor
  didn't have (it installed but failed at runtime); the upper bound guards against a
  breaking 2.0 SDK. Tested against mcp 1.26–1.27.
- Docs: the benchmark numbers now carry an explicit **sample-bias** note (every repo
  measured is an HTTP-client library, so results may differ on other architectures),
  and the HTTP hardening section documents the no-shell subprocess invocation and the
  `GRAPHIFY_TIMEOUT` knob for shared deployments.
- Pinned the optional tree-sitter extras: `tree-sitter>=0.22,<1.0` (the stable
  `Parser(Language)` core API) and `tree-sitter-language-pack>=1.6,<2.0` (the
  bundled grammars churn, so the 1.x cap keeps golden span tests from silently
  regressing on a grammar update). Added per-language golden span tests for Java
  and TypeScript (Python · JS · Go · Rust · C++ already covered).
- CI now runs **mypy** (non-strict baseline over the package) and **pytest with
  coverage** (term + XML report, floored at 80% via `--cov`); push-CI also fixed to
  trigger on `master`, the actual default branch. Tool `annotations=` now use typed
  `ToolAnnotations` objects instead of plain dicts, so the type checker validates the
  tool metadata FastMCP turns into each tool's schema. Added a corrupt-`graph.json`
  test alongside the existing missing-graph / unreachable-`built_at` ones.

### Added (transport & hardening)
- Optional HTTP transport: `GRAPHIFY_TRANSPORT=streamable-http|sse` serves over
  `GRAPHIFY_HOST:GRAPHIFY_PORT` (stdio stays the default).
- Opt-in build-path containment via `GRAPHIFY_RESTRICT_PATHS`; **auto-enabled**
  whenever an HTTP transport is selected, so a network client can't drive
  `graphify_build` to extract arbitrary paths.
- `graphify_overview` now reports `id_collisions` and warns when distinct nodes
  collapse to one id (degrees/neighbors would otherwise be silently understated).
- `graphify_validate` — read-only graph linter: dangling edges (endpoint not in
  the node set), duplicate edges, self-loops, and orphan (degree-0) nodes.
- `graphify_freshness` now returns a `recommended_action` (fresh / update /
  rebuild) with a `reason`: deletions, renames, or a large change set steer to a
  full rebuild, since incremental `update` can't drop nodes for removed code.
- `graphify_freshness` now verifies that the recorded `built_at_commit` is
  actually reachable in the clone (`git cat-file -e`). When it isn't (shallow
  clone, gc, rebase or squash), it reports `built_commit_reachable: false` and
  recommends a full rebuild — incremental update can't trust an unknown base —
  instead of mislabeling it "an older commit" and offering an update.
- Fixed a latent bug in `graphify_freshness`'s changed-file list: `_git` stripped
  the leading status column, mangling the first file name for unstaged
  modifications/deletions (` M`/` D`). `_git` now `rstrip`s only.
- `graphify_locate` (optional `[semble]` extra) — joins
  [semble](https://github.com/MinishLab/semble) semantic search to the graph:
  NL query → enclosing node → token-budgeted subgraph, plus `hidden_links`
  (semantically similar but structurally disconnected code, with hop distance).
  Refactored the subgraph BFS into a shared `_bfs_subgraph` helper. The
  chunk→node join prefers `file_type == "code"` symbols over docstring
  (`rationale`) / `document` nodes and uses the chunk's full line range, so a
  seed resolves to the enclosing function/class, not a docstring node.
- `graphify_set_labels` — assistant-driven community naming: the calling
  assistant pushes `{id: name}` (no key, no sampling — works in clients like
  Claude Code that lack sampling), persisted to `.graphify_labels.json` and
  patched into `graph.html`. Surfaced as the fallback in `graphify_label_communities`
  and `graphify_sampling_status` when sampling/keys are unavailable.

### Added (canonical span join)
- `graphify_locate`'s chunk→node join is now span-based, not single-point. Graph
  nodes carry only one `source_location` line (no end-line), so the old
  "greatest line ≤ chunk-start" heuristic could attribute a chunk to a function
  that had already ended, or fall back to the whole-file node. `_node_for_location`
  now resolves a semble chunk to the def/class whose **real line range** encloses
  it — via a decorator-aware AST span pass (stdlib `ast`, Python files; zero new
  deps) — then maps that symbol to its graph node, walking outward to the nearest
  enclosing symbol that has a node. The point heuristic remains the fallback for
  non-Python files or when no source is on disk. Measured on httpx: true
  containment rose from ~86/108 to 101/108 sampled chunks; the rest are
  module-level-start chunks resolved to the first symbol they introduce.
- `graphify_locate` seeds now include a span-recovered `qualname` (FQN, e.g.
  `AsyncClient._send_single_request`), disambiguating same-named symbols.
- The AST span pass is confined to `PROJECT_DIR` (the only code path that reads a
  source file from a chunk-supplied path) and cached per file by mtime.

### Added (multi-language span/structure backend)
- The span/structure extraction behind `graphify_locate` and `graphify_freshness`
  is no longer Python-only. Python keeps the stdlib `ast` fast path (zero deps,
  decorator-aware); every other language is handled by an optional **tree-sitter**
  backend (`[treesitter]` extra — also ships with graphify) with automatic
  language detection from the file path. So the chunk→symbol span join and the
  cosmetic-vs-structural freshness check now work for JS/TS, Go, Rust, Java, Ruby,
  C/C++, and the ~165 other languages the grammar pack covers.
- Symbol detection is generic (a named def/class/method/struct/… node), so no
  per-language table is maintained; qualnames chain enclosing symbols
  (`Service.fetch`). The tree-sitter parser is built from the stable core
  `Parser(Language)` API (not the pack's churning `get_parser` wrapper).
- Cosmetic detection for non-Python compares a comment-stripped tree-sitter
  skeleton over **all** tokens — operators and keywords included — so any semantic
  edit (an operator flip `+`→`-`, `==`→`!=`, a `sync`→`async` or `let`→`const`
  change, a rename or value change) is structural, while only comment/whitespace
  edits compare equal. When the backend or a language is unavailable, both
  features degrade to the prior behaviour (point heuristic / treat-as-structural)
  — never an error.
- tree-sitter spans now absorb a symbol's leading **doc-comment / decorator /
  annotation** lines into `region_start` (mirroring the Python decorator path), so
  a chunk that starts on the doc comment above a Go/Java/JS method resolves to that
  method. Measured on real repos this lifted Go span-join precision from 48%→80%.
- Broader tree-sitter symbol/qualname coverage: an anonymous function bound to a
  name (`const f = () => …`, object property `{ foo: () => … }`, class field
  `handler = (r) => …`, `var h = func(){}`) takes the binding name; a method
  receiver is type-qualified (Go `func (c *Client) Get()` → `Client.Get`); C/C++
  function names are read from the declarator chain (`Session::Get` → `Session.Get`)
  and template calls / sub-declarators no longer leak in as bogus symbols; `region_start`
  also absorbs Rust `#[attribute]` lines. C++ `class_specifier`/`struct_specifier` are
  recognized as definitions.
- **Multi-language validation benchmark** (`benchmarks/multilang.py` + the
  "Across languages" section in `docs/benchmark*.html`): on real HTTP-client repos
  in five more languages (`got` JS/TS, `resty` Go, `retrofit` Java, `ureq` Rust,
  `cpr` C++) span-join precision is 69–91% (vs 91% for Python/httpx), qualname
  recovery 50–100%, locate 200–748× cheaper than grep+read, and the
  cosmetic-vs-structural freshness check is correct in every language.

### Added (Phase 3 hardening)
- Optional **bearer auth** for the HTTP transports: set `GRAPHIFY_API_KEY` and
  every HTTP/WebSocket request must carry `Authorization: Bearer <key>`
  (constant-time compared; 401 otherwise). Unset = prior behaviour (rely on
  loopback binding / a fronting proxy); a stderr warning now fires if an HTTP
  transport binds a non-loopback host without a key.
- `graphify_freshness` now separates **cosmetic from structural** changes: each
  changed `.py` file is AST-diffed against its HEAD version (`ast.dump` equality),
  so a comment/whitespace/formatting-only edit no longer pushes the graph toward
  `update`/`rebuild`. Docstring edits still count as structural. The payload gains
  `structural_changes` / `cosmetic_changes`; the AST diff is skipped for change
  sets > 25 files (which already route to a full rebuild).
- Optional **lean tool surface**: `GRAPHIFY_TOOLSET=lean` exposes a coherent,
  mostly dependency-free core that still supports the whole flow — build, orient
  (`graphify_overview`), find (`graphify_search`), traverse (`graphify_subgraph`,
  `graphify_neighbors`), jump to source (`graphify_node_details`), plus
  `graphify_communities` and `graphify_freshness`. `graphify_locate` is included
  only when the `[semble]` extra is installed (otherwise it would just error), and
  `graphify_overview` filters its suggested next steps to the active surface so it
  never points at a trimmed tool. Default `full` is unchanged.

## [0.1.0] - 2026-06-13

### Added
- Initial release.
- CLI-backed tools: `graphify_build`, `graphify_query`, `graphify_path`,
  `graphify_explain`, `graphify_add`.
- graph.json analysis tools (no CLI required): `graphify_overview`,
  `graphify_god_nodes`, `graphify_communities`, `graphify_surprises`,
  `graphify_search`, `graphify_neighbors`, `graphify_subgraph`,
  `graphify_node_details`, `graphify_freshness`.
- Resources: `graphify://report`, `graphify://graph`, `graphify://community/{id}`.
- Prompts: `onboard`, `trace_bug`, `explain_flow`.
- LLM-friendliness: tool annotations, server instructions, `as_json` structured
  output, and token-budgeted subgraph extraction.
- Packaging (`graphify-mcp` console script), pytest suite, ruff config and CI.
