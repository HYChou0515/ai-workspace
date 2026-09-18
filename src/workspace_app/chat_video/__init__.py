"""A chat transcript rendered as a video (`docs/plan-chat-video.md`).

Three layers, each importable on its own:

- ``options`` / ``timeline`` — pure: the export's messages become a list of
  animation steps with a time estimate. No browser, no ffmpeg, so CI runs it
  and an API pod can import it without the ``chat-video`` extra.
- ``player`` — pure: the timeline becomes one self-contained HTML page (no
  CDN, nothing fetched) that plays itself and marks ``done`` when finished.
- ``render`` / ``service`` — the heavy end: a headless browser records the
  page, ffmpeg encodes it. Lazy imports, so the extra is needed only here.

``service.render_chat_video`` is the one entry point: the CLI calls it today,
and the job handler that a worker pod will run calls the same function.
"""
