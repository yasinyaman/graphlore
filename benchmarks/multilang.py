#!/usr/bin/env python3
"""Multi-language validation benchmark for graphlore.

Proves the tree-sitter span join (graphify_locate) and the cosmetic-vs-structural
freshness check on REAL HTTP-client repos in JS/TS, Go and Java — the same kind of
queries the Python/httpx benchmark uses.

Setup (one venv with graphify + semble + tree-sitter-language-pack, then clone +
build an AST-only graph per repo — no LLM/API key needed):

  mkdir -p /tmp/ml-bench && cd /tmp/ml-bench
  git clone --depth 1 https://github.com/sindresorhus/got.git
  git clone --depth 1 https://github.com/go-resty/resty.git
  git clone --depth 1 https://github.com/square/retrofit.git
  graphify update got/source --no-cluster
  graphify update resty --no-cluster
  graphify update retrofit/retrofit/src/main/java --no-cluster
  # (the Python row reuses an existing /tmp/httpx-demo graph; drop that entry to skip)

Run:
  /path/to/venv/bin/python benchmarks/multilang.py [--json results.json]

Span, token, hidden-link and freshness numbers are measured live. The one
exception is the locate side of the call-count row: locate is 1 tool call / 0
agent file reads BY CONSTRUCTION (it is a single call whose reply embeds the
map), so those two constants are the tool's contract, not a measurement — the
naive side of the same row IS measured from the live grep. It reports, per repo:
  * span-join precision  — % of semble hits whose resolved node's real tree-sitter
    span actually CONTAINS the chunk (true containment), replicating the httpx metric
  * qualname recovery    — % of locate seeds that recover a span FQN (e.g. Service.fetch)
  * hidden links / query — the structural-cross-check signal
  * token spot-check     — locate map tokens vs a naive grep+read, for one query
  * call spot-check      — 1 locate call / 0 file reads vs the naive baseline's
    1 grep + N file-read calls (N = the files the same grep matches, i.e. what a
    grep-driven agent would open) — both metrics share one baseline
and a per-language freshness correctness check (comment/reformat = cosmetic,
operator/value edit = structural). --json persists the full result set.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from graphlore import server as s  # noqa: E402

BENCH = Path("/tmp/ml-bench")

REPOS = [
    {   # Python baseline, measured the same way as the others (graph already built)
        "lang": "Python", "name": "encode/httpx", "dir": Path("/tmp/httpx-demo"),
        "ext": ".py",
        "queries": [
            "asynchronous HTTP client send request",
            "digest authentication challenge",
            "follow redirect location header",
            "content decoding gzip brotli",
            "cookie jar storage",
            "request timeout configuration",
        ],
        "spotcheck": "asynchronous HTTP client send request", "grep": "timeout",
    },
    {
        "lang": "JS/TS", "name": "sindresorhus/got", "dir": BENCH / "got/source",
        "ext": ".ts",
        "queries": [
            "send http request and receive response",
            "follow redirect location header",
            "request timeout handling",
            "retry a failed request",
            "set request headers",
            "parse json response body",
        ],
        "spotcheck": "request timeout handling", "grep": "timeout",
    },
    {
        "lang": "Go", "name": "go-resty/resty", "dir": BENCH / "resty",
        "ext": ".go",
        "queries": [
            "execute GET and POST request",
            "retry request on failure with backoff",
            "request and response middleware",
            "set timeout on the client",
            "redirect policy follow location",
            "parse response body into a struct",
        ],
        "spotcheck": "retry request on failure with backoff", "grep": "Retry",
    },
    {
        "lang": "Java", "name": "square/retrofit",
        "dir": BENCH / "retrofit/retrofit/src/main/java", "ext": ".java",
        "queries": [
            "create a service from an interface",
            "execute synchronous and asynchronous call",
            "convert the response body",
            "build an http request from method annotations",
            "call adapter for the return type",
            "add a request to the call queue",
        ],
        "spotcheck": "convert the response body", "grep": "Converter",
    },
    {
        "lang": "Rust", "name": "algesten/ureq", "dir": BENCH / "ureq/src",
        "ext": ".rs",
        "queries": [
            "send an http request and read the response",
            "connection pool and agent configuration",
            "follow redirects",
            "set request timeout",
            "tls certificate handling",
            "parse the response body",
        ],
        "spotcheck": "set request timeout", "grep": "timeout",
    },
    {
        "lang": "C++", "name": "libcpr/cpr", "dir": BENCH / "cpr/cpr",
        "ext": ".cpp",
        "queries": [
            "perform a GET and POST request",
            "set the request timeout",
            "session options and configuration",
            "parse the response",
            "add request headers and authentication",
            "redirect handling",
        ],
        "spotcheck": "perform a GET and POST request", "grep": "Get",
    },
]


def _reset(project_dir: Path):
    s.config.PROJECT_DIR = project_dir
    s._GRAPH_CACHE.clear()
    s._SPAN_CACHE.clear()
    s._TS_PARSERS.clear()


def _node_span_contains(node, fp, line):
    """Does the real tree-sitter span of the resolved node contain the chunk start?"""
    if node is None:
        return None
    try:
        nl = int(s._node_line(node))
    except (TypeError, ValueError):
        return None
    spans = s._spans_for_file(fp)
    own = [sp for sp in spans if sp[2] == nl]
    if not own:
        return None  # node has no span (module-level / not a tracked symbol)
    # Same-qualname family: C++/Java overloads collapse into ONE graph node
    # (pointing at the first definition) while every overload keeps its own
    # span. A chunk inside ANY overload of that symbol is a correct join, so
    # containment is checked against the whole family, not just the span
    # starting at the node's recorded line (cpr's Session::SetOption has 46).
    quals = {sp[3] for sp in own}
    family = [sp for sp in spans if sp[3] in quals]
    return any(sp[0] <= line <= sp[1] for sp in family)


def measure_repo(repo) -> dict:
    _reset(repo["dir"])
    graph = s._load_graph()
    if isinstance(graph, str):
        raise SystemExit(f"{repo['name']}: {graph}")
    nodes, _ = s._nodes_edges(graph)
    index = s._semble_index()
    if index is None:
        raise SystemExit("semble not installed in this interpreter")

    contained = total = no_span = 0
    hidden_total = 0
    quals = seeds = 0
    for q in repo["queries"]:
        hits = index.search(q, top_k=3)
        pool = list(hits)
        if hits:
            pool += list(index.find_related(hits[0], top_k=6))
        for h in pool:
            c = h.chunk
            fp, sl, el = str(c.file_path), int(c.start_line), int(c.end_line)
            n = s._node_for_location(nodes, fp, sl, el)
            total += 1
            r = _node_span_contains(n, fp, sl)
            if r is True:
                contained += 1
            elif r is None:
                no_span += 1
        data = json.loads(s.graphify_locate(q, as_json=True))
        seed = data.get("seed") or {}
        if seed:
            seeds += 1
            if seed.get("qualname"):
                quals += 1
        hidden_total += len(data.get("hidden_links") or [])

    # token spot-check: locate map vs naive grep+read for one query
    locate_txt = s.graphify_locate(repo["spotcheck"])
    locate_tokens = max(1, len(locate_txt) // 4)
    grep_files = subprocess.run(
        ["grep", "-rli", "--include", f"*{repo['ext']}", repo["grep"], "."],
        cwd=str(repo["dir"]), capture_output=True, text=True,
    ).stdout.split()
    grep_bytes = 0
    for rel in grep_files:
        try:
            grep_bytes += (repo["dir"] / rel).stat().st_size
        except OSError:
            pass
    grep_tokens = max(1, grep_bytes // 4)

    # call/read accounting for the same query+grep baseline: locate is one tool
    # call reading zero files; the naive agent runs 1 grep then opens each match.
    naive_calls = 1 + len(grep_files)
    return {
        "lang": repo["lang"], "name": repo["name"], "nodes": len(nodes),
        "hits": total, "contained": contained, "no_span": no_span,
        "precision": contained / total if total else 0.0,
        "seeds": seeds, "quals": quals,
        "qual_rate": quals / seeds if seeds else 0.0,
        "hidden_avg": hidden_total / len(repo["queries"]),
        "spot_query": repo["spotcheck"], "locate_tokens": locate_tokens,
        "grep_tokens": grep_tokens, "grep_files": len(grep_files),
        "ratio": grep_tokens / locate_tokens,
        "locate_calls": 1, "locate_file_reads": 0,
        "naive_calls": naive_calls, "naive_file_reads": len(grep_files),
        "call_reduction": 1 - 1 / naive_calls,
    }


def freshness_check(repo) -> dict:
    """Cosmetic (comment/reindent) -> True; structural (value/rename) -> False."""
    _reset(repo["dir"])
    src_file = next(
        (p for p in sorted(repo["dir"].rglob(f"*{repo['ext']}"))
         if "test" not in p.name.lower() and p.stat().st_size > 400), None)
    if src_file is None:
        return {"lang": repo["lang"], "cosmetic": None, "structural": None}
    rel = str(src_file.relative_to(repo["dir"]))
    original = src_file.read_bytes()
    comment = b"# bench note" if repo["lang"] == "Python" else b"// bench note"
    lines = original.split(b"\n")
    # cosmetic: a trailing comment (+ blank line) — robust across languages, no risk
    # of splitting a docstring/string literal
    cosmetic = original + b"\n" + comment + b"\n"
    # structural: rename a real symbol on its DEFINITION line (guaranteed in-code,
    # parse-safe). Bumping "the first integer" is unreliable — it often lands in a
    # license-header comment, which _structurally_equal correctly treats as cosmetic.
    structural = None
    for _rs, _end, dl, qual in s._spans_for_file(rel):
        name = qual.split(".")[-1].encode()
        if 1 <= dl <= len(lines) and name in lines[dl - 1]:
            new = lines[dl - 1].replace(name, name + b"_x", 1)
            structural = b"\n".join(lines[:dl - 1] + [new] + lines[dl:])
            break
    if structural is None:                  # no resolvable symbol — make a token change
        structural = re.sub(rb"\breturn\b", b"return  /*x*/", original, count=1)
    return {
        "lang": repo["lang"], "file": rel,
        "cosmetic": s._structurally_equal(rel, original, cosmetic),
        "structural": s._structurally_equal(rel, original, structural),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", metavar="PATH", default=None,
        help="also write the full results as JSON to PATH (for docs provenance)")
    args = parser.parse_args(argv)

    results = [measure_repo(r) for r in REPOS]
    fresh = [freshness_check(r) for r in REPOS]

    print("\n=== Span-join + locate (measured on real repos) ===")
    c = ("Lang", "repo", "nodes", "hits", "precision", "qualname", "hid/q")
    hdr = (f"{c[0]:6} {c[1]:20} {c[2]:>6} {c[3]:>5} {c[4]:>20} {c[5]:>14} {c[6]:>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        prec = f"{r['contained']}/{r['hits']} ({r['precision']*100:.0f}% +{r['no_span']} mod-lvl)"
        qual = f"{r['quals']}/{r['seeds']} ({r['qual_rate']*100:.0f}%)"
        print(f"{r['lang']:6} {r['name']:20} {r['nodes']:>6} {r['hits']:>5} "
              f"{prec:>20} {qual:>14} {r['hidden_avg']:>9.1f}")

    print("\n=== Token spot-check (locate map vs naive grep+read) ===")
    for r in results:
        print(f"  {r['lang']:6} {r['spot_query']!r}: locate {r['locate_tokens']} tok"
              f"  vs grep+read {r['grep_tokens']} tok ({r['grep_files']} files)"
              f"  -> {r['ratio']:.0f}x fewer")

    print("\n=== Call-count spot-check (same query/grep; locate side by construction) ===")
    for r in results:
        print(f"  {r['lang']:6} locate: {r['locate_calls']} call / "
              f"{r['locate_file_reads']} file reads  vs naive: {r['naive_calls']} calls "
              f"({r['naive_file_reads']} file reads)"
              f"  -> {r['call_reduction']*100:.0f}% fewer calls")

    print("\n=== Freshness correctness (cosmetic vs structural) ===")
    for f in fresh:
        print(f"  {f['lang']:6} {f.get('file','?'):40} cosmetic={f['cosmetic']} (expect True)  "
              f"structural={f['structural']} (expect False)")

    payload = {
        "results": results,
        "freshness": fresh,
        "measured": date.today().isoformat(),
    }
    print("\nJSON:", json.dumps({"results": results, "freshness": fresh}, default=str))
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"results written to {args.json}")


if __name__ == "__main__":
    main()
