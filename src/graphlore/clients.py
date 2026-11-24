"""MCP clients ``graphlore setup`` knows: where each keeps its servers, in what
shape, and the block that starts graphlore there.

A client is a row of `CLIENTS`: its config files (project-level and user-level,
either may be absent), the format (JSON under a key, or Codex's TOML), and the
file its agent reads project instructions from (for ``--with-agents-md``).
`render` gives the text to paste; `write` merges the server into the file,
keeping every other server, after a backup, and refuses a file it cannot parse
rather than rewrite it. The formats are from each client's own documentation
(`docs/clients.md` cites the pages).

Imports nothing from graphlore: ``graphlore setup`` never loads the server.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_NAME = "graphlore"
# What the client installs and runs, through uv (the same extras as server.json).
UVX_PACKAGE = "graphlore[engine,treesitter]"


class SetupError(Exception):
    """A config file graphlore will not touch (unparseable, or not an object)."""


@dataclass(frozen=True)
class Client:
    name: str
    title: str
    fmt: str                        # "json" | "toml"
    key: str                        # the object servers sit under ("mcpServers", …)
    project_file: str | None        # relative to the project
    user_file: str | None           # "~"-relative; an OS-dependent one is resolved below
    guide_file: str = "AGENTS.md"   # where its agent reads project instructions
    add_command: str = ""           # the client's own command, when it has one
    project_dir_value: str = ""     # what GRAPHLORE_PROJECT_DIR is in the project file
    stdio_type: bool = False        # entries carry "type": "stdio"
    note: str = ""
    absolute_command: bool = False  # name the launcher by its full path


def _zed_settings() -> str:
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", "~"), "Zed", "settings.json")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", "~/.config"), "zed", "settings.json")


def _desktop_config() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", "~"), "Claude",
                            "claude_desktop_config.json")
    return "~/.config/Claude/claude_desktop_config.json"


CLIENTS: dict[str, Client] = {c.name: c for c in (
    Client("claude-code", "Claude Code", "json", "mcpServers", ".mcp.json", None,
           guide_file="CLAUDE.md",
           # an option must stand between --env and the name, or the name is
           # read as one more KEY=value
           add_command="claude mcp add --env 'GRAPHLORE_PROJECT_DIR=${{CLAUDE_PROJECT_DIR:-.}}' "
                       "--transport stdio --scope project graphlore -- {command}",
           project_dir_value="${CLAUDE_PROJECT_DIR:-.}", stdio_type=True,
           note="The plugin (/plugin install graphlore@graphlore) sets all of this up."),
    Client("claude-desktop", "Claude Desktop", "json", "mcpServers", None, _desktop_config(),
           absolute_command=True,
           note="Restart Claude Desktop after editing. It needs absolute paths."),
    Client("codex", "OpenAI Codex CLI", "toml", "mcp_servers", ".codex/config.toml",
           "~/.codex/config.toml",
           add_command="codex mcp add graphlore --env GRAPHLORE_PROJECT_DIR={project} "
                       "-- {command}",
           note="Codex reads a project's .codex/config.toml only in a trusted project."),
    Client("gemini", "Google Gemini CLI", "json", "mcpServers", ".gemini/settings.json",
           "~/.gemini/settings.json", guide_file="GEMINI.md",
           note="Gemini starts a project's stdio servers only in a trusted folder "
                "(GEMINI_CLI_TRUST_WORKSPACE=true for a headless run)."),
    Client("cursor", "Cursor", "json", "mcpServers", ".cursor/mcp.json",
           "~/.cursor/mcp.json", project_dir_value="${workspaceFolder}", stdio_type=True),
    Client("vscode", "VS Code (GitHub Copilot)", "json", "mcpServers", ".mcp.json",
           "~/.copilot/mcp-config.json", guide_file=".github/copilot-instructions.md",
           stdio_type=True,
           note="Copilot shows no MCP prompts: the guide from --with-agents-md is what "
                "tells it the flow. (.vscode/mcp.json is the deprecated format.)"),
    Client("kimi", "Kimi Code CLI", "json", "mcpServers", ".kimi-code/mcp.json",
           "~/.kimi-code/mcp.json",
           note="A headless run (kimi -p) starts a project's servers only in a trusted "
                "workspace (KIMI_CODE_TRUST_WORKSPACE=1). The older kimi-cli read "
                "~/.kimi/mcp.json."),
    Client("zed", "Zed", "json", "context_servers", None, _zed_settings(),
           note="Zed's settings may hold comments: --write refuses such a file; paste "
                "the block in Settings > AI > MCP Servers instead."),
    Client("windsurf", "Windsurf", "json", "mcpServers", None,
           "~/.codeium/windsurf/mcp_config.json",
           note="Cascade sees at most 100 tools at a time: lean (12) fits."),
    Client("cline", "Cline", "json", "mcpServers", None,
           "~/.cline/data/settings/cline_mcp_settings.json",
           add_command="cline mcp add graphlore --yes -- {command}",
           note="That is the Cline CLI's file; the extension's opens from its MCP panel "
                "(Configure MCP Servers). cline mcp add sets no environment: add "
                "GRAPHLORE_PROJECT_DIR to the entry."),
)}


def server_config(
    project: Path, toolset: str, command: str = "graphlore", absolute: bool = False,
) -> dict[str, Any]:
    """``{command, args, env}`` that start graphlore on ``project``: the
    ``graphlore`` command on PATH (default: the README's install), through
    ``uvx`` from PyPI, or a path. ``absolute`` names the command by its full
    path, for a client that does not start servers with the user's shell PATH
    (Claude Desktop)."""
    if command == "uvx":
        cmd = (shutil.which("uvx") or "uvx") if absolute else "uvx"
        args = ["--from", UVX_PACKAGE, "graphlore"]
    elif command in ("graphlore", "installed"):
        cmd = (shutil.which("graphlore") or "graphlore") if absolute else "graphlore"
        args = []
    else:
        cmd, args = command, []
    env = {"GRAPHLORE_PROJECT_DIR": str(project)}
    if toolset != "lean":
        env["GRAPHLORE_TOOLSET"] = toolset
    return {"command": cmd, "args": args, "env": env}


def _entry(client: Client, cfg: dict[str, Any], user: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if client.stdio_type:
        entry["type"] = "stdio"
    entry.update(cfg)
    entry["env"] = dict(cfg["env"])
    if not user and client.project_file and client.project_dir_value:
        # a project file travels with the repository: no absolute path in it
        entry["env"]["GRAPHLORE_PROJECT_DIR"] = client.project_dir_value
    return entry


def target(client: Client, project: Path, user: bool = False) -> Path | None:
    """The file ``--write`` merges into: the project's, else (or with ``user``)
    the user's; None when the client keeps neither."""
    if not user and client.project_file:
        return project / client.project_file
    if client.user_file:
        return Path(os.path.expanduser(client.user_file))
    return None


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a TOML basic string


def _toml_block(entry: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{SERVER_NAME}]",
             f"command = {_toml_str(entry['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in entry["args"]) + "]",
             f"[mcp_servers.{SERVER_NAME}.env]"]
    lines += [f"{k} = {_toml_str(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def render(client: Client, cfg: dict[str, Any], project: Path, user: bool = False) -> str:
    """The block to paste, where it goes, and the client's own add command."""
    entry = _entry(client, cfg, user)
    where = target(client, project, user)
    if client.fmt == "toml":
        body = _toml_block(entry)
    else:
        body = json.dumps({client.key: {SERVER_NAME: entry}}, indent=2, ensure_ascii=False)
    place = f"put this in {where}" if where else "add this to its MCP config"
    out = [f"# {client.title}: {place}", body.rstrip("\n")]
    if client.add_command:
        command = shlex.join([cfg["command"], *cfg["args"]])
        out.append("\n# or with its own command:\n" + client.add_command.format(
            command=command, project=shlex.quote(cfg["env"]["GRAPHLORE_PROJECT_DIR"])))
    if client.note:
        out.append(f"\n# {client.note}")
    return "\n".join(out) + "\n"


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".graphlore.bak"))


