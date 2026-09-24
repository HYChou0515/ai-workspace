"""Named markings sent with a chat message reach the AI (#847 PR 3 P7, Q10).

A marking is a linked selection — `column → values`, both opaque strings — that
the user kept as a chip when sending. The send:

1. writes each one to ``.markings/<name>.json`` through the file facade, so the
   workspace quota applies exactly as to any other write;
2. records it on the persisted user message (`SentMarking`), so a reload still
   shows the chip — a refused one with its reason;
3. gives the model one line per written marking: its name, how many values per
   column, and the path to read them from.

A refused write fails THAT chip, never the send: the question the user typed is
still a question, and the reason travels on the chip.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ..files import WorkspaceFiles, WorkspaceFull, rel_path
from ..perm import Verb
from ..quota.disk_ledger import UserDiskFull
from ..resources.conversation import SentMarking
from .schemas import MarkingInput

MARKINGS_DIR = "/.markings"
MAX_NAME = 100


def _name_problem(name: str) -> str | None:
    """Why `name` cannot be a file name under `.markings/`, or None.

    Names come from a spec's `marking:` and are otherwise opaque, so anything a
    file name can hold is fine — except what would leave the directory or hide."""
    if not name:
        return "a marking needs a name"
    if len(name) > MAX_NAME:
        return f"the name is longer than {MAX_NAME} characters"
    if "/" in name or "\\" in name or "\0" in name or name.startswith("."):
        return "the name cannot be used as a file name"
    return None


async def write_markings(
    files: WorkspaceFiles,
    workspace_id: str,
    markings: list[MarkingInput],
    may_write: Callable[[Verb], str | None],
) -> list[SentMarking]:
    """Write each non-empty marking; one `SentMarking` per marking, in order.
    A marking with no values is not a marking and is dropped.

    `may_write(verb)` answers for the SENDER: None if they hold `verb`, else the
    reason. A new file asks `add_content`, replacing one asks `edit_content` —
    what every other write into the workspace asks. Sending a message only
    asks `converse`, so without this a member who may chat but not write could
    create or overwrite files here."""
    out: list[SentMarking] = []
    for m in markings:
        columns = {c: sorted(set(v)) for c, v in m.columns.items() if v}
        if not columns:
            continue
        counts = {c: len(v) for c, v in columns.items()}
        sent = SentMarking(name=m.name, counts=counts, source=m.source)
        if (problem := _name_problem(m.name)) is not None:
            sent.error = problem
            out.append(sent)
            continue
        path = f"{MARKINGS_DIR}/{m.name}.json"
        doc = {
            "name": m.name,
            "sources": [m.source] if m.source else [],
            "columns": columns,
        }
        verb: Verb = "edit_content" if await files.exists(workspace_id, path) else "add_content"
        if (refused := may_write(verb)) is not None:
            sent.error = refused
            out.append(sent)
            continue
        try:
            await files.write(
                workspace_id, path, json.dumps(doc, ensure_ascii=False, indent=1).encode()
            )
        except (WorkspaceFull, UserDiskFull) as exc:
            sent.error = str(exc)
        else:
            sent.path = path
        out.append(sent)
    return out


def markings_prompt_block(sent: list[SentMarking]) -> str:
    """The model's view of the markings: one line each for those written. ""
    when there are none — a refused chip is the user's to see, not the model's."""
    lines = [
        f"- `{m.name}` ({', '.join(f'{c}: {n}' for c, n in m.counts.items())}) → {rel_path(m.path)}"
        for m in sent
        if m.path
    ]
    if not lines:
        return ""
    return (
        "The user sent these markings (selections made in linked views) with this "
        "message. Each file holds the marked values per column; read it for them.\n"
        + "\n".join(lines)
    )
