"""`graphlore review`: what a commit range changes, read off the code graph.

A deterministic report for a pull request, with no model and no network: the
definitions the diff touches (each changed line given to the innermost
definition around it), what depends on them across the graph, the tests that
reach them, and the import cycles and layer rules the change brings in. It is
built from the MCP tools' own functions (``graphlore_diff``, the impact walk,
``graphlore_tests_for``, the import cycles, ``graphlore_layers``), so a review
and an agent asking the same questions get the same answers.

This module sits on top of the stack: it imports ``server`` (and so the MCP
SDK), and ``cli.py`` imports it only when ``graphlore review`` runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, engine
from . import server as srv
from .spans import _nodes_by_file, _spans_python, _spans_treesitter

MARKER = "<!-- graphlore-review -->"
FAIL_ON = ("cycle", "layer", "untested")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# how much of the report each part may hold; every cut is stated
_MAX_CHANGED = 80
_MAX_DEPENDENTS = 60
_MAX_TESTED = 40
_MARKDOWN_CHARS = 20_000
_MAX_REMOVED = 40


class ReviewError(Exception):
    """A review that cannot be made here: not a repository, an unknown ref, no
    graph at the head commit. The message says what to do."""


@dataclass
class _Change:
    """One changed definition (or a file's top level, ``node`` None)."""
    file: str
    node: dict | None
    lines: list[int] = field(default_factory=list)


# --- git -------------------------------------------------------------------

# the repository a workspace review reads (None: the project's own)
_cwd: Path | None = None


def _git(args: list[str]) -> str | None:
    return srv._git(args, _cwd)


def _commit(ref: str) -> str:
    sha = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    if not sha:
        raise ReviewError(
            f"git ref '{ref}' is not in this clone. In CI, fetch the history the range "
            "needs (actions/checkout with fetch-depth: 0).")
    return sha.strip()


def _project_prefix() -> str:
    """The project directory's path inside the repository ("" at its root), as
    git writes it: diff paths are repository-relative."""
    return (_git(["rev-parse", "--show-prefix"]) or "").strip()


def _changed_lines(base: str, head: str, files: list[str]) -> dict[str, set[int]]:
    """Head-side line numbers each file's hunks touch (``-U0``). A hunk that
    only deletes lines touches the line it was deleted after."""
    out: dict[str, set[int]] = {f: set() for f in files}
    if not files:
        return out
    raw = _git(["diff", "-U0", "-M", "--no-color", "--no-ext-diff", base, head, "--", *files])
    current: str | None = None
    for line in (raw or "").splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            current = path[2:] if path.startswith("b/") else None
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            start, count = int(m.group(1)), int(m.group(2) or "1")
            first = start if count else max(start, 1)
            lines = range(first, first + max(count, 1))
            out.setdefault(current, set()).update(lines)
    return out


# --- graph -------------------------------------------------------------------

def _graph_at(head: str, build: bool, repo: str = "") -> dict:
    """The graph, built at ``head`` (of ``repo`` in a workspace). A missing or
    stale one is built (an update when one exists) when ``build`` is on and the
    native engine is installed."""
    graph = srv._load_graph()
    built = str(graph.get("built_at_commit") or "") if isinstance(graph, dict) else ""
    if repo and isinstance(graph, dict):
        built = srv._graph_repos(graph).get(repo) or ""
    if built and (head.startswith(built) or built.startswith(head)):
        return graph  # type: ignore[return-value]
    if not build:
        raise ReviewError(
            "the graph is not built at the head commit"
            + (f" (it is at {built[:10]})" if built else "")
            + ": run `graphlore review` without --no-build, or build it first.")
    refusal = engine.refusal(requested=engine.requested(), native_found=engine.available())
    if refusal:
        raise ReviewError(
            "the graph is not built at the head commit, and it cannot be built here: "
            + refusal.removeprefix("ERROR: "))
    exists = isinstance(graph, dict)
    # graphlore_build's own steps, without the graph.html page a review never reads
    out = srv._build_native(".", code_only=False, verbose=False, note="",
                            update=exists and bool(built), viz_page=False)
    if out.startswith("ERROR"):
        raise ReviewError(out)
    graph = srv._load_graph()
    if isinstance(graph, str):
        raise ReviewError(graph)
    return graph


def _line(n: dict) -> int | None:
    raw = srv._node_line(n)
    try:
        return int(str(raw).split("-")[0].lstrip("Ll"))
    except (TypeError, ValueError):
        return None


def _span(n: dict) -> tuple[int, int] | None:
    line = _line(n)
    if line is None:
        return None
    carried = srv._carried_span(n, line)
    if carried:
        return carried
    name = srv._node_label(n).strip().lstrip(".").removesuffix("()").rsplit(".", 1)[-1]
    span = srv._span_of_definition(srv._node_file(n), line, name)
    if span is None:
        return None
    return span[0], span[1]


def _changes_in(file: str, lines: set[int], nodes: list[dict]) -> list[_Change]:
    """Each changed line to the innermost definition around it; a line no
    definition holds is the file's top level. A line left blank at the head
    commit changes nothing anyone reads."""
    if lines:
        got = srv._read_source_lines(file, min(lines), max(lines))
        if got is not None:
            text, lo, _hi = got
            lines = {ln for ln in lines
                     if not (lo <= ln < lo + len(text) and not text[ln - lo].strip())}
    spans: list[tuple[int, int, dict]] = []
    for n in _nodes_by_file(nodes).get(file, []):
        if not srv._is_code_definition(n) or srv._is_file_hub_node(n):
            continue
        s = _span(n)
        if s and s[0] <= s[1]:
            spans.append((s[0], s[1], n))
    by_id: dict[str, _Change] = {}
    top = _Change(file, None)
    for ln in sorted(lines):
        around = [s for s in spans if s[0] <= ln <= s[1]]
        if not around:
            top.lines.append(ln)
            continue
        lo, hi, n = min(around, key=lambda s: (s[1] - s[0], -s[0]))
        nid = str(srv._node_id(n))
        by_id.setdefault(nid, _Change(file, n)).lines.append(ln)
    out = sorted(by_id.values(), key=lambda c: (min(c.lines), str(srv._node_id(c.node or {}))))
    return out + ([top] if top.lines else [])


def _definitions_at(commit: str, repo_path: str) -> dict[str, int] | None:
    """Qualified name -> definition line of a source file at ``commit``, read
    with the span engine from git (None when git or the span engine cannot)."""
    text = _git(["show", f"{commit}:{repo_path}"])
    if text is None:
        return None
    src = text.encode("utf-8", "replace")
    try:
        spans = (_spans_python(src) if repo_path.lower().endswith(".py")
                 else _spans_treesitter(src, repo_path))
    except Exception:  # noqa: BLE001 - a file the span engine cannot read names nothing
        return None
    out: dict[str, int] = {}
    for _start, _end, line, qualname in spans:
        out.setdefault(qualname, line)
    return out


def _removed_definitions(merge: str, head: str, files: list[dict]) -> list[dict]:
    """Definitions the base had and the head has not: a file removed, or a
    definition deleted or renamed in a file still there. The head graph has no
    node for them, so they come from the two commits' sources."""
    out: list[dict] = []
    for f in files:
        if Path(f["path"]).suffix.lower() not in srv._SOURCE_EXTS:
            continue
        old_path = f.get("from_repo_path") or f["repo_path"]
        before = _definitions_at(merge, old_path)
        if not before:
            continue
        after = {} if f["kind"] == "removed" else _definitions_at(head, f["repo_path"])
        if after is None:
            continue
        for qualname, line in sorted(before.items(), key=lambda kv: kv[1]):
            if qualname not in after:
                out.append({"name": qualname, "file": f.get("from") or f["path"], "line": line,
                            "file_removed": f["kind"] == "removed"})
    return out


def _ranges(lines: list[int]) -> str:
    out: list[str] = []
    for ln in sorted(set(lines)):
        if out and int(out[-1].split("-")[-1]) == ln - 1:
            out[-1] = f"{out[-1].split('-')[0]}-{ln}"
        else:
            out.append(str(ln))
    return ",".join(out)


def _dependents(nodes: list[dict], edges: list[dict], seeds: list[str], hops: int,
                labels: Any) -> dict[str, Any]:
    """What depends on any of ``seeds``, one walk from all of them: the nearest
    first, production code before tests, the most connected first.

    A heading that names the code documents it and depends on nothing
    (``documents`` is not walked). Containment is walked, since the files that
    import a changed definition's file are reached through the file's node,
    but what holds a changed definition is not one of its dependents: a row
    reached by a containment edge is left out of the list and the counts."""
    nav = srv._nav_view(nodes, edges, "", True)
    if isinstance(nav, str):
        return {"rows": [], "total": 0}
    view, _resolved = nav
    _forward, reverse = srv._directed_edge_adjacency(view.edges)
    skip = (srv._IMPACT_SKIPPED_RELATIONS | srv._STATE_RELATIONS | srv._COUPLING_RELATIONS
            | srv._DOCUMENT_RELATIONS)
    if not seeds:
        return {"rows": [], "total": 0}
    recs = srv._hop_records(reverse, seeds[0], hops, None, seeds[1:], skip)
    byid = {str(srv._node_id(n)): n for n in nodes}
    seed_set = set(seeds)
    hits = [(nid, d, via, e) for nid, (d, via, e) in recs.items()
            if nid not in seed_set and e is not None
            and srv._edge_rel(e).strip().lower() not in srv._CONTAINMENT_RELATIONS]

    def in_test(nid: str) -> bool:
        n = byid.get(nid)
        return n is not None and srv._is_test_node(n)

    def degree(nid: str) -> int:
        return len(_forward.get(nid, ())) + len(reverse.get(nid, ()))

    hits.sort(key=lambda h: (h[1], in_test(h[0]), -degree(h[0]), labels.get(h[0], h[0])))
    rows = []
    for nid, d, via, e in hits[:_MAX_DEPENDENTS]:
        n = byid.get(nid) or {}
        f, ln = srv._node_file(n), _line(n)
        rows.append({
            "node": labels.name(nid, nid), "distance": d,
            "relation": srv._edge_rel(e).strip(), "via": labels.get(via, via),
            "site": f"{f}:{ln}" if f and ln else f, "test": in_test(nid),
        })
    left = hits[_MAX_DEPENDENTS:]
    return {
        "rows": rows, "total": len(hits),
        "production": sum(not in_test(h[0]) for h in hits),
        "tests": sum(in_test(h[0]) for h in hits),
        "left_out": len(left), "left_out_tests": sum(in_test(h[0]) for h in left),
        "files": len({srv._node_file(byid[h[0]]) for h in hits if h[0] in byid}),
    }


def _tests_of(changes: list[_Change]) -> dict[str, Any]:
    reached: dict[str, dict] = {}
    untested: list[str] = []
    asked = 0
    # a variable, a constant or a field is read, not called: no test "reaches"
    # one by a call, so only callables and types are looked up
    candidates = [c for c in changes if c.node is not None and not srv._is_test_node(c.node)
                  and not srv._is_state_node(c.node)]
    for c in candidates[:_MAX_TESTED]:
        asked += 1
        nid = str(srv._node_id(c.node or {}))
        got = json.loads(srv.graphlore_tests_for(nid, as_json=True))
        found = [*got.get("direct", []), *got.get("through_one_caller", [])]
        if not found:
            untested.append(nid)
        for t in found:
            key = f"{t.get('test')}|{t.get('file')}"
            reached.setdefault(key, {"test": t.get("test"), "file": t.get("file"),
                                     "for": []})["for"].append(nid)
    return {"tests": sorted(reached.values(), key=lambda t: (t["file"] or "", t["test"] or "")),
            "untested": untested, "asked": asked,
            "not_asked": max(0, len(candidates) - asked)}


def _site_line(e: dict) -> int | None:
    loc = str(e.get("source_location") or "").lstrip("Ll").split("-")[0].split(":")[0]
    return int(loc) if loc.isdigit() else None


def _import_cycles(nodes: list[dict], edges: list[dict], touched: dict[str, set[int]]
                   ) -> list[dict]:
    """Import cycles between files that pass through a changed file. A cycle is
    ``introduced`` when one of its imports is written on a changed line."""
    checker = srv.ImportChecker()
    forward, _implicit = srv._module_import_adjacency(nodes, edges, checker.kind)
    sites: dict[tuple[str, str], list[int | None]] = {}
    for sf, tf, e, _dst, _kind in srv._file_import_edges(nodes, edges, checker.kind):
        sites.setdefault((sf, tf), []).append(_site_line(e))
    out = []
    for g in srv._tiered_import_cycles(forward):
        members = set(g["nodes"])
        if not members & set(touched):
            continue
        path = list(g["path"])
        pairs = list(zip(path, path[1:] + path[:1], strict=True)) if path else []
        introduced = any(
            ln is not None and ln in touched.get(a, set())
            for a, b in pairs for ln in sites.get((a, b), []))
        out.append({"files": sorted(members), "path": path, "runs": g["tier"],
                    "introduced": introduced})
    out.sort(key=lambda c: (not c["introduced"], c["runs"] != "runtime", c["path"]))
    return out


def _layer_violations(touched: dict[str, set[int]]) -> dict[str, Any] | None:
    if not (config.PROJECT_DIR / srv._LAYERS_DEFAULT_FILE).is_file():
        return None
    got = json.loads(srv.graphlore_layers(as_json=True, limit=10_000))
    if "error" in got:
        return {"error": got["error"], "violations": []}
    found = []
    for layer in got.get("layers", []):
        for v in layer.get("violations", []):
            if v.get("from_file") not in touched:
                continue
            site = str(v.get("site") or "")
            tail = site.rsplit(":", 1)[-1].lstrip("Ll").split("-")[0] if ":" in site else ""
            line = int(tail) if tail.isdigit() else None
            found.append({**v, "layer": layer.get("layer"),
                          "introduced": line is not None and line in touched[v["from_file"]]})
    found.sort(key=lambda v: (not v["introduced"], v["from_file"], v["site"]))
    return {"violations": found}


# --- the review ----------------------------------------------------------------

def analyze(base: str = "HEAD~1", head: str = "HEAD", *, build: bool = True, hops: int = 3,
            fail_on: tuple[str, ...] = (), repo: str = "") -> dict[str, Any]:
    """The review of ``base``..``head`` as a dict (``render_markdown`` prints it).

    ``base`` is compared through its merge base with ``head``: what the branch
    changed, not what the target branch did meanwhile. ``head`` must be the
    commit checked out, since the graph and the spans are read from the files.
    In a workspace, ``repo`` names the repository whose range it is; the
    dependents and tests are the whole workspace graph's.
    """
    global _cwd
    repo = repo.strip().strip("/")
    if repo:
        repos = srv._workspace()
        if repo not in repos:
            listed = ", ".join(sorted(repos)) or "none"
            raise ReviewError(f"'{repo}' is no repository of this workspace "
                              f"(its repositories: {listed}).")
    _cwd = srv._repo_dir(repo) if repo else None
    try:
        return _analyze(base, head, build=build, hops=hops, fail_on=fail_on, repo=repo)
    finally:
        _cwd = None


def _analyze(base: str, head: str, *, build: bool, hops: int, fail_on: tuple[str, ...],
             repo: str) -> dict[str, Any]:
    bad = sorted(set(fail_on) - set(FAIL_ON))
    if bad:
        raise ReviewError(f"unknown --fail-on value(s) {', '.join(bad)}: "
                          f"use {', '.join(FAIL_ON)}")
    if _git(["rev-parse", "HEAD"]) is None:
        where = srv._repo_dir(repo)
        hint = ""
        if not repo and srv._workspace():
            hint = " It is a workspace: name the repository with --repo."
        raise ReviewError(f"{where} is not in a git repository.{hint}")
    head_sha, base_sha = _commit(head), _commit(base)
    if head_sha != _commit("HEAD"):
        raise ReviewError(f"'{head}' is not the commit checked out: check it out first "
                          "(the graph and the spans are read from the working tree).")
    merge = (_git(["merge-base", base_sha, head_sha]) or "").strip()
    if not merge:
        raise ReviewError(f"'{base}' and '{head}' share no history.")
    dirty = bool((_git(["status", "--porcelain", "--untracked-files=no"]) or "").strip())
    graph = _graph_at(head_sha, build, repo)
    nodes, edges = srv._nodes_edges(graph)
    labels = srv._display_labels(nodes)

    diff = json.loads(srv.graphlore_diff(merge, head_sha, budget_tokens=10**7, as_json=True,
                                         repo=repo))
    if "error" in diff:
        raise ReviewError(diff["error"])
    prefix = _project_prefix()
    # a workspace's diff leads each path with the repository; git reads it without
    lead = f"{repo}/" if repo else ""

    def in_repo(path: str) -> str:
        return path[len(lead):] if lead and path.startswith(lead) else path

    def local(path: str) -> str | None:
        inner = in_repo(path)
        return lead + inner[len(prefix):] if inner.startswith(prefix) else None

    files: list[dict] = []
    for rec in diff.get("structural", []):
        path = rec.get("to") or rec.get("path") or ""
        rel = local(path)
        if rel is not None:
            f = {**rec, "repo_path": in_repo(path), "path": rel}
            if rec.get("from"):
                f["from_repo_path"] = in_repo(rec["from"])
                f["from"] = local(rec["from"]) or rec["from"]
            files.append(f)
    cosmetic = [local(r.get("to") or r.get("path") or "") for r in diff.get("cosmetic", [])]
    present = [f for f in files if f["kind"] != "removed"]
    # definitions are read in source files; a document's or a config file's change
    # is listed with the files, not given to a definition
    source = [f for f in present if Path(f["path"]).suffix.lower() in srv._SOURCE_EXTS]
    lines = _changed_lines(merge, head_sha, [f["repo_path"] for f in source])
    touched = {f["path"]: lines.get(f["repo_path"], set()) for f in source}

    changes: list[_Change] = []
    for f in source:
        changes += _changes_in(f["path"], touched[f["path"]], nodes)
    defs = [c for c in changes if c.node is not None]
    seeds = [str(srv._node_id(c.node)) for c in defs if c.node is not None]
    deps = _dependents(nodes, edges, seeds, hops, labels)
    tests = _tests_of(defs)
    cycles = _import_cycles(nodes, edges, touched)
    layers = _layer_violations(touched)
    removed = _removed_definitions(merge, head_sha, files)

    def show(c: _Change) -> dict[str, Any]:
        if c.node is None:
            return {"node": None, "file": c.file, "lines": _ranges(c.lines), "top_level": True}
        nid = str(srv._node_id(c.node))
        return {"node": labels.name(nid, nid), "id": nid, "file": c.file,
                "line": _line(c.node), "lines": _ranges(c.lines),
                "test": srv._is_test_node(c.node)}

    gates = {
        "cycle": [c for c in cycles if c["introduced"] and c["runs"] == "runtime"],
        "layer": [v for v in (layers or {}).get("violations", []) if v["introduced"]],
        "untested": tests["untested"],
    }
    failed = [g for g in fail_on if gates[g]]
    return {
        "base": base, "head": head, "merge_base": merge[:12], "head_commit": head_sha[:12],
        **({"repo": repo} if repo else {}),
        "dirty_worktree": dirty,
        "files": [{k: v for k, v in f.items() if k not in ("repo_path", "from_repo_path")}
                  for f in files],
        "source_files": len(source),
        "cosmetic_files": [p for p in cosmetic if p],
        "changed": [show(c) for c in changes[:_MAX_CHANGED]],
        "changed_total": len(changes),
        "removed": removed[:_MAX_REMOVED], "removed_total": len(removed),
        "dependents": deps, "hops": hops,
        "tests": {**tests, "untested": [labels.get(n, n) for n in tests["untested"]]},
        "cycles": cycles, "layers": layers,
        "fail_on": list(fail_on), "failed": failed,
        "content_trust": srv._CONTENT_TRUST,
    }


# --- rendering ------------------------------------------------------------------

def _code(text: object) -> str:
    """Repository text as a Markdown code span: names come from the code under
    review, so nothing in them may become markup, a link or a mention."""
    s = str(text).replace("\n", " ").replace("\r", " ")
    s = s.replace("`", "ˋ")
    return f"`{s}`" if s else "``"


def render_markdown(r: dict[str, Any]) -> str:
    out: list[str] = [MARKER, "### graphlore review", ""]
    deps = r["dependents"]
    defs = [c for c in r["changed"] if c.get("node")]
    summary = (f"{len(r['files'])} file(s) changed structurally ({r.get('source_files', 0)} "
               f"source), {r['changed_total']} "
               f"definition(s) or top level(s) touched; {deps['total']} dependent(s) within "
               f"{r['hops']} hop(s) ({deps.get('production', 0)} production, "
               f"{deps.get('tests', 0)} test) in {deps.get('files', 0)} file(s).")
    out.append(summary)
    out.append(("Repository: " + _code(r["repo"]) + ". " if r.get("repo") else "")
               + f"Range: {_code(r['merge_base'])}..{_code(r['head_commit'])}"
               + (f", {len(r['cosmetic_files'])} cosmetic-only file(s) left out"
                  if r["cosmetic_files"] else ""))
    if r["dirty_worktree"]:
        out.append("> The working tree has uncommitted changes: the graph was read from it.")
    if r["failed"]:
        out.append(f"\n**Gate failed:** {', '.join(r['failed'])}")
    out.append("")
    if r["files"]:
        out.append("<details><summary>Files</summary>\n")
        for f in r["files"]:
            name = (f"{_code(f.get('from'))} → {_code(f['path'])}" if f["kind"] == "renamed"
                    else _code(f["path"]))
            out.append(f"- {f['kind']}: {name}")
        out.append("\n</details>\n")
    if r["changed"]:
        out.append("#### Changed")
        out.append("| Definition | File | Lines |\n|---|---|---|")
        for c in r["changed"]:
            what = "(top level)" if c.get("top_level") else _code(c["node"]) + (
                " (test)" if c.get("test") else "")
            out.append(f"| {what} | {_code(c['file'])} | {c['lines']} |")
        if r["changed_total"] > len(r["changed"]):
            out.append(f"\n…and {r['changed_total'] - len(r['changed'])} more "
                       "(`--format json` lists the first ones too; narrow the range for the rest).")
        out.append("")
    removed = r.get("removed") or []
    if removed:
        out.append(f"#### Removed ({r.get('removed_total', len(removed))})")
        out.append("| Definition | File | Line (base) |\n|---|---|---|")
        for d in removed:
            gone = " (file removed)" if d.get("file_removed") else ""
            out.append(f"| {_code(d['name'])} | {_code(d['file'])}{gone} | {d['line']} |")
        if r.get("removed_total", 0) > len(removed):
            out.append(f"\n…and {r['removed_total'] - len(removed)} more "
                       "(`--format json` lists the first ones).")
        out.append("")
    if deps["rows"]:
        out.append(f"#### What depends on it ({deps['total']})")
        out.append("| Dependent | Hop | Through | Site |\n|---|---|---|---|")
        for d in deps["rows"]:
            out.append(f"| {_code(d['node'])}{' (test)' if d['test'] else ''} | {d['distance']} | "
                       f"{_code(d['relation'])} of {_code(d['via'])} | {_code(d['site'])} |")
        if deps.get("left_out"):
            out.append(f"\n{deps['left_out']} more left out ({deps['left_out_tests']} in tests).")
        out.append("")
    elif defs:
        out.append("#### What depends on it\nNo dependent in the graph. That is not proof "
                   "there is none: calls through an object or a callback may not be recorded.\n")
    t = r["tests"]
    if t["tests"] or t["untested"]:
        out.append("#### Tests")
        for x in t["tests"][:40]:
            out.append(f"- {_code(x['test'])} in {_code(x['file'])}")
        if len(t["tests"]) > 40:
            out.append(f"- …and {len(t['tests']) - 40} more")
        if t["untested"]:
            more = len(t["untested"]) - 30
            out.append("\nNo test found for: " + ", ".join(_code(u) for u in t["untested"][:30])
                       + (f" and {more} more" if more > 0 else ""))
        if t["not_asked"]:
            out.append(f"\n({t['not_asked']} changed definition(s) not looked up.)")
        out.append("")
    if r["cycles"]:
        out.append("#### Import cycles through changed files")
        for c in r["cycles"]:
            flag = "**introduced**" if c["introduced"] else "already there"
            out.append(f"- {flag}, {c['runs']}: " + " → ".join(_code(p) for p in c["path"]))
        out.append("")
    layers = r["layers"]
    if layers and layers.get("error"):
        out.append(f"#### Layers\n{_code(layers['error'])}\n")
    elif layers and layers["violations"]:
        out.append("#### Layer rules (.graphlore-layers.json)")
        for v in layers["violations"][:40]:
            flag = "**introduced**" if v["introduced"] else "already there"
            out.append(f"- {flag}: {_code(v['site'])} imports {_code(v['to_file'])} "
                       f"(layer {_code(v['layer'])})")
        out.append("")
    if not r["files"]:
        out.append("No structural change: nothing for the graph to say.\n")
    out.append("<sub>Deterministic, offline: read from the code graph by graphlore; "
               "no model was asked.</sub>")
    text = "\n".join(out) + "\n"
    if len(text) > _MARKDOWN_CHARS:
        cut = text[:_MARKDOWN_CHARS].rsplit("\n", 1)[0]
        text = (cut + "\n\n…cut at 20,000 characters: `graphlore review --format json` "
                "has the whole report.\n")
    return text


def run(base: str, head: str, *, fmt: str = "markdown", fail_on: tuple[str, ...] = (),
        build: bool = True, hops: int = 3, output: str = "", repo: str = "") -> int:
    """The CLI's entry: 0 clean, 1 a gate in ``fail_on`` failed, 2 no review."""
    import sys

    try:
        report = analyze(base, head, build=build, hops=hops, fail_on=fail_on, repo=repo)
    except ReviewError as e:
        print(f"graphlore review: {e}", file=sys.stderr)
        return 2
    text = (json.dumps(report, indent=2, ensure_ascii=False) + "\n" if fmt == "json"
            else render_markdown(report))
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if report["failed"] else 0
