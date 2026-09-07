This workspace declares its own python dependencies.

`pyproject.toml` and `uv.lock` in the workspace root decide what `python` has.
They are synced before your first command, so the packages listed there are
already importable — you do not need to install them.

To add one, run `uv add <package>`. It updates both files, so the package is
still there after the environment is rebuilt.
