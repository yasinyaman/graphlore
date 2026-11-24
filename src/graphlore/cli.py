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
            note = clients.write(client, target, server_cfg)
        except clients.SetupError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(note)
    if args.with_hooks:
        if client.name not in clients.HOOK_MATCHERS:
            print("--with-hooks: hooks are known for "
                  + " and ".join(sorted(clients.HOOK_MATCHERS)) + " (docs/clients.md)",
                  file=sys.stderr)
            return 2
        settings = clients.hooks_file(client.name, project, user=args.user)
        try:
            print(clients.write_hooks(settings, server_cfg, client.name))
        except clients.SetupError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
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


def _cmd_review(args: argparse.Namespace) -> int:
    if args.project:
        import os

        os.environ["GRAPHLORE_PROJECT_DIR"] = str(Path(args.project).resolve())
    from . import change_review, config

    if args.project:
        config.PROJECT_DIR = Path(args.project).resolve()
    fail_on = tuple(x.strip() for x in args.fail_on.split(",") if x.strip())
    return change_review.run(args.base, args.head, fmt=args.format, fail_on=fail_on,
                             build=not args.no_build, hops=args.hops, output=args.output,
                             repo=args.repo)


def _cmd_hook(args: argparse.Namespace) -> int:
    from . import hooks

    return hooks.main([args.event])


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="graphlore",
        description="With no arguments: run the graphlore MCP server (stdio). "
                    "The subcommands set an MCP client up for it, or review a change.")
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
    s.add_argument("--with-hooks", action="store_true",
                   help="claude-code / codex: also merge graphlore's hooks into "
                        ".claude/settings.json or .codex/hooks.json (a pointer to the graph "
                        "on a search for a name, a note when the graph goes stale)")
    s.set_defaults(fn=_cmd_setup)
    c = sub.add_parser("clients", help="the clients `setup` knows")
    c.set_defaults(fn=_cmd_clients)
    a = sub.add_parser("agents-md", help="the usage guide for AGENTS.md and the like")
    a.add_argument("--toolset", default="lean", choices=("lean", "full", "locate"))
    a.add_argument("--write", metavar="PATH",
                   help="merge it into this file (between its graphlore markers)")
    a.set_defaults(fn=_cmd_agents_md)
    r = sub.add_parser("review", help="what a commit range changes, read off the code graph",
                       description="A deterministic report of a commit range: the definitions "
                                   "it touches, what depends on them, the tests that reach them, "
                                   "and the import cycles and layer rules it brings in. Exit 0 "
                                   "clean, 1 a --fail-on gate failed, 2 no review possible.")
    r.add_argument("--base", default="HEAD~1",
                   help="the commit the change is compared with, through its merge base")
    r.add_argument("--head", default="HEAD", help="the changed commit; it must be checked out")
    r.add_argument("--project", default="", help="the project directory (default: here)")
    r.add_argument("--repo", default="",
                   help="in a workspace of several repositories, the one whose range it is")
    r.add_argument("--format", default="markdown", choices=("markdown", "json"))
    r.add_argument("--fail-on", default="",
                   help="comma-separated gates that exit 1: cycle (a runtime import cycle "
                        "introduced), layer (a layer rule broken on a changed line), untested "
                        "(a changed definition no test reaches)")
    r.add_argument("--hops", type=int, default=3, help="how far dependents are followed")
    r.add_argument("--no-build", action="store_true",
                   help="refuse instead of building the graph when it is not at the head commit")
    r.add_argument("--output", default="", help="write the report to this file")
    r.set_defaults(fn=_cmd_review)
    h = sub.add_parser("hook", help="a Claude Code hook: the call's JSON on stdin, a note on "
                                    "stdout (`graphlore setup claude-code --with-hooks`)")
    h.add_argument("event", choices=("pre", "post"),
                   help="pre: PreToolUse on Grep/Glob; post: PostToolUse on Bash/Edit/Write")
    h.set_defaults(fn=_cmd_hook)
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
