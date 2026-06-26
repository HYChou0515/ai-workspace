"""Standalone env-based config loader."""

from __future__ import annotations

from sandbox_host.config import SandboxHostSettings, load_settings


def test_defaults_when_env_empty():
    s = load_settings({})
    assert s == SandboxHostSettings()
    # spot-check the security-relevant defaults
    assert s.bind == "0.0.0.0:8000"
    assert (s.uid_min, s.uid_max) == (100000, 199999)
    assert s.tools_dir is None  # no tools unless explicitly configured (#251)
    assert s.cgroup_root is None


def test_reads_all_sandbox_host_env_vars():
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
    )


def test_ignores_unrelated_env_keys():
    s = load_settings({"PATH": "/usr/bin", "SANDBOX_HOST_BIND": "0.0.0.0:1234"})
    assert s.bind == "0.0.0.0:1234"
    assert s.uid_min == 100000  # untouched default
