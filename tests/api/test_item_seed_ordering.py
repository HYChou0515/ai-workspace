"""Creating an App item must seed ALL its durable files BEFORE the WorkItem
resource exists — otherwise the item is discoverable/warmable while its files are
still being written, and a sandbox warm that lands in that window restores a
partial set and serves it (the reported "PM item only has one file").

The facade serves any live sandbox regardless of readiness, so the only robust
defence is to close the window: durable is complete the moment the item appears.
This pins that ordering invariant.
"""

from workspace_app.apps.registry import app_model


def test_create_seeds_all_durable_files_before_the_workitem_exists(harness):
    order: list[str] = []

    fs = harness.filestore
    orig_write = fs.write

    async def rec_write(ws, path, data, *a, **k):
        order.append(f"write:{path}")
        return await orig_write(ws, path, data, *a, **k)

    fs.write = rec_write  # type: ignore[method-assign]

    rm = harness.spec.get_resource_manager(app_model("pm"))
    orig_create = rm.create

    def rec_create(item, **k):
        order.append("create-workitem")
        return orig_create(item, **k)

    rm.create = rec_create  # type: ignore[method-assign]

    r = harness.client.post("/a/pm/items", json={"title": "Demo project"})
    assert r.status_code == 200, r.text

    assert "create-workitem" in order, "the WorkItem was never created"
    writes = [i for i, e in enumerate(order) if e.startswith("write:")]
    assert writes, "no profile files were seeded"
    create_at = order.index("create-workitem")
    assert all(w < create_at for w in writes), (
        "the WorkItem became discoverable before its files were seeded — a warm "
        f"racing the seed can serve a partial workspace. order={order}"
    )
