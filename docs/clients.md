# graphlore in each MCP client

graphlore is a local stdio MCP server. Every client below starts it the same
way, as the command `graphlore` (installed as the [README](../README.md#quick-start)
says), with `GRAPHLORE_PROJECT_DIR` naming the repository it reads (and
`GRAPHLORE_TOOLSET=full` for all 34 tools; the default `lean` has 12).
`uvx --from "graphlore[engine,treesitter]" graphlore` starts it from PyPI with
nothing installed (`--command uvx` below); the examples on this page use that
form.

The quickest way is to let graphlore write the block:

```bash
graphlore setup codex            # print the block and where it goes
```

```bash
graphlore setup cursor --write --with-agents-md   # merge it into .cursor/mcp.json, write the guide
```

`graphlore clients` lists the names. `setup` takes `--project DIR` (default:
the current directory), `--toolset lean|full|locate`, `--user` (the client's
user-level file instead of the project's) and `--command` (`graphlore`, the
default: the command on PATH; `uvx`; or a path). `--write` keeps every other
server in the file, saves the old file as `<name>.graphlore.bak`, and refuses
a file it cannot parse (a JSON file with comments) rather than rewrite it.

Back to the [README](../README.md) · [Tools](tools.md) ·
[Configuration](configuration.md)

## At a glance

| Client | Project file | User file | Key | Tools it shows | MCP prompts | Server instructions |
|---|---|---|---|---|---|---|
| Claude Code | `.mcp.json` | `~/.claude.json` | `mcpServers` | all (deferred by tool search) | yes | yes, up to 4,096 characters |
| Claude Desktop | — | `claude_desktop_config.json` | `mcpServers` | — | yes | — |
| OpenAI Codex CLI | `.codex/config.toml` (trusted projects) | `~/.codex/config.toml` | `[mcp_servers.<name>]` | — | no | — |
| Google Gemini CLI | `.gemini/settings.json` | `~/.gemini/settings.json` | `mcpServers` | — | yes, as slash commands | yes, appended to the system prompt |
| Cursor | `.cursor/mcp.json` | `~/.cursor/mcp.json` | `mcpServers` | capped across servers | yes | — |
| VS Code (Copilot) | `.mcp.json` | `~/.copilot/mcp-config.json` | `mcpServers` | 128 per request (Local harness) | no | — |
| Kimi Code CLI | `.kimi-code/mcp.json` | `~/.kimi-code/mcp.json` | `mcpServers` | — | — | — |
| Zed | — | `~/.config/zed/settings.json` | `context_servers` | — | yes | — |
| Windsurf | — | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | 100 in all | yes | — |
| Cline | — | `~/.cline/data/settings/cline_mcp_settings.json` (CLI) | `mcpServers` | — | — | — |

"—": not documented by the client. Where a client caps the tools of all its
servers together (Cursor, Windsurf, Copilot), keep the `lean` toolset.

## Install as a plugin

The repository root is a plugin in three formats, so these clients install the
server and the `graphlore-explore` skill in one step, with no config to write
(each needs [`uv`](https://docs.astral.sh/uv/); see
[configuration](configuration.md#plugins-for-other-clients)):

| Client | Format it reads | Install |
|---|---|---|
| Claude Code | `.claude-plugin/` (plugin and marketplace) | `/plugin install graphlore --marketplace yasinyaman/graphlore` |
| OpenAI Codex CLI | [Agent Plugins 1.0](https://github.com/agentplugins/agent-plugins-spec) (`plugin.json`, `mcp.json`, `skills/`), listed by `.claude-plugin/marketplace.json` | `codex plugin marketplace add yasinyaman/graphlore`, then `codex plugin add graphlore@graphlore` |
| GitHub Copilot CLI | Agent Plugins 1.0, listed by `.claude-plugin/marketplace.json` | `copilot plugin marketplace add yasinyaman/graphlore`, then `copilot plugin install graphlore@graphlore` |
| Google Gemini CLI | `gemini-extension.json`, `skills/`, `.gemini-extension/GRAPHLORE.md` | `gemini extensions install https://github.com/yasinyaman/graphlore` |
| Cursor | Agent Plugins 1.0, by Cursor's documentation | not tried |
| Kimi Code CLI | its own `kimi.plugin.json`, which graphlore does not ship | `graphlore setup kimi` |

Codex 0.171, Copilot CLI 1.0.114 and Gemini CLI 0.70 were checked with a local
copy of the repository (November 2026): each installs the plugin and finds the
skill; Codex and Copilot resolve the server to `uvx --from "graphlore[…] @ ."`
in the installed copy, and Gemini connects to it.
Codex and Copilot start it in the plugin's directory and name no project, so
graphlore takes the client's first workspace root, or asks the agent to call
`graphlore_project` with the repository's path (`GRAPHLORE_PROJECT_FROM_CLIENT`);
Gemini names the workspace itself. Copilot warns that a direct install (a path
or URL) is deprecated: install through the marketplace. The Gemini extension
gallery lists a public repository with the `gemini-cli-extension` topic and a
`gemini-extension.json` at its root, crawled once a day after a tag.

## Telling the agent to use graphlore

A client gives its model the server's tool descriptions; few pass on the
server's `instructions`, and Codex and Copilot show no MCP prompts. What every
agent does read is a project instructions file, so graphlore writes its usage
guide there:

```bash
graphlore agents-md --write AGENTS.md            # Codex, Cursor, Kimi, Copilot, Zed, Cline
```

```bash
graphlore agents-md --write GEMINI.md            # Gemini CLI reads GEMINI.md by default
```

The guide sits between `<!-- graphlore:begin -->` and `<!-- graphlore:end -->`;
running it again replaces only that part. It names only the tools of the
toolset (`--toolset full` for all), says when graphlore beats `grep` and when
not, and how to read an answer (inferred edges, empty results, `did_you_mean`).
`graphlore setup <client> --with-agents-md` writes it into the file that client
reads. Claude Code users get the richer `graphlore-explore` skill with the
plugin instead.

## Claude Code

```bash
claude mcp add --env 'GRAPHLORE_PROJECT_DIR=${CLAUDE_PROJECT_DIR:-.}' --transport stdio --scope project graphlore -- uvx --from 'graphlore[engine,treesitter]' graphlore
```

Or `.mcp.json` at the project root (`graphlore setup claude-code`). An option
must stand between `--env` and the server's name, or the name is read as one
more `KEY=value`. Claude Code passes `CLAUDE_PROJECT_DIR` to the server, so
`${CLAUDE_PROJECT_DIR:-.}` points it at the project. The plugin
(`/plugin install graphlore@graphlore`) does all of this and adds the skill.
Claude Code cuts a tool description and the server instructions at 4,096
characters (graphlore's stay under 900 and 4,000), warns at 10,000 tokens of
output and stops at 25,000 (`MAX_MCP_OUTPUT_TOKENS`). Headless:
`claude -p "<prompt>" --output-format json`.

Claude Code defers MCP tools behind its tool search: until it searches, the
model sees a tool's name and description only, which is why every `lean`
tool's description says what it is for and what it is not for. To have
graphlore's tools loaded from the start instead, set `"alwaysLoad": true` in
the server's entry of `.mcp.json` (all of a server's tools load then, and
startup waits up to 5 seconds for them), or turn deferral off for every server
with `ENABLE_TOOL_SEARCH=false`. The 12 `lean` tools' definitions are about
17,000 characters (~5k tokens), the 34 of `full` about 50,000.
Source: [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp).

**Hooks.** An agent asked to find a name often greps for it, then reads file
after file to learn who calls it. The plugin adds two command hooks
(`hooks/hooks.json`; outside the plugin,
`graphlore setup claude-code --write --with-hooks` merges the same two into
`.claude/settings.json`, keeping every other hook, after a `.graphlore.bak`):

- before a `Grep` or `Glob`, or a `Bash` command that is a search (`rg`,
  `grep`, `git grep`, `ag`, `ack`, not part of a pipeline), whose pattern is
  one identifier the graph defines (`compute`, `\bcompute\b`,
  `def compute`), a line of context names where
  (`a.py:12`, at most three places) and that `graphlore_neighbors` /
  `graphlore_impact` answer who calls it and what a change breaks;
- after a `git commit`, `checkout`, `merge`, `pull` or `rebase` that moves
  HEAD past the graph's build commit, or an edit of a source file, a line says
  the graph is behind and how to update it, once per state.

The hook never blocks a call and never changes its input: it adds
`hookSpecificOutput.additionalContext` or prints nothing (any error, no graph,
a git that does not answer within a second). It reads `graph.json` once per
build into an index under `GRAPHLORE_CACHE_DIR`, so a call costs about 50 ms,
and imports none of the server. Paths from the graph are passed on only when
they are plain paths, and the line says they are repository data.
`GRAPHLORE_HOOKS=0` turns both off. The plugin's hooks run
`python3 -m graphlore.hooks` from the plugin's own copy, and say nothing where
there is no `python3`.
Source: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks),
[plugin manifest](https://code.claude.com/docs/en/plugins/manifest-reference).

## Claude Desktop

`claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`,
Windows `%APPDATA%\Claude\`, Linux `~/.config/Claude/`), key `mcpServers`,
absolute paths only; restart the app after editing. `graphlore setup
claude-desktop` names `uvx` by its full path, since the app does not start
servers with your shell's PATH.
Source: [connect-local-servers](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/develop/connect-local-servers.mdx).

## OpenAI Codex CLI

```bash
codex mcp add graphlore --env GRAPHLORE_PROJECT_DIR=/abs/path/to/repo -- uvx --from 'graphlore[engine,treesitter]' graphlore
```

Or in `~/.codex/config.toml` (a project's `.codex/config.toml` is read only in
a trusted project):

```toml
[mcp_servers.graphlore]
command = "uvx"
args = ["--from", "graphlore[engine,treesitter]", "graphlore"]
[mcp_servers.graphlore.env]
GRAPHLORE_PROJECT_DIR = "/abs/path/to/repo"
```

Codex compacts a tool input schema over 5,000 bytes (graphlore's are under
it), reads `AGENTS.md`, and has no MCP prompts. Headless: `codex exec --json
"<prompt>"` (JSONL events; MCP calls are `mcp_tool_call` items, token use is in
`turn.completed`). Source: the Codex source
([`mcp_cmd.rs`](https://github.com/openai/codex/blob/main/codex-rs/cli/src/mcp_cmd.rs),
[`mcp_types.rs`](https://github.com/openai/codex/blob/main/codex-rs/config/src/mcp_types.rs));
its documentation site was not readable from where this page was written.

**Hooks.** Codex runs the same two hooks as Claude Code (above), in the same
file shape. The plugin's `hooks/hooks.json` is Codex's default place for a
plugin's hooks, and Codex sets `CLAUDE_PLUGIN_ROOT` for it; outside the plugin,
`graphlore setup codex --with-hooks` writes them into the project's
`.codex/hooks.json` (`--user`: `$CODEX_HOME/hooks.json`, `~/.codex/hooks.json`
by default), keeping every other hook. Codex has no Grep tool: it searches in
a shell, so the `pre` hook reads a `Bash` command that is a search (`rg -n
compute src`); a file edit is `apply_patch`, whose patch names the files the
`post` hook checks. Codex runs a hook only after it is reviewed and trusted:
open `/hooks` once after installing. Hooks are on by default
(`[features] hooks = false` turns them all off). Source: Codex's "Hooks"
documentation page (event names, `hooks.json` locations, the
`additionalContext` output of `PreToolUse` and `PostToolUse`, tool names
`Bash` and `apply_patch`, plugin-bundled hooks), read from a copy, November
2026.

## Google Gemini CLI

`.gemini/settings.json` (project) or `~/.gemini/settings.json`, key
`mcpServers`, fields `command`, `args`, `env`, `cwd`, `timeout`, `trust`,
`includeTools`. Gemini starts a project's stdio servers only in a trusted
folder (`GEMINI_CLI_TRUST_WORKSPACE=true` for a headless run), names the tools
`mcp_graphlore_<tool>` (at most 63 characters: graphlore's longest is 40),
strips `additionalProperties` and `anyOf` defaults from schemas (graphlore's
use neither), truncates a reply at 40,000 characters, appends the server's
instructions to its system prompt and turns MCP prompts into slash commands.
It reads `GEMINI.md`; add `AGENTS.md` with `context.fileName`. Headless:
`gemini -p "<prompt>" --output-format json`.
Source: [geminicli.com/docs/tools/mcp-server](https://geminicli.com/docs/tools/mcp-server).

## Cursor

`.cursor/mcp.json` (project) or `~/.cursor/mcp.json`, key `mcpServers`, with
`"type": "stdio"`; `${workspaceFolder}` names the project, so the project file
holds no absolute path. Cursor reads `AGENTS.md` (nested ones too). Headless:
`agent -p "<prompt>" --output-format json`.
Source: [cursor.com/docs/mcp](https://cursor.com/docs/mcp).

## VS Code with GitHub Copilot

Copilot's current files are `.mcp.json` at the workspace root and
`~/.copilot/mcp-config.json` (shared with Copilot CLI), key `mcpServers`, with
`"type": "stdio"`; `.vscode/mcp.json` (key `servers`) is the deprecated
format. Copilot sessions show no MCP prompts; it reads
`.github/copilot-instructions.md` and `AGENTS.md`. The Local harness takes at
most 128 tools per request.
Source: [code.visualstudio.com/docs/agents/reference/mcp-configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## Kimi Code CLI

`.kimi-code/mcp.json` (project) or `~/.kimi-code/mcp.json`, key `mcpServers`;
add it from the TUI with `/mcp-config`. A headless run (`kimi -p "<prompt>"
--output-format stream-json`) starts a project's servers only in a trusted
workspace (`KIMI_CODE_TRUST_WORKSPACE=1`). Kimi reads `AGENTS.md`. The older
`kimi-cli` read `~/.kimi/mcp.json` and had `kimi mcp add`.
Source: [MoonshotAI/kimi-code docs](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/customization/mcp.md).

## Zed

`~/.config/zed/settings.json` (Windows `%APPDATA%\Zed\settings.json`), key
`context_servers`, or Settings → AI → MCP Servers. Zed's settings may hold
comments, which `--write` will not rewrite: paste the block from `graphlore
setup zed`. Zed reads the first of `.rules`, `.cursorrules`, …, `AGENTS.md`,
`CLAUDE.md`, `GEMINI.md` it finds.
Source: [zed docs: ai/mcp.md](https://github.com/zed-industries/zed/blob/main/docs/src/ai/mcp.md).

## Windsurf

`~/.codeium/windsurf/mcp_config.json`, key `mcpServers`; Cascade sees at most
100 tools at a time (`lean`'s 12 fit).
Source: [docs.windsurf.com](https://docs.windsurf.com/windsurf/cascade/mcp).

## Cline

The CLI keeps `~/.cline/data/settings/cline_mcp_settings.json` (key
`mcpServers`; `cline mcp add graphlore --yes -- <command>` sets no
environment, so add `GRAPHLORE_PROJECT_DIR` to the entry); the extension's
file opens from its MCP panel (Configure MCP Servers). Cline reads `AGENTS.md`.
Source: [docs.cline.bot/mcp](https://docs.cline.bot/mcp/mcp-overview).

## Remote clients

claude.ai's custom connectors and ChatGPT's connectors reach graphlore over
HTTPS (`GRAPHLORE_TRANSPORT=streamable-http` behind a public name) and sign in
with OAuth: set `GRAPHLORE_AUTH_ISSUER` and `GRAPHLORE_AUTH_RESOURCE_URL` as
[configuration](configuration.md#oauth-for-remote-clients) says, and register
the connector with your provider (claude.ai's callback is
`https://claude.ai/api/mcp/auth_callback`; both clients use a Client ID
Metadata Document or dynamic registration). claude.ai takes only the first
authorization server graphlore names, and the resource must equal the server's
URL exactly.

## Limits graphlore keeps

Every tool description is at most 900 characters, every parameter has its own
description, every input schema uses plain types only (no `anyOf`, free-key
objects or `null`) and stays under 5,000 bytes, the server instructions stay
under 4,000 characters, and a reply with default arguments under 35,000
characters (~10k tokens): under Claude Code's warning, Codex's usual limit and
Gemini CLI's truncation. Tests hold each of these.

Client formats change between releases; each section names the page it was
read from (November 2026).
