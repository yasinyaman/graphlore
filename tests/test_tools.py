"""Tests for the graph.json analysis tools and resources."""

import json

from graphify_mcp import server, spans


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


def test_approx_tokens_uses_conservative_divisor():
    # 3.5 chars/token (denser, code-aware): 35 chars -> 10 tokens.
    # The old 4.0 rule of thumb would under-report this as 8.
    assert server._approx_tokens("x" * 35) == 10


def test_subgraph_approx_tokens_not_underreported(project):
    """Reported approx_tokens must not undercount the serialized payload: it should
    clear the naive len(serialized)//4 lower bound (3.5 divisor + JSON envelope)."""
    data = json.loads(server.graphify_subgraph("Client", hops=2, as_json=True))
    serialized = json.dumps(data["edges"], ensure_ascii=False)
    assert data["approx_tokens"] >= len(serialized) // 4


def test_count_tokens_heuristic_by_default(monkeypatch):
    monkeypatch.delenv("GRAPHIFY_TOKENIZER", raising=False)
    s = "def foo(x): return bar(x) + baz(x)"
    assert server._count_tokens(s) == server._approx_tokens(s)


def test_count_tokens_tiktoken_exact(monkeypatch):
    import importlib.util

    import pytest
    if importlib.util.find_spec("tiktoken") is None:
        pytest.skip("tiktoken extra not installed")
    import tiktoken

    import graphify_mcp.graph as g
    g._TIKTOKEN_ENC = None  # reset the lazy probe so the env switch is honored
    monkeypatch.setenv("GRAPHIFY_TOKENIZER", "tiktoken")
    s = "def handle_request(self, request, *, follow_redirects=True): return self._send(request)"
    enc = tiktoken.get_encoding("cl100k_base")
    assert server._count_tokens(s) == len(enc.encode(s))     # exact, not the heuristic
    assert server._count_tokens(s) != server._approx_tokens(s)


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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_node_details("graphify_overview", as_json=True))
    assert data["file"] == "src/graphify_mcp/server.py"
    assert data["line"] == 295
    # source_location is consumed as the line, not echoed back in extra
    assert "source_location" not in data.get("extra", {})


def test_missing_graph_errors(empty_project):
    assert "not found" in server.graphify_overview()
    assert "not found" in server.graphify_god_nodes()


def test_corrupt_graph_json_errors_gracefully(tmp_path, monkeypatch):
    # malformed graph.json must surface a parse error, not raise, across tools.
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text("{ not valid json", encoding="utf-8")
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    assert "failed to parse" in server.graphify_overview()
    assert "failed to parse" in server.graphify_subgraph("X", as_json=True)


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
    assert "graphify_locate" in names
    assert "graphify_set_labels" in names
    assert len(names) == 19
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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    ov = json.loads(server.graphify_overview(as_json=True))
    su = json.loads(server.graphify_surprises(as_json=True))
    assert ov["surprise_edges"] == 1  # only the is_surprise edge; inferred is NOT counted
    assert su["fallback"] is False
    assert {"from": "A", "to": "B", "relation": "x"} in su["surprises"]


def test_load_graph_caches_by_mtime(tmp_path, monkeypatch):
    _write_graph(tmp_path, {"nodes": [{"id": "A", "label": "A"}], "links": []})
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)

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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)

    # Deleting a tracked source file: incremental update would keep phantom nodes,
    # so freshness should steer to a full rebuild.
    (tmp_path / "mod.py").unlink()
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is True
    assert data["recommended_action"] == "rebuild"
    assert "mod.py" in data["deleted_or_renamed"]


def test_freshness_unquotes_spaced_path_for_cosmetic_classification(tmp_path, monkeypatch):
    """A tracked file whose name has a space must be parsed without git's C-quotes
    so the cosmetic-vs-structural AST diff (`git show HEAD:path`) can resolve it.

    Regression: the old `git status --porcelain` parser left the literal quotes in
    the path (`"my module.py"`), so `_ast_equivalent` always failed and a merely
    cosmetic edit looked like a structural change.
    """
    import shutil as _sh
    import subprocess

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    spaced = tmp_path / "my module.py"
    spaced.write_text("x = 1\n", encoding="utf-8")
    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "init")
    head = git("rev-parse", "HEAD").stdout.strip()
    # built_at_commit == HEAD so the graph isn't "behind"; the only freshness
    # signal is the pending cosmetic edit below.
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": head})
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)

    # Comment-only edit -> AST-identical to HEAD -> cosmetic, not structural.
    spaced.write_text("# just a comment\nx = 1\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))

    assert "my module.py" in data["cosmetic_changes"]
    assert data["structural_changes"] == []
    assert data["stale"] is False
    assert data["recommended_action"] == "fresh"
    # the un-mangled name surfaces with no leftover C-quotes
    assert all('"' not in f for f in data["uncommitted_or_untracked_files"])


def test_freshness_parses_spaced_rename(tmp_path, monkeypatch):
    """`-z` emits a rename as two NUL fields (new path, then old path) with no
    `old -> new` arrow and no quoting; the renamed spaced file must land in
    deleted_or_renamed under its real old name and steer to a rebuild."""
    import shutil as _sh
    import subprocess

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    _write_graph(tmp_path, {"nodes": [], "links": []})
    (tmp_path / "old name.py").write_text("x = 1\n", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "init")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)

    git("mv", "old name.py", "new name.py")
    data = json.loads(server.graphify_freshness(as_json=True))

    assert data["recommended_action"] == "rebuild"
    assert "old name.py" in data["deleted_or_renamed"]
    assert "new name.py" in data["uncommitted_or_untracked_files"]
    surfaced = data["deleted_or_renamed"] + data["uncommitted_or_untracked_files"]
    assert all('"' not in f for f in surfaced)


# --- opt-in path containment -------------------------------------------------

def test_path_containment_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    # off by default -> documented absolute/sibling path still allowed
    monkeypatch.setattr(server, "RESTRICT_PATHS", False)
    assert server._path_escapes_project("../../etc") is None
    # on -> contained ok, escaping rejected
    monkeypatch.setattr(server, "RESTRICT_PATHS", True)
    assert server._path_escapes_project("sub/dir") is None
    err = server._path_escapes_project("../../etc")
    assert err and "escapes the project" in err


def test_build_rejects_escaping_path_when_restricted(tmp_path, monkeypatch):
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
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
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_validate(as_json=True))
    assert data["healthy"] is False
    assert data["issues"]["duplicate_edges"] == 1
    assert data["issues"]["dangling_edges"] == 1
    assert data["issues"]["self_loops"] == 1
    assert data["issues"]["orphan_nodes"] == 1
    assert data["examples"]["dangling"][0]["missing"] == ["Z"]


# --- semantic bridge: _node_for_location, _bfs_subgraph, graphify_locate ------

