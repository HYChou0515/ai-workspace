# Plan — `read_image` agent tool (#112)

Give the live agent a `read_image` tool that hands a workspace image to the
VLM and returns its reply. The VLM stack (`IVlm`, `VlmDescriber`,
`LitellmVlm`) already exists but is wired only into KB *ingestion* parsers —
the gap is the interactive agent, which today can only `read_file` an image
into useless raw bytes.

Available on **three surfaces**: `rca`, `playground`, `topic-hub`. All build
`AgentToolContext` in `api/app.py` with the same `files` facade, so one tool
serves all three.

## Locked decisions (grill-me)

- **Signature** — `read_image(path, question=None)`. With a `question` →
  `IVlm.collect(question, …)` (no formatter); without → existing
  `VlmDescriber.describe()` (full OCR/describe). ("B but question optional".)
- **Dependency** — single field `describer: VlmDescriber | None` on
  `AgentToolContext`, injected at both `api/app.py` context-build sites from
  `create_app` (`get_kb_vlm` + `VlmDescriber(...)`, the same construction
  `factories.py` already uses). Raw `IVlm` stays encapsulated inside the
  describer; add `VlmDescriber.answer(image, mime, *, question, on_chunk)`.
- **No VLM configured** (`describer is None`) — tool still registers; impl
  returns a clear error telling the caller the deployment has **no VLM
  registered, do not retry** (no assert, no dynamic ceiling removal — the
  ceiling is a static manifest).
- **Image bytes** — via the `files` facade (`_workspace(ctx)` → `fs.read(inv,
  path)`), raw bytes (not decoded). `FileNotFound` → `error: file not found`.
- **mime** — magic-sniff, the same `magic.from_buffer(data, mime=True)` KB
  ingest uses (`kb/ingest.py:232`). Reject when not `image/*` →
  `error: not an image (detected {mime})`. No extension whitelist.
- **Streaming** — relay the VLM stream live to the tool card via
  `ctx.on_exec_output` (same as `exec` / `ask_knowledge_base`). Adapter
  `OnChunk → OutputSink`: `lambda t, _r: sink(t.encode("utf-8"))` when a sink
  is set. Return value is `collect()`'s non-reasoning content.
- **Output cap** — `_truncate_middle(out, ctx.read_file_max_chars)` (200k, the
  read_file cap — an image description is a *read*, not noisy exec output).
- **No cache.**
- **Registration** — `read_image_impl` → `_IMPLS`; `"read_image"` added to the
  `agent.tools` ceiling in `apps/{rca,playground,topic-hub}/app.json`.

## Phases (TDD, flat integers)

- **Phase 1** — `VlmDescriber.answer(image, mime, *, question, on_chunk=None)`:
  raw `self._vlm.collect(question, images=[(image, mime)], on_chunk=...)`,
  bypassing the formatter. Unit test with a fake `IVlm`.
- **Phase 2** — `describer` field on `AgentToolContext` (default `None`).
- **Phase 3** — `read_image_impl` in `agent/tools.py` + entry in `_IMPLS`.
  Tests (fake `IVlm`/describer, in-memory filestore via `WorkspaceFiles`):
  - question given → `answer()` path, non-image-aware reply returned;
  - no question → `describe()` path;
  - `describer is None` → no-VLM error, no call made;
  - non-image bytes → `not an image` error, VLM never called;
  - file not found → `error: file not found`;
  - streaming relayed to `on_exec_output`;
  - output over cap → truncated.
- **Phase 4** — wire `describer` into both `AgentToolContext(...)` sites in
  `api/app.py` from `create_app` (`get_kb_vlm`/`VlmDescriber`); add
  `"read_image"` to the three `app.json` ceilings.
- **Phase 5** — gate: full suite + 100% coverage, ruff, ty.
