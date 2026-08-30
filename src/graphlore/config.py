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


PROJECT_DIR = Path(env("PROJECT_DIR", ".")).resolve()
OUT_DIR_NAME = env("OUT_DIR", "graphify-out")