def test_node_for_location():
    nodes = [
        {"id": "f", "label": "f", "source_file": "m.py", "source_location": "L10"},
        {"id": "g", "label": "g", "source_file": "m.py", "source_location": "L20"},
        {"id": "h", "label": "h", "source_file": "other.py", "source_location": "L5"},
    ]
    assert server._node_for_location(nodes, "m.py", 15)["id"] == "f"   # enclosing 10<=15<20
    assert server._node_for_location(nodes, "m.py", 25)["id"] == "g"   # enclosing 20<=25
    assert server._node_for_location(nodes, "m.py", 3)["id"] == "f"    # closest (none <= 3)
    assert server._node_for_location(nodes, "./m.py", 15)["id"] == "f"  # "./" normalized
    assert server._node_for_location(nodes, "missing.py", 1) is None


def test_node_for_location_prefers_code_over_docstring():
    # a docstring (rationale) node sits nearer the chunk start than the function;
    # the join must still resolve to the enclosing code symbol, not the docstring.
    nodes = [
        {"id": "fn", "label": "is_error", "source_file": "m.py",
         "source_location": "L100", "file_type": "code"},
        {"id": "doc", "label": "A property which is True for 4xx...",
         "source_file": "m.py", "source_location": "L101", "file_type": "rationale"},
    ]
    assert server._node_for_location(nodes, "m.py", 101)["id"] == "fn"          # start on docstring
    assert server._node_for_location(nodes, "m.py", 101, 110)["id"] == "fn"     # chunk range form

    # a def that begins inside the chunk (no code def before it) wins over a docstring above
    nodes2 = [
        {"id": "later", "label": "target", "source_file": "m.py",
         "source_location": "L100", "file_type": "code"},
        {"id": "txt", "label": "module note", "source_file": "m.py",
         "source_location": "L98", "file_type": "rationale"},
    ]
    assert server._node_for_location(nodes2, "m.py", 98, 140)["id"] == "later"


# --- Phase 2: canonical AST span/FQN join -------------------------------------

# A precisely-numbered source module exercised by the span tests below.
#  1 import os            7     @property          13         return self.x == 3
#  2 (blank)              8     def is_error():    14 (blank)
#  3 (blank)              9         return >4      15 (blank)
#  4 class Cattr:        10 (blank)                16 def a():
#  5     x = 1           11     @property          17     return 1
#  6 (blank)             12     def is_redirect(): 18 (blank) / 19 X = 5 / 20 (blank)
#                                                  21 def b(): 22 inner() 23 ret 24 ret inner()
_SPAN_SRC = (
    "import os\n"                       # 1
    "\n\n"                              # 2,3
    "class Cattr:\n"                    # 4
    "    x = 1\n"                       # 5
    "\n"                               # 6
    "    @property\n"                   # 7
    "    def is_error(self):\n"         # 8
    "        return self.x > 4\n"       # 9
    "\n"                               # 10
    "    @property\n"                   # 11
    "    def is_redirect(self):\n"      # 12
    "        return self.x == 3\n"      # 13
    "\n\n"                             # 14,15
    "def a():\n"                        # 16
    "    return 1\n"                    # 17
    "\n"                               # 18
    "X = 5\n"                           # 19
    "\n"                               # 20
    "def b():\n"                        # 21
    "    def inner():\n"                # 22
    "        return 1\n"                # 23
    "    return inner()\n"              # 24
)

_SPAN_NODES = [
    {"id": "Cattr", "label": "Cattr", "source_file": "m.py",
     "source_location": "L4", "file_type": "code"},
    {"id": "is_error", "label": "is_error", "source_file": "m.py",
     "source_location": "L8", "file_type": "code"},
    {"id": "is_redirect", "label": "is_redirect", "source_file": "m.py",
     "source_location": "L12", "file_type": "code"},
    {"id": "a", "label": "a", "source_file": "m.py",
     "source_location": "L16", "file_type": "code"},
    {"id": "b", "label": "b", "source_file": "m.py",
     "source_location": "L21", "file_type": "code"},
    # note: no node for the nested closure b.inner (line 22)
]


