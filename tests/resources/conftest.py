import pytest
from specstar import SpecStar

from workspace_app.resources import make_spec


@pytest.fixture
def spec_instance() -> SpecStar:
    s = make_spec(default_user="test-user")
    return s
