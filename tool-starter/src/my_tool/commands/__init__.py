"""Every command your tool offers, by the name the model will call it with.

Adding one: write a module beside this file with `DESCRIPTION`, an `Args`
model and a `run(args)`, then add a line here. Nothing else changes.
"""

from __future__ import annotations

from my_tool.commands import count

COMMANDS = {
    "count": count,
}
