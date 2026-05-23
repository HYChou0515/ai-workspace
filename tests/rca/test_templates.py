from datetime import UTC, datetime

import pytest
from specstar import SpecStar

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.rca.templates import list_profiles, seed_investigation
from workspace_app.resources import Investigation, Severity, Status


@pytest.fixture
def filestore() -> SpecstarFileStore:
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    return SpecstarFileStore(spec)


def test_list_profiles_includes_default_and_example():
    profiles = list_profiles()
    assert "default" in profiles
    assert "methodology" in profiles
    assert "smt-reflow-example" in profiles


async def test_default_profile_seeds_something(filestore: SpecstarFileStore):
    """The default profile is user-owned content — we don't pin its files,
    only that creating an investigation seeds at least one non-empty file."""
    inv = Investigation(title="Solder voids spike", owner="alice")
    written = await seed_investigation(filestore, "inv-1", inv)
    assert len(written) > 0
    first = (await filestore.read("inv-1", written[0])).decode()
    assert first.strip() != ""


# The methodology profile holds the blank skeleton (brief / 5-why / fishbone
# / report) the substitution + copy tests assert against — stable test
# content independent of whatever the user puts in `default`.
async def test_methodology_profile_skeleton(filestore: SpecstarFileStore):
    """methodology seeds title + methodology skeletons. .tpl suffix is
    stripped on the way in."""
    inv = Investigation(title="Solder voids spike", owner="alice")
    written = await seed_investigation(filestore, "inv-1", inv, profile="methodology")
    assert set(written) == {
        "/brief.md",
        "/5-why.md",
        "/fishbone.canvas",
        "/report.v1.md",
    }
    # report is just the title, no D1-D8 SOP
    report = (await filestore.read("inv-1", "/report.v1.md")).decode()
    assert report.strip() == "# RCA Report — Solder voids spike"


async def test_tpl_substitutes_investigation_fields(filestore: SpecstarFileStore):
    inv = Investigation(
        title="Solder voids spike",
        owner="alice",
        description="Void rate 2.3x baseline since 14:00",
        product="MX-7 board",
        severity=Severity.P1,
        status=Status.TRIAGING,
    )
    await seed_investigation(filestore, "inv-1", inv, profile="methodology")
    brief = (await filestore.read("inv-1", "/brief.md")).decode()
    assert "Solder voids spike" in brief
    assert "alice" in brief
    assert "MX-7 board" in brief
    assert "P1" in brief
    assert "triaging" in brief
    assert "Void rate 2.3x baseline" in brief
    # the $-style placeholders are fully substituted — no literal $ left
    assert "$title" not in brief


async def test_empty_product_renders_em_dash(filestore: SpecstarFileStore):
    inv = Investigation(title="t", owner="alice")  # no product
    await seed_investigation(filestore, "inv-1", inv, profile="methodology")
    brief = (await filestore.read("inv-1", "/brief.md")).decode()
    assert "**Product**: —" in brief


async def test_smt_example_profile_has_rich_files(filestore: SpecstarFileStore):
    """The worked-example profile demonstrates the full kit: notebooks,
    sample data, filled-in brief — copied verbatim for non-.tpl files."""
    inv = Investigation(title="t", owner="alice")
    written = await seed_investigation(filestore, "inv-ex", inv, profile="smt-reflow-example")
    assert "/drift.ipynb" in written
    assert "/pareto.ipynb" in written
    assert "/data/reflow.zone3.sample.csv" in written

    csv = (await filestore.read("inv-ex", "/data/reflow.zone3.sample.csv")).decode()
    assert csv.startswith("ts,zone3_setpoint,zone3_actual,void_rate")

    ipynb = (await filestore.read("inv-ex", "/drift.ipynb")).decode()
    assert '"nbformat": 4' in ipynb


async def test_non_tpl_files_copied_byte_for_byte(filestore: SpecstarFileStore):
    """fishbone.canvas has no .tpl suffix → copied verbatim, $-looking
    text inside (if any) is never substituted."""
    inv = Investigation(title="t", owner="alice")
    await seed_investigation(filestore, "inv-1", inv, profile="methodology")
    canvas = (await filestore.read("inv-1", "/fishbone.canvas")).decode()
    assert "Machine" in canvas
    assert "Environment" in canvas


async def test_unknown_profile_raises(filestore: SpecstarFileStore):
    inv = Investigation(title="t", owner="alice")
    with pytest.raises(ValueError, match="unknown template profile"):
        await seed_investigation(filestore, "inv-1", inv, profile="does-not-exist")


async def test_seed_into_multiple_investigations_is_isolated(
    filestore: SpecstarFileStore,
):
    await seed_investigation(
        filestore, "inv-1", Investigation(title="first", owner="alice"), profile="methodology"
    )
    await seed_investigation(
        filestore, "inv-2", Investigation(title="second", owner="bob"), profile="methodology"
    )
    b1 = (await filestore.read("inv-1", "/brief.md")).decode()
    b2 = (await filestore.read("inv-2", "/brief.md")).decode()
    assert "first" in b1 and "alice" in b1
    assert "second" in b2 and "bob" in b2
    assert "second" not in b1
    assert "first" not in b2