def _span_project(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text(_SPAN_SRC, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()


def test_spans_for_file_is_decorator_aware(tmp_path, monkeypatch):
    _span_project(tmp_path, monkeypatch)
    spans = {q: (rs, end, dl) for rs, end, dl, q in server._spans_for_file("m.py")}
    # region_start includes the @property decorator (line 7), def_line is the def (8)
    assert spans["Cattr.is_error"] == (7, 9, 8)
    assert spans["Cattr.is_redirect"][0] == 11      # decorator line, not the def at 12
    assert spans["b.inner"][2] == 22                 # nested def captured with qualname
    assert spans["Cattr"][0] == 4                    # class region


def test_node_for_location_span_beats_stale_point(tmp_path, monkeypatch):
    # chunk STARTS on is_redirect's @property decorator (line 11). The old point
    # heuristic (greatest line <= 11) would wrongly pick is_error@8; span
    # containment knows line 11 is inside is_redirect's region.
    _span_project(tmp_path, monkeypatch)
    assert server._node_for_location(_SPAN_NODES, "m.py", 11, 13)["id"] == "is_redirect"


def test_node_for_location_skips_function_that_already_ended(tmp_path, monkeypatch):
    # line 19 (X = 5) is module-level; a() ended at 17. The point heuristic would
    # attribute it to a@16; span containment knows a() ended, so the chunk maps to
    # the first symbol it actually introduces (b@21), never the closed-out a().
    _span_project(tmp_path, monkeypatch)
    assert server._node_for_location(_SPAN_NODES, "m.py", 19, 24)["id"] == "b"


_OUTWARD_SRC = (
    "class Box:\n"               # 1
    "    def earlier(self):\n"   # 2
    "        return 1\n"         # 3
    "    def outer(self):\n"     # 4
    "        def closure():\n"   # 5
    "            return 2\n"     # 6
    "        return closure()\n"  # 7
)


def test_node_for_location_walks_outward_past_ended_sibling(tmp_path, monkeypatch):
    # chunk in a node-less closure (line 6). Only the class and an already-ended
    # sibling method have nodes. Span resolution walks outward to the enclosing
    # class Box; the point heuristic alone would wrongly pick the closed-out
    # sibling `earlier` (greatest line <= 6). This makes the outward walk
    # load-bearing — the two answers diverge.
    (tmp_path / "w.py").write_text(_OUTWARD_SRC, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    nodes = [
        {"id": "Box", "label": "Box", "source_file": "w.py",
         "source_location": "L1", "file_type": "code"},
        {"id": "earlier", "label": "earlier", "source_file": "w.py",
         "source_location": "L2", "file_type": "code"},
    ]
    assert server._node_for_location(nodes, "w.py", 6)["id"] == "Box"  # span: outward to class
    # contrast: with no source on disk the point heuristic alone picks the ended sibling
    absent = [
        {"id": "earlier", "label": "earlier", "source_file": "absent.py",
         "source_location": "L2", "file_type": "code"},
        {"id": "later", "label": "later", "source_file": "absent.py",
         "source_location": "L10", "file_type": "code"},
    ]
    assert server._node_for_location(absent, "absent.py", 6)["id"] == "earlier"


def test_span_qualname_returns_fqn(tmp_path, monkeypatch):
    _span_project(tmp_path, monkeypatch)
    assert server._span_qualname("m.py", 9) == "Cattr.is_error"
    assert server._span_qualname("m.py", 23) == "b.inner"
    assert server._span_qualname("m.py", 1) is None          # module top, no symbol


def test_spans_for_file_non_python_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "notes.txt").write_text("class NotCode:\n", encoding="utf-8")
    assert server._spans_for_file("notes.txt") == []          # non-Python ignored
    assert server._spans_for_file("missing.py") == []          # absent file


def test_spans_for_file_confined_to_project(tmp_path, monkeypatch):
    # a chunk path escaping PROJECT_DIR must not get parsed (defense in depth)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(server.config, "PROJECT_DIR", proj)
    server._SPAN_CACHE.clear()
    outside = tmp_path / "outside.py"
    outside.write_text("def secret():\n    return 1\n", encoding="utf-8")
    assert server._spans_for_file(str(outside)) == []     # absolute escape
    assert server._spans_for_file("../outside.py") == []   # .. escape


def test_spans_for_file_unparseable_is_cached_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")  # syntax error
    assert server._spans_for_file("broken.py") == []
    assert str(tmp_path / "broken.py") in server._SPAN_CACHE  # not re-parsed next call


def test_node_for_location_falls_back_without_source(tmp_path, monkeypatch):
    # nodes reference a file with no source on disk -> span path is inert and the
    # point heuristic still resolves (guards the non-Python / source-less path).
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    nodes = [
        {"id": "f", "label": "f", "source_file": "gone.py",
         "source_location": "L10", "file_type": "code"},
        {"id": "g", "label": "g", "source_file": "gone.py",
         "source_location": "L20", "file_type": "code"},
    ]
    assert server._node_for_location(nodes, "gone.py", 25)["id"] == "g"


def test_locate_enriches_seed_with_qualname(tmp_path, monkeypatch):
    _span_project(tmp_path, monkeypatch)
    _write_graph(tmp_path, {
        "nodes": _SPAN_NODES,
        "edges": [{"source": "is_error", "target": "Cattr", "type": "method_of"}],
    })
    server._GRAPH_CACHE.clear()
    fake = _FakeIndex(search_hits=[_FakeHit("m.py", 9)], related_hits=[])
    monkeypatch.setattr(server, "_semble_index", lambda: fake)
    data = json.loads(server.graphify_locate("status error", as_json=True))
    assert data["seed"]["node"] == "is_error"
    assert data["seed"]["qualname"] == "Cattr.is_error"   # span-recovered FQN


def test_node_for_location_resolves_body_pointing_node(tmp_path, monkeypatch):
    # an LLM-origin node whose source_location points into the body (line 9 > def 8)
    # must still bind to its own symbol, not walk outward to the enclosing class.
    # A node "owns" the span most tightly enclosing its own line.
    _span_project(tmp_path, monkeypatch)
    nodes = [
        {"id": "Cattr", "label": "Cattr", "source_file": "m.py",
         "source_location": "L4", "file_type": "code"},
        {"id": "is_error", "label": "is_error", "source_file": "m.py",
         "source_location": "L9", "file_type": "code"},   # body line, not the def line
    ]
    assert server._node_for_location(nodes, "m.py", 9)["id"] == "is_error"


def test_locate_seed_qualname_names_resolved_node_not_inner_closure(tmp_path, monkeypatch):
    # hit lands inside the node-less closure b.inner (line 23); the seed resolves
    # outward to b, so the qualname must name b (here suppressed, == label) and never
    # the inner-closure FQN 'b.inner'.
    _span_project(tmp_path, monkeypatch)
    _write_graph(tmp_path, {"nodes": _SPAN_NODES, "edges": []})
    server._GRAPH_CACHE.clear()
    fake = _FakeIndex(search_hits=[_FakeHit("m.py", 23)], related_hits=[])
    monkeypatch.setattr(server, "_semble_index", lambda: fake)
    data = json.loads(server.graphify_locate("inner", as_json=True))
    assert data["seed"]["node"] == "b"
    assert "qualname" not in data["seed"]           # NOT 'b.inner'


def test_locate_seed_qualname_suppressed_and_module_top_safe(tmp_path, monkeypatch):
    _span_project(tmp_path, monkeypatch)
    _write_graph(tmp_path, {"nodes": _SPAN_NODES, "edges": []})
    server._GRAPH_CACHE.clear()
    # hit inside top-level def a() (line 17): FQN 'a' == label 'a' -> no qualname key
    monkeypatch.setattr(
        server, "_semble_index",
        lambda: _FakeIndex(search_hits=[_FakeHit("m.py", 17)], related_hits=[]),
    )
    data = json.loads(server.graphify_locate("alpha", as_json=True))
    assert data["seed"]["node"] == "a" and "qualname" not in data["seed"]
    # module-top hit (line 1, no enclosing symbol): qualname None, key omitted, no crash
    monkeypatch.setattr(
        server, "_semble_index",
        lambda: _FakeIndex(search_hits=[_FakeHit("m.py", 1)], related_hits=[]),
    )
    data2 = json.loads(server.graphify_locate("imports", as_json=True))
    assert "qualname" not in data2["seed"]


def test_spans_property_setter_resolve_by_line(tmp_path, monkeypatch):
    # same-name getter/setter share a qualname; the join is by line range, so each
    # body resolves to its own node despite the identical FQN.
    src = (
        "class C:\n"                # 1
        "    @property\n"           # 2
        "    def val(self):\n"      # 3
        "        return self._v\n"  # 4
        "    @val.setter\n"         # 5
        "    def val(self, v):\n"   # 6
        "        self._v = v\n"     # 7
    )
    (tmp_path / "p.py").write_text(src, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    nodes = [
        {"id": "getter", "label": "val", "source_file": "p.py",
         "source_location": "L3", "file_type": "code"},
        {"id": "setter", "label": "val", "source_file": "p.py",
         "source_location": "L6", "file_type": "code"},
    ]
    assert server._node_for_location(nodes, "p.py", 4)["id"] == "getter"
    assert server._node_for_location(nodes, "p.py", 7)["id"] == "setter"


def test_spans_for_file_handles_bom(tmp_path, monkeypatch):
    # a UTF-8 BOM would make read_text(utf-8)+ast choke; parsing bytes honors it.
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfdef alpha():\n    return 1\n")
    quals = [q for _rs, _e, _dl, q in server._spans_for_file("bom.py")]
    assert "alpha" in quals


def test_spans_for_file_survives_pathological_nesting(tmp_path, monkeypatch):
    # a flat but very deep AST can overflow the recursive walk; the contract is a
    # graceful empty/partial list, never an escaping RecursionError.
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "deep.py").write_text("x = a" + ".b" * 8000 + "\n", encoding="utf-8")
    assert isinstance(server._spans_for_file("deep.py"), list)   # does not raise


def test_span_cache_is_bounded(tmp_path, monkeypatch):
    # the per-file span cache must not grow without bound (long-lived HTTP + churn)
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(spans, "_SPAN_CACHE_MAX", 8)
    server._SPAN_CACHE.clear()
    for i in range(20):
        f = tmp_path / f"mod_{i}.py"
        f.write_text(f"def fn_{i}():\n    return {i}\n", encoding="utf-8")
        server._spans_for_file(f"mod_{i}.py")
    assert len(server._SPAN_CACHE) <= 8


def test_bfs_subgraph_helper():
    adj = {"A": [("B", "calls")], "B": [("A", "calls"), ("C", "calls")], "C": [("B", "calls")]}
    labels = {"A": "A", "B": "B", "C": "C"}
    visited, edges, truncated, tokens = server._bfs_subgraph(adj, labels, "A", 2, 10000)
    assert {"A", "B", "C"} <= visited and truncated is False and tokens > 0
    _, _, trunc2, _ = server._bfs_subgraph(adj, labels, "A", 5, 1)
    assert trunc2 is True


class _FakeChunk:
    def __init__(self, file_path, start_line, end_line=None):
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line if end_line is not None else start_line


class _FakeHit:
    def __init__(self, file_path, start_line, end_line=None):
        self.chunk = _FakeChunk(file_path, start_line, end_line)


class _FakeIndex:
    """Stand-in for semble's SembleIndex so the bridge is testable without semble."""

    def __init__(self, search_hits, related_hits):
        self._search = search_hits
        self._related = related_hits

    def search(self, query, top_k=3):
        return self._search[:top_k]

    def find_related(self, hit, top_k=8):
        return self._related[:top_k]


def test_locate_cross_check_flags_hidden_link(tmp_path, monkeypatch):
    # A--B connected; C is semantically similar (find_related) but structurally disconnected.
    _write_graph(tmp_path, {
        "nodes": [
            {"id": "A", "label": "A", "source_file": "a.py", "source_location": "L1"},
            {"id": "B", "label": "B", "source_file": "b.py", "source_location": "L1"},
            {"id": "C", "label": "C", "source_file": "c.py", "source_location": "L1"},
        ],
        "edges": [{"source": "A", "target": "B", "type": "calls"}],
    })
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    fake = _FakeIndex(
        search_hits=[_FakeHit("a.py", 1)],
        related_hits=[_FakeHit("b.py", 1), _FakeHit("c.py", 1)],
    )
    monkeypatch.setattr(server, "_semble_index", lambda: fake)

    data = json.loads(server.graphify_locate("anything", as_json=True))
    assert data["seed"]["node"] == "A"
    assert data["structure"]["nodes"] >= 2  # A + B reached structurally
    cousins = {c["node"]: c for c in data["semantic_cousins"]}
    assert cousins["B"]["linked"] is True and cousins["B"]["distance"] == 1
    assert cousins["C"]["linked"] is False and cousins["C"]["distance"] == "unreachable"
    hidden = {c["node"] for c in data["hidden_links"]}
    assert "C" in hidden and "B" not in hidden  # the emergent signal


def test_locate_without_semble_degrades(project, monkeypatch):
    monkeypatch.setattr(server, "_semble_index", lambda: None)
    out = server.graphify_locate("anything")
    assert "semble" in out and "pip install" in out


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
    assert "graphify_set_labels" in out  # points to the assistant-driven fallback


def test_set_labels_persists_and_patches_html(tmp_path, monkeypatch):
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "A", "label": "A", "community": 0},
            {"id": "B", "label": "B", "community": 2},
        ],
        "links": [],
    }), encoding="utf-8")
    (out / "graph.html").write_text(
        '"community_name": "Community 0" ... "community_name": "Community 2" ... '
        '{"0": "Community 0", "2": "Community 2"}', encoding="utf-8",
    )
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)

    data = json.loads(server.graphify_set_labels(
        {"0": "Authentication", "2": "Tests", "99": "Nope"}, as_json=True))
    assert data["labeled"] == 2
    assert data["unknown_ids"] == ["99"]
    # source of truth updated
    labels = json.loads((out / ".graphify_labels.json").read_text(encoding="utf-8"))
    assert labels["0"] == "Authentication" and labels["2"] == "Tests"
    # graph.html patched in place (both per-node and the labels map)
    html = (out / "graph.html").read_text(encoding="utf-8")
    assert "Authentication" in html and '"Community 0"' not in html
    assert data["graph_html_patched"] >= 2


