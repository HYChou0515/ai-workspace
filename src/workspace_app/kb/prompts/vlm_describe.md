You are converting an image into searchable text for a knowledge base. The text is embedded for retrieval, so be complete and faithful — but stay concise: many of these images are slides and one-pagers whose content is already dense.

{context_line}Cover these three things, in order:

1. **What it shows and means** — describe the image's structure (diagram layout, chart type and trends, photographed objects, screenshot UI state) AND explain what it is trying to convey: the main point, the relationships between elements (arrows, groupings, hierarchy), and any conclusion it argues. Read every annotation a human added — boxes, circles, arrows, highlights, callouts, hand-written notes — and say what each one marks and why it matters.
2. **Verbatim transcription** — transcribe ALL visible text exactly as written (labels, axis ticks, captions, annotations, code, error messages). Do not paraphrase or translate. If the image has no visible text, write exactly: (no visible text)
3. **Tables and chart data** — render any table verbatim as a Markdown table; for charts, extract the underlying data points or series into a Markdown table when they are legible. If there are none, write exactly: (none)

Describe only what is in the image. Skip boilerplate that carries no information — company logos, branding, decorative chrome, page furniture — and never spend a sentence naming whose slide or template it is. Output the content only — no preamble, no commentary about the task.
