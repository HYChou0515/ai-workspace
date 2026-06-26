You are drafting **context cards** — a lightweight glossary for a knowledge base.
A context card maps a term (and its surface forms) to a short, authoritative
explanation. Readers look cards up by EXACT key match, so every alias must be its
own key.

You are given one document. Draft a card for each term in it that a reader of this
collection might not already know: domain-specific terms, abbreviations, acronyms,
proper nouns, internal code names, or Chinese/English name pairs. Skip ordinary
words and anything only meaningful in this one document.

For each card produce:

- `title`: the human-readable display name (e.g. "Reflow Zone 3").
- `keys`: every surface form a reader might search, EACH as its own string — the
  abbreviation, the full name, English and Chinese forms, common misspellings.
  Keys are matched by exact normalised membership, so list `"M4"` and `"Metal 4"`
  as two keys; never collapse them into one and never write a sentence as a key.
- `body`: a concise Markdown explanation (1–4 sentences). State what it is, not
  where it appeared.
- `confident`: `true` if the document clearly defines the term; `false` if you are
  inferring or unsure (an uncertain card is reviewed but not committed by default).
- `snippet`: the short verbatim passage from the document that justifies the card,
  so a human reviewer can audit it. Quote the source text, do not paraphrase.

Output ONLY a JSON object of this exact shape — no prose, no code fence:

{"cards": [{"title": "...", "keys": ["...", "..."], "body": "...", "confident": true, "snippet": "..."}]}

If the document defines no glossary-worthy terms, output {"cards": []}.

Document path: {path}

Document:
{document}
