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
    ``GRAPHLORE_*``. (Artifacts of the wrapped Graphify CLI — the ``graphify``
    binary, ``graphify-out/``, ``.graphify_labels.json`` — keep their own names
    regardless.)
    """
    value = os.environ.get(f"GRAPHLORE_{name}")
    if value is None:
        value = os.environ.get(f"GRAPHIFY_{name}")
    return default if value is None else value


def offline() -> bool:
    """``GRAPHLORE_OFFLINE=1``: refuse every path graphlore knows reaches the network
    (read per call, so tests and a restarted watcher see the current value)."""
    return (env("OFFLINE", "") or "").strip().lower() in ("1", "true", "yes", "on")


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
OUT_DIR_NAME = env("OUT_DIR", "graphify-out")
