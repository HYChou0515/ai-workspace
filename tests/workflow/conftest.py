import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.workflow.handle import WorkflowHandle


@pytest.fixture
def spec_instance() -> SpecStar:
    return make_spec(default_user="test-user")


@pytest.fixture
def wf() -> WorkflowHandle:
    """A run handle over an in-memory workspace, for engine/handle tests."""
    return WorkflowHandle(
        store=MemoryFileStore(), workspace_id="ws", config={"collections": ["a", "b"]}, user="alice"
    )
