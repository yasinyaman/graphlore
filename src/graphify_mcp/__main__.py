"""Enable ``python -m graphify_mcp``.

This is the collision-proof way to launch this server: the ``graphify-mcp``
console script can be shadowed by the one ``graphifyy`` also ships (its embedded
server), but ``python -m graphify_mcp`` always runs this package.
"""

from graphify_mcp.server import main

if __name__ == "__main__":
    main()
