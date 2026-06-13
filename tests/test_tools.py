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
    assert len(names) == 14
    assert prompts == {"onboard", "trace_bug", "explain_flow"}
