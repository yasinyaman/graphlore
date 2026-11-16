# How graphlore works

What graphlore adds on top of the Graphify graph, how it checks what it reads,
and how it keeps answers honest when the code moves.

Back to the [README](../README.md) · [Tool reference](tools.md) ·
[Configuration](configuration.md) · [Benchmark](benchmark.md)

## What graphlore adds to the graph it reads

graphify's local extractor links `from pkg import helper; helper()` to the
function, but not `from pkg import mod; mod.helper()` — and often not the import
of `mod` either. Test files are written almost entirely in the second form, so a
function could come back with no callers and no tests while four tests call it.

graphlore closes that gap at read time: for `mod.name(...)` where `mod` is bound
by an import to a first-party module that defines `name` (or re-exports it), it
adds the call edge, marked `resolved_by: "graphlore"`; the missing module import
is added the same way. It fills what each language's extractor is seen to leave
out, and nothing more:

| Language | Added by graphlore |
|---|---|
| Python | every call through a module object, and the module import |
| JS / TS | calls through a default import (`import api from './api'`) and through a dynamically imported module (`const m = await import(...)`) — graphify already links namespace imports and `require` |
| Go, Java | nothing — graphify links package-selector and static calls itself |
 `graphlore_impact`, `graphlore_neighbors`,
`graphlore_subgraph`, `graphlore_tests_for`, `graphlore_unreferenced`,
`graphlore_cycles` and `graphlore_layers` read these edges; `graphlore_validate`
says how many there are. graph.json is never modified.

Python is read with the stdlib `ast`, JS/TS with the `[treesitter]` extra. It
costs one parse per Python and JS/TS file of the graph, once per process (about
a second per hundred files); `GRAPHLORE_RESOLVE=0` turns it off.
A call through an object, a callback or dynamic dispatch stays invisible, which
is why an empty answer from these tools says "no edge was indexed", not "nothing
calls this".

## Import edges, checked against the source

`graphlore_cycles`, `graphlore_layers` and `graphlore_surprises` read each import
back from the importing file before they trust it — which file it can mean, and
when it runs:

| Language | How | What is told apart |
|---|---|---|
| Python | stdlib `ast` | module-level · inside a function (deferred) · under `if TYPE_CHECKING:` |
| JS / TS | `[treesitter]` | top-level `import` / `export … from` / `require` · `import()` or `require` inside a function (deferred) · `import type` |
| Go, Java, Kotlin, Scala, C#, Rust, Swift, C, C++, Objective-C | — | imports are resolved at compile time (a `#include` by the preprocessor): always module-level |
| PHP | — | a `use` only aliases a name (the file loads on first use, by autoload): always module-level |
| Ruby | — | graphify records no file-level import edge for `require`: nothing to read |
| anything else | — | kept as it is and counted as module-level; the result says so |

An edge the source contradicts is dropped and listed: Python's `import yaml`
linked to a first-party `yaml.py`, or a bare JS specifier (`'yaml'`) linked to
`lib/yaml.js`. A tsconfig path alias cannot be told from a scoped package
without the config, so those edges are kept.

A graph the native engine built (`GRAPHLORE_ENGINE=native`) needs none of this:
it resolves each import by path when it reads the statement, and records when it
runs on the edge (`runs`), so the check takes that and rejects nothing. The calls
and imports through a module object that graphlore adds to graphify's graphs
(above) are in such a graph already, and nothing is added to it.

An import through a re-exporting entry file is kept too. graphify links
`import { CreateDto } from './dto'` to the file that defines `CreateDto`, not to
`dto/index.ts`; the statement still loads that file, so the edge holds. Only
re-exports are followed (`export … from`, `module.exports = require(…)`),
through at most three files: a file the entry merely imports is not what the
importer asked for.

## Analysis scope

`graphlore_god_nodes`, `graphlore_validate`, `graphlore_cycles`,
`graphlore_surprises`, `graphlore_duplication_scan`, `graphlore_unreferenced` and
`graphlore_same_names` take one `scope` string. By default it leaves out what is
not the project's own code, and each result says how many nodes that was:

