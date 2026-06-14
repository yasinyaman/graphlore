"""Shared runtime configuration.

Lives in its own module so the analysis layers (``graph``, ``spans``) and the MCP
surface (``server``) all read the SAME ``PROJECT_DIR`` by attribute access — and so
tests can repoint it with ``monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)``.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("GRAPHIFY_PROJECT_DIR", ".")).resolve()
OUT_DIR_NAME = os.environ.get("GRAPHIFY_OUT_DIR", "graphify-out")
