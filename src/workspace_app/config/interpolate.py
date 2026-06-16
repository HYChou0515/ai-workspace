"""`${FOO}` / `$FOO` env-var interpolation for YAML string values.

The config loader walks the parsed YAML tree and runs every string value
through `expand_env(...)` so deploys can drive any field from the
environment with `pg_dsn: ${SPECSTAR_PG_DSN}`-style templates. This is
the single env-override mechanism — there is no per-field 1:1 env-var
lookup. An explicit `${...}` in YAML is the marker that says "this value
comes from the environment"; absence of the marker means "this value is
fixed by the YAML."

Unset env vars raise `EnvVarUnset` at load time. Silent empty-string
substitution would turn a typo (`${OPENAPI_KEY}` for `${OPENAI_API_KEY}`)
into a quiet auth-disabled state that only surfaces at the first LLM
call. Empty *set* values (`FOO=""`) substitute to the empty string and
do NOT raise — "set but blank" is a legitimate choice (e.g.
`llm.base_url: ${LLM_BASE_URL}` with `LLM_BASE_URL=""` means "use the
litellm default").

Escape: `$$` → literal `$`. Common docker-compose / Make idiom.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


class EnvVarUnset(ValueError):
    """Raised when a `${FOO}` / `$FOO` reference points at an env var that
    is not present in the environment. Message names the offending var."""


# Match order matters:
#   1. `$$`                — escape, becomes a literal `$`
#   2. `${NAME}`           — brace form (chains without separator: `${A}${B}`)
#   3. `$NAME`             — bare form (greedy identifier: alphanumeric + `_`)
# Anything else (lone `$`, `$1`, `$,`) is kept verbatim — operator may
# legitimately have `$1` in regex/template strings, and erroring on a lone
# `$` would be a poor surprise.
_PATTERN = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def has_env_reference(value: str) -> bool:
    """True if `value` carries a `${NAME}` / `$NAME` env marker — i.e. its
    final value comes (wholly or in part) from the environment. `$$` escapes
    and a lone `$` / `$1` are NOT references. Used by provenance tracking to
    label a leaf as `env`-sourced rather than a fixed `config.yaml` literal."""
    return any(m.group(1) or m.group(2) for m in _PATTERN.finditer(value))


def expand_env(value: str, env: Mapping[str, str]) -> str:
    """Substitute `${NAME}` / `$NAME` markers in `value` from `env`.

    Raises `EnvVarUnset` (with the missing name in the message) if any
    referenced var is absent. `$$` substitutes a literal `$`. A lone `$`
    or `$1`-style non-identifier sequence is kept verbatim.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(2)
        if name not in env:
            raise EnvVarUnset(
                f"env var {name!r} is referenced in config but unset; "
                f"set it in the environment or remove the ${{{name}}} reference"
            )
        return env[name]

    return _PATTERN.sub(replace, value)
