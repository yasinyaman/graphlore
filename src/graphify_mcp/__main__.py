"""Enable ``python -m graphify_mcp``.

This is the collision-proof way to launch this server. ``graphifyy`` ships its
own ``graphify-mcp`` console script (its embedded server), so this package
deliberately does NOT declare a bare ``graphify-mcp`` of its own — it would be
shadowed by whichever installed last. Use the ``graphify-mcp-server`` script or
``python -m graphify_mcp``; both always run this package.
"""

from graphify_mcp.server import main

if __name__ == "__main__":
    main()
