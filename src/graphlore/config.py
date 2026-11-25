"""Shared runtime configuration.

Lives in its own module so the analysis layers (``graph``, ``spans``) and the MCP
surface (``server``) all read the SAME ``PROJECT_DIR`` by attribute access — and so
tests can repoint it with ``monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import overload


@overload
def env(name: str) -> str | None: ...
@overload
def env(name: str, default: str) -> str: ...


def env(name: str, default: str | None = None) -> str | None:
    """Read a server setting: ``GRAPHLORE_<name>`` first, ``GRAPHIFY_<name>`` as
    the legacy fallback.

    The ``GRAPHIFY_*`` spellings predate the graphlore rename and are still
    honored so existing configs keep working; new configs should use
    ``GRAPHLORE_*``. (``graphify-out/`` and ``.graphify_labels.json``, named
    after the Graphify CLI that built the graph before 0.9.0, keep their names
    regardless.)
    """
    value = os.environ.get(f"GRAPHLORE_{name}")
    if value is None:
        value = os.environ.get(f"GRAPHIFY_{name}")
    return default if value is None else value


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def offline() -> bool:
    """``GRAPHLORE_OFFLINE=1``: refuse every path graphlore knows reaches the network
    (read per call, so tests and a restarted watcher see the current value)."""
    return _truthy(env("OFFLINE", ""))


# The Hugging Face libraries behind the embedding models (semble's, and the
# built-in search's dense tier) read these before any download.
HF_OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def export_offline_env() -> None:
    """Offline: tell the Hugging Face libraries not to download, for this process
    and every child. Unset values only, so an explicit setting wins. Takes effect
    only for libraries not yet imported."""
    if offline():
        for k, v in HF_OFFLINE_ENV.items():
            os.environ.setdefault(k, v)


PROJECT_DIR = Path(env("PROJECT_DIR", ".")).resolve()
# A plugin client (Agent Plugins 1.0: Codex, Copilot CLI) starts the server in the
# plugin's own directory and names no project. With GRAPHLORE_PROJECT_FROM_CLIENT=1
# and no GRAPHLORE_PROJECT_DIR, the project is the client's first root (MCP
# `roots/list`, asked on the first tool call) or the directory `graphlore_project`
# is given; until then `project_pending` is True and the tools say so.
PROJECT_FROM_CLIENT = _truthy(env("PROJECT_FROM_CLIENT")) and env("PROJECT_DIR") is None
project_pending = PROJECT_FROM_CLIENT
OUT_DIR_NAME = env("OUT_DIR", "graphify-out")


def cache_root() -> Path | None:
    """graphlore's cache directory on this machine, never in a project:
    ``GRAPHLORE_CACHE_DIR``, else ``$XDG_CACHE_HOME/graphlore`` or
    ``~/.cache/graphlore``; None when ``GRAPHLORE_CACHE_DIR`` is off."""
    raw = (env("CACHE_DIR", "") or "").strip()
    if raw.lower() in ("off", "none", "0", "false", "no"):
        return None
    if raw:
        return Path(raw).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "graphlore"


WORKSPACE_FILE = ".graphlore-workspace.json"
_WORKSPACE_SKIP = ("node_modules", "__pycache__")


def workspace_repos(project_dir: Path | None = None) -> list[str]:
    """The repositories of a workspace, project-relative (``svc-a``,
    ``libs/common``), the way the native engine finds them: the ``repos`` a
    ``.graphlore-workspace.json`` names (an object's keys, or a list), else,
    when the project directory is no git repository itself, every directory one
    or two levels down holding a ``.git``. Empty for a project that is one
    repository, with submodules or not."""
    root = Path(project_dir or PROJECT_DIR)
    try:
        import json

        data = json.loads((root / WORKSPACE_FILE).read_text(encoding="utf-8"))
        raw = data.get("repos") if isinstance(data, dict) else None
        names = list(raw) if isinstance(raw, (dict, list)) else []
        named = sorted({
            n.strip("/") for n in names
            if isinstance(n, str) and n.strip("/")
            and not any(p in ("", ".", "..") for p in n.strip("/").split("/"))
            and (root / n.strip("/")).is_dir()
        })
        if named:
            return named
    except (OSError, ValueError):
        pass
    if (root / ".git").exists():
        return []

    def skip(p: Path) -> bool:
        return (p.name.startswith(".") or p.name in _WORKSPACE_SKIP
                or p.name == OUT_DIR_NAME or not p.is_dir() or p.is_symlink())

    found: list[str] = []
    try:
        level1 = sorted(p for p in root.iterdir() if not skip(p))
    except OSError:
        return found
    for d in level1:
        if (d / ".git").exists():
            found.append(d.name)
            continue
        if (d / "pyvenv.cfg").exists():
            continue
        try:
            inner = sorted(p for p in d.iterdir() if not skip(p) and (p / ".git").exists())
        except OSError:
            continue
        found += [f"{d.name}/{p.name}" for p in inner]
    return found
