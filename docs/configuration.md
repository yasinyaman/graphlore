# Configuring graphlore

Toolsets, transports, environment variables and network behavior.

Back to the [README](../README.md) · [Tool reference](tools.md) ·
[Concepts](concepts.md) · [Benchmark](benchmark.md)

## Running the server

```bash
GRAPHLORE_PROJECT_DIR=/path/to/repo graphlore
# equivalently:
GRAPHLORE_PROJECT_DIR=/path/to/repo python -m graphlore
```

A folder of several git checkouts can be the project too: see
[Workspaces](workspace.md).

The boot banner on stderr
(`graphlore vX.Y.Z | transport=… | toolset=… | egress=… | project=…`) confirms
which server, toolset and project directory you are running. The project was
renamed from `graphify-mcp`: that name collided with the `graphify-mcp` console
script `graphifyy` ships for its own embedded server.

## Claude Code plugin

The plugin starts the server with [`uvx`](https://docs.astral.sh/uv/) from its
own copy of this repository, so **`uv` must be installed**. It installs graphlore
with the `treesitter` and `embed` extras into an isolated environment —
graphlore's native engine included, as a prebuilt wheel — and points `GRAPHLORE_PROJECT_DIR` at the project you
opened. The first start downloads the dependencies and can take a while; later
starts come from uv's cache. Without `uv`, use the manual setup below. The plugin
sets `GRAPHLORE_TOOLSET=full`: its skill uses the review and naming tools that the
default `lean` surface leaves out.

**Start a new session after updating the plugin.** A client keeps the tool
schemas it loaded earlier in the session, so after an update mid-session the
server already has the new parameters (results mention them) while tools loaded
before the update still show the old ones — no `scope`, no `verbose`.
`graphlore_project()` and `graphlore_overview()` print the server version, so the
mismatch can be seen: a schema that lacks a parameter the docs describe,
next to a current version number, means the session predates the update.

## Plugins for other clients

The repository root is also an [Agent Plugins 1.0](https://github.com/agentplugins/agent-plugins-spec)
plugin (`plugin.json`, `mcp.json`, `skills/`), which Codex and Copilot CLI
install, and a Gemini CLI extension (`gemini-extension.json`); the commands are
in [clients](clients.md#install-as-a-plugin). Each starts the server as the
Claude Code plugin does, with `uvx` from its own copy and the same extras and
toolset, so the same requirements hold: `uv` installed, a first start that
downloads the dependencies.

An Agent Plugins client starts the server in the plugin's own directory and has
no variable that names the project. `mcp.json` therefore sets
`GRAPHLORE_PROJECT_FROM_CLIENT=1`: graphlore asks the client for its workspace
roots on the first tool call and reads the first directory among them. A client
that gives none gets a `project_unknown` answer that tells the agent to call
`graphlore_project` with the repository's absolute path, once per session. Gemini
CLI names the workspace (`${workspacePath}`), so its extension sets
`GRAPHLORE_PROJECT_DIR` directly.

## Which toolset

The server starts on the **`lean`** surface: 12 tools (11 when
`GRAPHLORE_SEMANTIC_BACKEND` names a backend that is not available, which drops
`graphlore_locate`) — `build`, `overview`, `locate`,
`search`, `neighbors`, `subgraph`, `impact`, `node_details`, `fetch`,
`communities`, `freshness` and `project`. That covers the whole explore loop:
orient → find → traverse → blast radius → read the code. A smaller surface helps
a model pick the right tool, and every tool definition costs context on every
turn. Choose with `GRAPHLORE_TOOLSET`:

| Value | Tools | Use when |
|---|---|---|
| `lean` (default) | 12 | Exploring, debugging, answering "where/how is X" |
| `full` | all 33 | Reviewing structure (cycles, layers, dead or duplicated code), routes and package APIs, naming communities, diffs, `query`, `path` and `explain` |
| `locate` | 5 | One way in (`graphlore_locate`), one way to the code (`graphlore_fetch`), plus `overview` / `build` / `freshness`; falls back to `lean` only when `GRAPHLORE_SEMANTIC_BACKEND` names a backend that is not available |

An unknown value falls back to `lean` with a warning on stderr; the boot banner
prints the toolset and its tool count. The server instructions and prompts are
rendered for the active surface, so they never name a tool that is switched
off. **The Claude Code plugin pins `full`**, since its skill uses the review and
naming tools.

**Next to Graphify's embedded MCP server.** Running both gives the model two
tools for the same verbs — Graphify's `query` / node lookup / `neighbors` /
`communities` / god nodes / shortest path against graphlore's `query`,
`node_details`, `neighbors`, `communities`, `god_nodes` and `path`. Prefer one
server per project. If you need both (Graphify's PR triage, its multi-project
serving), keep graphlore on `lean`, which leaves out its `query` / `path` /
`explain` passthroughs, and tell the model which server answers what.

## How large a reply gets

A tool called with its default arguments answers in at most 35,000 characters
(about 10,000 tokens), JSON or text: under Claude Code's warning (10,000
tokens; it stops at `MAX_MCP_OUTPUT_TOKENS`, 25,000), Codex's usual
`tool_output_token_limit` and Gemini CLI's 40,000-character truncation (see
[clients](clients.md)). A list cut to fit says so (`truncated`, a count of
what was left out, a `hint`), and the arguments that ask for more are the
tool's `limit`, `top_n`, `budget_tokens` or `max_cycles`. JSON replies are
compact (no indentation). `test_default_replies_stay_under_what_clients_keep`
and `benchmarks/polyglot.py` (`MAX_REPLY_CHARS`) hold the budget.

## Transport (stdio default, optional HTTP)

stdio is the default and the right choice for a per-developer local server. To
serve over HTTP instead (e.g. a shared graph for a team or a web MCP client):

```bash
GRAPHLORE_TRANSPORT=streamable-http GRAPHLORE_HOST=127.0.0.1 GRAPHLORE_PORT=8000 \
  GRAPHLORE_PROJECT_DIR=/path/to/repo graphlore
```

Any HTTP transport **force-enables path containment** (`GRAPHLORE_RESTRICT_PATHS`)
so a network client can't drive `graphlore_build` to extract arbitrary filesystem
paths. HTTP binds `127.0.0.1` by default. To expose it beyond localhost, set
`GRAPHLORE_API_KEY` — every request must then send `Authorization: Bearer <key>`
(constant-time checked, 401 otherwise); binding a non-loopback host without a key
prints a warning.

When bound to a loopback host, the MCP SDK auto-enables **DNS-rebinding
protection**: only `Host: 127.0.0.1 / localhost / ::1` requests are accepted. A
reverse proxy in front (nginx/caddy on a public name forwarding to
`127.0.0.1`) must therefore rewrite the `Host` header — or set
`GRAPHLORE_ALLOWED_HOSTS` to the public name(s) (comma-separated, `:*` port
wildcards allowed; `*` disables the protection for a trusted proxy).

**Stateless HTTP.** By default a streamable-HTTP client opens a session
(`Mcp-Session-Id`) and every request of it must reach the same process. Set
`GRAPHLORE_STATELESS_HTTP=1` to answer each request on its own, with no session,
so several replicas behind a load balancer can share the traffic, as the MCP
specification's 2026-07-28 revision assumes; `GRAPHLORE_JSON_RESPONSE=1` answers
with one JSON body instead of an SSE stream. Both are streamable-HTTP options
(ignored, with a warning, for `sse`). A stateless server has no session to ask
the client anything, so it reads no workspace roots and cannot sample the
client's model: `graphlore_label_communities` names communities without it. One
process serves one project, the one `GRAPHLORE_PROJECT_DIR` names:
`graphlore_project` is refused over HTTP, and `GRAPHLORE_PROJECT_FROM_CLIENT`
stops the server at boot.

The CLI is always invoked as an argument list with **no shell** (`subprocess.run`
with `shell=False`), so a build `path` or query string can't inject shell commands.
Per-file analyzers (spans/APIs/routes/fetch) are confined to the project
directory through a single path-resolution boundary, so a hostile path in a
graph or chunk can't read files outside the project. For a shared/network
deployment, also consider lowering `GRAPHLORE_TIMEOUT` (default `600`s) so a
single slow `graphlore_build` can't tie up a worker for ten minutes.

```bash
GRAPHLORE_TRANSPORT=streamable-http GRAPHLORE_HOST=0.0.0.0 GRAPHLORE_API_KEY=$(openssl rand -hex 16) \
  GRAPHLORE_PROJECT_DIR=/path/to/repo graphlore
```

### OAuth for remote clients

Remote MCP clients — claude.ai and ChatGPT connectors, Claude Code or Codex
over HTTP — sign in with OAuth, as the MCP specification describes. graphlore
is the resource server: it checks the tokens your own OAuth / OpenID provider
issues (Auth0, Keycloak, Entra ID, Okta, Google…) and issues none itself; the
clients register with the provider. Install the `remote` extra and set:

```bash
GRAPHLORE_TRANSPORT=streamable-http GRAPHLORE_HOST=0.0.0.0 \
  GRAPHLORE_AUTH_ISSUER=https://your-tenant.example.com \
  GRAPHLORE_AUTH_RESOURCE_URL=https://graphlore.example.com/mcp \
  GRAPHLORE_AUTH_SCOPES=graphlore.read \
  GRAPHLORE_PROJECT_DIR=/path/to/repo graphlore
```

graphlore then publishes `/.well-known/oauth-protected-resource/<path>`
(RFC 9728: the resource and its authorization server), answers a request
without a valid token with 401 and `WWW-Authenticate: Bearer
resource_metadata=…`, and one whose token lacks a required scope with 403. A
token is valid when the provider signed it (keys found by RFC 8414 or OpenID
discovery, asymmetric algorithms only), its `iss` is the issuer, its `aud` is
`GRAPHLORE_AUTH_RESOURCE_URL` exactly and it has not expired. Both URLs must be
`https` (or localhost). OAuth and `GRAPHLORE_API_KEY` exclude each other, and
`GRAPHLORE_OFFLINE` refuses OAuth (the keys come from the provider).

## What reaches the network, and when

The graph.json analysis tools read local files only, and so does the built-in
search index's BM25 tier. These are the exceptions:

| Path | When it goes out | With `GRAPHLORE_OFFLINE=1` |
|---|---|---|
| Semantic search (`[semble]`: `graphlore_locate`, `graphlore_query`, `graphlore_duplication_scan`) | First use: semble downloads its embedding model from the Hugging Face Hub, then caches it; only with `GRAPHLORE_SEMANTIC_BACKEND=semble` | `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` are set before semble is imported, so only a cached model loads; otherwise the error says how to prefetch it |
| Built-in search, dense tier (`[embed]`: the same three tools) | First use: model2vec downloads `GRAPHLORE_EMBED_MODEL` (default `minishlab/potion-code-16M-v2`) from the Hugging Face Hub, then caches it | The same flags are set before model2vec is imported, so only a cached model loads; otherwise the index answers with BM25 alone and `backend.dense_unavailable` says why |
| `GRAPHLORE_TOKENIZER=tiktoken` | First use: tiktoken fetches its encoding file unless cached | Not loaded; the chars/3.5 estimate is used |
| tree-sitter grammars (`[treesitter]`) | First use of a language: the grammar is downloaded from GitHub releases and cached | Only grammars already in the cache are used; for the other files the native engine's compiled-in grammars answer instead |
| `graph.html` (written after a build) and the viewer (`viewer.html`, also on GitHub Pages) | Never: the page's code and data are in the file, a graph.json opened in it is read in the browser, and its Content Security Policy allows no request (the Pages copy is fetched once, like any web page, and sends nothing) | Unchanged |
| A SCIP index (`GRAPHLORE_SCIP`) | Never: graphlore reads a local file an indexer made; it runs no indexer | Unchanged |
| Native engine (`graphlore-engine`, `graphlore_build`) | Never: its grammars are compiled into the wheel and a build reads local files only | Unchanged |
| A custom `GRAPHLORE_SEMANTIC_BACKEND` | Whatever that code does | **Not constrained** — graphlore cannot see inside it |
| HTTP transports | Inbound only (the server listens) | Unchanged |
| OAuth on HTTP (`GRAPHLORE_AUTH_ISSUER`, the `remote` extra) | At the first token: the provider's metadata (RFC 8414 / OpenID discovery) and its signing keys (JWKS), then the keys again when they change (cached an hour) | Refused at start-up: the server exits with the reason |

What graphlore enforces is its own side: it refuses the calls above and sets the
Hugging Face offline flags for itself and its children. What a custom backend
does beyond that is its own. For a guarantee, pair the switch with
a network policy (an egress firewall, a container without a route out). The
boot banner on stderr prints `egress=offline` or `egress=on-demand`.

## The native engine

`engine/` in this repository is graphlore's own extractor, written in Rust and
shipped as a separate package, `graphlore-engine`, whose wheels carry the
tree-sitter grammars for Python, JavaScript, TypeScript/TSX, Go, Java, Kotlin,
Scala, C#, Rust, Swift, C, C++, Ruby and PHP. It builds the graph: graphify,
the CLI that built it before, was removed as an extractor in 0.9.0 (a graph it
wrote is still read). A native graph
has a node per file, per definition (with its `start..end` span, `kind` and
`qualname`), property, module-level constant and Markdown / reStructuredText
heading (Sphinx `.txt` sources included), with a `documents` edge from a heading
to the definition a name of code under it means; containment; and the imports (with when each runs), calls, bases and
type references it resolves from the source, each `EXTRACTED` or, when found
by name alone, `INFERRED`. It is written in graphify's id and label conventions,
so every tool reads it. It also has shared state: variables, fields and
constants, and environment variables, files, tables, events and cache keys,
linked by `reads` / `writes` to the definitions that touch them
([native-engine.md](native-engine.md#shared-state-and-resources)). Its communities are its own (Leiden on edges weighted
by kind, confidence and directory, see
[native-engine.md](native-engine.md#communities)), not graphify's, and stay put
across rebuilds, so names saved for them stay attached; every native build says
so.

It is a dependency of graphlore, prebuilt for Linux (glibc and musl, x86-64
and ARM), macOS and Windows, so `pip install graphlore` installs it; no Rust
toolchain is needed. (The `engine` extra is kept, empty, so `graphlore[engine]`
still installs.)

From a checkout, with a Rust toolchain:

```bash
pip install maturin
maturin build -m engine/Cargo.toml --release --out dist-engine
pip install dist-engine/*.whl
```

`graphlore_build`'s `update` parses only the files that
changed since the last native build (a facts cache in
`graphify-out/cache/graphlore-native.bin`; resolution and communities still run
over the whole project, so the graph is the one a full build writes, and the
nodes of deleted files go), `cluster_only` detects the communities of the graph
on disk again, `code_only` skips the document headings, and every build writes
`graph.html` beside graph.json. The build runs in a child process bounded by
`GRAPHLORE_TIMEOUT`. `benchmarks/compare_engines.py REPO` compares its graph of
a repository with graphify's, per language; [native-engine.md](native-engine.md)
has the results on 31 repositories and the roadmap.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GRAPHLORE_PROJECT_DIR` | `.` | Project root to extract the graph from |
| `GRAPHLORE_PROJECT_FROM_CLIENT` | off | `1`: the project is not the working directory but the client's: its first workspace root (MCP `roots/list`, asked on the first tool call), else the directory `graphlore_project` is given; until then every other tool answers `code: project_unknown`. Ignored when `GRAPHLORE_PROJECT_DIR` is set. The [plugin](#plugins-for-other-clients) of a client that starts the server in the plugin's own directory sets it |
| `GRAPHLORE_OUT_DIR` | `graphify-out` | Output folder name |
| `GRAPHLORE_ENGINE` | `auto` | What `graphlore_build` extracts with: `auto` and `native` both mean graphlore's native engine (the `graphlore-engine` package; an error when it is not installed). `graphify` was removed in 0.9.0 and is an error that says so, as is another value. See [The native engine](#the-native-engine) |
| `GRAPHLORE_TIMEOUT` | `600` | Native build timeout (seconds) |
| `GRAPHLORE_RESTRICT_PATHS` | `0` | Confine `graphlore_build`'s `path` to the project dir (auto-on for HTTP) |
| `GRAPHLORE_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `GRAPHLORE_HOST` | `127.0.0.1` | Bind host for HTTP transports |
| `GRAPHLORE_PORT` | `8000` | Bind port for HTTP transports |
| `GRAPHLORE_API_KEY` | _(unset)_ | Require `Authorization: Bearer <key>` on HTTP transports |
| `GRAPHLORE_AUTH_ISSUER` | _(unset)_ | OAuth on HTTP transports: the provider's issuer URL ([OAuth for remote clients](#oauth-for-remote-clients)) |
| `GRAPHLORE_AUTH_RESOURCE_URL` | _(unset)_ | OAuth: this server's public URL, which a token's `aud` must be |
| `GRAPHLORE_AUTH_SCOPES` | _(unset)_ | OAuth: scopes every token must carry, comma-separated |
| `GRAPHLORE_STATELESS_HTTP` | off | `1`: streamable HTTP with no session, each request answered on its own (no roots, no sampling). See [Transport](#transport-stdio-default-optional-http) |
| `GRAPHLORE_JSON_RESPONSE` | off | `1`: streamable HTTP answers with a JSON body instead of an SSE stream |
| `GRAPHLORE_ALLOWED_HOSTS` | _(unset)_ | DNS-rebinding `Host` allowlist for HTTP (comma-separated, `:*` port wildcards; `*` disables). Unset = SDK default: loopback-only when bound to loopback |
| `GRAPHLORE_TOOLSET` | `lean` | `lean` (12 core exploration tools) \| `full` (all 33) \| `locate` (minimal locate-first surface; falls back to `lean` when the configured backend is unavailable). See [Which toolset](#which-toolset) |
| `GRAPHLORE_TOKENIZER` | _(heuristic)_ | `tiktoken` → exact token counts (needs the `[tiktoken]` extra); else chars/3.5 estimate |
| `GRAPHLORE_SEMANTIC_BACKEND` | _(auto)_ | Search backend of `graphlore_locate` and the duplication scan: unset / `auto` / `builtin` → the built-in index, whether or not semble is installed; `semble` → semble only (the one way to use it); or `module.path:Factory` implementing `search`/`find_related` (its module is checked at boot, the factory at the first call) |
| `GRAPHLORE_EMBED_MODEL` | `minishlab/potion-code-16M-v2` | The built-in index's dense tier (`[embed]` extra): a Hugging Face model id, a local model directory, or `off` for BM25 only |
| `GRAPHLORE_CACHE_DIR` | `$XDG_CACHE_HOME/graphlore` (`~/.cache/graphlore`) | Where the built-in index keeps chunk vectors between runs, and `graphlore hook` its index of the graph's names (one file per project; never inside the project); `off` keeps them in memory only |
| `GRAPHLORE_HOOKS` | on | `0`: `graphlore hook` (the Claude Code plugin's hooks, `graphlore setup claude-code --with-hooks`) says nothing. See [Claude Code](clients.md#claude-code) |
| `GRAPHLORE_SEARCH_KERNEL` | _(auto)_ | `python` forces the built-in index's reference kernel even when the native engine's (graphlore-engine 0.5.80 or later) is installed |
| `GRAPHLORE_SCIP` | `auto` | SCIP index files to check the graph against ([concepts](concepts.md#a-compilers-answer-scip-indexes)): `auto` reads `index.scip` at the project root or in the output directory, `off` none, or a comma-separated list of paths inside the project |
| `GRAPHLORE_RESOLVE` | `1` | Resolve calls and imports made through a module object (`mod.func()`) from the source at read time; `0` reads graph.json as it is |
| `GRAPHLORE_WATCH` | `0` | Filesystem watcher: auto re-sync on structural changes (`[watch]` extra) |
| `GRAPHLORE_WATCH_DEBOUNCE` | `2.0` | Watcher debounce window (seconds) |
| `GRAPHLORE_OFFLINE` | `0` | Refuse every path graphlore knows reaches the network (see [What reaches the network](#what-reaches-the-network-and-when)) |
| `GRAPHLORE_STRUCTURED` | `1` | `0` → plain-text tool results, without `structuredContent` / `isError` |

> Every variable is also honored under its legacy `GRAPHIFY_*` spelling (the
> pre-rename names); when both are set, `GRAPHLORE_*` wins. `graphify-out/` and
> `.graphify_labels.json`, named after the Graphify CLI that built the graph
> before 0.9.0, keep their names regardless.
