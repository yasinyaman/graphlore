"""The usage guide graphlore gives coding agents that read a project file
(AGENTS.md, GEMINI.md, a Cursor rule, Copilot's instructions) rather than an
MCP server's instructions or prompts: Codex, Kimi, Cursor, Copilot, Zed, Gemini
CLI. It names only the tools of the toolset the server runs with, says when
graphlore is the better tool and when grep is, and how to read an answer.

`render(tools)` gives the block between its markers; `merge(text, block)`
puts it into an existing file, replacing only what is between the markers.
Imports nothing from graphlore.
"""
from __future__ import annotations

from collections.abc import Iterable

BEGIN = "<!-- graphlore:begin -->"
END = "<!-- graphlore:end -->"

# (the tools a line needs, all of them registered; the line)
_FLOW: tuple[tuple[tuple[str, ...], str], ...] = (
    (("graphlore_overview",),
     "Start with `graphlore_overview`: the graph's size, its most central nodes and "
     "its communities."),
    (("graphlore_locate",),
     "To find code by what it does, `graphlore_locate(\"<question>\")`: the best hit, "
     "its graph neighbourhood and similar code elsewhere."),
    (("graphlore_impact",),
     "Before changing something, `graphlore_impact(\"<name>\")`: what depends on it, "
     "by hop, and the chokepoints it funnels through."),
    (("graphlore_neighbors", "graphlore_subgraph"),
     "Around one node: `graphlore_neighbors` (one hop) or `graphlore_subgraph` "
     "(a token-budgeted neighbourhood)."),
    (("graphlore_node_details",),
     "A node's file, line, kind and docstring: `graphlore_node_details`."),
    (("graphlore_fetch",),
     "Read the code of the nodes you settled on with `graphlore_fetch([...])`: their "
     "exact lines, under one budget."),
    (("graphlore_tests_for",),
     "Tests that exercise a symbol: `graphlore_tests_for(\"<name>\")`."),
    (("graphlore_path",),
     "How A reaches B: `graphlore_path(\"A\", \"B\")`, the most certain path."),
    (("graphlore_cycles", "graphlore_layers", "graphlore_unreferenced"),
     "Reviewing structure: `graphlore_cycles` (import cycles, runtime first), "
     "`graphlore_layers` (layer rules), `graphlore_unreferenced` (dead-code "
     "candidates: confirm each before deleting)."),
    (("graphlore_bridges", "graphlore_god_nodes"),
     "Where the code hangs by one link: `graphlore_bridges`; what everything leans "
     "on: `graphlore_god_nodes(rank=\"pagerank\")`."),
    (("graphlore_freshness", "graphlore_build"),
     "After editing code, `graphlore_freshness` says whether the graph is stale; "
     "`graphlore_build(update=True)` brings it up to date."),
)

_READING = (
    "An edge marked `INFERRED` was matched by name, not resolved: confirm it in the "
    "source before relying on it.",
    "An empty answer means no edge was indexed, not that the code has none: check "
    "with `rg`.",
    "A name several nodes share resolves to one, and the reply says which "
    "(`matched`); pass the node's id to pick another. A miss answers `did_you_mean`.",
    "Answers carry `file:line`. Read those lines before editing them.",
    "Analysis tools leave vendored files and external packages out by default and "
    "say so; `scope=\"-tests\"` leaves test code out.",
)


def render(tools: Iterable[str]) -> str:
    """The guide for an agent whose graphlore server registers ``tools``,
    between the markers."""
    have = set(tools)
    lines = [
        BEGIN,
        "## graphlore: this repository's code graph (MCP)",
        "",
        "The graphlore MCP server holds a graph of this repository: its definitions "
        "with exact `file:line` spans, and the calls, imports, references and bases "
        "between them. Use it for structure: who calls this, what breaks if it "
        "changes, where a behaviour lives, how two parts connect. Use `grep` / `rg` "
        "for an exact string or identifier: it is faster there.",
        "",
    ]
    lines += [f"- {text}" for needs, text in _FLOW if all(t in have for t in needs)]
    lines += ["", "Reading an answer:", ""]
    lines += [f"- {text}" for text in _READING]
    lines.append(END)
    return "\n".join(lines) + "\n"


def merge(existing: str, block: str) -> str:
    """``existing`` with ``block`` in place of what stands between the markers,
    or appended (after one blank line) when the file has none."""
    start, end = existing.find(BEGIN), existing.find(END)
    if start >= 0 and end > start:
        return existing[:start] + block.rstrip("\n") + existing[end + len(END):]
    if not existing.strip():
        return block
    return existing.rstrip("\n") + "\n\n" + block
