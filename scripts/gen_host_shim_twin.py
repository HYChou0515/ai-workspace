"""Generate `sandbox-host/tests/test_project_venv_shim.py` from the app copy.

Run from the repo root:  `python scripts/gen_host_shim_twin.py`

It lives here because two commit messages claimed the twin was "generated" while
the generator existed only on the machine that ran it — an unverifiable claim
about our own process, which is the kind this branch kept tripping over.

Kept mechanical on purpose: the two files exist because a fix landing in only
one of them is the exact failure the shim guards against, so the twin is
derived rather than retyped.

They are not identical. The two backends lay their tools dir out differently —
app-side `.tools` symlinks straight at the tools dir, host-side it points at a
per-sandbox view assembled from `<tools>/builtin/` — so the carrier is planted
one segment deeper on the host. That is the only behavioural difference; the
rest is header, imports and one doc reference.
"""

import pathlib
import sys

SUBS = [
    (
        """App-side copy. See `sandbox-host/tests/` for the twin — the one production
runs. Duplicated rather than shared because the host deliberately shares no
modules with workspace_app, and a fix that lands in only one of the two copies
is exactly the failure this shim exists to prevent.""",
        """Host-side copy — the one production runs. See `tests/sandbox/` app-side for
the twin. Duplicated rather than shared because the host deliberately shares no
modules with workspace_app, and a fix that lands in only one of the two copies
is exactly the failure this shim exists to prevent.""",
    ),
    (
        "from workspace_app.sandbox.local_process import LocalProcessSandbox\n"
        "from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec\n",
        "from sandbox_host.local_process import LocalProcessSandbox\n"
        "from sandbox_host.protocol import SandboxHandle, SandboxSpec\n"
        "from sandbox_host.tool_cache import BUILTIN_DIR\n",
    ),
    # The one behavioural difference: where a carrier has to be planted to be seen.
    ('    stack = tools / "python-stack"\n', '    stack = tools / BUILTIN_DIR / "python-stack"\n'),
    # And one naming difference: the app calls the item id `sandbox_id` on
    # `create` (#345 keys each item to a fixed dir by it); the host calls it
    # `item_id`, which is what its controller, protocol and request body all use.
    ("sandbox_id=", "item_id="),
    ("`tests/sandbox/test_project_env_e2e.py`", "the app repo's `test_project_env_e2e.py`"),
]

src = pathlib.Path("tests/sandbox/test_project_venv_shim.py").read_text(encoding="utf-8")
out = src
for old, new in SUBS:
    if old not in out:
        sys.exit(f"twin generator is out of date; not found:\n{old[:160]}")
    # every occurrence: the naming difference below appears more than once
    out = out.replace(old, new)

if "from workspace_app" in out or "import workspace_app" in out:
    sys.exit("an app import survived the rewrite")

pathlib.Path("sandbox-host/tests/test_project_venv_shim.py").write_text(out, encoding="utf-8")
print("regenerated sandbox-host/tests/test_project_venv_shim.py")
