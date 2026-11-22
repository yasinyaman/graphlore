"""graphlore — an MCP server exposing a codebase knowledge graph, joined to source spans."""

from graphlore.apis import api_uses_for_source
from graphlore.routes import routes_for_source
from graphlore.server import __version__, main, mcp

__all__ = ["__version__", "api_uses_for_source", "main", "mcp", "routes_for_source"]
