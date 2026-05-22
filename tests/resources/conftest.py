from datetime import UTC, datetime

import pytest
from specstar import SpecStar


@pytest.fixture
def spec_instance() -> SpecStar:
    s = SpecStar()
    s.configure(default_user="test-user", default_now=lambda: datetime.now(UTC))
    return s
