"""`LlmLogWriter` — persist LLM call records to disk (observability feature B).

Layout (date-partitioned so a day deletes in one `rm -rf`):

    <root>/
      replay.py                 # re-fire any record: python replay.py <file>
      2026-06-09/
        index.jsonl             # one summary line per call (scannable / grep)
        0001-acompletion-<id>.json   # the full faithful record
        0002-acompletion-<id>.json

Generative calls get a full per-call file + an index line; embedding/rerank
summaries get an index line only. The writer is synchronous — the litellm
logger calls it via ``asyncio.to_thread`` so file I/O never blocks the event
loop. JSON is written with ``default=str`` so an exotic value coerces rather
than raising mid-write.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class LlmLogWriter:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._seq = 0
        self._root.mkdir(parents=True, exist_ok=True)
        self._drop_replay_helper()

    def write_call(self, record: dict[str, Any]) -> Path:
        """Write one full record to its own file and append an index summary
        line. Returns the file path."""
        meta = record.get("meta", {})
        day_dir = self._day_dir(str(meta.get("ts", "")))
        self._seq += 1
        call_type = _safe(str(meta.get("call_type", "call")))
        corr = _safe(str(meta.get("litellm_call_id") or "nocorr"))
        path = day_dir / f"{self._seq:04d}-{call_type}-{corr}.json"
        path.write_text(_dumps(record, indent=2), encoding="utf-8")
        self._append_index(day_dir, {**meta, "seq": self._seq, "file": path.name})
        return path

    def write_summary(self, summary: dict[str, Any]) -> None:
        """Append a non-generative call's one-line summary to the index — no
        per-call file (vectors aren't worth a full record)."""
        day_dir = self._day_dir(str(summary.get("ts", "")))
        self._append_index(day_dir, summary)

    # ─── internals ──────────────────────────────────────────────────────

    def _day_dir(self, ts: str) -> Path:
        day = ts.split("T", 1)[0] or "unknown-date"
        d = self._root / day
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _append_index(self, day_dir: Path, entry: dict[str, Any]) -> None:
        with (day_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_dumps(entry) + "\n")

    def _drop_replay_helper(self) -> None:
        (self._root / "replay.py").write_text(_REPLAY_PY, encoding="utf-8")


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name)


def _dumps(obj: Any, *, indent: int | None = None) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=indent)


_REPLAY_PY = '''#!/usr/bin/env python
"""Re-fire a logged LLM call: `python replay.py <record.json>`.

Reads the record's `request` block (which is exactly litellm.completion kwargs)
and re-sends it, printing the reply. Streaming is turned off so the response
prints directly. Edit the JSON and re-run to test a prompt/param tweak."""

import json
import sys

import litellm

litellm.drop_params = True


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python replay.py <record.json>")
        raise SystemExit(2)
    record = json.loads(open(sys.argv[1], encoding="utf-8").read())
    req = {k: v for k, v in dict(record["request"]).items() if v is not None}
    req["stream"] = False
    resp = litellm.completion(**req)
    msg = resp.choices[0].message
    print(json.dumps(msg.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''
