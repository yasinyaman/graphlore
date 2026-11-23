"""The native engine (graphlore_engine) end to end: a real build of the polyglot
fixture, read back through graphlore's own tools, and its spans compared with
spans.py's. Skipped when the engine is not installed (build it with
``maturin develop -m engine/Cargo.toml --release``)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from graphlore import server, spans

graphlore_engine = pytest.importorskip("graphlore_engine")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def native(tmp_path, monkeypatch):
    """A copy of fixtures/polyglot with no graph, the server pointed at it and
    GRAPHLORE_ENGINE=native."""
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURES / "polyglot", root, ignore=shutil.ignore_patterns("graph.json"))
    monkeypatch.setattr(server.config, "PROJECT_DIR", root)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    server._SPAN_CACHE.clear()
    return root


def _graph(root):
    return json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))


def test_native_build_is_read_by_the_tools(native):
    out = server.graphlore_build(".")
    assert out.startswith("native engine "), out
    assert "graph.json ready" in out
    page = native / "graphify-out" / "graph.html"     # graphlore's own view of it
    assert "graph.html written" in out and server.viz.is_ours(page)
    graph = _graph(native)
    assert graph["graph"]["engine"] == "graphlore"
    assert graph["graph"]["schema_version"] == graphlore_engine.SCHEMA_VERSION

    overview = json.loads(server.graphlore_overview(as_json=True))
    assert overview["nodes"] == len(graph["nodes"])

    hits = json.loads(server.graphlore_search("acquire", as_json=True))
    assert [h["node"] for h in hits["matches"]] == [".acquire()", ".acquire()"]

    fetched = json.loads(server.graphlore_fetch(["core_src_lib_fixed_acquire"], as_json=True))
    (item,) = fetched["fetched"]
    assert item["qualname"] == "Fixed.acquire" and item["lines"] == "13-15"
    assert "fn acquire(&self) -> i32" in item["code"]

    skeleton = server.graphlore_skeleton(file="lib/shop/cart.rb")
    assert "def empty?" in skeleton and "@lines.empty?" not in skeleton

    details = json.loads(server.graphlore_node_details("Cart", as_json=True))
    assert details["id"] == "lib_shop_cart_shop_cart"
    assert details["extra"]["qualname"] == "Shop.Cart"
    neighbors = json.loads(server.graphlore_neighbors("lib_shop_cart_shop_cart", as_json=True))
    assert {(n["node"], n["relation"]) for n in neighbors["neighbors"]} >= {
        ("Shop", "contains"), (".add()", "method"), (".empty?()", "method")}

    lint = json.loads(server.graphlore_validate(as_json=True))
    assert lint["healthy"], lint["issues"]
    # the engine resolved the imports and calls itself: nothing left to add
    assert lint["resolved_edges"] == {"calls": 0, "imports": 0}


def test_native_build_records_the_commit_freshness_reads(native):
    if shutil.which("git") is None:
        pytest.skip("git not available")

    def git(*a):
        subprocess.run(["git", *a], cwd=native, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@e")
    git("config", "user.name", "t")
    git("add", ".")
    git("commit", "-m", "init")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=native, capture_output=True,
                          text=True).stdout.strip()
    server.graphlore_build(".")
    assert _graph(native)["built_at_commit"] == head
    data = json.loads(server.graphlore_freshness(as_json=True))
    assert data["recommended_action"] == "fresh"
    (native / "svc" / "store.py").write_text(
        "def load(item_id: int):\n    return None\n\n\ndef save():\n    pass\n", encoding="utf-8")
    data = json.loads(server.graphlore_freshness(as_json=True))
    assert data["recommended_action"] == "update"
    server.graphlore_build(".", update=True)            # a full rebuild under native
    ids = {n["id"] for n in _graph(native)["nodes"]}
    assert "svc_store_save" in ids and "svc_store_kind" not in ids


def test_native_code_only_skips_documents(native):
    server.graphlore_build(".", code_only=True)
    graph = _graph(native)
    assert not [n for n in graph["nodes"] if n.get("file_type") == "document"]
    server.graphlore_build(".")
    pages = [n for n in _graph(native)["nodes"] if n.get("node_kind") == "page"]
    assert [p["label"] for p in pages] == ["README.md"]


def test_engine_languages_are_source_extensions_graphlore_knows():
    exts = {"." + e for _name, es in graphlore_engine.languages() for e in es}
    # every extension graphlore treats as source and the engine reads is listed
    read = exts & server._SOURCE_EXTS
    assert read >= {".py", ".ts", ".tsx", ".go", ".java", ".kt", ".scala", ".cs", ".rs",
                    ".swift", ".c", ".h", ".cpp", ".rb", ".php"}
    assert {name for name, _ in graphlore_engine.languages()} >= {
        "python", "javascript", "typescript", "tsx", "go", "java", "kotlin", "scala",
        "csharp", "rust", "swift", "c", "cpp", "ruby", "php"}


# One snippet per language, from the golden span tests in test_tools.py and the
# polyglot fixture: the native engine must answer exactly as spans.py does.
_SNIPPETS = {
    "a.js": "const fetchUser = (id) => {\n  return get(id);\n};\n"
            "exports.normalize = function (type) {\n  return type;\n};\n"
            "class Service { handler = (req) => { return req; }; fetch(u) { return u; } }\n",
    "client.ts": "interface Fetcher {\n  fetch(url: string): number;\n}\nclass Api {\n"
                 "  async send(url: string): Promise<number> {\n    return get(url);\n  }\n}\n",
    "form.tsx": "function FormMessage({ className }: React.ComponentProps<\"p\">) {\n"
                "  return null\n}\n",
    "c.go": "package m\n\n// Client talks.\ntype Client struct{}\n"
            "func (c *Client) Get(u string) error { return nil }\n"
            "var hashes = map[string]func() int{\n\t\"a\": f,\n}\n",
    "Api.java": "class Api {\n    int fetch(String u) {\n        return get(u);\n    }\n}\n",
    "Greeter.kt": "package demo\n\nclass Greeter(val name: String) {\n  @JvmStatic\n"
                  "  fun greet(): String {\n    return name\n  }\n}\n\nobject Registry {\n}\n",
    # a KDoc block before a declaration with modifiers, and a `//` line after it
    "Doc.kt": "package a\n\n/**\n * Doc.\n */\nabstract class C(\n    val x: Int,\n) {\n}\n\n"
              "/**\n * Doc.\n */\n// note\ninterface O<A> : D<A> {\n}\n\n"
              "val y = 1 // trailing\nclass E\n",
    "Model.scala": "class ResponseBlob(val bytes: Array[Byte]) {\n  def size = bytes.length\n}\n",
    "Parameter.cs": "namespace Shop {\npublic record Parameter(string Name) {\n"
                    "  public int Size() {\n    return 1;\n  }\n}\n}\n",
    # a callback no definition holds is a scope to the resolver, never a span
    "a.test.js": "describe('x', () => {\n  const a = 1\n  it('y', () => {\n    a\n  })\n})\n"
                 "function f() {\n  return 1\n}\n",
    # a macro between `class` and the name is blanked: a class, not a function
    "env.h": "class LEVELDB_EXPORT Env {\n public:\n  Env();\n"
             "  virtual int NowMicros() = 0;\n  int Twice() {\n    return 2;\n  }\n};\n",
    # an `#if` inside an expression is parsed once: the class keeps its methods
    "Ext.cs": "static class Ext {\n    static int F(bool a)\n        => a\n            ? 1\n"
              "#if NET\n            : 2;\n#else\n            : 3;\n#endif\n"
              "    static int G() => 4;\n    static int H(bool a) =>\n#if NET\n"
              "        a ? 5 :\n#endif\n"
              "        6;\n    static int I() => 7;\n}\n",
    # a brace an `#if` opens and a later `#if` closes: the members after it stay
    "Client.cs": "class Client {\n    static void Configure(Handler h) {\n#if NET\n"
                 "        if (!OperatingSystem.IsBrowser()) {\n#endif\n        h.Cookies = false;\n"
                 "#if NET\n        }\n#endif\n    }\n    public void Dispose() {\n    }\n}\n",
    "lib.rs": (FIXTURES / "polyglot" / "core" / "src" / "lib.rs").read_text(encoding="utf-8"),
    "Retrier.swift": "protocol DataDecoder {\n  func decode(_ data: Data) -> Value\n}\n"
                     "class OfflineRetrier {\n  init(monitor: Monitor) {\n  }\n}\n",
    "test.c": "struct config { int port; };\n"
              "static int do_connect(struct config config) {\n  return config.port;\n}\n",
    "session.h": "namespace cpr {\nclass Session {\n public:\n  void SetUrl(const Url& url);\n"
                 "};\n}\n",
    "cookies.cpp": "const std::string& Cookie::GetDomain() const {\n  return domain_;\n}\n",
    "api.h": "namespace cpr {\nusing Header = std::map<std::string, std::string>;\n"
             "using AsyncResponse = AsyncWrapper<Response>;\n}\n",
    # an `#if` whose branches each leave a brace open is read once
    "pool.cpp": "namespace m {\nvoid Pool::Run() {\n#if NEW_API\n    if (poll(1)) {\n#else\n"
                "    if (wait(1)) {\n#endif\n        return;\n    }\n}\n"
                "void Pool::Stop() {\n}\n}\n",
    # definitions whose node type has no hint word, or no name: a delegate, a
    # conversion and an arithmetic operator, an indexer
    "Ops.cs": "public delegate void Cb(int x);\npublic class C\n{\n"
              "    public static implicit operator string(C c) => \"\";\n"
              "    public static C operator +(C a, C b) => a;\n"
              "    public int this[int i] => i;\n}\n",
    # a macro is `name!`; a subscript is named by its keyword; one `case` line
    # declares each of its names
    "macros.rs": "macro_rules! ext {\n    () => {};\n}\nfn ext() {}\n",
    "Kinds.swift": "enum K {\n    case a, b, c\n    case d\n}\n"
                   "struct H {\n    subscript(_ name: String) -> String? { nil }\n}\n",
    # a lock annotation after a declarator is blanked: the field and the
    # function keep their names; a function named in parentheses
    "locks.cc": "class S {\n  int num GUARDED_BY(mu);\n  int stats_[4] GUARDED_BY(mu);\n"
                "  void f() EXCLUSIVE_LOCKS_REQUIRED(mu);\n"
                "  int Recover(int* edit,\n      bool* save)\n      EXCLUSIVE_LOCKS_REQUIRED(mu);\n"
                "  void g() const LOCKS_EXCLUDED(mu) {\n  }\n};\n"
                "int ADD(int a, int b) {\n  return ADD2(a);\n}\n"
                "typedef void (Cb)(int);\n",
    # a gtest test is named by its two names; a macro alone on a line before a
    # call is no type of a declaration
    "gtest.cc": "class Fix {\n};\nTEST(Suite, Works) {\n  int x = 1;\n}\n"
                "TEST_F(Fix, Reads) { Do(1); }\n",
    "unity.c": "void testA(void)\n{\n    EXPECT_ABORT_BEGIN\n    TEST_ASSERT(0);\n"
               "    VERIFY_FAILS_END\n}\nvoid testB(void)\n{\n}\n",
    "util.ts": "export namespace util {\n  export const keys: number = 1;\n"
               "  export const f = () => 1;\n}\n",
    "cart.rb": (FIXTURES / "polyglot" / "lib" / "shop" / "cart.rb").read_text(encoding="utf-8"),
    "Stack.php": (FIXTURES / "polyglot" / "php" / "src" / "Stack.php").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("name", sorted(_SNIPPETS))
def test_native_spans_match_spans_py(name, monkeypatch):
    src = _SNIPPETS[name].encode("utf-8")
    spans._TS_PARSERS.clear()
    parser, _lang = spans._ts_parser_for(name, src)
    if parser is None:
        pytest.skip("Python tree-sitter (the [treesitter] extra) is not installed")
    assert graphlore_engine.file_spans(src, name) == spans._spans_treesitter(src, name)
    if hasattr(graphlore_engine, "definition_spans"):  # engine 0.5.80: spans alone
        fast = graphlore_engine.definition_spans(src, name)
        assert fast == graphlore_engine.file_spans(src, name)


def test_native_python_spans_match_ast():
    src = (b"import functools\n\n# not part of f\n@functools.cache\ndef f():\n"
           b"    return 1\n    # trailing\n\nclass A:\n    def m(self):\n        pass\n")
    assert graphlore_engine.file_spans(src, "m.py") == spans._spans_python(src)


def test_native_cli_reports_and_fails_cleanly(native, tmp_path):
    import sys
    run = subprocess.run([sys.executable, "-m", "graphlore_engine", "build", str(native),
                          "--out", str(tmp_path / "g.json")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["nodes"] > 0
    run = subprocess.run([sys.executable, "-m", "graphlore_engine", "build",
                          str(tmp_path / "missing")], capture_output=True, text=True)
    assert run.returncode == 1 and run.stderr.startswith("ERROR: cannot read")


_PY_PROJECT = {
    "pkg/__init__.py": "from .core import Engine\n",
    "pkg/core.py": (
        "from typing import TYPE_CHECKING\nfrom pkg import util\nif TYPE_CHECKING:\n"
        "    from pkg.cli import Cli\n\n\nclass Engine:\n    def run(self, n: int) -> int:\n"
        "        return util.double(n) + self.step()\n\n    def step(self) -> int:\n"
        "        return 1\n"),
    "pkg/util.py": (
        "def double(n):\n    from pkg.core import Engine\n    return n * 2\n\n\n"
        "def unused_helper():\n    return 3\n"),
    "pkg/cli.py": (
        "from pkg import Engine\n\n\nclass Cli:\n    pass\n\n\ndef main():\n"
        "    e = Engine()\n    return e.run(2)\n"),
    "tests/test_core.py": (
        "from pkg.core import Engine\n\n\ndef test_run():\n    assert Engine().run(1) == 3\n"),
}


@pytest.fixture()
def py_project(tmp_path, monkeypatch):
    for rel, text in _PY_PROJECT.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    server._SPAN_CACHE.clear()
    assert server.graphlore_build(".").startswith("native engine")
    return tmp_path


def _edges(root):
    g = _graph(root)
    return {(e["source"], e["relation"], e["target"]): e for e in g["links"]}


def test_native_imports_say_when_they_run(py_project):
    edges = _edges(py_project)
    runs = {(s, t): e.get("runs") for (s, rel, t), e in edges.items() if rel.startswith("imports")}
    assert runs[("pkg_core", "pkg_cli")] == "type-checking"
    assert runs[("pkg_util", "pkg_core")] == "deferred"
    assert runs[("pkg_core", "pkg_util")] == "module-level"
    # a name imported through a package's re-export lands on its definition
    assert ("pkg_cli", "imports", "pkg_core_engine") in edges
    assert edges[("pkg_core", "imports_from", "pkg_util")]["context"] == "submodule_import"
    ext = [n for n in _graph(py_project)["nodes"] if n.get("external")]
    assert {n["label"] for n in ext} == {"typing"}


def test_native_calls_and_the_tools_that_walk_them(py_project):
    edges = _edges(py_project)
    assert edges[("pkg_core_engine_run", "calls", "pkg_util_double")]["confidence"] == "EXTRACTED"
    assert ("pkg_core_engine_run", "calls", "pkg_core_engine_step") in edges
    # `e = Engine(); e.run(2)`: the receiver's type comes from what it was assigned
    assert edges[("pkg_cli_main", "calls", "pkg_core_engine_run")]["confidence"] == "INFERRED"

    cycles = json.loads(server.graphlore_cycles(as_json=True))
    assert cycles["import_edges_rejected"] == 0
    assert cycles["runtime_import_cycle_count"] == 0
    assert cycles["deferred_import_cycle_count"] == 1
    assert cycles["type_only_import_cycle_count"] == 1

    tests = json.loads(server.graphlore_tests_for("Engine.run", as_json=True))
    assert [t["test"] for t in tests["direct"]] == ["test_run()"]
    impact = json.loads(server.graphlore_impact("double", as_json=True))
    assert ".run()" in {r["node"] for r in impact["impacted"]}
    dead = json.loads(server.graphlore_unreferenced(as_json=True))
    assert {d["node"] for d in dead["unreferenced"]} == {"unused_helper()", "main()"}


def test_native_communities_are_read_kept_and_reclustered(native):
    server.graphlore_build(".")
    first = {n["id"]: n["community"] for n in _graph(native)["nodes"]}
    assert all(isinstance(c, int) for c in first.values())
    assert _graph(native)["graph"]["communities"]["count"] == len(set(first.values()))
    comms = json.loads(server.graphlore_communities(as_json=True))
    assert comms["total"] == len(set(first.values())) and comms["communities"][0]["name"]
    # a saved name survives a rebuild, and so do the ids
    top = comms["communities"][0]["id"]
    server.graphlore_set_labels({str(top): "The core"}, regenerate=False)
    server.graphlore_build(".")
    assert {n["id"]: n["community"] for n in _graph(native)["nodes"]} == first
    comms = json.loads(server.graphlore_communities(as_json=True))
    assert comms["communities"][0]["name"] == "The core"
    # cluster_only detects them again from the graph on disk, the same way
    out = server.graphlore_build(".", cluster_only=True)
    assert out.startswith("native engine: communities: "), out
    assert "graph.html written" in out
    assert {n["id"]: n["community"] for n in _graph(native)["nodes"]} == first
    labels = json.loads((native / "graphify-out" / ".graphify_labels.json").read_text())
    assert labels[str(top)] == "The core"
    skeleton = json.loads(server.graphlore_skeleton(community=str(top), as_json=True))
    assert skeleton["sections"]


def test_derived_community_names_are_the_engine_s(native):
    """graphlore names a graphify graph's communities by the rule the engine
    names its own with: on a native graph with its names taken off, the Python
    rule gives back every one of them."""
    server.graphlore_build(".")
    graph = _graph(native)
    nodes, edges = graph["nodes"], graph.get("links", graph.get("edges", []))
    engine = {n["community"]: n["community_name"] for n in nodes}
    bare = [{k: v for k, v in n.items() if k != "community_name"} for n in nodes]
    assert server._derived_community_names(bare, edges, sorted(engine)) == engine


def test_native_update_parses_only_what_changed(py_project):
    cache = py_project / "graphify-out" / "cache" / "graphlore-native.bin"
    assert cache.is_file()
    before = _graph(py_project)
    out = server.graphlore_build(".", update=True)
    assert "0 files parsed" in out, out
    (py_project / "pkg" / "util.py").write_text(
        "def double(x):\n    return x + x\n\ndef halve(x):\n    return x / 2\n", encoding="utf-8")
    out = server.graphlore_build(".", update=True)
    assert "update: 1 file parsed" in out, out
    ids = {n["id"] for n in _graph(py_project)["nodes"]}
    assert "pkg_util_halve" in ids and "pkg_util_unused_helper" not in ids
    # the communities keep their ids through the update
    after = {n["id"]: n["community"] for n in _graph(py_project)["nodes"]}
    kept = sum(after.get(n["id"]) == n["community"] for n in before["nodes"])
    assert kept >= 0.9 * len(before["nodes"]) - 2


def test_native_shared_state_end_to_end(tmp_path, monkeypatch):
    # put() writes CACHE, get() in another package reads it; no call between them
    files = {
        "pkg/__init__.py": "",
        "pkg/cache.py": "CACHE = {}\n",
        "pkg/a.py": "from pkg.cache import CACHE\n\n\ndef put(k, v):\n    CACHE[k] = v\n",
        "web/__init__.py": "",
        "web/b.py": "from pkg.cache import CACHE\n\n\ndef get(k):\n    return CACHE.get(k)\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    assert server.graphlore_build(".").startswith("native engine")
    nb = json.loads(server.graphlore_neighbors("CACHE", as_json=True))
    assert {(r["node"], r["relation"], r["direction"]) for r in nb["neighbors"]} >= {
        ("put()", "writes", "in"), ("get()", "reads", "in")}
    plain = json.loads(server.graphlore_impact("put", as_json=True))
    assert "get()" not in {r["node"] for r in plain["impacted"]}
    shared = json.loads(server.graphlore_impact("put", shared_state=True, as_json=True))
    assert "get()" in {r["node"] for r in shared["impacted"]}
    sp = json.loads(server.graphlore_surprises(as_json=True))
    assert [r["state"] for r in sp["shared_state"]] == ["CACHE"]
    # no call joins put() and get(): the pair is hidden
    (pair,) = sp["shared_state"][0]["pairs"]
    assert (pair["writer"], pair["other"], pair["call_path"]) == ("put()", "get()", None)
    details = json.loads(server.graphlore_node_details("put", as_json=True))
    assert details["state"]["shared_with"][0]["node"] == "get()"


def test_native_implicit_coupling_matches_the_python_reading(tmp_path, monkeypatch):
    # put() and warm() write CACHE, and nothing joins the two. get() reads it
    # and nothing joins it to either: hidden.
    # refresh() calls put(): joined. Disk.load() is reached from warm() only
    # through the method of the base it is written for: joined by dispatch.
    files = {
        "pkg/__init__.py": "",
        "pkg/cache.py": "CACHE = {}\n",
        "pkg/a.py": "from pkg.cache import CACHE\n\n\ndef put(k, v):\n    CACHE[k] = v\n",
        "pkg/store.py": (
            "from pkg.cache import CACHE\n\n\nclass Store:\n    def load(self):\n"
            "        return None\n\n\nclass Disk(Store):\n    def load(self):\n"
            "        return CACHE.get('disk')\n"),
        "web/__init__.py": "",
        "web/b.py": "from pkg.cache import CACHE\n\n\ndef get(k):\n    return CACHE.get(k)\n",
        "web/c.py": (
            "from pkg.a import put\nfrom pkg.cache import CACHE\nfrom pkg.store import Store\n\n\n"
            "def refresh():\n    put('k', 1)\n    return CACHE.get('k')\n\n\n"
            "def warm(store: Store):\n    CACHE['warm'] = 1\n    return store.load()\n"),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    out = server.graphlore_build(".")
    assert "implicit coupling: 7 pairs of definitions share state, 5 with no call path" in out, out
    graph = _graph(tmp_path)
    assert graph["graph"]["schema_version"] >= 6  # coupling pairs arrived in 6
    label = {n["id"]: n["label"] for n in graph["nodes"]}
    qual = {n["id"]: n.get("qualname") for n in graph["nodes"]}
    rel = lambda name: {(qual[e["source"]], qual[e["target"]])  # noqa: E731
                        for e in graph["links"] if e["relation"] == name}
    assert rel("dispatches_to") == {("Store.load", "Disk.load")}
    # from the definition that sees the state to the one that writes it
    assert rel("couples_with") == {
        ("get", "put"), ("Disk.load", "put"), ("warm", "put"), ("get", "warm"),
        ("refresh", "warm")}
    assert all(e["via"] == ["pkg_cache_cache"] and e["context"] == "shared_state"
               for e in graph["links"] if e["relation"] == "couples_with")
    cache = next(n for n in graph["nodes"] if n["label"] == "CACHE")
    assert (cache["coupling"]["pairs"], cache["coupling"]["hidden"]) == (7, 5)
    listed = {(label[w], label[o]): (n, fwd) for w, o, n, fwd in cache["coupling"]["listed"]}
    assert listed[("put()", "refresh()")] == (1, False)
    assert listed[("warm()", ".load()")] == (2, True)

    # the same rows whether the reader takes the engine's pairs or looks itself
    nodes, edges = server._nodes_edges(graph)
    bare = [{k: v for k, v in n.items() if k != "coupling"} for n in nodes]
    (given,), (looked,) = server._shared_state(nodes, edges), server._shared_state(bare, edges)
    assert given == looked and given["hidden_pairs"] == 5

    plain = json.loads(server.graphlore_impact("put", as_json=True))
    assert "get()" not in {r["node"] for r in plain["impacted"]}
    # a call of the base's method reaches the method written for it
    reached = json.loads(server.graphlore_impact("Disk.load", as_json=True))
    assert "warm()" in {r["node"] for r in reached["impacted"]}


def test_native_routes_and_requests_end_to_end(tmp_path, monkeypatch):
    # a FastAPI route under two prefixes from two other files, and a generated
    # TypeScript client that requests it: the tools cross the wire
    files = {
        "backend/app/core/config.py": (
            "class Settings:\n    API_V1_STR: str = \"/api/v1\"\n\n\nsettings = Settings()\n"),
        "backend/app/api/users.py": (
            "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/users\")\n\n\n"
            "@router.get(\"/{user_id}\")\ndef read_user(user_id: int):\n    return user_id\n"),
        "backend/app/main.py": (
            "from fastapi import FastAPI\n\nfrom app.api import users\n"
            "from app.core.config import settings\n\napp = FastAPI()\n"
            "app.include_router(users.router, prefix=settings.API_V1_STR)\n"),
        "frontend/src/sdk.gen.ts": (
            "import { client } from './client';\n\nexport function readUserById(options: any) {\n"
            "  return client.get({ url: '/api/v1/users/{user_id}', ...options });\n}\n"),
        "frontend/src/page.ts": (
            "import { readUserById } from './sdk.gen';\n\n"
            "export function UserPage() {\n  return readUserById({});\n}\n"),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    assert server.graphlore_build(".").startswith("native engine")
    routes = json.loads(server.graphlore_routes(as_json=True))
    assert routes["source"] == "graph"
    (row,) = routes["routes"]
    assert (row["method"], row["pattern"], row["handler"]) == (
        "GET", "/api/v1/users/{user_id}", "read_user()")
    assert row["callers"] == ["readUserById()"]
    impact = json.loads(server.graphlore_impact("read_user", as_json=True))
    assert {"GET /api/v1/users/{user_id}", "readUserById()", "UserPage()"} <= {
        r["node"] for r in impact["impacted"]}
    details = json.loads(server.graphlore_node_details("read_user", as_json=True))
    assert details["http"]["called_by"] == ["readUserById()"]


def test_native_documents_name_code_end_to_end(tmp_path, monkeypatch):
    """A document's names of code are `documents` edges (Sphinx `.txt` sources
    too); they are no dependency to the review tools, the impact walk lists the
    page that describes a definition, and graph.html draws a page's outline."""
    files = {
        "pkg/_client.py": (
            "class Client:\n    def send(self, request):\n        return request\n\n\n"
            "def unused_helper():\n    return 1\n"),
        "README.md": "# Usage\n\nCreate a `pkg.Client` and call `Client.send()`.\n",
        "docs/conf.py": "source_suffix = '.txt'\n",
        "docs/api.txt": ("API\n===\n\n.. currentmodule:: pkg\n\n.. autofunction:: unused_helper\n"),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    out = server.graphlore_build(".")
    assert "documents: 2 headings, 3 links" in out, out
    graph = _graph(tmp_path)
    assert graph["graph"]["schema_version"] == 7
    label = {n["id"]: n["label"] for n in graph["nodes"]}
    docs = {(label[e["source"]], label[e["target"]], e["confidence"], e["context"])
            for e in graph["links"] if e["relation"] == "documents"}
    assert docs == {
        ("Usage", "Client", "INFERRED", "literal"),
        ("Usage", ".send()", "INFERRED", "literal"),
        ("API", "unused_helper()", "EXTRACTED", "directive"),
    }
    # a document naming a function keeps no graph reference to it alive: its
    # name in the text is counted as a mention, as before
    unref = json.loads(server.graphlore_unreferenced(include_mentioned=True, as_json=True))
    row = next(r for r in unref["unreferenced"] if r["node"] == "unused_helper()")
    assert row["mentions"] >= 1
    # the page describing a definition is part of what changing it touches
    impact = json.loads(server.graphlore_impact("Client", as_json=True))
    assert "Usage" in {r["node"] for r in impact["impacted"]}
    # graph.html: the documents group, and document nodes flagged as such
    data = server.viz.page_data(graph)
    assert "documents" in data["groups"]
    flagged = {row[1] for row in data["nodes"] if row[6] & 8}
    assert {"README.md", "Usage", "api.txt", "API"} <= flagged
    assert "Client" not in flagged


def test_review_builds_the_graph_at_the_head_commit(tmp_path, monkeypatch):
    """With no graph, `graphlore review` builds one with the native engine at the
    commit checked out, then reads the change off it."""
    from graphlore import change_review

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def helper(x):\n    return x + 1\n\n\ndef compute(x):\n    return helper(x) * 2\n")
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.core import compute\n\n\ndef handle(x):\n    return compute(x)\n")
    (tmp_path / ".gitignore").write_text("graphify-out/\n")
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-qm", "base")
    (tmp_path / "pkg" / "core.py").write_text(
        "def helper(x):\n    return x + 2\n\n\ndef compute(x):\n    return helper(x) * 2\n")
    git("commit", "-qam", "change")
    monkeypatch.setattr(server.config, "PROJECT_DIR", tmp_path)
    monkeypatch.setenv("GRAPHLORE_ENGINE", "native")
    server._GRAPH_CACHE.clear()
    server._SPAN_CACHE.clear()

    r = change_review.analyze("HEAD~1", "HEAD")
    assert _graph(tmp_path)["built_at_commit"].startswith(r["head_commit"])
    assert [c["node"] for c in r["changed"]] == ["helper()"]
    deps = {d["node"] for d in r["dependents"]["rows"]}
    assert {"compute()", "handle()"} <= deps
