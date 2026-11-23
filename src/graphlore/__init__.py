"""graphlore — an MCP server exposing a codebase knowledge graph, joined to source spans."""

from typing import Any

__all__ = ["__version__", "api_uses_for_source", "main", "mcp", "routes_for_source"]

# Loaded on first use: importing graphlore.server brings in the MCP SDK and
# registers every tool (~0.6 s), which `graphlore hook` and the other quick
# subcommands of the CLI never need.
_LAZY = {
    "__version__": "graphlore.server",
    "main": "graphlore.server",
    "mcp": "graphlore.server",
    "api_uses_for_source": "graphlore.apis",
    "routes_for_source": "graphlore.routes",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'graphlore' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
