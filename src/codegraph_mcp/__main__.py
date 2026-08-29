"""Enable ``python -m codegraph_mcp``.

This is the collision-proof way to launch this server. ``graphifyy`` ships its
own ``codegraph-mcp`` console script (its embedded server), so this package
deliberately does NOT declare a bare ``codegraph-mcp`` of its own — it would be
shadowed by whichever installed last. Use the ``codegraph-mcp`` script or
``python -m codegraph_mcp``; both always run this package.
"""

from codegraph_mcp.server import main

if __name__ == "__main__":
    main()
