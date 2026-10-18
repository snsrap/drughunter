"""``python -m proteinviewer [PORT] [--no-open]``: the same as ./serve.py."""

from .server import main

raise SystemExit(main())
