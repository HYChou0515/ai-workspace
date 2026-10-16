"""`python -m workspace_app.capacity check` — see `capacity.check`."""

from __future__ import annotations

import sys

from .check import main

if __name__ == "__main__":
    sys.exit(main())
