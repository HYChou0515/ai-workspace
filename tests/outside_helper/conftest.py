"""The outside helper (`outside-helper/`) is its own package, installed on the
person's PC, not part of `workspace_app`. Its tests run here, against the real
backend, so the contract between the two is tested from both sides."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "outside-helper" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
