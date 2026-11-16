"""graphlore's native code-graph extractor.

``build`` walks a project and writes a graphify-compatible ``graph.json``;
``file_spans`` / ``file_facts`` read one file. The tree-sitter grammars are
compiled in: nothing is downloaded, nothing reaches the network.
"""

from ._native import (
    SCHEMA_VERSION,
    __version__,
    build,
    file_facts,
    file_spans,
    languages,
)

__all__ = ["SCHEMA_VERSION", "__version__", "build", "file_facts", "file_spans", "languages"]
