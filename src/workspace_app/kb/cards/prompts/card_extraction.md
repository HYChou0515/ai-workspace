You are reading one document from a technical corpus and recording what it
STATES about the terms it uses.

You are not writing definitions. You are recording claims, each with the words
from the document that make it. Another pass will turn the accumulated claims
about a term into its definition — it will have claims from documents you cannot
see, so a definition written here would be one facet of several and would be
thrown away.

Work through the terms a reader of this collection might not already know:
domain-specific terms, abbreviations, acronyms, proper nouns, internal code
names, Chinese/English name pairs. For each one the document says something
about, record:

- `term`: the surface form the document uses.
- `keys`: every surface form a reader might search for, EACH as its own string —
  the abbreviation, the full name, the English and Chinese forms. Readers look
  cards up by exact match, so list "M4" and "Metal 4" as two keys; never collapse
  them into one, and never write a sentence as a key.
- `statements`: what this document says about the term. For each:
  - `text`: the claim, in your own concise words.
  - `quote`: the passage from the document that states it, copied EXACTLY —
    character for character, in the document's own script and spelling.

Record only what the document says. If you would have to draw on knowledge from
outside this document to write a claim, that claim belongs to someone else's
knowledge base, not this one — leave it out. A term the document merely uses,
without saying anything about it, has no statements: leave the term out
entirely rather than recording an empty card.

Every quote is checked against the document and a claim whose quote is not found
is discarded, so paraphrasing a quote loses the claim it was carrying.

Output ONLY a JSON object of this exact shape — no prose, no code fence:

{"cards": [{"term": "...", "keys": ["...", "..."], "statements": [{"text": "...", "quote": "..."}]}]}

If the document states nothing about any such term, output {"cards": []}.

Document:
{text}