def test_set_labels_rejects_unknown_only(project):
    out = server.graphify_set_labels({"999": "X"})
    assert "No valid community ids" in out


def test_label_communities_placeholder(project):
    out = _run_in_memory(
        project, "graphify_label_communities", {"method": "placeholder", "as_json": True}
    )
    data = json.loads(out)
    assert data["method"] == "placeholder"
    assert all(c["name"] == f"Community {c['id']}" for c in data["communities"])


# --- review-pass regression tests + confirmed coverage gaps ------------------

def test_surprises_ignores_uncommunitied_target(tmp_path, monkeypatch):
    # fallback must not flag an edge to a community-less node as cross-community
    _write_graph(tmp_path, {
        "nodes": [
            {"id": "A", "label": "A", "community": 0},
            {"id": "B", "label": "B", "community": 0},
            {"id": "X", "label": "X"},  # no community
        ],
        "edges": [
            {"source": "A", "target": "X", "type": "uses"},  # 0 -> none: not a surprise
            {"source": "A", "target": "B", "type": "uses"},  # 0 -> 0: same community
        ],
    })
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_surprises(as_json=True))
    assert data["fallback"] is True
    assert data["surprises"] == []


def test_locate_no_semantic_matches(project, monkeypatch):
    monkeypatch.setattr(server, "_semble_index", lambda: _FakeIndex([], []))
    assert "No semantic matches" in server.graphify_locate("nothing here")


def test_locate_seed_not_in_graph(project, monkeypatch):
    fake = _FakeIndex(search_hits=[_FakeHit("not_in_graph.py", 1)], related_hits=[])
    monkeypatch.setattr(server, "_semble_index", lambda: fake)
    data = json.loads(server.graphify_locate("x", as_json=True))
    assert data["seed"] is None
    assert "note" in data and data["semantic_hits"]


def test_node_for_location_in_chunk_first_def_wins():
    nodes = [
        {"id": "early", "label": "early", "source_file": "m.py",
         "source_location": "L50", "file_type": "code"},
        {"id": "late", "label": "late", "source_file": "m.py",
         "source_location": "L60", "file_type": "code"},
    ]
    # chunk [49, 75]: no def <= 49; both 50 and 60 begin inside -> first (min) wins
    assert server._node_for_location(nodes, "m.py", 49, 75)["id"] == "early"


def test_bfs_subgraph_handles_self_loop():
    adj = {"A": [("A", "self"), ("B", "calls")], "B": [("A", "calls")]}
    labels = {"A": "A", "B": "B"}
    visited, edges, truncated, tokens = server._bfs_subgraph(adj, labels, "A", 2, 10000)
    assert {"A", "B"} <= visited  # terminates, no infinite loop
    assert any(e["relation"] == "self" for e in edges)


def test_validate_with_label_fallback_ids(tmp_path, monkeypatch):
    _write_graph(tmp_path, {
        "nodes": [{"label": "A"}, {"label": "B"}],  # no explicit id -> label fallback
        "edges": [
            {"source": "A", "target": "B", "type": "calls"},
            {"source": "A", "target": "Z", "type": "calls"},  # Z dangling
        ],
    })
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_validate(as_json=True))
    assert data["issues"]["dangling_edges"] == 1


