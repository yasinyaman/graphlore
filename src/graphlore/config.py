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
    ``GRAPHLORE_*``. (The output directory and the saved community names were
    ``graphify-out/`` and ``.graphify_labels.json`` before 0.9.0; a graph
    written under those names is still read, see :func:`out_dir`.)
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
OUT_DIR_NAME = env("OUT_DIR", "graphlore-out")
# Set by GRAPHLORE_OUT_DIR (or GRAPHIFY_OUT_DIR): that name is used as given,
# never the legacy directory in its place and never a move.
OUT_DIR_EXPLICIT = env("OUT_DIR") is not None
# What graphify and graphlore before 0.9.0 wrote. Read while no graph has been
# built under the new names; a build moves it (`migrate_out_dir`).
LEGACY_OUT_DIR_NAME = "graphify-out"
LABELS_FILE = ".graphlore_labels.json"
LEGACY_LABELS_FILE = ".graphify_labels.json"


def out_dir(project: Path | None = None) -> Path:
    """The output directory to read in ``project``: ``OUT_DIR_NAME``, or the
    legacy ``graphify-out/`` while it holds a graph.json and the new one does
    not (and the name was not set explicitly)."""
    root = Path(project or PROJECT_DIR)
    new = root / OUT_DIR_NAME
    if OUT_DIR_EXPLICIT or (new / "graph.json").exists():
        return new
    old = root / LEGACY_OUT_DIR_NAME
    return old if (old / "graph.json").exists() else new


def out_dir_names() -> tuple[str, ...]:
    """The directory names a walk of the project leaves out as graph output."""
    if OUT_DIR_NAME == LEGACY_OUT_DIR_NAME:
        return (OUT_DIR_NAME,)
    return (OUT_DIR_NAME, LEGACY_OUT_DIR_NAME)


def labels_path(directory: Path) -> Path:
    """The saved community names in an output directory: ``.graphlore_labels.json``,
    else graphify's ``.graphify_labels.json`` when only that one is there."""
    new = directory / LABELS_FILE
    old = directory / LEGACY_LABELS_FILE
    return old if not new.exists() and old.exists() else new


def migrate_out_dir(project: Path) -> str:
    """Before a build: move a legacy ``graphify-out/`` to ``OUT_DIR_NAME`` so the
    build keeps its cache, its communities and their names. A directory git
    tracks is left where it is (a move would change the work tree): the saved
    names are copied and the build starts afresh. Returns a note, or ""."""
    import shutil
    import subprocess

    if OUT_DIR_EXPLICIT or OUT_DIR_NAME == LEGACY_OUT_DIR_NAME:
        return ""
    new = project / OUT_DIR_NAME
    old = project / LEGACY_OUT_DIR_NAME
    if new.exists() or not old.is_dir() or old.is_symlink():
        return ""
    tracked = False
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), "ls-files", "--", LEGACY_OUT_DIR_NAME],
            capture_output=True, text=True, timeout=1,
        )
        tracked = proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        if not tracked:
            os.replace(old, new)
            labels = new / LEGACY_LABELS_FILE
            if labels.exists() and not (new / LABELS_FILE).exists():
                os.replace(labels, new / LABELS_FILE)
            return (f"moved {LEGACY_OUT_DIR_NAME}/ to {OUT_DIR_NAME}/ (graphlore's output "
                    f"directory since 0.9.0); a .gitignore line for {LEGACY_OUT_DIR_NAME}/ "
                    f"wants {OUT_DIR_NAME}/ now")
        labels = old / LEGACY_LABELS_FILE
        if labels.exists():
            new.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(labels, new / LABELS_FILE)
        return (f"{LEGACY_OUT_DIR_NAME}/ is tracked by git, so it was left in place: this "
                f"build writes {OUT_DIR_NAME}/ afresh"
                + (" (the saved community names copied)" if labels.exists() else "")
                + f"; remove {LEGACY_OUT_DIR_NAME}/ once nothing reads it")
    except OSError as e:
        return f"{LEGACY_OUT_DIR_NAME}/ could not be moved to {OUT_DIR_NAME}/ ({e})"


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
                or p.name in out_dir_names() or not p.is_dir() or p.is_symlink())

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