_TOML_TABLE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*$")


def _merge_toml(text: str, block: str) -> str:
    """``text`` with graphlore's ``[mcp_servers.graphlore]`` tables (and their
    subtables) replaced by ``block``, or the block appended."""
    own = f"mcp_servers.{SERVER_NAME}"
    out: list[str] = []
    skipping = placed = False
    for line in text.splitlines():
        m = _TOML_TABLE.match(line)
        if m:
            name = m.group(1).replace('"', "")
            mine = name == own or name.startswith(own + ".")
            if mine and not placed:
                out.append(block.rstrip("\n"))
                placed = True
            skipping = mine
            if mine:
                continue
        if not skipping:
            out.append(line)
    if not placed:
        if out and out[-1].strip():
            out.append("")
        out.append(block.rstrip("\n"))
    return "\n".join(out) + "\n"


def write(client: Client, path: Path, cfg: dict[str, Any], user: bool = False) -> str:
    """Merge graphlore into ``path``; a sentence saying what was done."""
    entry = _entry(client, cfg, user)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if client.fmt == "toml":
        new = _merge_toml(old, _toml_block(entry))
        try:
            import tomllib  # Python 3.11+: check the result parses
        except ImportError:  # pragma: no cover - 3.10
            tomllib = None  # type: ignore[assignment]
        if tomllib is not None:
            try:
                tomllib.loads(old)
                tomllib.loads(new)
            except tomllib.TOMLDecodeError as e:
                raise SetupError(f"{path} is not TOML graphlore can merge into: {e}") from e
    else:
        try:
            data = json.loads(old) if old.strip() else {}
        except json.JSONDecodeError as e:
            raise SetupError(
                f"{path} is not plain JSON ({e}); add the block from "
                f"`graphlore setup {client.name}` by hand") from e
        if not isinstance(data, dict):
            raise SetupError(f"{path} does not hold a JSON object")
        servers = data.setdefault(client.key, {})
        if not isinstance(servers, dict):
            raise SetupError(f"{path}: {client.key!r} is not an object")
        servers[SERVER_NAME] = entry
        new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if new == old:
        return f"{path}: graphlore is already set up"
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(new, encoding="utf-8")
    kept = " (the previous file is kept as " + path.name + ".graphlore.bak)" if old else ""
    return f"{path}: graphlore {'updated' if old else 'added'}{kept}"


