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

Record only claims about WHAT THE TERM IS — claims that would still mean the
same thing in a document you have not read. A reader looking the term up later
has this card and nothing else.

「H2O2 是一種氧化劑」 is such a claim. Two kinds are not, and both are true
sentences you could quote exactly:

- **The occasion instead of the thing.** 「H2O2 是這次的材料」 — 這次 points at this
  document's own occasion, so outside it the sentence points at nothing. Same for
  本批 / 目前 / 我們這邊 / 上述.
- **A finding instead of a meaning.** "14k ratio increased from 7% in wave 1 to
  20% in wave 2" says what HAPPENED to the thing. A reader who looked the term up
  wanted to know what it IS; measurements, trends and results are not that,
  however important they are.

If the only thing a document says about a term is which run used it or what it
measured, that term gets no statement from this document. Leaving it out is the
right answer — another document may define it, and the card is written from all
of them together.

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