def test_locate_hidden_links_ordered_nearest_first(tmp_path, monkeypatch):
    # seed A (hops=2); B reachable at dist 3, C at dist 4, D unreachable.
    # hidden order must be nearest reachable first, unreachable last: B, C, D.
    _write_graph(tmp_path, {
        "nodes": [
            {"id": x, "label": x, "source_file": x + ".py",
             "source_location": "L1", "file_type": "code"}
            for x in ("A", "n1", "n2", "B", "n3", "C", "D")
        ],
        "edges": [
            {"source": "A", "target": "n1", "type": "x"},
            {"source": "n1", "target": "n2", "type": "x"},
            {"source": "n2", "target": "B", "type": "x"},
            {"source": "n2", "target": "n3", "type": "x"},
            {"source": "n3", "target": "C", "type": "x"},
        ],
    })
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    fake = _FakeIndex(
        search_hits=[_FakeHit("A.py", 1)],
        related_hits=[_FakeHit("C.py", 1), _FakeHit("D.py", 1), _FakeHit("B.py", 1)],
    )
    monkeypatch.setattr(server, "_semble_index", lambda: fake)
    data = json.loads(server.graphify_locate("x", hops=2, related_k=10, as_json=True))
    assert [c["node"] for c in data["hidden_links"]] == ["B", "C", "D"]
    dist = {c["node"]: c["distance"] for c in data["hidden_links"]}
    assert dist["B"] == 3 and dist["C"] == 4 and dist["D"] == "unreachable"


def test_set_labels_no_placeholders_message(tmp_path, monkeypatch):
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "A", "label": "A", "community": 0}], "links": []}),
        encoding="utf-8",
    )
    (out / "graph.html").write_text("already named, no placeholders", encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_set_labels({"0": "Auth"}, as_json=True))
    assert data["graph_html_patched"] == 0
    assert "no 'Community N' placeholders" in server.graphify_set_labels({"0": "Auth"})


def _git_init(tmp_path):
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@e")
    git("config", "user.name", "t")
    return git


def test_freshness_rename_reports_old_path(tmp_path, monkeypatch):
    import shutil as _sh

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    _write_graph(tmp_path, {"nodes": [], "links": []})
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "init")
    git("mv", "old.py", "new.py")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "rebuild"
    assert "old.py" in data["deleted_or_renamed"]
    assert all(" -> " not in p for p in data["deleted_or_renamed"])


def test_freshness_large_changeset_rebuild(tmp_path, monkeypatch):
    import shutil as _sh

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    _write_graph(tmp_path, {"nodes": [], "links": []})
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "init")
    for i in range(30):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "rebuild"
    assert "large change set" in data["reason"]


def test_freshness_fresh_state(tmp_path, monkeypatch):
    import os
    import shutil as _sh
    import time

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "init")
    future = time.time() + 30
    os.utime(out / "graph.json", (future, future))  # graph newer than the commit
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is False
    assert data["recommended_action"] == "fresh"


# --- Phase 3: cosmetic-vs-structural freshness, HTTP bearer auth, lean toolset --

def _require_git():
    import shutil as _sh

    import pytest
    if _sh.which("git") is None:
        pytest.skip("git not available")


def _head(tmp_path):
    import subprocess
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()


def test_ast_equivalent_detects_cosmetic_vs_structural(tmp_path, monkeypatch):
    _require_git()
    (tmp_path / "m.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", "m.py")
    git("commit", "-m", "init")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    # comment + blank line + reflow only -> AST-identical -> cosmetic
    (tmp_path / "m.py").write_text(
        "def f(x):\n    # tweak\n\n    return x + 1\n", encoding="utf-8")
    assert server._ast_equivalent("m.py", "HEAD") is True
    # logic change -> structural
    (tmp_path / "m.py").write_text("def f(x):\n    return x + 2\n", encoding="utf-8")
    assert server._ast_equivalent("m.py", "HEAD") is False
    # docstring change -> structural (docstrings live in the AST)
    (tmp_path / "m.py").write_text(
        'def f(x):\n    """doc"""\n    return x + 1\n', encoding="utf-8")
    assert server._ast_equivalent("m.py", "HEAD") is False
    # non-Python and absent-at-ref -> None (caller treats as structural)
    assert server._ast_equivalent("README.md", "HEAD") is None
    assert server._ast_equivalent("ghost.py", "HEAD") is None


def test_freshness_cosmetic_change_stays_fresh(tmp_path, monkeypatch):
    _require_git()
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", "m.py")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    (tmp_path / "m.py").write_text("def f():\n    # note\n    return 1\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "fresh"
    assert data["stale"] is False
    assert data["cosmetic_changes"] == ["m.py"]
    assert data["structural_changes"] == []


def test_freshness_structural_change_updates(tmp_path, monkeypatch):
    _require_git()
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", "m.py")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    (tmp_path / "m.py").write_text("def f():\n    return 999\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "update"
    assert data["structural_changes"] == ["m.py"]
    assert data["cosmetic_changes"] == []


def test_bearer_auth_asgi_enforces_token():
    import asyncio

    calls = {"app": 0}

    async def app(scope, receive, send):
        calls["app"] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = server._bearer_auth_asgi(app, "s3cret")

    async def run(headers):
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {}

        await guarded({"type": "http", "headers": headers}, receive, send)
        return sent

    # missing token -> 401, app not invoked
    sent = asyncio.run(run([]))
    assert sent[0]["status"] == 401 and calls["app"] == 0
    # wrong token -> 401
    sent = asyncio.run(run([(b"authorization", b"Bearer nope")]))
    assert sent[0]["status"] == 401 and calls["app"] == 0
    # correct token -> app runs
    sent = asyncio.run(run([(b"authorization", b"Bearer s3cret")]))
    assert any(m.get("status") == 200 for m in sent) and calls["app"] == 1

    # non-ASCII Authorization header -> clean 401, NOT a TypeError/500
    sent = asyncio.run(run([(b"authorization", b"Bearer caf\xe9")]))
    assert sent[0]["status"] == 401

    async def rs(*a):
        return {}

    # websocket scope with a bad token -> policy close (1008), app not invoked
    closed = []

    async def wsend(m):
        closed.append(m)

    before = calls["app"]
    asyncio.run(server._bearer_auth_asgi(app, "s3cret")(
        {"type": "websocket", "headers": []}, rs, wsend))
    assert closed and closed[0] == {"type": "websocket.close", "code": 1008}
    assert calls["app"] == before

    # non-http/ws scope (lifespan) passes straight through, no auth
    lif = {"ran": 0}

    async def lifapp(scope, receive, send):
        lif["ran"] += 1

    asyncio.run(server._bearer_auth_asgi(lifapp, "s3cret")({"type": "lifespan"}, rs, rs))
    assert lif["ran"] == 1


def _skip_without_uvicorn():
    import importlib.util

    import pytest
    if importlib.util.find_spec("uvicorn") is None:
        pytest.skip("uvicorn not available")


