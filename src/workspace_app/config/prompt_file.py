"""`prompt_file:` value resolver (Q6).

Operators set `agents.presets.*.prompt_file` to one of three forms:

- ``pkg:<dotted-package>/<path/to.md>`` — bundled package resource
  (ships with the wheel; the bundled defaults all use this form so
  the default presets work even with no on-disk prompt files).
- ``/abs/path.md`` — absolute filesystem path.
- ``relative/path.md`` — relative to the discovered ``config.yaml``'s
  directory (the typical deploy-local override pattern: drop a
  ``prompts/`` folder next to ``config.yaml``).

CWD-relative is deliberately NOT supported: in container / k8s
deploys the CWD is whatever the unit/entrypoint chose and operators
can't predict it; raising loud here forces the deploy to declare
intent (`pkg:`, absolute, or "next to config.yaml").

Missing file raises `PromptFileNotFound` at load time — earlier is
better than a NoSuchFile on the first agent turn.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


class PromptFileNotFound(ValueError):
    """A `prompt_file:` value couldn't be resolved (unknown package /
    missing file / no config_dir for a relative path). The message
    names the offending value + the resolved path the loader tried."""


_PKG_PREFIX = "pkg:"


def resolve_prompt_file(value: str, config_dir: Path | None) -> str:
    """Return the prompt body for a `prompt_file:` value.

    `pkg:pkg.name/sub/path.md` reads from the package; absolute path
    reads from disk; otherwise interpreted as relative to `config_dir`.
    Raises `PromptFileNotFound` for any missing target.
    """
    if value.startswith(_PKG_PREFIX):
        return _read_pkg(value[len(_PKG_PREFIX) :])
    p = Path(value)
    if p.is_absolute():
        return _read_disk(p, original=value)
    if config_dir is None:
        # No config.yaml was discovered, so we have no anchor for a
        # relative path. Raising is better than guessing CWD: in
        # container/k8s the CWD is unpredictable, and a silent miss
        # would only surface at the first turn.
        raise PromptFileNotFound(
            f"prompt_file {value!r}: relative path needs a config_dir to "
            f"resolve against, but no config.yaml was discovered. Use "
            f"the `pkg:...` form, an absolute path, or run with a "
            f"config.yaml present."
        )
    return _read_disk(config_dir / p, original=value)


def _read_pkg(spec: str) -> str:
    """Parse `<pkg.name>/<sub/path.md>` and read the resource."""
    if "/" not in spec:
        raise PromptFileNotFound(f"prompt_file 'pkg:{spec}': expected `pkg:<package>/<path/to.md>`")
    package, _, sub = spec.partition("/")
    try:
        traversable = files(package)
    except (ModuleNotFoundError, TypeError) as e:
        raise PromptFileNotFound(
            f"prompt_file 'pkg:{spec}': package {package!r} not importable ({e})"
        ) from e
    target = traversable / sub
    try:
        return target.read_text("utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as e:
        raise PromptFileNotFound(
            f"prompt_file 'pkg:{spec}': {sub!r} not found inside package {package!r} ({e})"
        ) from e


def _read_disk(path: Path, *, original: str) -> str:
    try:
        return path.read_text("utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as e:
        raise PromptFileNotFound(f"prompt_file {original!r}: file not found at {path} ({e})") from e
