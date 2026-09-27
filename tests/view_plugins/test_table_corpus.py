"""Entity records in `wire-corpus/table-rows.json` are what the API sends (#847/#848 PR 5).

The corpus pairs each record, as the browser's table receives it, with the
marking strings a chart writes for it; the browser's reading is held to the
pairs (`web/src/lib/markingRows.test.ts`). The corpus script writes the
records with its own JSON encoder (the sandbox has no pydantic), so this
holds that encoder to the serializer the list route answers with.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from workspace_app.api.entity_routes import _entity_out
from workspace_app.entity.catalog import discover_catalog
from workspace_app.entity.store import EntityStore
from workspace_app.filestore.memory import MemoryFileStore

CORPUS = (
    Path(__file__).resolve().parents[2]
    / "view-plugins"
    / "chart"
    / "wire-corpus"
    / "table-rows.json"
)


async def _sent(files: dict[str, str], type_name: str) -> list[dict]:
    fs = MemoryFileStore()
    for path, text in files.items():
        await fs.write("ws", path, text.encode())
    catalog, _ = await discover_catalog(fs, "ws")
    result = await EntityStore(fs, "ws", catalog).query(type_name)
    return [_entity_out(e).model_dump(mode="json") for e in result.entities]


def test_each_corpus_record_is_what_the_list_route_sends():
    for case in json.loads(CORPUS.read_text())["entities"]:
        sent = asyncio.run(_sent(case["files"], case["type"]))
        assert [{"number": e["number"], "fields": e["fields"]} for e in sent] == case["records"]
