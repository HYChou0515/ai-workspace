"""python-stack — venv carrier for the sandbox's data-science stack.

This package intentionally has no public API. Its sole purpose is to
exist as something `uv sync --frozen --no-editable` can install
non-editable into the prebuilt bundle's venv, so the venv carries
pandas / numpy / scipy / matplotlib reproducibly. The sandbox's jail
bootstrap routes the raw `python` shim to this bundle's launcher when
the package is provisioned; agent scripts then see the data stack
through plain `exec(["python", "script.py"])` calls.

See `src/workspace_app/tooling/prebuild.py` (venv-carrier branch) and
`_JAIL_BOOTSTRAP` in `src/workspace_app/sandbox/local_process.py`.
"""

__all__: list[str] = []
