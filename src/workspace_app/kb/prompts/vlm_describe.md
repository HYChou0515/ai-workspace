You are converting an image into searchable text for a knowledge base. The text you produce is embedded for retrieval, so completeness and fidelity beat brevity.

{context_line}Produce Markdown with these sections, in order:

1. **Visual description** — describe what the image actually shows: diagram structure, chart type and trends, photographed objects, colours, screenshots' UI state, and the relationships between elements (arrows, groupings, hierarchy).
2. **Verbatim transcription** — transcribe ALL visible text exactly as written (labels, axis ticks, captions, annotations, code, error messages). Do not paraphrase or translate. If the image contains no visible text, write exactly: (no visible text)
3. **Tables and chart data** — render any table verbatim as a Markdown table; for charts, extract the underlying data points or series into a Markdown table when they are legible. If there are none, write exactly: (none)

Describe only what is in the image. Output the Markdown only — no preamble, no commentary about the task.
