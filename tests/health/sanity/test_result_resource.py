"""SanityResult — current-only matrix cell, with a slash-free natural-key id."""

from __future__ import annotations

from specstar import QB

from workspace_app.resources import SanityResult, make_spec, sanity_result_id


def test_id_is_deterministic_and_slash_free():
    a = sanity_result_id("ollama_chat/qwen3:14b", "abc123", "none")
    b = sanity_result_id("ollama_chat/qwen3:14b", "abc123", "none")
    assert a == b  # same cell → same id (so a re-run upserts)
    assert "/" not in a
    # different level / model / question → different id
    assert a != sanity_result_id("ollama_chat/qwen3:14b", "abc123", "medium")
    assert a != sanity_result_id("ollama_chat/qwen3:8b", "abc123", "none")


def test_resource_is_registered_and_queryable_by_model():
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(SanityResult)
    rid = sanity_result_id("m1", "q1", "none")
    rm.create(
        SanityResult(model="m1", question_key="q1", level="none", output="台北", grade="pass"),
        resource_id=rid,
    )
    rm.create(
        SanityResult(model="m2", question_key="q1", level="none", output="x"),
        resource_id=sanity_result_id("m2", "q1", "none"),
    )
    # FE matrix hydration = list filtered by model
    got = [r.data for r in rm.list_resources((QB["model"] == "m1").build())]
    assert len(got) == 1
    only = got[0]
    assert isinstance(only, SanityResult)  # narrow Struct | UnsetType for ty
    assert only.output == "台北" and only.grade == "pass"

    # re-run overwrites the same id (current-only)
    rm.update(rid, SanityResult(model="m1", question_key="q1", level="none", output="臺北市"))
    again = rm.get(rid).data
    assert isinstance(again, SanityResult)
    assert again.output == "臺北市"
