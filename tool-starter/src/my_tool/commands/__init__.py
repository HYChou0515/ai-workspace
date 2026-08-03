"""Every command your tool offers, by the name a model will call it with.

Two ways in, and `cli.py` treats them the same:

* **spelled out** — a module with `DESCRIPTION`, `Args` and `run`, listed here
  (`count`);
* **decorated** — one function carrying all three, which registers itself when
  imported (`head`, via `common.py`).

Use whichever suits the command. Mixing them, as here, is fine.
"""

from __future__ import annotations

from my_tool.commands import count, head  # noqa: F401 — `head` registers on import
from my_tool.common import REGISTERED

COMMANDS = {
    "count": count,
    **REGISTERED,
}