- **vendored and minified files** — under `vendor/`, `node_modules/`,
  `third_party/`; named `*.min.js` or with a build hash (`decorate-BgofJUJ5.js`);
  or JS/CSS whose lines are far too long to be hand-written;
- **external nodes** — imported packages (`typing`, `pytest`) and names graphify
  could not resolve to a file.

| Token | Effect |
|---|---|
| `src/pkg`, `**/*.py` | keep only files under this prefix / matching this glob |
| `!docs` | leave these files out |
| `-tests` / `+tests` | leave out / include test code |
| `+external`, `+vendored` | bring those back |
| `all` | the whole graph |

What was left out comes back in every result as `scope.left_out`: node counts,
`vendored_file_count`, three example paths (`vendored_files`) and the directories
holding most of them (`vendored_dirs`), plus a one-line note in the text.
`graphlore_validate` lists every vendored file.

`graphlore_impact`, `graphlore_subgraph` and `graphlore_neighbors` take the same
`scope` string but leave nothing out by default: they walk out from a node you
named, and `scope="-tests"` or `scope="src"` is how to narrow the walk.

Tokens are separated by commas or spaces: `scope="src/pkg -tests"`. To keep
vendored files out of the graph itself, list them in `.graphifyignore` —
`graphlore_build` names them when it finds any.

## Keeping the graph fresh

The analysis tools surface staleness for you: `graphlore_overview` and
`graphlore_subgraph` carry a lightweight `graph_age` ("built 3 commits ago"), and
`graphlore_freshness` gives a full `recommended_action` (fresh / update / rebuild).
The answers themselves say which of *their* files changed: when the graph records
the commit it was built from, `graphlore_fetch` flags each entry
(`file_changed_since_build`, plus `span_mismatch` when the graph's line now lands
in a different symbol), and `graphlore_locate`, `graphlore_impact` and
`graphlore_subgraph` carry a `build_drift` block listing the changed files the
answer rests on. The comparison is the build commit against the working tree
(committed, staged and unstaged changes and untracked files, two git calls per
answer); a file that was already dirty when the graph was built reads as
changed. Without a recorded build commit or git, `build_drift.known` is false and
says why. To stop thinking about it, regenerate on every commit with a git **post-commit
hook** — the recommended first-class auto-update flow:

```sh
# .git/hooks/post-commit   (then: chmod +x .git/hooks/post-commit)
#!/bin/sh
# incremental, viz-free, backgrounded so the commit returns immediately
graphify . --update --no-viz >/dev/null 2>&1 &
```

Incremental `--update` only re-extracts changed files — it can't *drop* nodes for
deleted/renamed code on its own. `graphlore_prune` closes that gap: it surgically
removes the phantom nodes (and their edges) for source files that are gone from the
working tree, so after a delete/rename you can `graphlore_prune` (preview with
`dry_run=True`) + `graphlore_build(update=True)` instead of a full rebuild.
`graphlore_freshness` knows about this — it only steers to a rebuild while phantom
nodes for the removed files still linger, and reports them in `phantom_files`. An
agent can also just call `graphlore_build(update=True)` when `graph_age` /
`graphlore_freshness` says the graph drifted.

There's also an opt-in filesystem watcher (`GRAPHLORE_WATCH=1`, the `[watch]`
extra): it re-syncs the graph on structural source changes, ignores cosmetic
edits and non-source churn (VCS internals, virtualenvs, its own output), and
debounces via `GRAPHLORE_WATCH_DEBOUNCE`.

## Semantic bridge (optional `[semble]`)

