from specstar import SpecStar

from workspace_app.resources.eval import EvalBatchStat, EvalResult, EvalRun


def test_make_spec_registers_eval_resources(spec_instance: SpecStar):
    for model in (EvalResult, EvalRun, EvalBatchStat):
        assert spec_instance.get_resource_manager(model) is not None


def test_eval_result_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(EvalResult)
    rev = rm.create(
        EvalResult(
            collection_id="c1",
            run_label="r1",
            sample_size=300,
            n_kept=280,
            recall_chunk={"1": 0.42, "5": 0.71},
            mrr_chunk=0.5,
        )
    )
    got = rm.get(rev.resource_id).data
    assert isinstance(got, EvalResult)
    assert got.collection_id == "c1"
    assert got.recall_chunk == {"1": 0.42, "5": 0.71}
    assert got.mrr_chunk == 0.5


def test_eval_run_join_state_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(EvalRun)
    rev = rm.create(EvalRun(collection_id="c1", run_label="r1", total=3, done=[0, 1]))
    got = rm.get(rev.resource_id).data
    assert isinstance(got, EvalRun)
    assert got.total == 3
    assert got.done == [0, 1]
    assert got.finalized is False
