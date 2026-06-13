"""Tests for the graph.json analysis tools and resources."""

import json

from graphify_mcp import server


def test_overview(project):
    out = server.graphify_overview()
    assert "5 nodes" in out
    assert "3 communities" in out
    assert "Client" in out


def test_overview_json(project):
    data = json.loads(server.graphify_overview(as_json=True))
    assert data["nodes"] == 5
    assert data["edges"] == 4
    assert data["communities"] == 3
    assert data["surprise_edges"] == 1
    assert data["god_nodes"][0]["node"] in {"Client", "Request", "Response"}


def test_god_nodes(project):
    data = json.loads(server.graphify_god_nodes(as_json=True))
    nodes = {g["node"]: g["degree"] for g in data["god_nodes"]}
    assert nodes["Client"] == 2
    assert nodes["AsyncClient"] == 1


def test_surprises(project):
    data = json.loads(server.graphify_surprises(as_json=True))
    assert data["fallback"] is False
    assert {"from": "DigestAuth", "to": "Response", "relation": "inferred"} in data["surprises"]


def test_communities(project):
    data = json.loads(server.graphify_communities(as_json=True))
    assert len(data["communities"]) == 3
    biggest = data["communities"][0]
    assert biggest["size"] == 2


def test_search(project):
    data = json.loads(server.graphify_search("client", as_json=True))
    labels = {m["node"] for m in data["matches"]}
    assert labels == {"Client", "AsyncClient"}


def test_search_no_match(project):
    assert "No nodes match" in server.graphify_search("zzz")


def test_neighbors(project):
    data = json.loads(server.graphify_neighbors("Client", as_json=True))
    rels = {(n["relation"], n["node"]) for n in data["neighbors"]}
    assert ("calls", "Request") in rels
    assert ("returns", "Response") in rels


def test_neighbors_fuzzy(project):
    # case-insensitive substring match still resolves
    data = json.loads(server.graphify_neighbors("async", as_json=True))
    assert data["node"] == "AsyncClient"


def test_subgraph(project):
    data = json.loads(server.graphify_subgraph("Client", hops=2, as_json=True))
    assert data["center"] == "Client"
    assert data["nodes"] >= 3
    assert data["approx_tokens"] > 0


def test_subgraph_budget_truncates(project):
    # tiny budget forces truncation
    data = json.loads(server.graphify_subgraph("Client", hops=5, budget_tokens=1, as_json=True))
    assert data["truncated"] is True


def test_node_details(project):
    data = json.loads(server.graphify_node_details("Client", as_json=True))
    assert data["file"] == "httpx/_client.py"
    assert data["line"] == 50
    assert data["community"] == 0


def test_node_line_helper():
    assert server._node_line({"line": 7}) == 7
    assert server._node_line({"line": 0}) == 0  # falsy but valid
    assert server._node_line({"source_location": "L295"}) == 295
    assert server._node_line({"source_location": "L295-L312"}) == 295  # range -> start
    assert server._node_line({}) == ""


def test_node_details_real_graphify_schema(tmp_path, monkeypatch):
    """graphify's real output uses source_file + source_location='L295', not line."""
    out = tmp_path / "graphify-out"
    out.mkdir()
    graph = {
        "directed": True,
        "nodes": [{
            "id": "graphify_overview",
            "label": "graphify_overview()",
            "source_file": "src/graphify_mcp/server.py",
            "source_location": "L295",
            "community": 12,
        }],
        "links": [],
    }
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_node_details("graphify_overview", as_json=True))
    assert data["file"] == "src/graphify_mcp/server.py"
    assert data["line"] == 295
    # source_location is consumed as the line, not echoed back in extra
    assert "source_location" not in data.get("extra", {})


def test_missing_graph_errors(empty_project):
    assert "not found" in server.graphify_overview()
    assert "not found" in server.graphify_god_nodes()


def test_report_resource(project):
    assert "fixture" in server.report()


def test_graph_resource(project):
    raw = json.loads(server.graph_json())
    assert len(raw["nodes"]) == 5


def test_community_resource(project):
    md = server.community("0")
    assert "Community 0" in md
    assert "Client" in md
    assert "AsyncClient" in md


def test_community_resource_unknown(project):
    assert "No community" in server.community("999")


def test_add_rejects_non_http(project):
    assert "only http/https" in server.graphify_add("ftp://x")


