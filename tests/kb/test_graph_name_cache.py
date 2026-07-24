"""#633 P4 — the index has to come from somewhere, and stay cheap.

Building it means reading every identity's names, which is the one expensive
thing in this design (seconds at 40k rows). Doing that per message would be
worse than the problem it solves, so it is built once and reused, with a TTL as
the only freshness mechanism.

A stale index is SAFE, and that is what makes the TTL acceptable: a name it
hasn't learned yet simply isn't auto-injected, and the agent's dossier tool
still finds it by querying the database directly. Nothing becomes wrong; one
convenience is briefly missing.
"""

from __future__ import annotations

from workspace_app.kb.graph.name_cache import NameIndexCache


def test_it_builds_once_and_reuses():
    calls = []

    def load():
        calls.append(1)
        return {"回焊爐": ("e:1",)}

    cache = NameIndexCache(load, ttl_s=60, now=lambda: 0.0)
    assert cache.get().hits("回焊爐") == {"回焊爐": ("e:1",)}
    cache.get()
    cache.get()
    assert len(calls) == 1


def test_it_rebuilds_after_the_ttl():
    clock = {"t": 0.0}
    versions = [{"aa": ("e:1",)}, {"aa": ("e:1",), "bb": ("e:2",)}]

    def load():
        return versions[min(len(versions) - 1, int(clock["t"] // 100))]

    cache = NameIndexCache(load, ttl_s=60, now=lambda: clock["t"])
    assert len(cache.get()) == 1
    clock["t"] = 30.0
    assert len(cache.get()) == 1  # still inside the TTL
    clock["t"] = 100.0
    assert len(cache.get()) == 2  # past it, rebuilt


def test_a_failed_rebuild_keeps_serving_the_old_index():
    """A database hiccup must not turn auto-injection off AND take the turn down
    with it. The stale answer is the safe one here."""
    state = {"fail": False}

    def load():
        if state["fail"]:
            raise RuntimeError("db down")
        return {"回焊爐": ("e:1",)}

    clock = {"t": 0.0}
    cache = NameIndexCache(load, ttl_s=60, now=lambda: clock["t"])
    assert len(cache.get()) == 1
    state["fail"] = True
    clock["t"] = 1_000.0
    assert len(cache.get()) == 1  # served from the previous build, not an error


def test_the_first_build_failing_degrades_to_no_injection():
    """With nothing cached there is nothing to fall back to — an empty index
    injects nothing, which is exactly the behaviour before this feature."""

    def load():
        raise RuntimeError("db down")

    cache = NameIndexCache(load, ttl_s=60, now=lambda: 0.0)
    assert len(cache.get()) == 0
    assert cache.get().hits("回焊爐") == {}