def test_main_http_with_api_key_wraps_served_app_with_auth(monkeypatch):
    import asyncio

    _skip_without_uvicorn()
    import uvicorn

    base_called = {"n": 0}

    async def base_app(scope, receive, send):
        base_called["n"] += 1

    seen = {}
    monkeypatch.setattr(server, "TRANSPORT", "streamable-http")
    monkeypatch.setattr(server, "API_KEY", "k")
    monkeypatch.setattr(server, "RESTRICT_PATHS", False)
    monkeypatch.setattr(server.mcp, "streamable_http_app", lambda: base_app)
    monkeypatch.setattr(server.mcp, "run", lambda **kw: seen.setdefault("ran", kw))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(uvicorn=kw, app=app))
    server.main()
    assert "uvicorn" in seen and "ran" not in seen   # bearer path uses uvicorn, not mcp.run
    assert seen["uvicorn"]["host"] == server.HTTP_HOST
    assert server.RESTRICT_PATHS is True
    # the SERVED app must be the bearer guard, not the raw base: an unauth request -> 401
    sent = []

    async def send(m):
        sent.append(m)

    async def recv():
        return {}

    asyncio.run(seen["app"]({"type": "http", "headers": []}, recv, send))
    assert sent and sent[0]["status"] == 401 and base_called["n"] == 0


def test_main_http_sse_with_api_key_wraps_sse_app(monkeypatch):
    _skip_without_uvicorn()
    import uvicorn

    seen = {}

    def _boom():
        raise AssertionError("sse transport must wrap sse_app, not streamable_http_app")

    monkeypatch.setattr(server, "TRANSPORT", "sse")
    monkeypatch.setattr(server, "API_KEY", "k")
    monkeypatch.setattr(server.mcp, "sse_app", lambda: (lambda *a: None))
    monkeypatch.setattr(server.mcp, "streamable_http_app", _boom)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app))
    server.main()
    assert "app" in seen   # sse_app was selected + wrapped (streamable_http_app untouched)


def test_main_http_no_apikey_nonloopback_warns(monkeypatch, capsys):
    monkeypatch.setattr(server, "TRANSPORT", "streamable-http")
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setattr(server, "HTTP_HOST", "0.0.0.0")
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main()
    err = capsys.readouterr().err
    assert "WARNING" in err and "GRAPHIFY_API_KEY" in err


def test_main_http_no_apikey_loopback_no_warn(monkeypatch, capsys):
    monkeypatch.setattr(server, "TRANSPORT", "streamable-http")
    monkeypatch.setattr(server, "API_KEY", "")
    monkeypatch.setattr(server, "HTTP_HOST", "127.0.0.1")
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main()
    assert "WARNING" not in capsys.readouterr().err


def test_lean_toolset_membership_is_valid():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert server.LEAN_TOOLS <= names          # no typos: every lean tool exists


def test_lean_removals_keeps_core_drops_rest():
    removals = server._lean_removals(
        ["graphify_locate", "graphify_overview", "graphify_add", "graphify_explain"])
    assert "graphify_add" in removals and "graphify_explain" in removals
    assert "graphify_locate" not in removals and "graphify_overview" not in removals


def test_apply_toolset_full_is_noop(monkeypatch):
    monkeypatch.setattr(server, "TOOLSET", "full")
    before = len(server.mcp._tool_manager.list_tools())
    server._apply_toolset()
    assert len(server.mcp._tool_manager.list_tools()) == before


def test_effective_lean_tools_gates_locate_on_semble(monkeypatch):
    import importlib.util as iu
    # semble absent -> graphify_locate (needs the extra) is dropped from lean
    monkeypatch.setattr(iu, "find_spec", lambda name: None if name == "semble" else object())
    assert "graphify_locate" not in server._effective_lean_tools()
    # semble present -> it stays
    monkeypatch.setattr(iu, "find_spec", lambda name: object())
    assert "graphify_locate" in server._effective_lean_tools()


def test_lean_set_supports_documented_flow():
    # the lean core must let you resolve a node to source and search by name
    # without the optional semble extra
    assert {"graphify_node_details", "graphify_search", "graphify_subgraph"} <= server.LEAN_TOOLS


def test_overview_suggestions_respect_active_tools(project, monkeypatch):
    # when surprises is trimmed from the active surface, overview must not steer to it
    monkeypatch.setattr(
        server, "_registered_tool_names",
        lambda: {"graphify_subgraph", "graphify_communities", "graphify_overview"},
    )
    data = json.loads(server.graphify_overview(as_json=True))
    assert all("graphify_surprises" not in s for s in data["suggested_next"])
    assert "graphify_communities()" in data["suggested_next"]


def test_freshness_cosmetic_change_while_behind_updates(tmp_path, monkeypatch):
    _require_git()
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", "m.py")
    git("commit", "-m", "c1")
    c1 = _head(tmp_path)
    # advance HEAD so the graph (built at c1) is genuinely 'behind'
    (tmp_path / "other.py").write_text("y = 2\n", encoding="utf-8")
    git("add", "other.py")
    git("commit", "-m", "c2")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": c1})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    # a cosmetic-only working-tree edit must NOT mask the behind-HEAD staleness
    (tmp_path / "m.py").write_text("def f():\n    # note\n    return 1\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is True
    assert data["recommended_action"] != "fresh"
    assert data["cosmetic_changes"] == ["m.py"]


def test_freshness_unreachable_built_at_recommends_rebuild(tmp_path, monkeypatch):
    """A recorded built_at_commit git can't resolve (shallow clone / gc / rebase /
    squash) must steer to a full rebuild with a clear reason — not crash, and not be
    reported as merely 'an older commit' that an incremental update could catch up."""
    _require_git()
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "init")
    # syntactically valid but non-existent commit, as if history was rewritten away
    ghost = "1234567890abcdef1234567890abcdef12345678"
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": ghost})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["stale"] is True
    assert data["built_commit_reachable"] is False
    assert data["recommended_action"] == "rebuild"
    assert "unreachable" in data["reason"]


def test_freshness_reachable_built_at_marks_reachable(tmp_path, monkeypatch):
    """A built_at_commit at HEAD that git knows reports built_commit_reachable=True
    and stays fresh (guards against the reachability check false-positiving)."""
    _require_git()
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["built_commit_reachable"] is True
    assert data["recommended_action"] == "fresh"


def test_graph_age_reports_commits_behind(tmp_path, monkeypatch):
    """overview/subgraph carry a lightweight graph_age so an agent sees staleness
    without a separate graphify_freshness call."""
    _require_git()
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "c1")
    c1 = _head(tmp_path)
    (tmp_path / "n.py").write_text("y = 2\n", encoding="utf-8")  # advance HEAD by one commit
    git("add", ".")
    git("commit", "-m", "c2")
    _write_graph(tmp_path, {
        "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
        "edges": [{"source": "A", "target": "B", "relation": "x"}],
        "built_at_commit": c1,
    })
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    ov = json.loads(server.graphify_overview(as_json=True))
    sg = json.loads(server.graphify_subgraph("A", as_json=True))
    assert ov["graph_age"] == "built 1 commit ago"
    assert sg["graph_age"] == "built 1 commit ago"


