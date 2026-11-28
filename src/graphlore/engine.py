"""graphlore's native engine, the extractor that builds the graph.

The native engine is the ``graphlore-engine`` distribution (import name
``graphlore_engine``), a dependency of graphlore written in Rust with its
tree-sitter grammars compiled in (prebuilt wheels): it extracts definitions with
their real spans, containment, document headings, the imports, calls, bases and
type references it resolves from the source, and communities by its own
weighting; ``update=True`` parses only the files that changed.
``GRAPHLORE_ENGINE`` is read at call time: ``auto`` (the default) and ``native``
both mean the native engine. ``graphify``, the external CLI that built the graph
before, was removed in 0.9.0; a graph it wrote is still read (``graph_engine``).

Nothing here imports ``graphlore_engine`` at module import: ``available`` asks
``importlib`` whether it exists, builds run it in a child process, and
``file_spans`` imports it on first use.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config

ENGINES = ("native", "auto")
DEFAULT_ENGINE = "auto"
# graph.json schema versions of the native engine this graphlore reads.
SUPPORTED_SCHEMAS = frozenset({1, 2, 3, 4, 5, 6, 7})
INSTALL_HINT = (
    "graphlore-engine is a dependency of graphlore: reinstall graphlore, or "
    "pip install graphlore-engine (from a checkout of graphlore: pip install maturin "
    "&& maturin develop -m engine/Cargo.toml --release)."
)
# What a native graph does not have yet; said after every native build so an
# empty answer is not mistaken for a codebase without structure.
NATIVE_GAPS = (
    "Built by graphlore's native engine: this graph has files, definitions with "
    "their spans, containment, document headings and the code they name, the "
    "imports, calls, bases and type references it could resolve from the source, "
    "and communities by its own "
    "weighting (edge kind, confidence and directory). "
    "update=True parses only the files that changed."
)
# The same for a graph written before the engine detected communities.
NATIVE_GAPS_NO_COMMUNITIES = (
    "Built by graphlore's native engine: this graph has files, definitions with "
    "their spans, containment, document headings and the imports, calls, bases and "
    "type references it could resolve from the source, but no communities, and "
    "update=True rebuilds the whole graph. A current graphlore-engine detects them."
)


def requested() -> str:
    """``GRAPHLORE_ENGINE``, lower-cased; the default when unset. Read per call."""
    return (config.env("ENGINE", "") or "").strip().lower() or DEFAULT_ENGINE


def available() -> bool:
    """Whether ``graphlore_engine`` is importable, without importing it."""
    try:
        return importlib.util.find_spec("graphlore_engine") is not None
    except (ImportError, ValueError):
        return False


def graph_engine(graph: object) -> str:
    """``"native"`` for a graph the native engine wrote, else ``"graphify"``."""
    if isinstance(graph, dict):
        meta = graph.get("graph")
        if isinstance(meta, dict) and meta.get("engine") == "graphlore":
            return "native"
    return "graphify"


def refusal(*, requested: str, native_found: bool) -> str:
    """Why a build cannot run, as an ``ERROR`` message; "" when it can.

    ``requested`` is ``GRAPHLORE_ENGINE`` (``requested()``), ``native_found``
    whether the engine is installed (``available()``).
    """
    if requested == "graphify":
        return (
            "ERROR: GRAPHLORE_ENGINE=graphify: the graphify CLI was removed as "
            "graphlore's extractor in 0.9.0; graphlore's native engine builds the graph. "
            "Unset GRAPHLORE_ENGINE (or set it to native)."
        )
    if requested not in ENGINES:
        return (
            f"ERROR: GRAPHLORE_ENGINE={requested!r} is not an engine "
            f"({', '.join(ENGINES)}). Unset it for the default (auto)."
        )
    if not native_found:
        return (
            "ERROR: graphlore's native engine (graphlore_engine) is not installed, "
            f"and it is what builds the graph. {INSTALL_HINT}"
        )
    return ""


def build(
    target: Path,
    *,
    documents: bool = True,
    commit: str | None = None,
    timeout: int = 600,
    incremental: bool = False,
) -> dict[str, Any] | str:
    """Build ``target``'s graph with the native engine, in a child process.

    Writes ``target/<out dir>/graph.json`` and returns the engine's report, or an
    ``ERROR`` string. A child process, so that a crash in a grammar cannot take
    the server down and the timeout is a real one.
    """
    out = target / config.OUT_DIR_NAME / "graph.json"
    argv = [
        sys.executable, "-m", "graphlore_engine", "build", str(target),
        "--out", str(out), "--out-dir-name", config.OUT_DIR_NAME,
    ]
    if commit:
        argv += ["--commit", commit]
    if not documents:
        argv.append("--no-docs")
    if incremental:
        argv.append("--incremental")
    if (config.env("VENDOR") or "").strip().lower() in ("1", "true", "yes"):
        argv.append("--vendor")
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: the native engine did not finish within {timeout}s (GRAPHLORE_TIMEOUT)."
    except OSError as e:
        return f"ERROR: failed to run the native engine: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail.startswith("ERROR"):
            return detail
        return f"ERROR (native engine, exit {proc.returncode}):\n{detail}"
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return f"ERROR: the native engine's report is not JSON:\n{proc.stdout[:500]}"
    if not isinstance(report, dict):
        return "ERROR: the native engine's report is not a JSON object."
    if report.get("schema_version") not in SUPPORTED_SCHEMAS:
        return (
            f"ERROR: the native engine writes graph schema {report.get('schema_version')}, "
            f"which this graphlore does not read (it reads {sorted(SUPPORTED_SCHEMAS)}). "
            "Install a matching graphlore-engine."
        )
    return report


def cluster(graph: Path, *, timeout: int = 600) -> dict[str, Any] | str:
    """Detect the communities of the native graph at ``graph`` again, in a child
    process; returns the engine's community report or an ``ERROR`` string."""
    argv = [sys.executable, "-m", "graphlore_engine", "cluster", str(graph)]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: the native engine did not finish within {timeout}s (GRAPHLORE_TIMEOUT)."
    except OSError as e:
        return f"ERROR: failed to run the native engine: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail.startswith("ERROR"):
            return detail
        return f"ERROR (native engine, exit {proc.returncode}):\n{detail}"
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return f"ERROR: the native engine's report is not JSON:\n{proc.stdout[:500]}"
    if not isinstance(report, dict):
        return "ERROR: the native engine's report is not a JSON object."
    return report


