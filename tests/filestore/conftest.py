from datetime import UTC, datetime

import pytest
from specstar import SpecStar

from workspace_app.filestore.specstar_impl import SpecstarFileStore


@pytest.fixture
def store() -> SpecstarFileStore:
    spec = SpecStar()
    spec.configure(default_user="test-user", default_now=lambda: datetime.now(UTC))
    return SpecstarFileStore(spec)