def test_graph_age_built_at_head(tmp_path, monkeypatch):
    _require_git()
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    git = _git_init(tmp_path)
    git("add", ".")
    git("commit", "-m", "c1")
    _write_graph(tmp_path, {
        "nodes": [{"id": "A", "label": "A"}], "edges": [],
        "built_at_commit": _head(tmp_path),
    })
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    assert json.loads(server.graphify_overview(as_json=True))["graph_age"] == "built at HEAD"


def test_graph_age_none_without_git(project):
    # the `project` fixture is not a git repo -> no cheap age signal -> graph_age is None
    assert json.loads(server.graphify_overview(as_json=True))["graph_age"] is None


def test_freshness_large_cosmetic_set_skips_ast_and_rebuilds(tmp_path, monkeypatch):
    _require_git()
    git = _git_init(tmp_path)
    for i in range(26):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    # individually-cosmetic edits to 26 tracked files: the >25 gate must SKIP the AST
    # diff (so cosmetic stays empty) and route straight to a rebuild
    for i in range(26):
        (tmp_path / f"f{i}.py").write_text("x = 1  # touched\n", encoding="utf-8")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "rebuild"
    assert "large change set" in data["reason"]
    assert data["cosmetic_changes"] == []


# --- multi-language span/structure backend (tree-sitter) ----------------------

_JS_SRC = (
    b"class Service {\n"          # 1
    b"  fetch(url) {\n"           # 2
    b"    return get(url);\n"     # 3
    b"  }\n"                      # 4
    b"}\n"                        # 5
    b"function helper(x) {\n"     # 6
    b"  return x + 1;\n"          # 7
    b"}\n"                        # 8
)


def _skip_without_treesitter():
    import importlib.util

    import pytest
    if (importlib.util.find_spec("tree_sitter") is None
            or importlib.util.find_spec("tree_sitter_language_pack") is None):
        pytest.skip("tree-sitter backend not installed")


def test_is_ts_symbol_classification():
    assert server._is_ts_symbol("function_declaration")
    assert server._is_ts_symbol("class_definition")
    assert server._is_ts_symbol("method_declaration")
    assert server._is_ts_symbol("struct_item")
    assert server._is_ts_symbol("function_expression")   # named function expr must pass
    assert server._is_ts_symbol("class_specifier")       # C++ class/struct IS a def
    assert not server._is_ts_symbol("function_type")     # type look-alike excluded
    assert not server._is_ts_symbol("class_body")
    assert not server._is_ts_symbol("template_function")  # a C++ call, not a def
    assert not server._is_ts_symbol("function_declarator")
    assert not server._is_ts_symbol("method_invocation")  # Java call, not a def
    assert not server._is_ts_symbol("invocation_expression")  # C# call, not a def
    assert not server._is_ts_symbol("type_parameter")     # generic <T>, not a def
    assert not server._is_ts_symbol("type_binding")        # impl Iterator<Item=X>
    assert not server._is_ts_symbol("identifier")


def test_spans_treesitter_excludes_type_params_and_bindings(tmp_path, monkeypatch):
    # generic type parameters (<T>) and associated-type bindings (impl Iterator<Item=T>)
    # carry a name but are not definitions — they must not leak into qualnames
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "x.rs").write_bytes(
        b"type Alias = u32;\n"
        b"fn parse<T: Clone>(x: T) -> impl Iterator<Item = T> { std::iter::once(x) }\n"
        b"struct S;\nimpl S { fn run(&self) {} }\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("x.rs")}
    assert {"Alias", "parse", "S", "S.run"} <= quals          # real defs captured
    assert not any(q.split(".")[-1] in {"T", "Item"} for q in quals)  # no type-level noise


def test_spans_treesitter_go_and_rust(tmp_path, monkeypatch):
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "main.go").write_bytes(
        b"package main\n\nfunc Handler(w int) int {\n\treturn w\n}\n\ntype Server struct{}\n")
    go = {q for _rs, _e, _dl, q in server._spans_for_file("main.go")}
    assert "Handler" in go and "Server" in go
    # Rust impl methods carry the type-qualified qualname (impl `type` field fallback)
    (tmp_path / "lib.rs").write_bytes(
        b"struct Pool;\nimpl Pool {\n    fn acquire(&self) -> i32 {\n        1\n    }\n}\n")
    rs = {q for _rs, _e, _dl, q in server._spans_for_file("lib.rs")}
    assert "Pool" in rs and "Pool.acquire" in rs
    assert server._span_qualname("lib.rs", 4) == "Pool.acquire"


def test_spans_treesitter_absorbs_leading_doc_comment(tmp_path, monkeypatch):
    # Go/Java/JS put doc comments ABOVE the symbol (like Python decorators); the
    # span's region_start must absorb them so a chunk starting on the doc comment
    # still resolves to the symbol it documents.
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "x.go").write_bytes(
        b"package m\n\n// Handler does the thing.\nfunc Handler() int {\n\treturn 1\n}\n")
    spans = {q: (rs, e, dl) for rs, e, dl, q in server._spans_for_file("x.go")}
    rs, _e, dl = spans["Handler"]
    assert dl == 4 and rs == 3          # def at L4; region_start absorbs the doc comment (L3)
    nodes = [{"id": "Handler", "label": "Handler", "source_file": "x.go",
              "source_location": "L4", "file_type": "code"}]
    assert server._node_for_location(nodes, "x.go", 3)["id"] == "Handler"   # doc-comment chunk


def test_spans_treesitter_anonymous_bound_function(tmp_path, monkeypatch):
    # an arrow / anonymous function bound to a name takes the binding name as qualname
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "a.js").write_bytes(
        b"const fetchUser = (id) => {\n  return get(id);\n};\n"
        b"const retry = async function () {\n  return 1;\n};\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("a.js")}
    assert "fetchUser" in quals and "retry" in quals
    nodes = [{"id": "fetchUser", "label": "fetchUser", "source_file": "a.js",
              "source_location": "L1", "file_type": "code"}]
    assert server._node_for_location(nodes, "a.js", 2)["id"] == "fetchUser"  # chunk in body


def test_spans_treesitter_go_receiver_qualname(tmp_path, monkeypatch):
    # Go method receivers become Type.method, not a bare method name
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "c.go").write_bytes(
        b"package m\ntype Client struct{}\n"
        b"func (c *Client) Get(u string) error { return nil }\n"
        b"func Helper() int { return 1 }\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("c.go")}
    assert "Client.Get" in quals and "Helper" in quals
    assert server._span_qualname("c.go", 3) == "Client.Get"


def test_spans_treesitter_object_and_class_field_arrows(tmp_path, monkeypatch):
    # object-property arrows and class-field arrows bind the property/field name
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "z.js").write_bytes(
        b"const obj = { arrowProp: (x) => x + 1, shorthand() { return 1; } };\n"
        b"class Service { handler = (req) => { return req; }; }\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("z.js")}
    assert {"arrowProp", "shorthand", "Service", "Service.handler"} <= quals


def test_spans_treesitter_cpp_declarator_names(tmp_path, monkeypatch):
    # C++ names live in a declarator chain; a qualified method reads Class.method,
    # and template calls / nested declarators don't leak in as bogus symbols
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "t.cpp").write_bytes(
        b"namespace cpr {\nResponse Session::Get() {\n  return holds_alternative<int>(r);\n}\n"
        b"int helper(int x){ return x; }\n}\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("t.cpp")}
    assert "cpr.Session.Get" in quals and "cpr.helper" in quals
    assert not any("holds_alternative" in q for q in quals)        # the call isn't a def
    assert not any(q.endswith("Get.Session.Get") for q in quals)   # no declarator double-count


def test_spans_treesitter_named_function_expression(tmp_path, monkeypatch):
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "z.js").write_bytes(b"const x = function bar() {\n  return 1;\n};\n")
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("z.js")}
    assert "bar" in quals          # not dropped by the _expression suffix filter


