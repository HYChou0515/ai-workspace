import pytest

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec


@pytest.fixture
def store() -> SpecstarFileStore:
    spec = make_spec(default_user="test-user")
    return SpecstarFileStore(spec)
