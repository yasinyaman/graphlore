"""Shared pytest fixtures."""

import shutil
from pathlib import Path

import pytest

from graphify_mcp import server

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A temp project dir with a graphify-out/ populated from fixtures.

    Repoints the server's module-level PROJECT_DIR at the temp project so the
    analysis tools and resources read the fixture graph.
    """
    out = tmp_path / "graphify-out"
    out.mkdir()
    shutil.copy(FIXTURES / "graph.json", out / "graph.json")
    shutil.copy(FIXTURES / "GRAPH_REPORT.md", out / "GRAPH_REPORT.md")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def empty_project(tmp_path, monkeypatch):
    """A temp project dir with no graph built yet."""
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    return tmp_path