def render_communities(c: dict[str, Any]) -> str:
    """One line on the communities a native build or re-cluster found."""
    line = (
        f"communities: {c.get('count', 0)} (largest {c.get('largest', 0)} nodes, "
        f"modularity {c.get('modularity', 0)})"
    )
    if c.get("kept_ids"):
        line += f", {c['kept_ids']} kept their id from the previous graph"
    if c.get("renamed_labels"):
        line += (
            f"; {_plural(c['renamed_labels'], 'saved name')} dropped from "
            ".graphlore_labels.json because its community changed beyond recognition"
        )
    return line


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def render_report(report: dict[str, Any], verbose: bool = False, update: bool = False) -> str:
    """The build reply: counts first, then everything that was left out."""
    langs: dict[str, dict[str, int]] = report.get("languages") or {}
    secs = (report.get("elapsed_ms") or 0) / 1000
    head = (
        f"native engine {report.get('engine_version', '?')}: "
        f"{report.get('nodes', 0)} nodes, {report.get('edges', 0)} edges from "
        f"{_plural(report.get('files', 0), 'source file')} "
        f"({_plural(len(langs), 'language')}) and "
        f"{_plural(report.get('documents', 0), 'document')} in {secs:.1f}s"
    )
    lines = [head]
    if langs:
        rows = [
            f"{name} {_plural(s.get('files', 0), 'file')}, "
            f"{_plural(s.get('definitions', 0), 'definition')}"
            for name, s in sorted(langs.items(), key=lambda kv: -kv[1].get("files", 0))
        ]
        lines.append("  " + " · ".join(rows if verbose else rows[:8])
                     + ("" if verbose or len(rows) <= 8 else f" · +{len(rows) - 8} more"))
    resolution: dict[str, dict[str, Any]] = report.get("resolution") or {}
    if resolution:
        rows = []
        for fam, r in sorted(resolution.items()):
            edges = sum((r.get("edges") or {}).values())
            if not edges and not r.get("unresolved"):
                continue
            bit = f"{fam} {_plural(edges, 'edge')}"
            if r.get("inferred"):
                bit += f" ({r['inferred']} inferred)"
            if r.get("unresolved"):
                bit += f", {r['unresolved']} unresolved"
            rows.append(bit)
        if rows:
            lines.append("  resolved: " + " · ".join(rows if verbose else rows[:8])
                         + ("" if verbose or len(rows) <= 8 else f" · +{len(rows) - 8} more"))
    if report.get("documents"):
        line = f"  documents: {_plural(report.get('headings', 0), 'heading')}"
        if "doc_links" in report:
            line += (f", {_plural(report['doc_links'], 'link')} to the definition a name "
                     "of code in them means")
        lines.append(line)
    inc = report.get("incremental")
    if isinstance(inc, dict) and inc.get("cache"):
        line = (f"  update: {_plural(inc.get('parsed', 0), 'file')} parsed, "
                f"{inc.get('reused', 0)} unchanged reused from the cache")
        if inc.get("removed"):
            line += f", {inc['removed']} gone since the last build"
        lines.append(line)
    elif isinstance(inc, dict) and update:
        lines.append("  update: no cache from this engine version and options, "
                     "so every file was parsed")
    state = report.get("state")
    if isinstance(state, dict) and (state.get("reads") or state.get("writes")):
        line = (f"  shared state: {_plural(state.get('state_nodes', 0), 'variable/field')}, "
                f"{state.get('reads', 0)} reads, {state.get('writes', 0)} writes")
        res = state.get("resources") or {}
        if res:
            line += "; resources: " + ", ".join(f"{k} {v}" for k, v in sorted(res.items()))
        if state.get("single_touch"):
            line += f" ({state['single_touch']} more touched in one place only, not shown)"
        lines.append(line)
    coupling = report.get("coupling")
    if isinstance(coupling, dict) and coupling.get("pairs"):
        line = (f"  implicit coupling: {_plural(coupling['pairs'], 'pair')} of definitions "
                f"share state, {coupling.get('hidden', 0)} with no call path between them")
        if coupling.get("hidden_without_edge"):
            line += (f" ({coupling['hidden_without_edge']} of them past the limit of "
                     "couples_with edges per state node: listed on the node only)")
        lines.append(line)
    communities = report.get("communities")
    if isinstance(communities, dict) and communities.get("count"):
        lines.append("  " + render_communities(communities))
    errors = {k: v["parse_errors"] for k, v in langs.items() if v.get("parse_errors")}
    if errors:
        lines.append(
            "  parse errors (definitions found are partial): "
            + ", ".join(f"{k} {_plural(v, 'file')}" for k, v in sorted(errors.items()))
        )
    not_read = report.get("not_read") or {}
    if not_read:
        lines.append(
            "  not read (no module for these source files): "
            + ", ".join(f"{ext} {n}" for ext, n in sorted(not_read.items()))
        )
    for key, what in (
        ("too_large", f"skipped as larger than {(report.get('max_file_bytes') or 0) // 1024} KB"),
        ("timed_out", "parse timed out"),
        ("unreadable", "could not be read"),
    ):
        count = report.get(f"{key}_count") or 0
        if count:
            sample = report.get(key) or []
            shown = sample if verbose else sample[:3]
            more = f", +{count - len(shown)} more" if count > len(shown) else ""
            lines.append(f"  {what}: {_plural(count, 'file')} ({', '.join(shown)}{more})")
    vendored = report.get("vendored_skipped") or []
    if vendored:
        dirs = vendored if verbose else vendored[:3]
        more = f", +{len(vendored) - len(dirs)} more" if len(vendored) > len(dirs) else ""
        count = _plural(report.get("vendored_skipped_count", 0), "source file")
        lines.append(f"  Go vendor/ left out: {count} in {', '.join(dirs)}{more} "
                     "(GRAPHLORE_VENDOR=1 reads them)")
    if report.get("non_utf8_paths"):
        skipped = _plural(report["non_utf8_paths"], "file")
        lines.append(f"  skipped {skipped} whose name is not UTF-8")
    phases = report.get("phases_ms")
    if verbose and isinstance(phases, dict) and phases:
        lines.append("  time per phase: " + ", ".join(f"{k} {v} ms" for k, v in phases.items()))
    if verbose and report.get("id_collisions"):
        lines.append(f"  {report['id_collisions']} node id(s) suffixed _2, _3… to stay unique")
    has = isinstance(communities, dict) and bool(communities.get("count"))
    lines.append(NATIVE_GAPS if has or not report.get("nodes") else NATIVE_GAPS_NO_COMMUNITIES)
    return "\n".join(lines)


def file_spans(src: bytes, rel: str) -> list[tuple[int, int, int, str]] | None:
    """The native engine's ``(region_start, end_line, def_line, qualname)`` spans
    of one file, or None when the engine is not installed or cannot read it."""
    try:
        import graphlore_engine
    except ImportError:
        return None
    # `definition_spans` (engine 0.5.80) is the same answer without reading the
    # rest of the file's facts, about five times faster
    read = getattr(graphlore_engine, "definition_spans", None) or graphlore_engine.file_spans
    try:
        return [tuple(s) for s in read(src, rel)]  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - a span backend that fails reads as "no spans"
        return None
