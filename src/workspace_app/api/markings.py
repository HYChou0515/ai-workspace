"""Named markings sent with a chat message reach the AI (#847 PR 3 P7, Q10).

A marking is a linked selection — the picked key TUPLES (#861 D1): `keys` names
the columns, each row is one picked row's values on them, all opaque strings —
that the user kept as a chip when sending. The send:

1. writes each one to ``.markings/<name>.json`` through the file facade, so the
   workspace quota applies exactly as to any other write;
2. records it on the persisted user message (`SentMarking`), so a reload still
   shows the chip — a refused one with its reason;
3. gives the model one line per written marking: its name, how many rows by
   which keys, and the path to read them from (#861 D6: no cap — the file holds
   them all).

A refused write fails THAT chip, never the send: the question the user typed is
still a question, and the reason travels on the chip.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence

from ..files import WorkspaceFiles, WorkspaceFull, rel_path
from ..perm import Verb
from ..quota.disk_ledger import UserDiskFull
from ..resources.conversation import SentMarking
from ..sandbox.protocol import SandboxBusy, SandboxNotFound
from .schemas import MarkingInput

MARKINGS_DIR = "/.markings"
# A file name holds 255 bytes, `.json` takes five: measured as the disk does,
# since 100 emoji are 400 bytes.
MAX_NAME_BYTES = 250


def normalize_marking(
    keys: Sequence[str], rows: Sequence[Sequence[str]]
) -> tuple[list[str], list[list[str]]]:
    """A marking in its one spelling (#861 Formats): keys sorted, each row's
    values permuted to match, rows sorted and distinct. A row that does not hold
    one value per key is not a picked row and is dropped. The keys are taken to
    be distinct — `write_markings` refuses a marking that names one twice."""
    order = sorted(range(len(keys)), key=lambda i: keys[i])
    fitting = {tuple(row[i] for i in order) for row in rows if len(row) == len(keys)}
    return [keys[i] for i in order], [list(t) for t in sorted(fitting)]


def marking_digest(keys: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """One marking's picked tuples as a stable hash (#861 Formats): sha256 of the
    compact JSON of ``{"keys", "rows"}`` in their normalized spelling. The send
    records it on the chip (P7), and "save as table" from that chip compares it
    with the marking the rows were actually lit by — a later send under the
    same name rewrites the file."""
    keys, rows = normalize_marking(keys, rows)
    text = json.dumps({"keys": keys, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _name_problem(name: str) -> str | None:
    """Why `name` cannot be a file name under `.markings/`, or None.

    Names come from a spec's `marking:` and are otherwise opaque, so anything a
    file name can hold is fine — except what would leave the directory or hide."""
    if not name:
        return "a marking needs a name"
    if len(name.encode()) > MAX_NAME_BYTES:
        return "the name is too long for a file name"
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
    A marking with no keys, or no row that fits them, is not a marking and is
    dropped.

    `may_write(verb)` answers for the SENDER: None if they hold `verb`, else the
    reason. A new file asks `add_content`, replacing one asks `edit_content` —
    what every other write into the workspace asks. Sending a message only
    asks `converse`, so without this a member who may chat but not write could
    create or overwrite files here."""
    out: list[SentMarking] = []
    for m in markings:
        if len(set(m.keys)) != len(m.keys):
            twice = "a marking cannot name a column twice"
            out.append(SentMarking(name=m.name, source=m.source, error=twice))
            continue
        keys, rows = normalize_marking(m.keys, m.rows)
        if not keys or not rows:
            continue
        sent = SentMarking(name=m.name, count=len(rows), keys=keys, source=m.source)
        if (problem := _name_problem(m.name)) is not None:
            sent.error = problem
            out.append(sent)
            continue
        path = f"{MARKINGS_DIR}/{m.name}.json"
        doc = {
            "name": m.name,
            "sources": [m.source] if m.source else [],
            "keys": keys,
            "rows": rows,
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
        except OSError as exc:  # the name, or `.markings` a file: the disk said no
            sent.error = f"the marking could not be saved: {exc.strerror or 'the disk refused it'}"
        except (SandboxNotFound, SandboxBusy):
            sent.error = "the workspace could not be reached — send the marking again"
        else:
            sent.path = path
            sent.digest = marking_digest(keys, rows)
        out.append(sent)
    return out


def markings_prompt_block(sent: list[SentMarking]) -> str:
    """The model's view of the markings: one line each for those written. ""
    when there are none — a refused chip is the user's to see, not the model's."""
    lines = [
        f"- `{m.name}` ({m.count} {'row' if m.count == 1 else 'rows'} by {', '.join(m.keys)})"
        f" → {rel_path(m.path)}"
        for m in sent
        if m.path
    ]
    if not lines:
        return ""
    return (
        "The user sent these markings (selections made in linked views) with this "
        "message. Each file holds the picked rows: `keys` names the columns, and each "
        "entry of `rows` is one picked row's values in that order; read it for them.\n"
        + "\n".join(lines)
    )
