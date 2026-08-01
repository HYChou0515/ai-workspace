"""One command, complete. Read it as the shape, not as the feature.

It counts lines and words in a workspace file — chosen because it exercises
the two things every tool gets wrong the first time: paths are relative to the
user's WORKSPACE (not to your tool), and everything you print to stdout is the
answer the model reads.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from my_tool.common import NeedsAction, Retryable

# The model decides whether to call your tool from this sentence alone, and
# gets the arguments right (or not) from the field descriptions. It is the
# highest-leverage text in the whole package — vaguer than this and the tool
# either goes unused or gets called with nonsense.
DESCRIPTION = "Count the lines and words in a text file in the user's workspace."


class Args(BaseModel):
    path: str = Field(
        description="Path to the file, relative to the workspace root, e.g. 'notes/log.txt'."
    )
    ignore_blank_lines: bool = Field(
        default=False, description="Skip lines that are empty or contain only whitespace."
    )


def run(args: Args) -> str:
    # Relative to the process's cwd, which the platform sets to the user's
    # workspace. Do NOT resolve against your own location: your tool is mounted
    # read-only somewhere else entirely, and a path built from __file__ points
    # at the wrong tree.
    target = Path(args.path)
    if not target.is_file():
        # Retryable: the model picked the path, so the model can pick a better
        # one. Naming what was looked for is what makes that possible.
        raise Retryable(f"no such file in the workspace: {args.path}")

    try:
        text = target.read_text("utf-8", errors="replace")
    except PermissionError as exc:
        # Not the model's mistake, and calling again changes nothing — a
        # person has to fix the permission. That distinction is the whole
        # reason `NeedsAction` exists; naming the file is what lets them act.
        raise NeedsAction(
            f"cannot read {args.path}: it is not readable by this tool. "
            "Change its permissions, then ask again."
        ) from exc

    lines = text.splitlines()
    if args.ignore_blank_lines:
        lines = [ln for ln in lines if ln.strip()]

    # Keep the answer small. The platform caps tool output, and a truncated
    # reply is worse than a summary — if you have a lot to say, write a file
    # into the workspace and return its path.
    return json.dumps(
        {"path": args.path, "lines": len(lines), "words": sum(len(ln.split()) for ln in lines)}
    )
