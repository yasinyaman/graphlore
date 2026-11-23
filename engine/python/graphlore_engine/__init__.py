"""graphlore's native code-graph extractor.

``build`` walks a project and writes a graphify-compatible ``graph.json``;
``cluster`` detects its communities again;
``file_spans`` / ``definition_spans`` / ``file_facts`` read one file;
``SearchKernel`` is the kernel of graphlore's built-in search (``KERNEL_ABI``).
The tree-sitter grammars are compiled in: nothing is downloaded, nothing
reaches the network.
"""

from ._native import (
    KERNEL_ABI,
    SCHEMA_VERSION,
    SearchKernel,
    __version__,
    build,
    cluster,
    definition_spans,
    file_facts,
    file_spans,
    languages,
)

__all__ = [
    "KERNEL_ABI", "SCHEMA_VERSION", "SearchKernel", "__version__", "build", "cluster",
    "definition_spans", "file_facts", "file_spans", "languages",
]
