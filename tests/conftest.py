"""Shared pytest fixtures."""

import shutil
from pathlib import Path

import pytest

from graphlore import server

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


@pytest.fixture()
def polyglot(tmp_path, monkeypatch):
    """A copy of fixtures/polyglot: five languages, and the graph graphify 0.9
    built of them (``graph.json``, copied to ``graphify-out/``)."""
    src = FIXTURES / "polyglot"
    root = tmp_path / "polyglot"
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("graph.json"))
    (root / "graphify-out").mkdir()
    shutil.copy(src / "graph.json", root / "graphify-out" / "graph.json")
    server._GRAPH_CACHE.clear()
    server._SPAN_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", root)
    return root


@pytest.fixture(autouse=True)
def _full_toolset(monkeypatch):
    """Pin the full tool surface for every test.

    The default GRAPHLORE_TOOLSET is lean, and ``server.main()`` trims the shared
    ``server.mcp`` for real; without this, the first test that calls ``main()``
    would remove 21 tools for every test after it. Tests of the default patch
    ``server.TOOLSET`` themselves.
    """
    monkeypatch.setattr(server, "TOOLSET", "full")


@pytest.fixture(autouse=True)
def _default_engine(monkeypatch):
    """Every test starts on the default engine (graphify), whatever the shell
    running pytest exports; tests of the native engine set it themselves."""
    for name in ("GRAPHLORE_ENGINE", "GRAPHIFY_ENGINE"):
        monkeypatch.delenv(name, raising=False)