`pip install "graphlore[semble]"` adds `graphlore_locate`, which joins
[semble](https://github.com/MinishLab/semble)'s semantic code search to the graph
in one call. Graphify gives **structure** (how code connects); semble gives
**retrieval** (which code is semantically relevant) — they're complementary.

`graphlore_locate("how does retry backoff work")`:
1. semble finds the most relevant code and resolves the top hit to its enclosing
   graph node (better than label matching).
2. returns the token-budgeted subgraph around it (**structure**).
3. runs semble `find_related` and cross-checks: a cousin that is semantically
   similar but **not** within the seed's structural neighborhood is flagged as a
   `hidden_link` (with its hop distance) — a duplication / missing-abstraction /
   implicit-coupling candidate that neither tool surfaces alone.

**The first semantic call downloads a model.** semble runs locally and needs no
API key, but on first use it fetches its embedding model from the Hugging Face Hub
(`minishlab/potion-code-16M-v2` as of semble 0.7) and caches it; later calls need
no network. To fetch it ahead of time:

```bash
python -c "from model2vec import StaticModel; StaticModel.from_pretrained('minishlab/potion-code-16M-v2')"
```

For a machine without network access, copy `~/.cache/huggingface` over from one
that has run the command, or point `SEMBLE_MODEL_NAME` at a local copy of the model.

The extra is optional: without it the core tools are unchanged and `graphlore_locate`
returns an install hint. Any other embedding backend can be plugged in via
`GRAPHLORE_SEMANTIC_BACKEND=module.path:Factory` (implementing `search` /
`find_related`). It also pairs well with running semble's own MCP server
alongside graphlore.

The chunk→node join and the freshness cosmetic-vs-structural check work
**across languages**: Python uses the stdlib `ast` (no extra deps), and every
other language (JS/TS, Go, Rust, Java, Kotlin, Scala, C#, Swift, Ruby, PHP,
C/C++, …) is handled by an optional **tree-sitter** backend —
`pip install "graphlore[treesitter]"`, also pulled in by graphify. Without it,
non-Python files fall back to nearest-line matching. A `.h` file that reads as
C++ (a `class`, a `namespace`, a `template<`) is parsed as C++.
[What was measured per language](benchmark-polyglot.md), on real repositories.

## Communities

A community is a group of nodes that belong together: `graphlore_communities`
lists them, `graphlore_skeleton(community=…)` shows one's code, and
`graphlore_surprises` counts an edge between two of them as a reason to look.
graphify finds them with Leiden on the graph as it is. The native engine runs
Leiden too, but on edges weighted by what they say (a containment or a call binds
tighter than a type named in passing, a base class tighter still), by how sure
they are (a guess by name counts half) and by where their ends live (the same
directory binds tighter, other top-level directories and test-to-production
edges looser), plus a weak tie between the files of a directory. Its
communities sit more in one directory and keep tests apart from the code they
test; [native-engine.md](native-engine.md#communities) has the rules and the
measurements. A native rebuild starts from the previous graph's communities:
a one-file change leaves almost every node where it was, the ids stay, and so
do the names saved for them, which are dropped only when their community
changed beyond recognition (the build says how many).

## Naming communities without an API key (MCP sampling)

The Leiden clustering is keyless, but turning `Community 7` into `Authentication`
needs a model. Three ways, in `graphlore_label_communities`'s preference order:

1. **Host-LLM sampling** — the server asks the *connected client* to run the
   completion via MCP `sampling/createMessage`. The model the user already uses
   (e.g. Claude in a sampling-capable client) does the naming; **the server holds
   no API key**. Subject to client support — call `graphlore_sampling_status`
   first; it degrades gracefully when unsupported. All communities are named in
   a single batched request, carried over whichever transport the negotiated
   protocol allows (the legacy back-channel, or input-required rounds on MCP
   2026-07-28+), so it works with both older and modern clients.
2. **Backend API key** (`method="cli"`) — set `GEMINI_API_KEY` / `OPENAI_API_KEY`
   / `ANTHROPIC_API_KEY` / … (or run a local **ollama**) and graphify's own
   backend names them. This option always remains available.
3. **Placeholders** — no model anywhere: names stay `Community N`.

If the client can't sample and you have no backend (e.g. **Claude Code**, which
doesn't support sampling), use the **assistant-driven fallback**: the assistant
is already a capable model in the loop, so it reads `graphlore_communities` and
pushes names back via **`graphlore_set_labels({"0": "Authentication", ...})`** —
no key, no sampling, works in any client. The names persist to
`.graphify_labels.json` and are patched into `graph.html`.
