"""Offline preview for the context-card criterion.

    uv run python -m workspace_app.card_preview --samples ./rounds/tune -o ./out

Runs the computation production would run (``kb.cards.build.build_cards``) over
plain text files and writes JSON. It opens no store — the module it calls holds
no ``SpecStar`` — so there is nothing it could write to even by mistake.

The sample folder is the one ``graph_preview --dump-samples`` produces. Drawing
the documents is the one read of the real corpus, and both pipelines read the
same draw, which is what makes their outputs comparable.
"""
