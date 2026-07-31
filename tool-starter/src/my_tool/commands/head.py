"""The same contract, written with the decorator (see `common.py`).

`count.py` next door spells the three pieces out; this one lets the decorator
assemble them. Both end up identical to `cli.py`, so pick whichever reads
better to you — and if you delete `common.py`, this is the only file to
rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from my_tool.common import command


class Args(BaseModel):
    path: str = Field(description="Path to the file, relative to the workspace root.")
    lines: int = Field(default=10, ge=1, le=200, description="How many lines to return.")


@command("head", description="Show the first lines of a text file in the user's workspace.")
def head(args: Args) -> str:
    target = Path(args.path)
    if not target.is_file():
        return json.dumps({"error": f"no such file in the workspace: {args.path}"})

    with target.open("r", encoding="utf-8", errors="replace") as fh:
        # Read only what was asked for: the file may be enormous, and the
        # answer is capped anyway.
        head_lines = [next(fh, "").rstrip("\n") for _ in range(args.lines)]

    return json.dumps({"path": args.path, "lines": [ln for ln in head_lines if ln != ""]})
