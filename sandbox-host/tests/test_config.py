"""Standalone env-based config loader."""

from __future__ import annotations

import dataclasses

import pytest

from sandbox_host.config import SandboxHostSettings, load_settings


def test_defaults_when_env_empty():
    s = load_settings({})
    assert s == SandboxHostSettings()
    # spot-check the security-relevant defaults
    assert s.bind == "0.0.0.0:8000"
    assert (s.uid_min, s.uid_max) == (100000, 199999)
    assert s.tools_dir is None  # no tools unless explicitly configured (#251)
    assert s.cgroup_root is None


def test_reads_the_env_vars_this_list_names():
    """A hand-written list, and named as one.

    It used to be called `..._reads_all_...`, which was a claim: it lists
    thirteen of fifteen fields, and both cache ceilings were added after it and
    never joined. The test below is the one that covers ALL of them, derived
    from the dataclass so it cannot fall behind — this one stays because an
    explicit expected value catches a field read with the wrong CONVERTER,
    which a uniform sample cannot.
    """
    env = {
        "SANDBOX_HOST_BIND": "127.0.0.1:9000",
        "SANDBOX_HOST_UID_MIN": "200000",
        "SANDBOX_HOST_UID_MAX": "200099",
        "SANDBOX_HOST_MEMORY_MAX": "1G",
        "SANDBOX_HOST_CPU_CORES": "2.5",
        "SANDBOX_HOST_PIDS_MAX": "256",
        "SANDBOX_HOST_CGROUP_ROOT": "/sys/fs/cgroup/delegated",
        "SANDBOX_HOST_ROOT": "/var/lib/sandboxes",
        "SANDBOX_HOST_EXEC_TIMEOUT": "120",
        "SANDBOX_HOST_LOG_TIMEOUT": "30",
        "SANDBOX_HOST_TOOLS_DIR": "/opt/tools",
        "SANDBOX_HOST_IDLE_TTL": "900",
        "SANDBOX_HOST_NFS_ROOT": "/mnt/nfs/workspaces",
    }
    s = load_settings(env)
    assert s == SandboxHostSettings(
        bind="127.0.0.1:9000",
        uid_min=200000,
        uid_max=200099,
        memory_max="1G",
        cpu_cores=2.5,
        pids_max=256,
        cgroup_root="/sys/fs/cgroup/delegated",
        root="/var/lib/sandboxes",
        exec_timeout=120.0,
        log_timeout=30.0,
        tools_dir="/opt/tools",
        idle_ttl=900.0,
        nfs_root="/mnt/nfs/workspaces",
    )


def test_every_setting_is_reachable_from_its_env_var():
    """A knob nobody can set is not a knob.

    The test above says "all" and lists thirteen of fifteen: both cache
    ceilings were added later and never joined the list, so a misspelt key in
    `load_settings` would have read as a silent default on every pod, with the
    suite green and the test's own name asserting otherwise. Deriving the keys
    from the dataclass covers the next field the day it is added, or says so.
    """
    # A bool is sampled as the value that is NOT its default, or a key that is
    # never read would still compare equal.
    sample = {"str": "sample", "int": "424242", "float": "42.5", "bool": "0"}
    cast = {"str": str, "int": int, "float": float, "bool": lambda _raw: False}

    env: dict[str, str] = {}
    want: dict[str, object] = {}
    for field in dataclasses.fields(SandboxHostSettings):
        kind = str(field.type).replace(" | None", "")
        raw = sample[kind]
        env[f"SANDBOX_HOST_{field.name.upper()}"] = raw
        want[field.name] = cast[kind](raw)

    got = load_settings(env)
    missed = [name for name, value in want.items() if getattr(got, name) != value]
    assert not missed, f"set by no SANDBOX_HOST_* key: {missed}"


def test_ignores_unrelated_env_keys():
    s = load_settings({"PATH": "/usr/bin", "SANDBOX_HOST_BIND": "0.0.0.0:1234"})
    assert s.bind == "0.0.0.0:1234"
    assert s.uid_min == 100000  # untouched default


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("1", True),
        ("true", True),
        ("Yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("OFF", False),
    ],
)
def test_archive_pack_reads_the_usual_spellings(raw: str, want: bool) -> None:
    assert load_settings({"SANDBOX_HOST_ARCHIVE_PACK": raw}).archive_pack is want


def test_archive_pack_is_on_by_default() -> None:
    assert load_settings({}).archive_pack is True


@pytest.mark.parametrize("raw", ["", "flase", "2", "disabled"])
def test_archive_pack_refuses_a_value_it_cannot_read(raw: str) -> None:
    """A string is not a bool: `"false"` read as truthy turned a pure producer
    into a consumer once (run_consumers). An unreadable value stops the boot
    with the key named, rather than silently meaning on."""
    with pytest.raises(ValueError, match="SANDBOX_HOST_ARCHIVE_PACK"):
        load_settings({"SANDBOX_HOST_ARCHIVE_PACK": raw})
