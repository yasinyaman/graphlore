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
            "func (c *Client) Get(u string) error { return nil }\n",
    "Api.java": "class Api {\n    int fetch(String u) {\n        return get(u);\n    }\n}\n",
    "Greeter.kt": "package demo\n\nclass Greeter(val name: String) {\n  @JvmStatic\n"
                  "  fun greet(): String {\n    return name\n  }\n}\n\nobject Registry {\n}\n",
    "Model.scala": "class ResponseBlob(val bytes: Array[Byte]) {\n  def size = bytes.length\n}\n",
    "Parameter.cs": "namespace Shop {\npublic record Parameter(string Name) {\n"
                    "  public int Size() {\n    return 1;\n  }\n}\n}\n",
    "lib.rs": (FIXTURES / "polyglot" / "core" / "src" / "lib.rs").read_text(encoding="utf-8"),
    "Retrier.swift": "protocol DataDecoder {\n  func decode(_ data: Data) -> Value\n}\n"
                     "class OfflineRetrier {\n  init(monitor: Monitor) {\n  }\n}\n",
    "test.c": "struct config { int port; };\n"
              "static int do_connect(struct config config) {\n  return config.port;\n}\n",
    "session.h": "namespace cpr {\nclass Session {\n public:\n  void SetUrl(const Url& url);\n"
                 "};\n}\n",
    "cookies.cpp": "const std::string& Cookie::GetDomain() const {\n  return domain_;\n}\n",
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