# --- Claude Code hooks (`graphlore setup claude-code --with-hooks`) ------------

HOOK_EVENTS = {"PreToolUse": ("Grep|Glob", "pre"),
               "PostToolUse": ("Bash|Edit|Write|MultiEdit", "post")}
HOOK_TIMEOUT = 5  # seconds; the hook gives up on its own long before


def hook_commands(cfg: dict[str, Any]) -> dict[str, str]:
    """The shell command each hook event runs: ``graphlore hook <event>``,
    started the way the server is (``cfg`` from :func:`server_config`)."""
    base = shlex.join([cfg["command"], *cfg["args"]])
    return {event: f"{base} hook {arg}" for event, (_m, arg) in HOOK_EVENTS.items()}


def _is_ours(handler: Any) -> bool:
    return isinstance(handler, dict) and " hook " in f" {handler.get('command', '')} " \
        and "graphlore" in str(handler.get("command", ""))


def write_hooks(path: Path, cfg: dict[str, Any]) -> str:
    """Merge graphlore's two hooks into a Claude Code settings file (a
    ``.claude/settings.json``): an earlier graphlore entry is replaced, every
    other hook and setting kept, a backup written first."""
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        data = json.loads(old) if old.strip() else {}
    except json.JSONDecodeError as e:
        raise SetupError(f"{path} is not plain JSON ({e}); add the hooks by hand "
                         "(docs/clients.md)") from e
    if not isinstance(data, dict):
        raise SetupError(f"{path} does not hold a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SetupError(f"{path}: 'hooks' is not an object")
    commands = hook_commands(cfg)
    for event, (matcher, _arg) in HOOK_EVENTS.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise SetupError(f"{path}: hooks.{event} is not a list")
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                rest = [h for h in g["hooks"] if not _is_ours(h)]
                if not rest:
                    continue
                g = {**g, "hooks": rest}
            kept.append(g)
        kept.append({"matcher": matcher, "hooks": [
            {"type": "command", "command": commands[event], "timeout": HOOK_TIMEOUT}]})
        hooks[event] = kept
    new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if new == old:
        return f"{path}: graphlore's hooks are already set up"
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(new, encoding="utf-8")
    kept_note = " (the previous file is kept as " + path.name + ".graphlore.bak)" if old else ""
    return f"{path}: graphlore's hooks {'updated' if old else 'added'}{kept_note}"
