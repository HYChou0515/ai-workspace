"""Running a candidate criterion against real documents, offline.

The point of the whole split: `build_cards` holds no store, so a folder of text
files drawn once from the corpus can be run against as often as a person likes,
and iterating costs a model call per document and nothing else. What lands on
disk is what a person diffs between attempts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.cards.preview import preview_samples
from workspace_app.kb.llm import ILlm


class _Corpus(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "STATEMENTS" in prompt:
            yield json.dumps({"title": "回焊爐", "body": "一種加熱設備"}), False
        elif "回焊爐是一種加熱設備" in prompt:
            yield (
                (
                    json.dumps(
                        {
                            "cards": [
                                {
                                    "term": "回焊爐",
                                    "keys": ["回焊爐"],
                                    "statements": [
                                        {"text": "是一種加熱設備", "quote": "回焊爐是一種加熱設備"}
                                    ],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
                False,
            )
        else:
            yield '{"cards": []}', False


def _samples(tmp_path):
    folder = tmp_path / "tune"
    folder.mkdir()
    (folder / "a.txt").write_text("回焊爐是一種加熱設備。")
    (folder / "b.txt").write_text("這份文件沒有定義任何東西。")
    return folder


def test_a_folder_of_documents_becomes_cards_on_disk(tmp_path):
    out = tmp_path / "out"

    cards = preview_samples(_Corpus(), _samples(tmp_path), out_dir=out)

    (card,) = cards
    assert card.body == "一種加熱設備"
    written = json.loads((out / "cards.json").read_text())
    assert written[0]["keys"] == ["回焊爐"]
    assert written[0]["statements"][0]["quote"] == "回焊爐是一種加熱設備"


def test_the_summary_counts_what_a_person_would_check_first(tmp_path):
    """Documents read, cards produced, and how much evidence each rests on —
    a card standing on one statement from one document is the shape that used
    to proliferate."""
    out = tmp_path / "out"

    preview_samples(_Corpus(), _samples(tmp_path), out_dir=out)

    summary = json.loads((out / "summary.json").read_text())
    assert summary["documents"] == 2
    assert summary["cards"] == 1
    assert summary["statements"] == 1
    assert summary["statements_per_card"] == 1.0
