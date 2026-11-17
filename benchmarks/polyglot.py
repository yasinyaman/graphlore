#!/usr/bin/env python3
"""Polyglot audit: run every graphlore tool on real repositories in 14 languages.

benchmarks/multilang.py measures one tool (graphlore_locate) on six libraries.
This script exercises the whole surface — all 33 tools, the resources and the
prompts — on real code in Python, JavaScript, TypeScript/TSX, Go, Java, Kotlin,
Scala, C#, Rust, Swift, C, C++, Ruby and PHP, and turns what it sees into a
list of findings. It needs no ground truth per repository: every check is an
invariant the tools promise (parseable JSON on every path, a cut that says what
it left out, a reported file:line that exists), a consistency rule between two
tools, or a comparison against a crude per-language oracle (a regex that knows
what a route, an import or a test looks like in that language) that turns a
silently empty answer into a finding.

Setup (one venv with graphlore[treesitter,semble] and graphifyy; no API key —
`graphify update` is a local AST build):

  python benchmarks/polyglot.py --setup --root /tmp/polyglot

Run:

  python benchmarks/polyglot.py --root /tmp/polyglot --json benchmarks/results-polyglot.json
  python benchmarks/polyglot.py --root /tmp/polyglot --only go,rust --tools routes,fetch
  python benchmarks/polyglot.py --root /tmp/polyglot --baseline benchmarks/results-polyglot.json

The read-only battery runs against the clone itself and is checked to leave
graph.json and the working tree untouched. Everything that writes (edits for
freshness/diff, prune, build, set_labels, label_communities) runs on a throwaway
copy. Findings are grouped by id; `kind` says whose they are: bug / convention
(graphlore), gap (a language or framework graphlore does not cover), upstream
(the graph the extractor built: graphify, or the native engine), perf.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from graphlore import graph as gl_graph  # noqa: E402
from graphlore import review as gl_review  # noqa: E402
from graphlore import server as s  # noqa: E402
from graphlore import spans as gl_spans  # noqa: E402

# Test regions inside source files (Rust `#[cfg(test)]`); absent from graphlore
# releases before 0.6, which this script also audits for comparison.
_test_regions = getattr(gl_spans, "_test_regions", lambda rel: [])
from graphlore.review import _Definitions  # noqa: E402

s.TOOLSET = "full"  # main() is never called, so the shared registry stays whole

# Pinned commits: a rerun measures the same code.
REPOS: list[dict[str, Any]] = [
    {"key": "httpx", "langs": ["python"], "url": "https://github.com/encode/httpx",
     "sha": "b5addb64f0161ff6bfe94c124ef76f6a1fba5254", "apis": True},
    {"key": "fastapi-template", "langs": ["python", "tsx"],
     "url": "https://github.com/fastapi/full-stack-fastapi-template",
     "sha": "aabb90126964a164e02aff39e7c385bb0598236d", "routes": ["fastapi"], "apis": True},
    {"key": "express", "langs": ["javascript"], "url": "https://github.com/expressjs/express",
     "sha": "43fca52d28a422c8be48fd489cbd4f2d6152bab6", "apis": True},
    {"key": "express-mongoose-demo", "langs": ["javascript"],
     "url": "https://github.com/madhums/node-express-mongoose-demo",
     "sha": "8a134ab5deeef553d6de51d91d4ed8ee378acace", "routes": ["express"], "apis": True},
    {"key": "got", "langs": ["typescript"], "url": "https://github.com/sindresorhus/got",
     "sha": "6b5aafc1cd538998728be6f45c65b0f80bae01b2", "apis": True},
    {"key": "nestjs-realworld", "langs": ["typescript"],
     "url": "https://github.com/lujakob/nestjs-realworld-example-app",
     "sha": "c1c2cc4e448b279ff083272df1ac50d20c3304fa", "routes": ["nestjs"], "apis": True},
    {"key": "resty", "langs": ["go"], "url": "https://github.com/go-resty/resty",
     "sha": "716987e2b60ffe0e55e6ede191b5c7854b5fba5a", "apis": True},
    {"key": "chi", "langs": ["go"], "url": "https://github.com/go-chi/chi",
     "sha": "c64ed7d6635f33a8b2feaff7dcfa55677023ac7d", "routes": ["chi"], "apis": True},
    {"key": "gin-examples", "langs": ["go"], "url": "https://github.com/gin-gonic/examples",
     "sha": "2661f002ee5de912f23b03ec293c8ab741170d5e", "routes": ["gin"], "apis": True},
    {"key": "retrofit", "langs": ["java"], "url": "https://github.com/square/retrofit",
     "sha": "7b6300dda7067085b51aa85dea7ffe951cc520d5", "apis": True},
    {"key": "spring-petclinic", "langs": ["java"],
     "url": "https://github.com/spring-projects/spring-petclinic",
     "sha": "500158f732419217507c7656904b8e6aa1bcc0d6", "routes": ["spring"], "apis": True},
    {"key": "kotlinpoet", "langs": ["kotlin"], "url": "https://github.com/square/kotlinpoet",
     "sha": "5a120729f764333857b522523c12a8f4bd1dd3fc"},
    {"key": "requests-scala", "langs": ["scala"],
     "url": "https://github.com/com-lihaoyi/requests-scala",
     "sha": "e3619c19ef551067d2979ced99b434d45bb60986"},
    {"key": "restsharp", "langs": ["csharp"], "url": "https://github.com/restsharp/RestSharp",
     "sha": "466e5f8782bd52f98df829f63a83b45fdb8472ae"},
    {"key": "ureq", "langs": ["rust"], "url": "https://github.com/algesten/ureq",
     "sha": "51bb001154f44cfab69653a74f7d45fbe1995b22"},
    {"key": "alamofire", "langs": ["swift"], "url": "https://github.com/Alamofire/Alamofire",
     "sha": "dea4bab91767a971d6e112bc955c6a82295589b7"},
    {"key": "hiredis", "langs": ["c"], "url": "https://github.com/redis/hiredis",
     "sha": "5bb7f030f6631b243438cf0fdd3be84352b905bb"},
    {"key": "cpr", "langs": ["cpp"], "url": "https://github.com/libcpr/cpr",
     "sha": "34e18e3f70152aec0767dd9b7089d86cc1b3d2bc"},
    {"key": "faraday", "langs": ["ruby"], "url": "https://github.com/lostisland/faraday",
     "sha": "565a149d4038d7adeb47bdc265ceabe59e1b403a"},
    {"key": "guzzle", "langs": ["php"], "url": "https://github.com/guzzle/guzzle",
     "sha": "f8aca40edffeae0ec508b92f4594766083c6e203"},
    # Applications picked for their routes: most implement the RealWorld API spec
    {"key": "kotlin-spring-realworld", "langs": ["kotlin"],
     "url": "https://github.com/gothinkster/kotlin-spring-realworld-example-app",
     "sha": "848c29e3abfe1bd72001cc7d7a3227aebec96e9b", "routes": ["spring"]},
    {"key": "ktor-realworld", "langs": ["kotlin"],
     "url": "https://github.com/Rudge/kotlin-ktor-realworld-example-app",
     "sha": "b9e9dec0ec03902073389edd251c4904ccbfef7a", "routes": ["ktor"]},
    {"key": "quarkus-realworld", "langs": ["java"],
     "url": "https://github.com/diegocamara/realworld-api-quarkus",
     "sha": "d4fe769be924805923764ea59f8ea72ad7b7fe89", "routes": ["jax-rs"]},
    {"key": "aspnetcore-realworld", "langs": ["csharp"],
     "url": "https://github.com/gothinkster/aspnetcore-realworld-example-app",
     "sha": "6fe67a53892213d264dc8fa244e3aaaf55f50390", "routes": ["aspnet"]},
    {"key": "todoapi", "langs": ["csharp"], "url": "https://github.com/davidfowl/TodoApi",
     "sha": "307a1eadbbd77a3004c318f2377e4818bc400af6", "routes": ["aspnet"]},
    {"key": "axum-realworld", "langs": ["rust"],
     "url": "https://github.com/launchbadge/realworld-axum-sqlx",
     "sha": "f1b25654773228297e35c292f357d33b7121a101", "routes": ["axum"]},
    {"key": "actix-realworld", "langs": ["rust"],
     "url": "https://github.com/fairingrey/actix-realworld-example-app",
     "sha": "8348aedd51e1462ed38e51c8e66fc1c1995bf756", "routes": ["actix-web"]},
    {"key": "sinatra-warden", "langs": ["ruby"],
     "url": "https://github.com/sklise/sinatra-warden-example",
     "sha": "f38454974f867de4b957c6a3a9d4f7501707cb45", "routes": ["sinatra"]},
    {"key": "rails-realworld", "langs": ["ruby"],
     "url": "https://github.com/gothinkster/rails-realworld-example-app",
     "sha": "a2ae4fff78b3337683cea988c023281dcb5551b9", "routes": ["rails"]},
    {"key": "laravel-realworld", "langs": ["php"],
     "url": "https://github.com/alexeymezenin/laravel-realworld-example-app",
     "sha": "1f482bfe92e0419ab5b9bb7c4c01b9e776c020b1", "routes": ["laravel"]},
    {"key": "symfony-demo", "langs": ["php"], "url": "https://github.com/symfony/demo",
     "sha": "f42980c690b4bf335fa41bbafdb075bb67b719c2", "routes": ["symfony"]},
]

# Extension -> the language name used in the report.
EXT_LANG = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".mts": "typescript",
    ".cts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".scala": "scala", ".cs": "csharp", ".rs": "rust", ".swift": "swift",
    ".c": "c", ".h": "c/c++ header", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hxx": "cpp", ".rb": "ruby", ".php": "php",
}
HASH_COMMENT_EXTS = {".py", ".pyi", ".rb"}

ANALYSIS_TOOLS = (
    "overview", "god_nodes", "surprises", "communities", "validate", "cycles",
    "unreferenced", "same_names", "package_apis", "routes", "freshness", "prune",
)
NODE_TOOLS = ("neighbors", "subgraph", "impact", "tests_for", "node_details", "skeleton")
# Tools the conventions say carry the analysis scope in their payload.
SCOPED_TOOLS = frozenset({
    "overview", "god_nodes", "surprises", "duplication_scan", "validate", "cycles",
    "unreferenced", "same_names", "neighbors", "subgraph", "impact",
})
QUOTING_TOOLS = frozenset({"node_details", "fetch", "skeleton"})
# The list a tool exists to return: empty means the tool had nothing to say.
PRIMARY = {
    "god_nodes": "god_nodes", "surprises": "surprises", "communities": "communities",
    "search": "matches", "neighbors": "neighbors", "subgraph": "edges",
    "impact": "impacted", "fetch": "fetched", "skeleton": "sections",
    "package_apis": "packages", "routes": "routes",
}
COUNT_KEYS = frozenset({
    "total", "count", "counts", "dropped", "not_fetched", "files_left_out",
    "files_left_out_count", "beyond_hops", "shown",
})
STATUS_RANK = {"skipped": 0, "ok": 1, "empty": 2, "degraded": 3, "error": 4, "crash": 5}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
SLOW_MS = 5000
MAX_REPLY_CHARS = 90_000

# --- per-language oracles: crude on purpose, they only have to disagree loudly ---
TEST_MARKERS = {
    ".py": r"^(import pytest|from pytest|import unittest|from unittest)",
    ".js": r"\b(describe|it|test)\s*\(\s*['\"`]",
    ".ts": r"\b(describe|it|test)\s*\(\s*['\"`]|^import test from 'ava'",
    ".tsx": r"\b(describe|it|test)\s*\(\s*['\"`]",
    ".go": r"^func (Test|Benchmark|Example|Fuzz)\w*\(",
    ".java": r"@(Test|ParameterizedTest)\b",
    ".kt": r"@(Test|ParameterizedTest)\b",
    ".scala": r"\b(test|it|describe)\s*\(\s*\"|extends \w*(Suite|Spec)\b|Tests\s*\{",
    ".cs": r"\[(Fact|Theory|Test|TestMethod)\b",
    ".rs": r"#\[(test|cfg\(test\))\]",
    ".swift": r"import XCTest|@Test\b",
    ".cpp": r"\bTEST(_F|_P)?\s*\(",
    ".rb": r"RSpec\.describe|^\s*describe\s|Minitest::Test",
    ".php": r"extends\s+\w*TestCase\b",
}
IMPORT_LINES = {
    ".kt": r"^import\s+[\w.]+", ".kts": r"^import\s+[\w.]+", ".scala": r"^import\s+[\w.]+",
    ".cs": r"^using\s+[\w.]+\s*;", ".rs": r"^\s*use\s+[\w:]+", ".swift": r"^import\s+\w+",
    ".c": r"^\s*#\s*include\s*<", ".h": r"^\s*#\s*include\s*<", ".cpp": r"^\s*#\s*include\s*<",
    ".hpp": r"^\s*#\s*include\s*<", ".rb": r"^\s*require\s+['\"]",
    ".php": r"^use\s+[\w\\]+", ".py": r"^\s*(import|from)\s+\w+",
    ".js": r"\brequire\(\s*['\"]|^import\s", ".ts": r"^import\s", ".tsx": r"^import\s",
    ".go": r"^import\s|^\s+\"[\w./-]+\"$", ".java": r"^import\s+[\w.]+",
}
ROUTE_IDIOMS = {
    ".py": r"@\w+\.(get|post|put|delete|patch|route)\(|\bpath\(\s*['\"]",
    ".js": r"\b\w+\.(get|post|put|delete|patch|all)\(\s*['\"`]/",
    ".ts": r"@(Get|Post|Put|Delete|Patch)\(|\b\w+\.(get|post|put|delete|patch)\(\s*['\"`]/",
    ".go": r"\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch|HandleFunc)\(\s*\"/",
    ".java": r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b",
    ".kt": r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b|\b(get|post|put|delete)\(\s*\"/",
    ".scala": r"\b(get|post|put|delete)\(\s*\"/|@(cask\.)?(get|post)\(",
    ".cs": r"\[Http(Get|Post|Put|Delete|Patch)\b|\.Map(Get|Post|Put|Delete|Patch)\(\s*\"",
    ".rs": r"#\[(get|post|put|delete|patch)\(|\.route\(\s*\"",
    ".swift": r"\b\w+\.(get|post|put|delete)\(\s*\"",
    ".rb": r"^\s*(get|post|put|delete|patch)\s+(['\"]/|:\w)|^\s*resources?\s+:",
    ".php": r"Route::(get|post|put|delete|patch|resource|apiResource)\(|#\[Route\(",
}


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot > 0 else ""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True).stdout


def _reset(project_dir: Path) -> None:
    s.config.PROJECT_DIR = project_dir
    s._GRAPH_CACHE.clear()
    s._SPAN_CACHE.clear()
    s._TS_PARSERS.clear()


def _graphify() -> str:
    beside = Path(sys.executable).parent / "graphify"
    return str(beside) if beside.exists() else (shutil.which("graphify") or "graphify")


def setup(root: Path, repos: list[dict[str, Any]]) -> None:
    """Fetch each pinned commit and build its graph (local AST extraction)."""
    root.mkdir(parents=True, exist_ok=True)
    for repo in repos:
        d = root / repo["key"]
        if not (d / ".git").exists():
            d.mkdir(parents=True, exist_ok=True)
            _git(d, "init", "-q")
            _git(d, "remote", "add", "origin", repo["url"])
            _git(d, "fetch", "-q", "--depth", "1", "origin", repo["sha"])
            _git(d, "checkout", "-q", "FETCH_HEAD")
        with (d / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
            fh.write("graphify-out/\n")
        if not (d / "graphify-out" / "graph.json").exists():
            res = subprocess.run([_graphify(), "update", "."], cwd=str(d),
                                 capture_output=True, text=True)
            tail = (res.stdout + res.stderr).strip().splitlines()[-3:]
            print(f"built {repo['key']}: rc={res.returncode} {' | '.join(tail)[:200]}")
        else:
            print(f"ready {repo['key']}")


class Audit:
    """Collects findings, the repo x tool status matrix and timings."""

    def __init__(self) -> None:
        self.repo = ""
        self.findings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.matrix: dict[str, dict[str, str]] = defaultdict(dict)
        self.timings: dict[str, dict[str, float]] = defaultdict(dict)
        self.metrics: dict[str, dict[str, Any]] = defaultdict(dict)
        self._line_counts: dict[Path, int] = {}

    # -- recording ------------------------------------------------------------
    def finding(self, fid: str, severity: str, kind: str, tool: str, summary: str,
                sample: Any = None, lang: str = "") -> None:
        key = (fid, self.repo, tool, lang)
        f = self.findings.setdefault(key, {
            "id": fid, "severity": severity, "kind": kind, "tool": tool, "repo": self.repo,
            "lang": lang, "summary": summary, "count": 0, "samples": [],
        })
        f["count"] += 1
        if sample is not None and len(f["samples"]) < 4:
            f["samples"].append(sample)

    def status(self, tool: str, status: str) -> None:
        row = self.matrix[self.repo]
        if STATUS_RANK[status] > STATUS_RANK.get(row.get(tool, "skipped"), 0):
            row[tool] = status

    # -- one tool call --------------------------------------------------------
    def call(self, tool: str, expect_error: bool = False, **kw: Any) -> dict[str, Any] | None:
        """Call a tool (as_json=True where it takes it); None on a crash or bad JSON.

        ``expect_error`` marks a probe: its outcome is checked like any other but
        does not count towards the tool's status for the repository.
        """
        fn = getattr(s, "graphlore_" + tool)
        as_json = kw.pop("as_json", True) and "as_json" in inspect.signature(fn).parameters
        if as_json:
            kw["as_json"] = True
        buf = io.StringIO()
        shown = {k: v for k, v in kw.items() if k != "as_json"}
        t0 = time.perf_counter()
        try:
            with contextlib.redirect_stdout(buf):
                out = fn(**kw)
        except Exception as exc:  # noqa: BLE001 - a crash is the finding
            tb = traceback.format_exc().strip().splitlines()
            where = next((ln.strip() for ln in reversed(tb) if ln.strip().startswith("File")), "")
            self.finding("CRASH", "high", "bug", tool,
                         f"{type(exc).__name__}: {str(exc)[:120]}",
                         {"args": shown, "at": where[-160:]})
            self.status(tool, "crash")
            return None
        ms = (time.perf_counter() - t0) * 1000
        prev = self.timings[self.repo].get(tool, 0.0)
        self.timings[self.repo][tool] = max(prev, round(ms, 1))
        if ms > SLOW_MS:
            self.finding("SLOW", "low", "perf", tool, f"took over {SLOW_MS} ms",
                         {"args": shown, "ms": round(ms)})
        if buf.getvalue().strip():
            self.finding("STDOUT-WRITE", "high", "bug", tool,
                         "wrote to stdout during a call (stdout is the protocol on stdio)",
                         {"args": shown, "stdout": buf.getvalue().strip()[:160]})
        if not isinstance(out, str) or not out.strip():
            self.finding("EMPTY-REPLY", "high", "bug", tool, "returned no text", {"args": shown})
            self.status(tool, "crash")
            return None
        asked_for_all = any(isinstance(x, int) and x >= 200 for x in shown.values())
        if len(out) > MAX_REPLY_CHARS and not asked_for_all:
            self.finding("REPLY-TOO-LARGE", "medium", "bug", tool,
                         f"a reply over {MAX_REPLY_CHARS // 1000} KB with default-sized "
                         "arguments (some 25,000 tokens and up)",
                         {"args": shown, "kb": len(out) // 1024})
        if not as_json:
            if not expect_error:
                self.status(tool, "ok")
            return {"text": out}
        try:
            payload = json.loads(out)
        except ValueError:
            self.finding("NOT-JSON", "high", "bug", tool, "as_json=True reply is not JSON",
                         {"args": shown, "reply": out[:160]})
            self.status(tool, "crash")
            return None
        if not isinstance(payload, dict):
            self.finding("NOT-JSON", "high", "bug", tool, "as_json=True reply is not an object",
                         {"args": shown, "reply": out[:160]})
            return None
        if "error" in payload:
            if not isinstance(payload["error"], str):
                self.finding("ERROR-NOT-STRING", "medium", "convention", tool,
                             "`error` is not a string", {"args": shown})
            if not expect_error:
                self.status(tool, "error")
            return payload
        self._generic(tool, shown, payload)
        if not expect_error:  # a probe with hostile arguments says nothing about coverage
            primary = PRIMARY.get(tool)
            self.status(tool, "empty" if primary and not payload.get(primary) else "ok")
        return payload

    def text(self, tool: str, expect_error: bool = False, **kw: Any) -> str:
        got = self.call(tool, expect_error=expect_error, as_json=False, **kw)
        return got["text"] if got else ""

    # -- invariants every reply must hold -------------------------------------
    def _generic(self, tool: str, args: dict[str, Any], p: dict[str, Any]) -> None:
        if p.get("truncated") is True:
            if not isinstance(p.get("hint"), str):
                self.finding("TRUNCATED-NO-HINT", "medium", "convention", tool,
                             "`truncated` without a `hint` on how to get the rest",
                             {"args": args, "keys": sorted(p)[:14]})
            if not any(k in COUNT_KEYS or k.endswith(("_count", "_left_out")) for k in p):
                self.finding("TRUNCATED-NO-COUNT", "medium", "convention", tool,
                             "`truncated` without a count of what was left out",
                             {"args": args, "keys": sorted(p)[:14]})
        if tool in SCOPED_TOOLS and not (
                isinstance(p.get("scope"), dict) and "left_out" in p["scope"]):
            self.finding("SCOPE-MISSING", "medium", "convention", tool,
                         "no `scope` payload", {"args": args})
        if tool in QUOTING_TOOLS and (
                p.get("content_trust") != s._CONTENT_TRUST or not p.get("untrusted_fields")):
            self.finding("TRUST-MISSING", "medium", "convention", tool,
                         "quotes repository text without content_trust/untrusted_fields",
                         {"args": args})
        self._walk_locations(tool, p)

    def _lines(self, rel: str) -> int | None:
        path = s.config.PROJECT_DIR / rel
        if path not in self._line_counts:
            try:
                self._line_counts[path] = path.read_bytes().count(b"\n") + 1
            except OSError:
                self._line_counts[path] = -1
        n = self._line_counts[path]
        return None if n < 0 else n

    def _walk_locations(self, tool: str, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._walk_locations(tool, item)
            return
        if not isinstance(value, dict):
            return
        rel, line = value.get("file"), value.get("line")
        if isinstance(rel, str) and rel and isinstance(line, int) and not isinstance(line, bool):
            total = self._lines(rel)
            if total is None:
                self.finding("FILE-MISSING", "medium", "bug", tool,
                             "reports a file that does not exist in the project", rel)
            elif not 0 <= line <= total:
                self.finding("LINE-OUT-OF-RANGE", "medium", "bug", tool,
                             "reports a line beyond the end of the file",
                             f"{rel}:{line} (file has {total})", _ext(rel))
        for item in value.values():
            self._walk_locations(tool, item)


class Subject:
    """What to ask the node-centric tools about, picked from the graph itself."""

    def __init__(self, nodes: list[dict], edges: list[dict]) -> None:
        self.nodes, self.edges = nodes, edges
        self.defs = _Definitions(nodes, edges)
        self.byid = self.defs.byid
        adj = s._adjacency(edges)
        self.degree = {nid: len(adj.get(nid, ())) for nid in self.byid}
        self.labels = Counter(s._node_label(n) for n in nodes)

        def ranked(kind: str, tests: bool = False) -> list[str]:
            ids = [i for i, k in self.defs.kind.items() if k == kind
                   and s._is_test_path(s._node_file(self.byid[i])) == tests
                   and not s._is_vendored_file(s._node_file(self.byid[i]))]
            return sorted(ids, key=lambda i: (-self.degree[i], i))

        self.functions, self.classes, self.methods = (
            ranked("function"), ranked("class"), ranked("method"))
        self.test_nodes = ranked("function", tests=True) or ranked("method", tests=True)
        self.by_ext: dict[str, list[str]] = defaultdict(list)
        for nid in sorted(self.defs.kind, key=lambda i: (-self.degree[i], i)):
            f = s._node_file(self.byid[nid])
            if not s._is_vendored_file(f):
                self.by_ext[_ext(f)].append(nid)

    def hubs(self) -> list[str]:
        """Up to one function, class and method per language, by degree."""
        out: list[str] = []
        seen: set[tuple[str, str]] = set()
        for ids in (self.functions, self.classes, self.methods):
            for nid in ids:
                key = (self.defs.kind[nid], _ext(s._node_file(self.byid[nid])))
                if key not in seen:
                    seen.add(key)
                    out.append(nid)
        return out[:12]

    def tested(self) -> str | None:
        for nid in self.functions + self.methods:
            for src, edge in self.defs.incoming.get(nid, ()):
                n = self.byid.get(src)
                if (n is not None and s._rel(edge) == "calls" and src in self.defs.kind
                        and s._is_test_path(s._node_file(n))):
                    return nid
        return None

    def ambiguous(self) -> str | None:
        for nid in self.functions + self.classes:
            if self.labels[s._node_label(self.byid[nid])] > 1:
                return s._node_label(self.byid[nid])
        return None

    def near_miss(self) -> tuple[str, str] | None:
        """A unique long name with two letters swapped, and the name it should suggest."""
        for nid in self.functions + self.classes:
            name = s._bare_name(s._node_label(self.byid[nid]))
            if len(name) >= 8 and name.isidentifier():
                typo = name[:3] + name[4] + name[3] + name[5:]
                if typo != name and not any(typo.lower() in lbl.lower() for lbl in self.labels):
                    return typo, nid
        return None


# --- checks ---------------------------------------------------------------------
def check_orientation(a: Audit, sub: Subject) -> None:
    ov = a.call("overview")
    gods = a.call("god_nodes")
    if ov and gods and "error" not in ov:
        degs = [g.get("degree", 0) for g in gods.get("god_nodes", [])]
        if degs != sorted(degs, reverse=True):
            a.finding("GOD-NODES-UNSORTED", "medium", "bug", "god_nodes", "not sorted by degree")
        if any(g.get("type") == "" for g in gods.get("god_nodes", [])):
            a.finding("EMPTY-TYPE-FIELD", "low", "bug", "god_nodes",
                      "every row carries `type: \"\"` (graphify >= 0.9 writes file_type)",
                      gods["god_nodes"][0])
        for hint in ov.get("suggested_next", []):
            for name in re.findall(r"graphlore_\w+", hint):
                if not hasattr(s, name):
                    a.finding("SUGGESTS-UNKNOWN-TOOL", "medium", "bug", "overview",
                              "suggested_next names a tool that does not exist", hint)
    a.call("god_nodes", top_n=3, scope="all", expect_error=True)
    sp = a.call("surprises")
    if sp and "error" not in sp and sp.get("basis") not in ("flagged", "computed", "none"):
        a.finding("SURPRISES-BASIS", "low", "bug", "surprises", "unexpected basis",
                  sp.get("basis"))
    if sp and "error" not in sp:
        hist = sp.get("history") or {}
        # a history too short to tell what changes together must say so
        if hist.get("commits", 0) < 10 and not hist.get("note"):
            a.finding("HISTORY-SILENT", "medium", "convention", "surprises",
                      "a short or missing git history is not named", hist)
        if sp.get("co_change") and hist.get("commits", 0) < 10:
            a.finding("CO-CHANGE-WITHOUT-HISTORY", "medium", "bug", "surprises",
                      "files changing together listed from a history too short", hist)
    a.call("surprises", limit=1, expect_error=True)
    com = a.call("communities")
    if com and com.get("communities"):
        with_file = {s._node_label(n) for n in sub.nodes if s._node_file(n)}
        dup = [lbl for lbl, c in Counter(com["communities"][0].get("members", [])).items()
               if c > 1 and lbl in with_file]
        if dup:
            a.finding("AMBIGUOUS-LABELS", "low", "convention", "communities",
                      "member list repeats a bare label; rows are not qualified", dup[:4])
    name = s._bare_name(s._node_label(sub.byid[(sub.functions or sub.classes or [""])[0]])) \
        if (sub.functions or sub.classes) else "a"
    found = a.call("search", pattern=name[:5])
    if found and "error" not in found and not found.get("matches"):
        a.finding("SEARCH-MISS", "medium", "bug", "search",
                  "a prefix of an existing label found nothing", name[:5])
    a.call("search", pattern=name[:2], limit=1, expect_error=True)
    a.call("search", pattern="zz_qx9_none", expect_error=True)
    a.call("project", path="")


def check_navigation(a: Audit, sub: Subject) -> None:
    resolved = s._resolved(sub.nodes, sub.edges).edges
    rows_of: dict[str, set[tuple[str, str, bool]]] = defaultdict(set)
    for e in resolved:
        x, y = s._edge_ends(e)
        rows_of[x].add((y, s._edge_rel(e), True))
        if x != y:
            rows_of[y].add((x, s._edge_rel(e), False))
    touching = {nid: len(rows) for nid, rows in rows_of.items()}
    for nid in sub.hubs():
        label = s._node_label(sub.byid[nid])
        nb = a.call("neighbors", node=nid)
        if nb and "error" not in nb:
            rows = nb.get("neighbors", [])
            if len(rows) != touching.get(nid, 0):
                a.finding("NEIGHBORS-COUNT", "low", "bug", "neighbors",
                          "row count differs from the edges touching the node",
                          {"node": label, "rows": len(rows), "edges": touching.get(nid, 0)})
        sg = a.call("subgraph", node=nid, hops=1, budget_tokens=10 ** 9)
        if nb and sg and "error" not in nb and "error" not in sg:
            sg_ends = {e.get("from") for e in sg.get("edges", [])} | {
                e.get("to") for e in sg.get("edges", [])}
            missing = {r["node"] for r in nb.get("neighbors", [])} - sg_ends
            if missing:
                a.finding("SUBGRAPH-MISSES-NEIGHBOR", "medium", "bug", "subgraph",
                          "hops=1 with an unlimited budget leaves out a 1-hop neighbor",
                          {"node": label, "missing": sorted(missing)[:3]})
        a.call("subgraph", node=nid, hops=2, budget_tokens=60, expect_error=True)
        for direction in ("dependents", "dependencies"):
            imp = a.call("impact", node=nid, direction=direction, hops=1,
                         budget_tokens=10 ** 9)
            if imp and "error" not in imp:
                if imp.get("count") != len(imp.get("impacted", [])) and not imp.get("truncated"):
                    a.finding("IMPACT-COUNT", "medium", "bug", "impact",
                              "`count` differs from the rows returned",
                              {"node": label, "count": imp.get("count")})
                if sum((imp.get("relation_counts") or {}).values()) != imp.get("count"):
                    a.finding("IMPACT-RELATION-COUNTS", "low", "bug", "impact",
                              "relation_counts do not add up to count", {"node": label})
        a.call("impact", node=nid, hops=3, budget_tokens=40, expect_error=True)
        a.call("impact", node=nid, relations="zz", expect_error=True)
        tf = a.call("tests_for", node=nid, limit=200)
        if tf and "error" not in tf:
            for row in tf.get("direct", []) + tf.get("through_one_caller", []):
                rel, at = row.get("file", ""), int(row.get("line") or 0)
                inline = any(lo <= at <= hi for lo, hi in _test_regions(rel))
                if not (s._is_test_path(rel) or inline):
                    a.finding("TESTS-FOR-NOT-A-TEST", "medium", "bug", "tests_for",
                              "a row lies outside every test path",
                              {"node": label, "row": row.get("file")}, _ext(row.get("file", "")))
        a.call("tests_for", node=nid, limit=1, expect_error=True)
        det = a.call("node_details", node=nid)
        if det and "error" not in det and det.get("id") != nid:
            a.finding("DETAILS-WRONG-NODE", "high", "bug", "node_details",
                      "asked by id, got another node", {"asked": nid, "got": det.get("id")})

    tested = sub.tested()
    if tested:
        tf = a.call("tests_for", node=tested)
        if tf and "error" not in tf and not tf.get("direct_count"):
            a.finding("TESTS-FOR-EMPTY", "medium", "bug", "tests_for",
                      "a node with an incoming edge from a test file has no direct tests",
                      s._node_label(sub.byid[tested]),
                      _ext(s._node_file(sub.byid[tested])))
    amb = sub.ambiguous()
    if amb:
        got = a.call("node_details", node=amb)
        if got and "error" not in got and "matched" not in got:
            a.finding("AMBIGUOUS-SILENT", "medium", "convention", "node_details",
                      "an ambiguous name resolved without a `matched` report", amb)
    miss = sub.near_miss()
    for tool in NODE_TOOLS:
        got = a.call(tool, node="zz_qx9_none", expect_error=True)
        if got is not None and got.get("code") != "not_found":
            a.finding("MISS-NO-CODE", "medium", "convention", tool,
                      "a miss does not answer with code=not_found", sorted(got)[:8])
        if miss:
            got = a.call(tool, node=miss[0], expect_error=True)
            want = s._bare_name(s._node_label(sub.byid[miss[1]])).lower()
            names = [s._bare_name(d.get("label", "")).lower()
                     for d in (got or {}).get("did_you_mean", [])]
            if got is not None and want not in names:
                a.finding("MISS-NO-SUGGESTION", "low", "bug", tool,
                          "a two-letter typo does not suggest the real name",
                          {"typo": miss[0], "wanted": want, "got": names[:3]})
    got = a.call("fetch", nodes=["zz_qx9_none"], expect_error=True)
    if got is not None and "not_found" not in got and got.get("code") != "not_found":
        a.finding("MISS-NO-CODE", "medium", "convention", "fetch",
                  "an all-miss fetch names neither not_found nor a code", sorted(got)[:8])


def check_hostile(a: Audit, sub: Subject) -> None:
    """Arguments nobody should pass still get parseable JSON back."""
    nid = (sub.functions or sub.classes or sub.methods or [""])[0]
    for kw in ({"limit": 0}, {"limit": -1}):
        for tool in ("surprises", "validate", "unreferenced", "same_names",
                     "package_apis", "routes"):
            a.call(tool, expect_error=True, **kw)
        a.call("search", pattern="a", expect_error=True, **kw)
        a.call("tests_for", node=nid, expect_error=True, **kw)
    a.call("god_nodes", top_n=0, expect_error=True)
    a.call("god_nodes", top_n=-3, expect_error=True)
    a.call("overview", top_n=0, expect_error=True)
    a.call("cycles", max_cycles=0, expect_error=True)
    a.call("cycles", kind="bogus", expect_error=True)
    a.call("search", pattern="", expect_error=True)
    a.call("search", pattern="[", expect_error=True)
    a.call("search", pattern="日本語", expect_error=True)
    for tool in ("neighbors", "node_details", "tests_for"):
        a.call(tool, node="", expect_error=True)
    a.call("subgraph", node=nid, hops=0, budget_tokens=0, expect_error=True)
    a.call("subgraph", node=nid, hops=-1, expect_error=True)
    a.call("impact", node=nid, hops=0, expect_error=True)
    a.call("impact", node=nid, direction="sideways", expect_error=True)
    a.call("fetch", nodes=[], expect_error=True)
    a.call("fetch", nodes=[nid], budget_tokens=0, expect_error=True)
    a.call("fetch", nodes=[nid], context_lines=-5, expect_error=True)
    a.call("skeleton", expect_error=True)
    a.call("skeleton", file="../../etc/passwd", expect_error=True)
    a.call("skeleton", file="/etc/passwd", expect_error=True)
    a.call("skeleton", community="999999", expect_error=True)
    a.call("layers", expect_error=True)
    a.call("layers", rules={}, expect_error=True)
    a.call("layers", rules_file="../../etc/passwd", expect_error=True)
    a.call("same_names", kinds="bogus", expect_error=True)
    a.call("god_nodes", scope="+bogus", expect_error=True)
    a.call("diff", ref_a="nope", ref_b="HEAD", expect_error=True)
    a.call("routes", framework="bogus", expect_error=True)
    a.call("package_apis", package="zz_qx9_none", expect_error=True)
    a.call("duplication_scan", node_budget=0, expect_error=True)
    a.call("locate", query="", expect_error=True)


def check_source(a: Audit, sub: Subject, sample: int) -> None:
    """fetch + skeleton across a sample of definitions per extension."""
    span = a.metrics[a.repo].setdefault("span", {})
    for ext, ids in sorted(sub.by_ext.items()):
        m: Counter[str] = Counter()
        for nid in ids[:sample]:
            n = sub.byid[nid]
            name = s._bare_name(s._node_label(n))
            got = a.call("fetch", nodes=[nid], budget_tokens=10 ** 6)
            if not got or "error" in got or not got.get("fetched"):
                m["not_fetched"] += 1
                continue
            row = got["fetched"][0]
            m["fetched"] += 1
            code = row.get("code", "")
            where = f"{row.get('file')}:{row.get('line')} {s._node_label(n)}"
            if row.get("spanned"):
                m["spanned"] += 1
            else:
                a.finding("FETCH-NO-SPAN", "medium", "gap", "fetch",
                          "a definition has no span: fetch falls back to a line window",
                          where, ext)
            first, _, last = str(row.get("lines", "")).partition("-")
            lines = _read(row.get("file", "")).splitlines()
            if first.isdigit() and last.isdigit() and lines:
                want = "\n".join(lines[int(first) - 1:int(last)])
                if want.strip() != code.strip():
                    a.finding("FETCH-CODE-MISMATCH", "high", "bug", "fetch",
                              "`code` is not the file slice `lines` names", where, ext)
            qual = (row.get("qualname") or "").rsplit(".", 1)[-1]
            starts_here = [sp for sp in s._spans_for_file(row.get("file", ""))
                           if sp[2] == row.get("line")]
            if row.get("spanned") and qual and qual != name and starts_here:
                m["shadow_span"] += 1
                a.finding("FETCH-SHADOW-SPAN", "high", "bug", "fetch",
                          "a span that starts on the node's line belongs to another symbol",
                          {"node": where, "qualname": row.get("qualname"),
                           "lines": row.get("lines")}, ext)
            elif row.get("spanned") and qual and qual != name:
                m["enclosing_span"] += 1
                a.finding("FETCH-ENCLOSING-SPAN", "low", "gap", "fetch",
                          "the node has no span of its own: fetch returns the definition "
                          "around it", {"node": where, "qualname": row.get("qualname"),
                                        "lines": row.get("lines")}, ext)
            elif row.get("spanned") and name and name not in code:
                m["name_absent"] += 1
                a.finding("FETCH-WRONG-CODE", "high", "bug", "fetch",
                          "the fetched code does not contain the node's name", where, ext)
            if (row.get("spanned") and sub.defs.kind[nid] != "class" and first == last
                    and len(lines) >= int(first or 0) > 0
                    and lines[int(first) - 1].rstrip().endswith(("{", ":", "("))):
                m["one_line"] += 1
                a.finding("FETCH-ONE-LINE-SPAN", "high", "bug", "fetch",
                          "a multi-line definition was fetched as its first line only",
                          {"node": where, "line": lines[int(first) - 1].strip()[:90]}, ext)
        files = Counter(s._node_file(sub.byid[i]) for i in ids)
        for rel, _ in files.most_common(4):
            sk = a.call("skeleton", file=rel, budget_tokens=10 ** 6)
            if not sk or "error" in sk:
                continue
            symbols = [sym for sec in sk.get("sections", []) for sym in sec.get("symbols", [])]
            m["skeleton_files"] += 1
            if not symbols:
                m["skeleton_empty"] += 1
                a.finding("SKELETON-EMPTY", "medium", "gap", "skeleton",
                          "a file with definition nodes yields no symbols", rel, ext)
            for sym in symbols:
                m["skeleton_symbols"] += 1
                last_seg = sym.get("qualname", "").rsplit(".", 1)[-1]
                header = sym.get("header", "")
                if last_seg and last_seg not in header:
                    m["header_without_name"] += 1
                    a.finding("SKELETON-HEADER-NO-NAME", "medium", "bug", "skeleton",
                              "a symbol's header does not show its name",
                              {"file": rel, "qualname": sym.get("qualname"),
                               "header": header[-120:]}, ext)
        if m:
            span[ext] = dict(m)
    if sub.classes:
        a.call("skeleton", node=sub.classes[0], budget_tokens=60, expect_error=True)
    a.call("fetch", nodes=(sub.functions + sub.methods)[:10], budget_tokens=80,
           expect_error=True)
    a.call("fetch", nodes=(sub.functions + sub.classes)[:1], context_lines=3)


def check_review(a: Audit, sub: Subject) -> None:
    val = a.call("validate")
    if val and "error" not in val:
        if val.get("healthy") != (val.get("total_issues") == 0):
            a.finding("VALIDATE-HEALTHY", "medium", "bug", "validate",
                      "`healthy` disagrees with total_issues")
        issues = val.get("issues") or {}
        if sum(issues.values()) != val.get("total_issues"):
            a.finding("VALIDATE-SUM", "low", "bug", "validate", "issue counts do not add up")
        for kind in ("dangling_edges", "duplicate_edges", "orphan_nodes"):
            if issues.get(kind):
                a.finding("GRAPH-" + kind.upper().replace("_", "-"), "info", "upstream",
                          "validate", f"the graph the extractor built has {kind}", issues[kind])
    a.call("validate", scope="all", limit=1)
    for kind in ("all", "imports", "calls", "other"):
        cyc = a.call("cycles", kind=kind, max_cycles=5)
        if cyc and "error" not in cyc:
            for c in cyc.get("cycles", []):
                path = c.get("path") or []
                if path and path[0] != path[-1]:
                    a.finding("CYCLE-NOT-CLOSED", "medium", "bug", "cycles",
                              "a cycle path does not return to its start", path[:4])
    a.call("cycles", max_cycles=1)
    un = a.call("unreferenced")
    if un and "error" not in un:
        total = (sum((un.get("registered_by_decorator") or {}).values())
                 + un.get("mentioned_elsewhere", 0) + un.get("unreferenced_count", 0))
        if total != un.get("no_graph_reference"):
            a.finding("UNREFERENCED-SUM", "low", "bug", "unreferenced",
                      "the breakdown does not add up to no_graph_reference",
                      {"sum": total, "no_graph_reference": un.get("no_graph_reference")})
        if not un.get("examined"):
            a.finding("UNREFERENCED-BLIND", "medium", "gap", "unreferenced",
                      "examined no definitions in a code graph")
        a.metrics[a.repo]["unreferenced"] = {
            k: un.get(k) for k in ("examined", "no_graph_reference", "mentioned_elsewhere",
                                   "unreferenced_count", "registered_by_decorator")}
        in_tests = 0
        for row in un.get("unreferenced", []):
            rel, line = row.get("file", ""), row.get("line") or 0
            if not s._is_test_path(rel):
                continue
            in_tests += 1
            # a test the runner collects is not dead code: the lines around the
            # definition carry the language's test marker
            pat = TEST_MARKERS.get(_ext(rel))
            around = "\n".join(_read(rel).splitlines()[max(0, line - 3):line + 1])
            if pat and re.search(pat, around, re.M):
                a.finding("UNREFERENCED-LISTS-A-TEST", "medium", "bug", "unreferenced",
                          "lists a test the runner collects as dead code",
                          f"{rel}:{line} {row.get('node')}", _ext(rel))
        a.metrics[a.repo]["unreferenced"]["rows_in_test_files"] = in_tests
    a.call("unreferenced", include_mentioned=True, limit=1)
    a.call("unreferenced", count_test_references=False, scope="-tests")
    sn = a.call("same_names")
    if sn and "error" not in sn:
        for row in sn.get("names", []):
            if len({d.get("file") for d in row.get("definitions", [])}) < 2:
                a.finding("SAME-NAMES-ONE-FILE", "low", "bug", "same_names",
                          "a group spans fewer than two files", row.get("name"))
        a.metrics[a.repo]["same_names"] = sn.get("name_count")
    a.call("same_names", across_languages=True, scope="+tests", limit=1)
    # layers: rule built from the two directories with the most imports between them.
    checker = s.ImportChecker()
    pairs = Counter((gl_graph._top_dir(sf), gl_graph._top_dir(tf))
                    for sf, tf, *_ in s._file_import_edges(sub.nodes, sub.edges, checker.kind)
                    if "." not in {gl_graph._top_dir(sf), gl_graph._top_dir(tf)}
                    and gl_graph._top_dir(sf) != gl_graph._top_dir(tf))
    if pairs:
        (src, dst), expected = pairs.most_common(1)[0]
        lay = a.call("layers", rules={src: [], dst: ["*"]})
        if lay and "error" not in lay and not lay.get("violation_count"):
            a.finding("LAYERS-BLIND", "medium", "bug", "layers",
                      "a rule forbidding the busiest cross-directory import finds nothing",
                      {"rule": f"{src} may import nothing", "edges": expected})
    else:
        a.call("layers", rules={"src": []})
        a.status("layers", "empty")
    dup = a.call("duplication_scan", node_budget=8, max_pairs=5)
    if dup and "error" not in dup:
        if dup.get("semantic_tier") != "ran":
            a.status("duplication_scan", "degraded")
        if dup.get("seeded_by") == "degree":
            a.finding("DUPLICATION-NO-DEFINITIONS", "medium", "gap", "duplication_scan",
                      "seeded by degree: no definition sizes were known")
        a.metrics[a.repo]["identical_groups"] = dup.get("identical_group_count")


def _read(rel: str) -> str:
    try:
        return (s.config.PROJECT_DIR / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def check_languages(a: Audit, sub: Subject, repo: dict[str, Any]) -> None:
    """Coverage probes: where a language gets a silently empty answer."""
    files = sorted({s._node_file(n) for n in sub.nodes
                    if s._node_file(n) and _ext(s._node_file(n)) in EXT_LANG
                    and not s._is_vendored_file(s._node_file(n))})
    by_ext: dict[str, list[str]] = defaultdict(list)
    for rel in files:
        by_ext[_ext(rel)].append(rel)
    m = a.metrics[a.repo]

    # tests: does the path rule agree with what the file contains?
    tests: dict[str, dict[str, Any]] = {}
    for ext, rels in by_ext.items():
        pat = TEST_MARKERS.get(ext)
        if not pat:
            continue
        rx = re.compile(pat, re.M)
        c: Counter[str] = Counter()
        missed: list[str] = []
        for rel in rels:
            # a Rust file that holds its tests inline is recognised by its regions
            marked = bool(rx.search(_read(rel)))
            path = s._is_test_path(rel) or bool(_test_regions(rel))
            c[f"marker={int(marked)},path={int(path)}"] += 1
            if marked and not path:
                missed.append(rel)
        tests[ext] = dict(c)
        if missed:
            a.finding("TEST-PATH-MISSED", "medium", "gap", "tests_for",
                      "files that contain tests are not recognised as test files",
                      {"files": len(missed), "of": len(rels), "examples": missed[:4]}, ext)
    m["test_paths"] = tests

    # imports: how many import edges per language can be classified at all.
    checker = s.ImportChecker()
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    for sf, _tf, _e, _dst, kind in s._file_import_edges(sub.nodes, sub.edges, checker.kind):
        kinds[_ext(sf)][kind] += 1
    for row in checker.rejected:
        # graphify links `import x from "pkg"` to the package.json that declares
        # pkg: rejecting that is the checker doing its job, so it is counted apart
        manifest = str(row.get("to_file", "")).endswith("package.json")
        kinds[_ext(str(row.get("from_file", "")))][
            "rejected_manifest" if manifest else "rejected"] += 1
    m["imports"] = {ext: dict(c) for ext, c in kinds.items()}
    for ext, c in kinds.items():
        total = sum(v for k, v in c.items() if not k.startswith("rejected"))
        if total >= 10 and c.get("unclassified", 0) == total:
            a.finding("IMPORTS-UNCLASSIFIED", "low", "gap", "cycles",
                      "every import edge of this language is unclassified",
                      {"edges": total}, ext)
        if c.get("rejected", 0) > max(5, total * 0.05):
            a.finding("IMPORTS-REJECTED", "medium", "bug", "cycles",
                      "over 5% of this language's import edges are rejected as "
                      "contradicted by the source", dict(c), ext)

    # package_apis: files that import something external, and what the extractor saw.
    apis: dict[str, dict[str, int]] = {}
    for ext, rels in by_ext.items():
        rx = re.compile(IMPORT_LINES[ext], re.M) if ext in IMPORT_LINES else None
        with_imports = sum(1 for rel in rels if rx and rx.search(_read(rel)))
        seen = sum(1 for rel in rels if s._api_uses_for_file(rel)[0])
        apis[ext] = {"files": len(rels), "with_import_lines": with_imports, "extracted": seen}
    m["package_apis"] = apis
    pa = a.call("package_apis", limit=10 ** 6)
    for ext, row in apis.items():
        if row["with_import_lines"] >= 3 and not row["extracted"]:
            if ext in ((pa or {}).get("not_read") or {}):
                a.finding("APIS-LANGUAGE-NOT-READ", "info", "gap", "package_apis",
                          "no package-API extractor for this language (the reply says so)",
                          row, ext)
            else:
                a.finding("APIS-SILENT-EMPTY", "medium", "bug", "package_apis",
                          "files of this language import packages; the reply finds none "
                          "and never says the language is not read", row, ext)
    if pa and pa.get("packages"):
        a.call("package_apis", package=pa["packages"][0]["package"], expect_error=True)
        a.call("package_apis", limit=1, expect_error=True)

    # routes: idiom hits against what the tool returned.
    rt = a.call("routes", limit=10 ** 6)
    rows = (rt or {}).get("routes", [])
    by_file_ext = Counter(_ext(r.get("file", "")) for r in rows)
    routes: dict[str, dict[str, int]] = {}
    for ext, rels in by_ext.items():
        pat = ROUTE_IDIOMS.get(ext)
        if not pat:
            continue
        rx = re.compile(pat, re.M)
        hits = sum(len(rx.findall(_read(rel))) for rel in rels if not s._is_test_path(rel))
        routes[ext] = {"idiom_hits": hits, "rows": by_file_ext.get(ext, 0)}
        if hits >= 3 and not by_file_ext.get(ext):
            if ext in ((rt or {}).get("not_read") or {}):
                a.finding("ROUTES-LANGUAGE-NOT-READ", "info", "gap", "routes",
                          "no route extractor for this language (the reply says so)",
                          routes[ext], ext)
            else:
                a.finding("ROUTES-SILENT-EMPTY", "medium", "gap", "routes",
                          "the source registers routes in an idiom the extractor does not "
                          "read; the reply lists none", routes[ext], ext)
    m["routes"] = routes
    frameworks = Counter(r.get("framework") for r in rows)
    m["route_frameworks"] = dict(frameworks)
    for want in repo.get("routes", []):
        if not frameworks.get(want):
            a.finding("ROUTES-EXPECTED-MISSING", "high", "gap", "routes",
                      f"no {want} routes found in an application built on {want}", dict(frameworks))
    for row in rows:
        if not row.get("node"):
            a.finding("ROUTE-NO-NODE", "low", "gap", "routes",
                      "a route row is not joined to a graph node",
                      f"{row.get('file')}:{row.get('line')}", _ext(row.get("file", "")))
        if s._is_vendored_file(row.get("file", "")):
            a.finding("ROUTE-IN-VENDORED", "low", "convention", "routes",
                      "lists routes from a vendored file", row.get("file"))
    for fw in frameworks:
        got = a.call("routes", framework=fw or "", limit=3, expect_error=True)
        if got and "error" not in got and any(r.get("framework") != fw for r in got["routes"]):
            a.finding("ROUTES-FILTER", "medium", "bug", "routes", "framework filter leaks", fw)
    a.call("routes", method="GET", pattern="/", limit=1, expect_error=True)

    # relations graphify emits that no graphlore vocabulary knows.
    # manifest, template and citation edges: real, and not code dependencies
    not_code = {"rationale_for", "declares", "depends_on", "includes", "cites"}
    known = (s._IMPORT_RELATIONS | s._CALL_RELATIONS | gl_graph._CONTAINMENT_RELATIONS
             | s._IMPACT_CODE_RELATIONS | s._STATE_RELATIONS | s._HTTP_RELATIONS
             | not_code)
    rels = Counter(s._rel(e) for e in sub.edges)
    m["relations"] = dict(rels.most_common())
    for rel, count in rels.items():
        if rel not in known:
            a.finding("RELATION-UNKNOWN", "low", "gap", "impact",
                      "graphify emits a relation no graphlore relation set names "
                      "(relations=\"code\" skips it)", {"relation": rel, "edges": count})
    # extensions in the graph that the source-extension tables leave out.
    for ext, rels_ in by_ext.items():
        missing = [name for name, table in (
            ("_SOURCE_EXTS", s._SOURCE_EXTS), ("_CODE_EXTS", gl_review._CODE_EXTS),
            ("_LANG_FAMILY", gl_graph._LANG_FAMILY)) if ext not in table]
        if missing:
            a.finding("EXT-TABLE-GAP", "medium", "bug", "freshness",
                      "an extension graphify indexes is missing from graphlore's tables",
                      {"missing_from": missing, "files": len(rels_)}, ext)


def check_semantic(a: Audit, sub: Subject) -> None:
    probe = a.call("locate", query="parse the response", top_k=1)
    if probe is None:
        return
    if "error" in probe:
        a.status("locate", "degraded")
        a.finding("LOCATE-UNAVAILABLE", "info", "gap", "locate", "semantic index unavailable",
                  probe["error"][:140])
        return
    seeds = quals = contained = 0
    for nid in (sub.functions + sub.classes)[:6]:
        n = sub.byid[nid]
        words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s._bare_name(s._node_label(n)))
        got = a.call("locate", query=words.replace("_", " ").lower())
        if not got or "error" in got:
            continue
        seed = got.get("seed") or {}
        if seed:
            seeds += 1
            quals += bool(seed.get("qualname"))
            spans = s._spans_for_file(seed.get("file", ""))
            line = seed.get("line") or 0
            contained += any(sp[0] <= line <= sp[1] for sp in spans)
        hidden = sum(1 for c in got.get("semantic_cousins") or [] if not c.get("linked"))
        if got.get("hidden_link_count") not in (None, hidden):
            a.finding("LOCATE-HIDDEN-COUNT", "low", "bug", "locate",
                      "hidden_link_count differs from the unlinked cousins listed")
    a.metrics[a.repo]["locate"] = {"seeds": seeds, "qualnames": quals, "in_span": contained}
    a.call("locate", query="request", top_k=0, expect_error=True)
    a.call("locate", query="request", budget_tokens=40, expect_error=True)


def check_cli(a: Audit, sub: Subject) -> None:
    hub = (sub.functions or sub.classes or sub.methods or [None])[0]
    if hub is None:
        return
    label = s._bare_name(s._node_label(sub.byid[hub]))
    asked = next((nm for nid in sub.functions + sub.classes + sub.methods
                  if len(nm := s._bare_name(s._node_label(sub.byid[nid]))) >= 6), label)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("query", {"question": f"what calls {asked}", "budget": 300}),
        ("explain", {"node": hub}),
    ]
    for e in sub.edges:  # a path that exists: the two ends of a real call
        x, y = s._edge_ends(e)
        if s._rel(e) == "calls" and x != y and x in sub.defs.kind and y in sub.defs.kind:
            calls.append(("path", {"node_a": x, "node_b": y}))
            break
    for tool, kw in calls:
        out = a.text(tool, **kw)
        if out.startswith("ERROR"):
            a.status(tool, "error")
            a.finding("CLI-ERROR", "medium", "bug", tool, "the graphify CLI call failed",
                      {"args": kw, "reply": out[:200]})
        elif "=== repo text" not in out:
            a.finding("CLI-UNFRAMED", "medium", "convention", tool,
                      "CLI output quoting the repository is not framed", out[:120])
    # the same node by its bare name: what an assistant types first
    bare = a.text("explain", node=label, expect_error=True)
    if bare.startswith("ERROR") and "Ambiguous" in bare:
        a.finding("CLI-AMBIGUOUS-NAME", "low", "gap", "explain",
                  "a bare name several nodes share is refused by the graphify CLI; "
                  "graphlore passes the refusal through instead of resolving the name",
                  bare[:160])


def _copy(repo_dir: Path, tmp: Path) -> Path:
    dst = tmp / repo_dir.name
    shutil.copytree(repo_dir, dst, symlinks=True)
    _git(dst, "config", "user.email", "bench@example.invalid")
    _git(dst, "config", "user.name", "bench")
    return dst


def _edit_targets(sub: Subject) -> dict[str, tuple[str, int, str]]:
    """One file per extension: (path, def line, name) of a definition to rename."""
    out: dict[str, tuple[str, int, str]] = {}
    for ext, ids in sub.by_ext.items():
        for nid in ids:
            rel = s._node_file(sub.byid[nid])
            if s._is_test_path(rel) or rel in {v[0] for v in out.values()}:
                continue
            lines = _read(rel).split("\n")
            for _start, _end, dl, qual in s._spans_for_file(rel):
                name = qual.rsplit(".", 1)[-1]
                if 1 <= dl <= len(lines) and re.search(rf"\b{re.escape(name)}\b", lines[dl - 1]) \
                        and len(name) > 3:
                    out[ext] = (rel, dl, name)
                    break
            if ext in out:
                break
    return out


def check_sandbox(a: Audit, sub: Subject, repo_dir: Path, keep: bool) -> None:
    """Everything that writes: edits for freshness/diff, prune, build, labels."""
    tmp = Path(tempfile.mkdtemp(prefix="polyglot-"))
    try:
        box = _copy(repo_dir, tmp)
        _reset(box)
        targets = _edit_targets(sub)
        a.metrics[a.repo]["edited_for_freshness"] = {
            ext: rel for ext, (rel, _dl, _name) in sorted(targets.items())}
        base = a.call("freshness")
        if base and base.get("stale"):
            a.finding("FRESHNESS-STALE-AT-BUILD", "medium", "bug", "freshness",
                      "a graph built at HEAD on a clean tree is reported stale",
                      base.get("reason"))

        def write(rel: str, text: str) -> None:
            (box / rel).write_text(text, encoding="utf-8")

        originals = {rel: (box / rel).read_text(encoding="utf-8", errors="replace")
                     for rel, _, _ in targets.values()}
        # cosmetic: a comment at the end of each file
        for ext, (rel, _dl, _name) in targets.items():
            mark = "# bench note" if ext in HASH_COMMENT_EXTS else "// bench note"
            write(rel, originals[rel].rstrip("\n") + "\n" + mark + "\n")
        fr = a.call("freshness")
        for ext, (rel, _dl, _name) in targets.items():
            if fr and rel not in fr.get("cosmetic_changes", []):
                where = next((k for k in ("structural_changes", "non_source_changes")
                              if rel in fr.get(k, [])), "nowhere")
                a.finding("FRESHNESS-COMMENT-NOT-COSMETIC", "medium", "bug", "freshness",
                          "a trailing comment is not classified as a cosmetic change",
                          {"file": rel, "listed_under": where}, ext)
        _git(box, "commit", "-qam", "cosmetic")
        # structural: rename one definition per file
        for rel, dl, name in targets.values():
            lines = (box / rel).read_text(encoding="utf-8", errors="replace").split("\n")
            lines[dl - 1] = re.sub(rf"\b{re.escape(name)}\b", lambda m: m.group(0) + "_x",
                                   lines[dl - 1], count=1)
            write(rel, "\n".join(lines))
        fr = a.call("freshness")
        for ext, (rel, _dl, _name) in targets.items():
            if fr and rel not in fr.get("structural_changes", []):
                a.finding("FRESHNESS-RENAME-NOT-STRUCTURAL", "high", "bug", "freshness",
                          "renaming a definition is not classified as a structural change",
                          {"file": rel, "cosmetic": rel in fr.get("cosmetic_changes", [])}, ext)
        _git(box, "commit", "-qam", "structural")
        d1 = a.call("diff", ref_a="HEAD~2", ref_b="HEAD~1")
        d2 = a.call("diff", ref_a="HEAD~1", ref_b="HEAD")
        for ext, (rel, _dl, _name) in targets.items():
            if d1 and "error" not in d1 and rel in json.dumps(d1.get("structural", [])):
                a.finding("DIFF-COMMENT-STRUCTURAL", "medium", "bug", "diff",
                          "a comment-only commit is reported as structural", rel, ext)
            if d2 and "error" not in d2 and rel not in json.dumps(d2.get("structural", [])):
                a.finding("DIFF-RENAME-NOT-STRUCTURAL", "high", "bug", "diff",
                          "a rename commit is not reported as structural", rel, ext)
        a.call("diff", ref_a="HEAD~2", ref_b="HEAD", budget_tokens=1)
        # phantom: delete one indexed file, then prune
        if targets:
            gone = next(iter(targets.values()))[0]
            (box / gone).unlink()
            fr = a.call("freshness")
            if fr and gone not in fr.get("phantom_files", []) + fr.get("deleted_or_renamed", []):
                a.finding("FRESHNESS-PHANTOM-MISSED", "medium", "bug", "freshness",
                          "a deleted indexed file is not reported", gone)
            dry = a.call("prune", dry_run=True)
            real = a.call("prune", dry_run=False)
            if dry and real and dry.get("removable_nodes") != real.get(
                    "removed_nodes", real.get("removable_nodes")):
                a.finding("PRUNE-DRY-RUN-DIFFERS", "medium", "bug", "prune",
                          "the dry run and the real run disagree",
                          {"dry": dry.get("removable_nodes"), "real": sorted(real)[:8]})
            _reset(box)
            after = a.call("validate", scope="all")
            if after and (after.get("issues") or {}).get("dangling_edges"):
                a.finding("PRUNE-LEAVES-DANGLING", "high", "bug", "prune",
                          "pruning left dangling edges", after["issues"])
            again = a.call("prune", dry_run=True)
            if again and again.get("removable_nodes"):
                a.finding("PRUNE-NOT-IDEMPOTENT", "medium", "bug", "prune",
                          "a second prune still finds nodes to remove")
            # back, so the build below reads the tree the graph was built from
            _git(box, "checkout", "--", gone)
        # labels
        com = a.call("communities")
        if com and com.get("communities"):
            cid = str(com["communities"][0]["id"])
            sl = a.call("set_labels", names={cid: "Bench Alpha", "999999": "nope"})
            if sl and "error" not in sl:
                if "999999" not in json.dumps(sl.get("unknown_ids", [])):
                    a.finding("SET-LABELS-UNKNOWN", "low", "bug", "set_labels",
                              "an unknown community id is not reported", sorted(sl)[:8])
                _reset(box)
                shown = json.dumps([a.call("communities"), a.call("overview")])
                if "Bench Alpha" not in shown:
                    a.finding("SET-LABELS-INVISIBLE", "medium", "bug", "set_labels",
                              "a name written by set_labels shows up in neither "
                              "graphlore_communities nor graphlore_overview")
            a.call("set_labels", names={"999999": "x"}, expect_error=True)
        # build through graphlore's own argv, from nothing; code_only=True skips
        # the docs, so the code nodes are what the two builds must agree on
        def code_nodes(nodes: list[dict]) -> int:
            return sum(1 for n in nodes if str(n.get("file_type") or "code") == "code")

        before = code_nodes(sub.nodes)
        plain = a.text("build", path=".", expect_error=True)
        if plain.startswith("ERROR") and "no LLM API key" in plain:
            a.finding("BUILD-DEFAULT-NEEDS-KEY", "info", "upstream", "build",
                      "graphlore_build() with its defaults fails without an LLM key when "
                      "the repository holds docs; code_only=True builds",
                      "named code_only=True" if "code_only=True" in plain
                      else "the reply names only the CLI flag")
        (box / s.config.OUT_DIR_NAME / "graph.json").unlink(missing_ok=True)
        out = a.text("build", path=".", code_only=True)
        if out.startswith("ERROR"):
            a.status("build", "error")
            a.finding("BUILD-ERROR", "high", "bug", "build",
                      "graphlore_build(code_only=True) fails with the installed graphify",
                      out[:240])
        else:
            _reset(box)
            graph = s._load_graph()
            if isinstance(graph, str):
                a.finding("BUILD-NO-GRAPH", "high", "bug", "build",
                          "after graphlore_build the graph cannot be loaded", graph[:160])
            elif code_nodes(s._nodes_edges(graph)[0]) < before * 0.5:
                a.finding("BUILD-SMALL-GRAPH", "medium", "bug", "build",
                          "the graph graphlore_build wrote has under half the code "
                          "nodes of the one `graphify update` built",
                          {"update": before, "build": code_nodes(s._nodes_edges(graph)[0])})
        a.call("project", path=str(box / s.config.OUT_DIR_NAME))
        a.call("project", path=str(tmp / "does-not-exist"), expect_error=True)
    finally:
        _reset(repo_dir)
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def check_protocol(a: Audit, sub: Subject) -> None:
    """Over a real in-memory MCP session: results, resources, prompts, the ctx tools."""
    from mcp import Client

    hub = (sub.functions or sub.classes or sub.methods or [""])[0]

    async def go() -> None:
        async with Client(s.mcp) as client:
            tools = (await client.list_tools()).tools
            if len(tools) != 33:
                a.finding("TOOL-COUNT", "high", "bug", "protocol",
                          "list_tools does not return 33 tools", len(tools))
            for tool in tools:
                if not (tool.description or "").strip():
                    a.finding("TOOL-NO-DESCRIPTION", "medium", "bug", "protocol",
                              "a tool has no description", tool.name)
            for name, args in (("graphlore_overview", {"as_json": True}),
                               ("graphlore_neighbors", {"node": hub, "as_json": True}),
                               ("graphlore_node_details", {"node": "zz_qx9_none"}),
                               ("graphlore_search", {"pattern": "zz_qx9_none"})):
                res = await client.call_tool(name, args)
                text = res.content[0].text if res.content else ""
                structured = getattr(res, "structured_content", None)
                if structured is None:
                    a.finding("NO-STRUCTURED-CONTENT", "medium", "convention", name,
                              "a result carries no structuredContent", args)
                miss = name == "graphlore_node_details"
                if bool(getattr(res, "is_error", False)) != miss:
                    a.finding("IS-ERROR-WRONG", "medium", "convention", name,
                              "isError does not match the outcome", text[:100])
            st = await client.call_tool("graphlore_sampling_status", {"as_json": True})
            status = json.loads(st.content[0].text)
            a.status("sampling_status", "ok")
            if status.get("host_sampling_supported"):
                a.finding("SAMPLING-FALSE-POSITIVE", "medium", "bug", "sampling_status",
                          "reports sampling support for a client that declared none")
            lb = await client.call_tool("graphlore_label_communities",
                                        {"method": "placeholder", "limit": 3, "as_json": True})
            labels = json.loads(lb.content[0].text)
            a.status("label_communities", "error" if "error" in labels else "ok")
            if "error" not in labels and labels.get("labeled") != min(
                    3, labels.get("total_communities", 0)):
                a.finding("LABEL-COUNT", "low", "bug", "label_communities",
                          "labeled differs from min(limit, total)", labels.get("labeled"))
            for uri in ("graphlore://report", "graphlore://graph", "graphlore://community/0",
                        "graphlore://community/999999"):
                res = await client.read_resource(uri)
                if not res.contents or not (getattr(res.contents[0], "text", "") or "").strip():
                    a.finding("RESOURCE-EMPTY", "medium", "bug", "resources",
                              "a resource returned nothing", uri)
            names = {t.name for t in tools}
            for prompt in (await client.list_prompts()).prompts:
                args = {arg.name: "login" for arg in (prompt.arguments or [])}
                got = await client.get_prompt(prompt.name, args)
                body = " ".join(getattr(msg.content, "text", "") for msg in got.messages)
                for ref in set(re.findall(r"graphlore_\w+", body)) - names:
                    a.finding("PROMPT-UNKNOWN-TOOL", "medium", "bug", "prompts",
                              "a prompt names a tool that is not registered",
                              {"prompt": prompt.name, "tool": ref})

    try:
        asyncio.run(go())
    except Exception as exc:  # noqa: BLE001 - a crash is the finding
        a.finding("CRASH", "high", "bug", "protocol", f"{type(exc).__name__}: {str(exc)[:160]}",
                  traceback.format_exc().strip().splitlines()[-1][:200])


def check_extension_tables(a: Audit) -> dict[str, dict[str, bool]]:
    """Repo-independent: do the tables that name source extensions agree?"""
    a.repo = "(static)"
    from graphlore import imports as im
    tables = {
        "_SOURCE_EXTS": s._SOURCE_EXTS, "_CODE_EXTS": gl_review._CODE_EXTS,
        "_LANG_FAMILY": frozenset(gl_graph._LANG_FAMILY),
        "imports(read or compiled)": frozenset(im._JS_EXTS) | frozenset(im._COMPILED_EXTS)
        | {".py", ".pyi"},
    }
    every = sorted(set().union(*tables.values()))
    matrix = {ext: {name: ext in table for name, table in tables.items()} for ext in every}
    for ext, row in matrix.items():
        # Ruby, PHP, Lua, Zig and Elixir import at run time and nothing reads them:
        # their edges are `unclassified` by design, so that column is not compared
        gaps = [name for name, present in row.items()
                if not present and not name.startswith("imports")]
        if gaps and ext != ".d.ts":
            a.finding("EXT-TABLES-DISAGREE", "low", "bug", "(tables)",
                      "an extension is known to some source-extension tables and not others",
                      {"missing_from": gaps}, ext)
    return matrix


NATIVE_OUT = "graphlore-native"


def build_native(root: Path, repos: list[dict[str, Any]], rebuild: bool) -> None:
    """Build each repo's graph with the native engine into graphlore-native/ and
    point the audit (and the build check inside it) at the native engine."""
    try:
        import graphlore_engine
    except ImportError:
        raise SystemExit("the native engine is not installed: "
                         "maturin develop -m engine/Cargo.toml --release") from None
    os.environ["GRAPHLORE_ENGINE"] = "native"
    s.config.OUT_DIR_NAME = NATIVE_OUT
    for repo in repos:
        d = root / repo["key"]
        if not d.exists():
            continue
        exclude = d / ".git" / "info" / "exclude"
        if exclude.exists() and NATIVE_OUT not in exclude.read_text(encoding="utf-8"):
            with exclude.open("a", encoding="utf-8") as fh:
                fh.write(f"{NATIVE_OUT}/\n")
        out = d / NATIVE_OUT / "graph.json"
        if out.exists() and not rebuild:
            continue
        commit = _git(d, "rev-parse", "HEAD").strip() or None
        rep = json.loads(graphlore_engine.build(str(d), out=str(out), commit=commit,
                                                out_dir_name=NATIVE_OUT))
        print(f"native {repo['key']}: {rep['nodes']} nodes, {rep['edges']} edges, "
              f"{rep['elapsed_ms']} ms")


def audit_repo(a: Audit, repo: dict[str, Any], root: Path, opts: argparse.Namespace) -> None:
    repo_dir = (root / repo["key"]).resolve()
    a.repo = repo["key"]
    _reset(repo_dir)
    graph = s._load_graph()
    if isinstance(graph, str):
        print(f"SKIP {repo['key']}: {graph}", file=sys.stderr)
        return
    nodes, edges = s._nodes_edges(graph)
    sub = Subject(nodes, edges)
    a.metrics[a.repo].update({
        "nodes": len(nodes), "edges": len(edges),
        "definitions": dict(Counter(sub.defs.kind.values())),
        "definitions_by_ext": {ext: len(ids) for ext, ids in sub.by_ext.items()},
    })
    graph_file = repo_dir / s.config.OUT_DIR_NAME / "graph.json"
    before = (hashlib.sha256(graph_file.read_bytes()).hexdigest(),
              _git(repo_dir, "status", "--porcelain"))
    want = set(opts.tools.split(",")) if opts.tools else None

    def on(*names: str) -> bool:
        return want is None or bool(want & set(names))

    if on("overview", "god_nodes", "surprises", "communities", "search", "project"):
        check_orientation(a, sub)
    if on(*NODE_TOOLS):
        check_navigation(a, sub)
    if on("fetch", "skeleton"):
        check_source(a, sub, opts.sample)
    if on("validate", "cycles", "unreferenced", "same_names", "layers", "duplication_scan"):
        check_review(a, sub)
    if on("package_apis", "routes", "languages"):
        check_languages(a, sub, repo)
    if on("locate") and not opts.skip_semantic:
        check_semantic(a, sub)
    if on("query", "path", "explain"):
        check_cli(a, sub)
    if want is None:
        check_hostile(a, sub)
        check_protocol(a, sub)
    after = (hashlib.sha256(graph_file.read_bytes()).hexdigest(),
             _git(repo_dir, "status", "--porcelain"))
    if before != after:
        a.finding("READ-ONLY-WROTE", "high", "bug", "(battery)",
                  "the read-only battery changed graph.json or the working tree",
                  after[1][:200])
    if not opts.skip_mutating and on("freshness", "diff", "prune", "build", "set_labels"):
        check_sandbox(a, sub, repo_dir, opts.keep_sandbox)
    a.call("add", url="ftp://example.invalid/x", expect_error=True)
    a.status("add", "skipped")
    row = a.matrix[a.repo]
    print(f"{repo['key']:18} nodes={len(nodes):5} "
          + " ".join(f"{t}={st}" for t, st in sorted(row.items()) if st not in ("ok",)))


def render(a: Audit, baseline: dict[str, Any] | None) -> None:
    tools = sorted({t for row in a.matrix.values() for t in row})
    letters = {"ok": ".", "empty": "e", "degraded": "d", "error": "E", "crash": "X",
               "skipped": "-"}
    print("\n=== repo x tool  (. ok  e empty  d degraded  E error  X crash) ===")
    for i, t in enumerate(tools):
        print(f"  {chr(97 + i % 26)}{i // 26 or ''} = {t}")
    for repo, row in a.matrix.items():
        print(f"{repo:18} " + "".join(letters[row.get(t, "skipped")] for t in tools))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in a.findings.values():
        grouped[f["id"]].append(f)
    old = {(f["id"], f["repo"], f["tool"], f["lang"]) for f in (baseline or {}).get("findings", [])}
    print("\n=== findings ===")
    for fid, rows in sorted(grouped.items(),
                            key=lambda kv: (SEVERITY_RANK[kv[1][0]["severity"]], kv[0])):
        first = rows[0]
        total = sum(r["count"] for r in rows)
        print(f"\n[{first['severity']}/{first['kind']}] {fid}: {first['summary']}  (x{total})")
        for r in sorted(rows, key=lambda r: -r["count"])[:8]:
            new = "" if baseline is None or (r["id"], r["repo"], r["tool"], r["lang"]) in old \
                else "  NEW"
            where = "/".join(x for x in (r["repo"], r["tool"], r["lang"]) if x)
            shown = json.dumps(r["samples"][:2], default=str)[:230]
            print(f"   {where}: x{r['count']}{new}  {shown}")
    if baseline is not None:
        now = set(a.findings)
        fixed = sorted(old - now)
        print(f"\n=== against the baseline: {len(now - old)} new, {len(fixed)} gone ===")
        for key in fixed[:40]:
            print("   gone:", "/".join(x for x in key if x))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", default="/tmp/polyglot", help="where the clones live")
    parser.add_argument("--setup", action="store_true", help="clone and build, then exit")
    parser.add_argument("--only", default="", help="comma-separated repo keys or languages")
    parser.add_argument("--tools", default="", help="comma-separated tool names to run")
    parser.add_argument("--sample", type=int, default=60,
                        help="definitions fetched per extension and repo")
    parser.add_argument("--skip-mutating", action="store_true")
    parser.add_argument("--skip-semantic", action="store_true")
    parser.add_argument("--keep-sandbox", action="store_true")
    parser.add_argument("--json", metavar="PATH", default=None)
    parser.add_argument("--baseline", metavar="PATH", default=None,
                        help="an earlier --json result: mark findings new or gone")
    parser.add_argument("--engine", choices=("graphify", "native"), default="graphify",
                        help="audit the graphs graphify built (graphify-out/) or the ones "
                             "graphlore's native engine builds (graphlore-native/, built "
                             "when missing or with --rebuild)")
    parser.add_argument("--rebuild", action="store_true",
                        help="with --engine native: rebuild every native graph first")
    opts = parser.parse_args(argv)

    only = {x for x in opts.only.split(",") if x}
    repos = [r for r in REPOS if not only or r["key"] in only or only & set(r["langs"])]
    root = Path(opts.root)
    if opts.setup:
        setup(root, repos)
        return
    for key in s._BACKEND_ENV:  # no tool may reach an LLM backend from here
        os.environ.pop(key, None)
    if opts.engine == "native":
        build_native(root, repos, opts.rebuild)

    audit = Audit()
    ext_matrix = check_extension_tables(audit)
    for repo in repos:
        try:
            audit_repo(audit, repo, root, opts)
        except Exception as exc:  # noqa: BLE001 - keep the other repos' results
            audit.finding("HARNESS-CRASH", "high", "bug", "(harness)",
                          f"{type(exc).__name__}: {str(exc)[:160]}",
                          traceback.format_exc().strip().splitlines()[-3:])
    if not audit.matrix:
        raise SystemExit("no repo graphs found — run with --setup first")
    baseline = json.loads(Path(opts.baseline).read_text(encoding="utf-8")) \
        if opts.baseline else None
    render(audit, baseline)
    if opts.json:
        from importlib.metadata import PackageNotFoundError, version

        def ver(pkg: str) -> str | None:
            try:
                return version(pkg)
            except PackageNotFoundError:
                return None

        payload = {
            "measured": date.today().isoformat(),
            "engine": opts.engine,
            "versions": {"graphlore": s.__version__, "graphify": ver("graphifyy"),
                         "graphlore_engine": ver("graphlore-engine"),
                         "tree_sitter_language_pack": ver("tree-sitter-language-pack"),
                         "semble": ver("semble"),
                         "python": ".".join(map(str, sys.version_info[:3]))},
            "repos": [{"key": r["key"], "langs": r["langs"], "url": r["url"], "sha": r["sha"],
                       **audit.metrics.get(r["key"], {})} for r in repos],
            "extension_tables": ext_matrix,
            "matrix": audit.matrix,
            "timings_ms": audit.timings,
            "findings": sorted(audit.findings.values(),
                               key=lambda f: (SEVERITY_RANK[f["severity"]], f["id"], f["repo"])),
        }
        Path(opts.json).write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"\nresults written to {opts.json}")


if __name__ == "__main__":
    main()
