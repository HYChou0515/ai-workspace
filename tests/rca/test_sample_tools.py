from workspace_app.agent.provision import ToolDef
from workspace_app.rca.sample_tools import (
    SAMPLE_TOOLS,
    SOURCES,
)


def test_sample_tools_run_from_the_mounted_infra_dir():
    by_name = {t.name: t for t in SAMPLE_TOOLS}
    assert set(by_name) == {"data-fetch", "csv-column-summary"}
    for t in SAMPLE_TOOLS:
        # No prebuilt-copy: the sandbox mounts the tools at /.tools (../ from the
        # workspace), and invoke runs the self-contained launcher there.
        assert t.prebuilt is None
        assert t.invoke == [f"../.tools/{t.name}/launch"]
        assert t.name in SOURCES
    # the dataset name is an enum so the model can't invent a bad argument
    assert by_name["data-fetch"].params_json_schema["properties"]["name"]["enum"]


def test_available_returns_only_tools_with_a_built_package(tmp_path, monkeypatch):
    import workspace_app.rca.sample_tools as st

    monkeypatch.setattr(st, "PREBUILT_DIR", tmp_path)
    (tmp_path / "a").mkdir()  # 'a' is built; 'b' is not
    a, b = (
        ToolDef(name="a", description="", invoke=["x"]),
        ToolDef(name="b", description="", invoke=["x"]),
    )
    assert st.available_sample_tools([a, b]) == [a]
