"""Enable ``python -m graphlore``.

This is the collision-proof way to launch this server. ``graphifyy`` ships its
own ``graphlore`` console script (its embedded server), so this package
deliberately does NOT declare a bare ``graphlore`` of its own — it would be
shadowed by whichever installed last. Use the ``graphlore`` script or
``python -m graphlore``; both always run this package.
"""

from graphlore.server import main

if __name__ == "__main__":
    main()
