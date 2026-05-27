from workspace_app.agent.provision import ToolDef
from workspace_app.rca.sample_tools import (
    SAMPLE_TOOLS,
    SOURCES,
    available_sample_tools,
)


def test_sample_tools_are_prebuilt_copy_defs():
    by_name = {t.name: t for t in SAMPLE_TOOLS}
    assert set(by_name) == {"data-fetch", "csv-column-summary"}
    for t in SAMPLE_TOOLS:
        # prebuilt-copy form: a prebuilt package + a workspace-relative install
        # dir, and invoke runs the copied venv binary (no uv at runtime).
        # tools install OUTSIDE the workspace (the infra area, via ../)
        assert t.prebuilt and t.install_dir == f"../.tools/{t.name}"
        assert t.invoke == [f"../.tools/{t.name}/launch"]  # self-contained launcher
        assert t.name in SOURCES
    # the dataset name is an enum so the model can't invent a bad argument
    assert by_name["data-fetch"].params_json_schema["properties"]["name"]["enum"]


def test_available_returns_only_tools_with_a_built_package(tmp_path):
    built = ToolDef(name="a", description="", invoke=["x"], prebuilt=str(tmp_path))
    missing = ToolDef(name="b", description="", invoke=["x"], prebuilt=str(tmp_path / "nope"))
    assert available_sample_tools([built, missing]) == [built]
