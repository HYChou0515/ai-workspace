"""Built-in deterministic gates (#100, manual §6).

Gates are postconditions on a step's result, verified mechanically wherever
possible (a hard guarantee, vs an LLM judging an LLM). Each builder returns a
``Check`` — a coroutine ``(wf, result) -> CheckResult``. The file-based gates here
only need the workspace; capability gates (``collection_has``) and sandbox
predicates (``exec``) arrive with their phases.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..filestore.protocol import FileNotFound
from .engine import Check, CheckResult
from .handle import WorkflowHandle

# The artifact formats a channel-P (prose) ``out`` may declare (plan §2.3). The
# structured kinds (json/yaml/csv) are validated by PARSING — so a reply that leaks
# conversational text ("Sure! {…}") fails to parse and the gate rejects it, which is
# exactly how the "file content is the AI's reply" bug is caught. The prose kinds
# (markdown/text/code) have no strong machine format, so L1 only checks non-emptiness;
# their structural strength comes from a producer-declared ``requires`` (plan §2.3 L2).
ARTIFACT_KINDS = ("markdown", "json", "csv", "yaml", "code", "text")


def _valid_json(text: str) -> str:
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "is not valid JSON (did the reply include conversational text around it?)"
    return ""


def _valid_yaml(text: str) -> str:
    import yaml

    try:
        yaml.safe_load(text)
    except yaml.YAMLError:
        return "is not valid YAML (did the reply include conversational text around it?)"
    return ""


def _valid_csv(text: str) -> str:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return "is not valid CSV"
    if not any(row for row in rows):
        return "has no CSV rows"
    return ""


def artifact_valid(path: str, kind: str) -> Check:
    """The channel-P (prose ``out``) default gate (plan §2.3 L1): the written file exists,
    is non-empty, and — for a structured ``kind`` — PARSES as that format. It never
    rewrites the file (no sanitize, plan §2.4): a polluted artifact FAILS the gate and its
    reason is fed back into the step's retry, so the model re-produces clean output at the
    source instead of the platform silently munging it."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            data = await wf.read(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        if not data.strip():
            return CheckResult(False, f"file {path} is empty")
        text = data.decode("utf-8", "replace")
        problem = ""
        if kind == "json":
            problem = _valid_json(text)
        elif kind == "yaml":
            problem = _valid_yaml(text)
        elif kind == "csv":
            problem = _valid_csv(text)
        if problem:
            return CheckResult(False, f"file {path} {problem}")
        return CheckResult(True)

    return _check


def file_nonempty(path: str) -> Check:
    """The agent actually wrote ``path`` and it has content."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            data = await wf.read(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        if not data.strip():
            return CheckResult(False, f"file {path} is empty")
        return CheckResult(True)

    return _check


def choice_in(path: str, *, key: str, allowed: list[Any]) -> Check:
    """The decision recorded at ``path[key]`` is within the allowed set (manual §8:
    clamp the agent's choice deterministically — the prompt may suggest, the gate
    enforces). On a bad pick the reason is fed back so the agent re-picks."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            obj = await wf.read_json(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        value = obj.get(key) if isinstance(obj, dict) else None
        if value not in allowed:
            return CheckResult(False, f"{key}={value!r} is not one of {allowed}")
        return CheckResult(True)

    return _check


def collection_has(collection: str, path: str) -> Check:
    """The deterministic ingest actually landed ``path`` in ``collection`` as a
    ``ready`` doc (manual §8) — a hard guarantee on the reliable side-effect, read
    back from the KB rather than trusting the node's exit code."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        if wf._collection_has is None:
            return CheckResult(
                False, "collection_has needs the KB capability (wired by the run driver)"
            )
        landed = await wf._collection_has(collection, path)
        if not landed:
            return CheckResult(
                False, f"{path!r} did not land in collection {collection!r} as ready"
            )
        return CheckResult(True)

    return _check
