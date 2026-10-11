"""`env.json`, written from the backend's contract.

The platform's builder reads `env.json` beside `pyproject.toml` and shows each
variable in the person's environment panel. Here it is never written by hand:
the backend declares `Contract.env`, and

    python -m job_tool.envfile

rewrites the file from it. A shape test fails the build when the two differ,
so the panel cannot describe variables the backend no longer reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_contract import Contract

FILENAME = "env.json"


def render(contract: Contract) -> str | None:
    """The file's text, or None when the contract says nothing about variables
    — which is not "needs none", and must not be written as `[]`."""
    if contract.env is None:
        return None
    entries: list[dict[str, Any]] = []
    for var in contract.env:
        entry: dict[str, Any] = {"name": var.name}
        if var.description is not None:
            entry["description"] = var.description
        if var.required is not None:
            entry["required"] = var.required
        entries.append(entry)
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


def write(root: Path, contract: Contract) -> None:
    """Make `root/env.json` say what `contract` says, removing it when that is nothing."""
    target = root / FILENAME
    text = render(contract)
    if text is None:
        target.unlink(missing_ok=True)
    else:
        target.write_text(text, encoding="utf-8")


def main() -> None:  # pragma: no cover - a one-line wrapper over `write`
    from job_tool.discover import load_backend

    write(Path.cwd(), load_backend().contract)


if __name__ == "__main__":  # pragma: no cover
    main()
