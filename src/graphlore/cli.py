"""The ``graphlore`` command.

With no arguments it runs the MCP server (stdio by default), exactly as before:
every client config, the Claude Code plugin and the MCP Registry entry start it
so. The subcommands are for setting a client up:

- ``graphlore setup <client>`` prints, or with ``--write`` merges, the block a
  client's MCP config needs (``clients.py``);
- ``graphlore clients`` lists the clients it knows;
- ``graphlore agents-md`` prints, or merges into a file, the usage guide for
  agents that read AGENTS.md / GEMINI.md and the like (``agents_md.py``).

The server module is imported only to run the server or to name its tools.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import agents_md, clients


def _tools_of(toolset: str) -> list[str]:
    """The tools the server registers with ``toolset``, by name."""
    from . import server  # registers every tool; only here

    names = server._registered_tool_names()
    keep = server.TOOLSETS.get(toolset)
    return sorted(names if keep is None else names & keep)


def _write_guide(path: Path, block: str) -> str:
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = agents_md.merge(old, block)
    if new == old:
        return f"{path}: already up to date"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return f"{path}: {'updated' if old else 'written'}"


def _cmd_clients(_args: argparse.Namespace) -> int:
    for c in clients.CLIENTS.values():
        where = ", ".join(str(p) for p in (c.project_file, c.user_file) if p) or "(printed only)"
        print(f"{c.name:15} {c.title:28} {where}")
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    client = clients.CLIENTS.get(args.client)
    if client is None:
        print(f"unknown client {args.client!r}; `graphlore clients` lists them",
              file=sys.stderr)
        return 2
    project = Path(args.project).expanduser().resolve()
    server_cfg = clients.server_config(project, args.toolset, args.command,
                                       absolute=client.absolute_command)
    target = clients.target(client, project, user=args.user)
    if not args.write or target is None:
        print(clients.render(client, server_cfg, project, user=args.user))
        if args.write and target is None:
            print(f"\n({client.title} keeps no config file graphlore writes: add the "
                  "block above by hand.)", file=sys.stderr)
            return 1
    else:
        try:
            note = clients.write(client, target, server_cfg, user=args.user)
        except clients.SetupError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(note)
    if args.with_agents_md:
        guide = project / client.guide_file
        print(_write_guide(guide, agents_md.render(_tools_of(args.toolset))))
    return 0


def _cmd_agents_md(args: argparse.Namespace) -> int:
    block = agents_md.render(_tools_of(args.toolset))
    if args.write:
        print(_write_guide(Path(args.write), block))
    else:
        sys.stdout.write(block)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="graphlore",
        description="With no arguments: run the graphlore MCP server (stdio). "
                    "The subcommands set an MCP client up for it.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("setup", help="the MCP config block for a client, printed or merged")
    s.add_argument("client", help="see `graphlore clients`")
    s.add_argument("--project", default=".", help="the repository graphlore reads")
    s.add_argument("--toolset", default="lean", choices=("lean", "full", "locate"))
    s.add_argument("--command", default="graphlore",
                   help="how the client starts graphlore: graphlore (default, the "
                        "command on PATH), uvx (from PyPI) or a path")
    s.add_argument("--user", action="store_true",
                   help="the client's user-level config instead of the project's")
    s.add_argument("--write", action="store_true",
                   help="merge it into the config file (a backup is kept)")
    s.add_argument("--with-agents-md", action="store_true",
                   help="also write the usage guide into the file this client reads")
    s.set_defaults(fn=_cmd_setup)
    c = sub.add_parser("clients", help="the clients `setup` knows")
    c.set_defaults(fn=_cmd_clients)
    a = sub.add_parser("agents-md", help="the usage guide for AGENTS.md and the like")
    a.add_argument("--toolset", default="lean", choices=("lean", "full", "locate"))
    a.add_argument("--write", metavar="PATH",
                   help="merge it into this file (between its graphlore markers)")
    a.set_defaults(fn=_cmd_agents_md)
    return p


def main(argv: list[str] | None = None) -> int | None:
    args_in = sys.argv[1:] if argv is None else argv
    if not args_in:
        from . import server

        server.main()
        return None
    args = _parser().parse_args(args_in)
    if not getattr(args, "fn", None):
        _parser().print_help()
        return 2
    return int(args.fn(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