def test_tool_and_prompt_registration(project):
    import asyncio

    async def _collect():
        tools = await server.mcp.list_tools()
        prompts = await server.mcp.list_prompts()
        return {t.name for t in tools}, {p.name for p in prompts}

    names, prompts = asyncio.run(_collect())
    assert "graphify_overview" in names
    assert "graphify_subgraph" in names
    assert "graphify_sampling_status" in names
    assert "graphify_label_communities" in names
    assert "graphify_validate" in names
    assert len(names) == 17
    assert prompts == {"onboard", "trace_bug", "explain_flow"}


def test_version_reported_over_mcp():
    import graphify_mcp

    assert server.__version__ == graphify_mcp.__version__
    # FastMCP otherwise reports the mcp library version; we override it.
    assert server.mcp._mcp_server.version == server.__version__


def test_main_module_wired():
    import importlib

    mod = importlib.import_module("graphify_mcp.__main__")  # must not run main()
    assert mod.main is server.main


def _write_graph(tmp_path, graph):
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def test_overview_and_surprises_share_one_definition(tmp_path, monkeypatch):
    """overview now counts is_surprise like surprises, and neither counts a mere
    INFERRED-confidence edge as a surprise (no false inflation)."""
    _write_graph(tmp_path, {
        "nodes": [
            {"id": "A", "label": "A", "community": 0},
            {"id": "B", "label": "B", "community": 1},
            {"id": "C", "label": "C", "community": 0},
        ],
        "edges": [
            {"source": "A", "target": "B", "is_surprise": True, "relation": "x"},
            {"source": "A", "target": "C", "relation": "y"},
            {"source": "B", "target": "C", "type": "inferred", "relation": "z"},  # not a surprise
        ],
    })
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    ov = json.loads(server.graphify_overview(as_json=True))
    su = json.loads(server.graphify_surprises(as_json=True))
    assert ov["surprise_edges"] == 1  # only the is_surprise edge; inferred is NOT counted
    assert su["fallback"] is False
    assert {"from": "A", "to": "B", "relation": "x"} in su["surprises"]


def test_load_graph_caches_by_mtime(tmp_path, monkeypatch):
    _write_graph(tmp_path, {"nodes": [{"id": "A", "label": "A"}], "links": []})
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    a = server._load_graph()
    b = server._load_graph()
    assert a is b  # same object returned from cache while mtime is unchanged


def test_freshness_flags_untracked_file(tmp_path, monkeypatch):
    import shutil as _sh
    import subprocess

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    _write_graph(tmp_path, {"nodes": [], "links": []})

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "init")
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)

    # A brand-new untracked .py is a real rebuild trigger that `git diff HEAD` misses.
    (tmp_path / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is True
    assert any("new_module.py" in f for f in data["uncommitted_or_untracked_files"])
    # additions without deletions -> incremental update is the right action
    assert data["recommended_action"] == "update"


def test_freshness_recommends_rebuild_on_deletion(tmp_path, monkeypatch):
    import shutil as _sh
    import subprocess

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    _write_graph(tmp_path, {"nodes": [], "links": []})
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "init")
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)

    # Deleting a tracked source file: incremental update would keep phantom nodes,
    # so freshness should steer to a full rebuild.
    (tmp_path / "mod.py").unlink()
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is True
    assert data["recommended_action"] == "rebuild"
    assert "mod.py" in data["deleted_or_renamed"]


# --- opt-in path containment -------------------------------------------------

def test_path_containment_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    # off by default -> documented absolute/sibling path still allowed
    monkeypatch.setattr(server, "RESTRICT_PATHS", False)
    assert server._path_escapes_project("../../etc") is None
    # on -> contained ok, escaping rejected
    monkeypatch.setattr(server, "RESTRICT_PATHS", True)
    assert server._path_escapes_project("sub/dir") is None
    err = server._path_escapes_project("../../etc")
    assert err and "escapes the project" in err


def test_build_rejects_escaping_path_when_restricted(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(server, "RESTRICT_PATHS", True)
    # guard returns before the CLI is ever invoked
    assert "escapes the project" in server.graphify_build("/etc")


# --- node-id collision diagnostic --------------------------------------------

def test_overview_flags_id_collisions(tmp_path, monkeypatch):
    _write_graph(tmp_path, {
        "nodes": [
            {"label": "X"},          # no id -> _node_id falls back to label "X"
            {"label": "X"},          # collides with the first
            {"id": "Y", "label": "Y"},
        ],
        "edges": [],
    })
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_overview(as_json=True))
    assert data["id_collisions"] == 1
    assert "collision" in server.graphify_overview().lower()


