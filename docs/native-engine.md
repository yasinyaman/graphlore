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
   type has, and in a file the caller imports or its directory). That is
   `INFERRED`. A name with several candidates gets no edge; the build reply
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
  Trait for` a type of another crate is the method beside it.
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
[polyglot audit](benchmark-polyglot.md) (graphify 0.9.111, engine 0.5.2,
measured 2026-11-18), as native / graphify edges and the share of graphify's
that the native graph has too:

| Language | same definition | imports (file pairs) | calls | bases | type references |
|---|--:|--:|--:|--:|--:|
| Python | 100% | 212 / 219 (96%) | 2,956 / 834 (94%) | 73 / 62 (100%) | 439 / 494 (68%) |
| JavaScript | 93.5%⁵ | 190 / 305 (59%) | 1,313 / 2,058 (43%)¹ | 1 / 1 | 3 / 0 |
| TypeScript | 99.4% | 527 / 634 (83%) | 809 / 687 (98%) | 25 / 20 (100%) | 736 / 156 (88%) |
| TSX | 99.7% | 253 / 316 (80%) | 435 / 456 (95%) | – | 42 / 4 |
| Go | 100% | 538 / 950 (47%)² | 5,936 / 3,167 (99%) | 19 / 14 (100%) | 679 / 355 (100%) |
| Java | 100% | 1,105 / 1,010 (100%) | 5,660 / 5,123 (85%) | 415 / 168 (94%) | 3,989 / 1,930 (98%) |
| Kotlin | 99.4% | 368 / 333 (95%) | 3,844 / 2,108 (70%) | 96 / 56 (93%) | 1,015 / 673 (81%) |
| Scala | 100% | 2 / 0 | 46 / 20 (45%) | 21 / 22 (95%) | 75 / 32 (100%) |
| C# | 92.7% | 347 / 280 (30%)³ | 2,615 / 2,450 (93%) | 87 / 86 (98%) | 1,252 / 806 (99%) |
| Rust | 93.3% | 314 / 370 (75%) | 1,753 / 853 (79%) | 53 / 41 (93%) | 1,242 / 895 (81%) |
| Swift | 95.9% | 165 / 166 (–)³ | 4,695 / 2,141 (82%) | 190 / 164 (100%) | 1,080 / 580 (62%) |
| C | 100% | 89 / 77 (99%) | 864 / 791 (100%) | – | 253 / 130 (97%) |
| C++ | 97.8% | 215 / 228 (93%) | 505 / 281 (33%)⁴ | 30 / 5 (100%) | 326 / 218 (88%) |
| C/C++ headers | 90.6% | 164 / 172 (95%) | 360 / 348 (85%) | 11 / 12 (92%) | 515 / 289 (84%) |
| Ruby | 99.7% | 42 / 0 | 409 / 375 (66%) | 46 / 15 (93%) | 28 / 0 |
| PHP | 80.6% | 429 / 505 (84%) | 8,863 / 6,140 (90%) | 65 / 60 (98%) | 648 / 213 (95%) |
| **All** | **94.4%** | **4,960 / 5,565 (75%)** | **41,063 / 27,832 (84%)** | **1,132 / 726 (97%)** | **12,322 / 6,775 (88%)** |

Of the native calls, 7,442 (18%) are `INFERRED`, against 47% of graphify's.
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
- **What the native graph still lacks**, read from the same samples: a call
  with an implicit receiver inside a lambda that has one (Kotlin
  `withIndent { add("…") }`, `apply { addProperty(…) }`); a receiver typed
  only by the result of a call that declares none the walk can read
  (`funBuilder.returns(…)` after `val funBuilder = builder(name)`,
  `retrofit.create(Service.class)`, Ruby's `stubs.get('/api')`); a
  target-typed construction (C# `Box b = new(1, 2)`) and a C++ `Buf b(3)`,
  which name no constructor.
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
5.4 s, got 0.9 s / 4.7 s. An update after a one-file change takes a third of
a build ([above](#incremental-updates)).

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

Rerun with engine 0.5.2 (2026-11-18), whose graphs carry 10% more call edges
(constructors, extensions, Ruby's bare names), the audit raises what the run
before it did and nothing else: `REPLY-TOO-LARGE` 5, `TEST-PATH-MISSED` 2,
`ROUTES-SILENT-EMPTY` 1 (express registers its own routes through
`app[method]`), 19 graphs with orphan nodes; 113 s of tool time. The clean
builds of the 31 repositories take 13.1 s.

`REPLY-TOO-LARGE` is gone since: `graphlore_neighbors` cuts a hub at `limit`
and says how its neighbors split by relation, `graphlore_fetch` cuts one
definition over 8,000 tokens at a line and names the budget that returns all
of it, and `graphlore_same_names` lists the first 25 definitions of a name
([tools](tools.md)). retrofit's `Call` went from 98 KB to 10 KB, guzzle's
`CurlFactory` from 124 KB to 27 KB, cpr's `TEST` from 133 KB to 2 KB.

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
