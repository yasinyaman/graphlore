"""Enable ``python -m graphlore``: the same as the ``graphlore`` command.

With no arguments it runs the MCP server; ``python -m graphlore setup codex``
and the other subcommands are those of ``graphlore.cli``.
"""

import sys

from graphlore.cli import main

if __name__ == "__main__":
    sys.exit(main())