# --- transport selection -----------------------------------------------------

def test_main_dispatches_stdio_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "TRANSPORT", "stdio")
    monkeypatch.setattr(server.mcp, "run", lambda **kw: seen.update(kw))
    server.main()
    assert seen.get("transport") == "stdio"


def test_main_http_transport_forces_containment(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "TRANSPORT", "streamable-http")
    monkeypatch.setattr(server, "RESTRICT_PATHS", False)
    monkeypatch.setattr(server.mcp, "run", lambda **kw: seen.update(kw))
    server.main()
    assert seen.get("transport") == "streamable-http"
    assert server.RESTRICT_PATHS is True  # HTTP auto-enables path containment


# --- graphify_validate (read-only graph linter) ------------------------------

def test_validate_healthy_fixture(project):
    data = json.loads(server.graphify_validate(as_json=True))
    assert data["healthy"] is True
    assert data["total_issues"] == 0


def test_validate_detects_structural_issues(tmp_path, monkeypatch):
    _write_graph(tmp_path, {
        "nodes": [
            {"id": "A", "label": "A"},
            {"id": "B", "label": "B"},
            {"id": "C", "label": "C"},   # no edges -> orphan
        ],
        "edges": [
            {"source": "A", "target": "B", "type": "calls"},
            {"source": "A", "target": "B", "type": "calls"},   # duplicate
            {"source": "A", "target": "Z", "type": "calls"},   # dangling (Z not a node)
            {"source": "B", "target": "B", "type": "loops"},   # self-loop
        ],
    })
    monkeypatch.setattr(server, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_validate(as_json=True))
    assert data["healthy"] is False
    assert data["issues"]["duplicate_edges"] == 1
    assert data["issues"]["dangling_edges"] == 1
    assert data["issues"]["self_loops"] == 1
    assert data["issues"]["orphan_nodes"] == 1
    assert data["examples"]["dangling"][0]["missing"] == ["Z"]


def test_detect_backend(monkeypatch):
    for env in server._BACKEND_ENV:
        monkeypatch.delenv(env, raising=False)
    assert server._detect_backend() is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert server._detect_backend() == "openai"


# --- host-LLM sampling: capability test + naming round-trip (in-memory) -------

def _run_in_memory(project, tool, args, sampling_callback=None):
    """Drive a tool over a real in-memory MCP session (optionally with sampling)."""
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session as connect

    async def _go():
        async with connect(server.mcp, sampling_callback=sampling_callback) as client:
            await client.initialize()
            res = await client.call_tool(tool, args)
            return res.content[0].text

    return asyncio.run(_go())


async def _first_member_host_llm(context, params):
    """Stand-in for the host model: name a community after its first member."""
    from mcp.types import CreateMessageResult, TextContent

    text = params.messages[0].content.text
    first = text.split("Members:", 1)[-1].split(",")[0].strip()
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=first),
        model="stub-host-model",
    )


def test_sampling_status_supported(project):
    out = _run_in_memory(
        project, "graphify_sampling_status", {"as_json": True},
        sampling_callback=_first_member_host_llm,
    )
    data = json.loads(out)
    assert data["host_sampling_supported"] is True
    assert data["preferred_method"] == "sampling"


def test_sampling_status_unsupported(project):
    data = json.loads(_run_in_memory(project, "graphify_sampling_status", {"as_json": True}))
    assert data["host_sampling_supported"] is False  # no sampling_callback -> not advertised


def test_label_communities_via_sampling(project):
    out = _run_in_memory(
        project, "graphify_label_communities", {"method": "auto", "as_json": True},
        sampling_callback=_first_member_host_llm,
    )
    data = json.loads(out)
    assert data["method"] == "sampling"
    assert data["labeled"] >= 1
    # the stub names each community after its first member -> proves the round-trip
    assert all(c["name"] == c["members"][0] for c in data["communities"])


def test_label_communities_sampling_unsupported_errors(project):
    out = _run_in_memory(project, "graphify_label_communities", {"method": "sampling"})
    assert "does not support" in out


def test_label_communities_placeholder(project):
    out = _run_in_memory(
        project, "graphify_label_communities", {"method": "placeholder", "as_json": True}
    )
    data = json.loads(out)
    assert data["method"] == "placeholder"
    assert all(c["name"] == f"Community {c['id']}" for c in data["communities"])
