from datetime import UTC, datetime

import pytest
from specstar import SpecStar

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.rca.templates import seed_investigation
from workspace_app.resources import Investigation, Severity, Status


@pytest.fixture
def filestore() -> SpecstarFileStore:
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    return SpecstarFileStore(spec)


async def test_seed_writes_the_six_designed_files(filestore: SpecstarFileStore):
    inv = Investigation(title="Solder voids spike", owner="alice")
    written = await seed_investigation(filestore, "inv-1", inv)
    expected = {
        "/brief.md",
        "/drift.ipynb",
        "/pareto.ipynb",
        "/fishbone.canvas",
        "/5-why.md",
        "/report.v1.md",
        "/data/reflow.zone3.sample.csv",
    }
    assert set(written) == expected


async def test_markdown_substitutes_investigation_fields(
    filestore: SpecstarFileStore,
):
    inv = Investigation(
        title="Solder voids spike",
        owner="alice",
        description="Void rate 2.3x baseline since 14:00",
        product="MX-7 board",
        severity=Severity.P1,
        status=Status.TRIAGING,
    )
    await seed_investigation(filestore, "inv-1", inv)
    brief = (await filestore.read("inv-1", "/brief.md")).decode()
    assert "Solder voids spike" in brief
    assert "alice" in brief
    assert "MX-7 board" in brief
    assert "P1" in brief
    assert "triaging" in brief
    assert "Void rate 2.3x baseline" in brief


async def test_empty_product_renders_em_dash(filestore: SpecstarFileStore):
    inv = Investigation(title="t", owner="alice")  # no product
    await seed_investigation(filestore, "inv-1", inv)
    brief = (await filestore.read("inv-1", "/brief.md")).decode()
    assert "**Product**: —" in brief


async def test_non_markdown_files_come_through_unchanged(
    filestore: SpecstarFileStore,
):
    """CSV / ipynb / canvas are copied byte-for-byte. No substitution
    (so `{title}` inside an ipynb cell wouldn't get mangled)."""
    inv = Investigation(title="t", owner="alice")
    await seed_investigation(filestore, "inv-1", inv)

    csv = (await filestore.read("inv-1", "/data/reflow.zone3.sample.csv")).decode()
    assert csv.startswith("ts,zone3_setpoint,zone3_actual,void_rate")
    assert "245.0" in csv

    canvas = (await filestore.read("inv-1", "/fishbone.canvas")).decode()
    assert "Machine" in canvas
    assert "Environment" in canvas

    ipynb = (await filestore.read("inv-1", "/drift.ipynb")).decode()
    assert '"nbformat": 4' in ipynb


async def test_seed_into_multiple_investigations_is_isolated(
    filestore: SpecstarFileStore,
):
    await seed_investigation(filestore, "inv-1", Investigation(title="first", owner="alice"))
    await seed_investigation(filestore, "inv-2", Investigation(title="second", owner="bob"))
    b1 = (await filestore.read("inv-1", "/brief.md")).decode()
    b2 = (await filestore.read("inv-2", "/brief.md")).decode()
    assert "first" in b1 and "alice" in b1
    assert "second" in b2 and "bob" in b2
    assert "second" not in b1
    assert "first" not in b2
