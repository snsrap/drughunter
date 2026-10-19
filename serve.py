#!/usr/bin/env python3
"""Serve the viewer's page (proteinviewer/app/) over HTTP and open it in a browser.

    ./serve.py            # port 8000, opens a browser
    ./serve.py 9000       # pick a port
    ./serve.py --no-open  # just serve

The server itself is the proteinviewer package; see proteinviewer/server.py.
This file stays as the entry point start.sh looks for.
"""

from proteinviewer.server import main

if __name__ == "__main__":
    raise SystemExit(main())