def test_uppercase_py_uses_ast_decorator_aware_path(tmp_path, monkeypatch):
    # an uppercase .PY extension must still take the decorator-aware ast path
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    src = b"import functools\n@functools.cache\ndef decorated():\n    return 1\n"
    (tmp_path / "U.PY").write_bytes(src)
    assert server._spans_for_file("U.PY") == [(2, 4, 3, "decorated")]   # region_start=2 (deco)


def test_freshness_structural_change_non_python(tmp_path, monkeypatch):
    _require_git()
    _skip_without_treesitter()
    (tmp_path / "app.js").write_bytes(b"function f() {\n  return 1;\n}\n")
    git = _git_init(tmp_path)
    git("add", "app.js")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    server._TS_PARSERS.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    (tmp_path / "app.js").write_bytes(b"function f() {\n  return 2;\n}\n")   # value change
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "update"
    assert data["structural_changes"] == ["app.js"]
    assert data["cosmetic_changes"] == []


def test_spans_treesitter_javascript(tmp_path, monkeypatch):
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "app.js").write_bytes(_JS_SRC)
    spans = {q: (rs, e, dl) for rs, e, dl, q in server._spans_for_file("app.js")}
    assert "Service" in spans and "Service.fetch" in spans and "helper" in spans
    assert spans["Service.fetch"][0] == 2          # method definition line
    assert spans["helper"][0] == 6


def test_node_for_location_resolves_non_python_via_treesitter(tmp_path, monkeypatch):
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    (tmp_path / "app.js").write_bytes(_JS_SRC)
    nodes = [
        {"id": "Service", "label": "Service", "source_file": "app.js",
         "source_location": "L1", "file_type": "code"},
        {"id": "fetch", "label": "fetch", "source_file": "app.js",
         "source_location": "L2", "file_type": "code"},
        {"id": "helper", "label": "helper", "source_file": "app.js",
         "source_location": "L6", "file_type": "code"},
    ]
    # a chunk inside Service.fetch resolves to fetch via span containment
    assert server._node_for_location(nodes, "app.js", 3)["id"] == "fetch"
    assert server._span_qualname("app.js", 3) == "Service.fetch"
    assert server._node_for_location(nodes, "app.js", 7)["id"] == "helper"


def test_spans_treesitter_java(tmp_path, monkeypatch):
    # Java: class + method chain to Class.method; a chunk in the body resolves to it.
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "Api.java").write_bytes(
        b"class Api {\n"                  # 1
        b"    int fetch(String u) {\n"    # 2
        b"        return get(u);\n"       # 3
        b"    }\n"                        # 4
        b"}\n")                           # 5
    spans = {q: (rs, e, dl) for rs, e, dl, q in server._spans_for_file("Api.java")}
    assert "Api" in spans and "Api.fetch" in spans
    assert spans["Api.fetch"][2] == 2                              # method def_line
    assert server._span_qualname("Api.java", 3) == "Api.fetch"     # chunk inside the body


def test_spans_treesitter_typescript(tmp_path, monkeypatch):
    # TypeScript (the "JS/TS" benchmark claim): interface + class + typed async method.
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    server._TS_PARSERS.clear()
    (tmp_path / "client.ts").write_bytes(
        b"interface Fetcher {\n"                            # 1
        b"  fetch(url: string): number;\n"                  # 2
        b"}\n"                                              # 3
        b"class Api {\n"                                    # 4
        b"  async send(url: string): Promise<number> {\n"   # 5
        b"    return get(url);\n"                           # 6
        b"  }\n"                                            # 7
        b"}\n")                                             # 8
    quals = {q for _rs, _e, _dl, q in server._spans_for_file("client.ts")}
    assert {"Fetcher", "Api", "Api.send"} <= quals
    assert server._span_qualname("client.ts", 6) == "Api.send"     # chunk inside send()


def test_structurally_equal_non_python(tmp_path, monkeypatch):
    _skip_without_treesitter()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._TS_PARSERS.clear()
    old = b"function f(){\n  return 1;\n}\n"
    cosmetic = b"function f() {\n  // a note\n  return 1;\n}\n"   # comment + reformat
    structural = b"function f(){\n  return 2;\n}\n"               # value change
    assert server._structurally_equal("app.js", old, cosmetic) is True
    assert server._structurally_equal("app.js", old, structural) is False
    # operator/keyword flips are STRUCTURAL — anonymous tokens count in the skeleton
    assert server._structurally_equal("app.js", b"x = a + b;", b"x = a - b;") is False
    assert server._structurally_equal("app.js", b"x = a && b;", b"x = a || b;") is False
    assert server._structurally_equal("app.js", b"if (a == b){}", b"if (a != b){}") is False
    assert server._structurally_equal("app.js", b"function g(){}", b"async function g(){}") is False
    # rename is structural
    assert server._structurally_equal("app.js", old, b"function g(){\n  return 1;\n}\n") is False
    # operator flip in another language too
    assert server._structurally_equal(
        "m.go", b"package m\nfunc F() int { return a + b }\n",
        b"package m\nfunc F() int { return a - b }\n") is False


def test_freshness_cosmetic_change_non_python(tmp_path, monkeypatch):
    _require_git()
    _skip_without_treesitter()
    (tmp_path / "app.js").write_bytes(b"function f() {\n  return 1;\n}\n")
    git = _git_init(tmp_path)
    git("add", "app.js")
    git("commit", "-m", "init")
    _write_graph(tmp_path, {"nodes": [], "links": [], "built_at_commit": _head(tmp_path)})
    server._GRAPH_CACHE.clear()
    server._TS_PARSERS.clear()
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    (tmp_path / "app.js").write_bytes(b"function f() {\n  // tweak\n  return 1;\n}\n")
    data = json.loads(server.graphify_freshness(as_json=True))
    assert data["recommended_action"] == "fresh"
    assert data["cosmetic_changes"] == ["app.js"]


def test_span_backend_graceful_without_treesitter(tmp_path, monkeypatch):
    # tree-sitter unavailable -> non-Python files yield no spans and structural
    # comparison is undetermined (None), so the caller degrades safely
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    server._SPAN_CACHE.clear()
    monkeypatch.setattr(spans, "_ts_parser_for", lambda rel: (None, None))
    (tmp_path / "app.js").write_bytes(b"function f(){ return 1; }\n")
    assert server._spans_for_file("app.js") == []
    assert server._structurally_equal("app.js", b"a", b"a // c") is None
