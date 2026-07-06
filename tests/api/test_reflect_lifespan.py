"""#479: the daily reflection sweeper is actually wired into the app lifespan.

When ``create_app(wiki_reflect_daily=…)`` is set, the lifespan starts a background
task that enqueues a ``reflect`` job for every due prose wiki collection — proving
the hook is wired (not just the helper class). No wiki LLM is configured here, so
the observable side-effect is the build status recording the misconfig the reflect
job hits (the same pattern as the code-build no-LLM route test)."""

from __future__ import annotations

import asyncio

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def test_lifespan_runs_the_reflect_sweeper():
    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_pipeline=build_doc_pipeline(embedder=text),
        # "00:00" is always past today, so a never-reflected prose wiki collection
        # is due on the sweeper's first (startup) tick regardless of wall-clock.
        wiki_reflect_daily="00:00",
    )
    client = TestClient(application)
    cid = client.post("/kb/collections", json={"name": "kb", "use_wiki": True}).json()[
        "resource_id"
    ]
    with client:  # enter lifespan → the reflect sweeper ticks at startup

        async def _wait() -> str | None:
            for _ in range(40):  # ~2s budget
                st = client.get(f"/kb/collections/{cid}/wiki/status").json()
                if st.get("last_error"):
                    return st["last_error"]
                await asyncio.sleep(0.05)
            return None

        err = asyncio.run(_wait())
    assert err and "wiki LLM not configured" in err
