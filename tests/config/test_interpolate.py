"""${FOO} / $FOO env-var interpolation inside YAML string values.

The interpolator runs against every string value the YAML loader produces,
so `pg_dsn: ${SPECSTAR_PG_DSN}` and `llm: { api_key: ${OPENAI_API_KEY} }`
both become live values at load time. Unset env vars raise loud (silent
empty-string substitution would mean a typo'd `${OPENAPI_KEY}` quietly
disables auth and the operator only finds out at the first LLM call).

`$$` is the literal-dollar escape — common docker-compose convention.
Empty env vars (`FOO=""`) substitute the empty string and do NOT raise:
"set but blank" is a valid choice (e.g. `llm.base_url: ${LLM_BASE_URL}`
with LLM_BASE_URL unset on purpose means "use the litellm default").
"""

from __future__ import annotations

import pytest

from workspace_app.config.interpolate import EnvVarUnset, expand_env


def test_string_without_dollar_is_returned_verbatim():
    assert expand_env("just text", {}) == "just text"


def test_brace_form_substitutes_from_env():
    assert expand_env("${FOO}", {"FOO": "bar"}) == "bar"


def test_bare_form_substitutes_from_env():
    assert expand_env("$FOO", {"FOO": "bar"}) == "bar"


def test_mixed_text_around_brace_substitution():
    """`prefix-${FOO}-suffix` is the common docker / k8s template idiom."""
    assert (
        expand_env("postgresql://admin:${PWD}@db/main", {"PWD": "s3cret"})
        == "postgresql://admin:s3cret@db/main"
    )


def test_two_brace_substitutions_chain_without_separator():
    """`${A}${B}` should compose without a literal between them."""
    assert expand_env("${A}${B}", {"A": "ab", "B": "cd"}) == "abcd"


def test_bare_form_stops_at_first_non_identifier_char():
    """`$FOO-bar` — `-` is not an identifier char, so $FOO ends there."""
    assert expand_env("$FOO-bar", {"FOO": "ok"}) == "ok-bar"


def test_bare_form_takes_alphanumeric_and_underscore():
    """`$FOO_BAR` is one whole identifier, not `$FOO` followed by `_BAR`."""
    assert expand_env("$FOO_BAR", {"FOO_BAR": "joined"}) == "joined"


def test_empty_env_var_substitutes_empty_string_not_raise():
    """`set but blank` is a valid choice — operator can `FOO=""` to mean
    "use the upstream default" without YAML rewrite."""
    assert expand_env("${FOO}", {"FOO": ""}) == ""


def test_unset_env_var_raises_loud_with_var_name_in_message():
    """Silent empty substitution would let a typo'd `${OPENAPI_KEY}`
    quietly disable auth; raise so the deploy fails fast."""
    with pytest.raises(EnvVarUnset, match="FOO"):
        expand_env("${FOO}", {})


def test_unset_bare_form_raises_too():
    with pytest.raises(EnvVarUnset, match="FOO"):
        expand_env("$FOO", {})


def test_double_dollar_escapes_to_literal_dollar():
    """`$$FOO` → literal `$FOO`, no substitution. Same idiom as
    docker-compose / Make variable escaping."""
    assert expand_env("$$FOO", {"FOO": "ignored"}) == "$FOO"


def test_double_dollar_brace_escapes_to_literal_dollar_brace():
    """`$${FOO}` → literal `${FOO}`; lets operator put `${...}` in YAML
    without it being interpolated."""
    assert expand_env("$${FOO}", {"FOO": "ignored"}) == "${FOO}"


def test_lone_dollar_at_end_of_string_is_kept_literal():
    """A trailing `$` with no identifier or `{` after it has no
    interpolation form to match; keep verbatim rather than raise."""
    assert expand_env("$", {}) == "$"
    assert expand_env("price: $", {}) == "price: $"


def test_dollar_followed_by_non_identifier_is_kept_literal():
    """`$1` / `$,` aren't valid env names — keep verbatim. Useful for
    things like regex strings that legitimately contain `$1`."""
    assert expand_env("$1 $2", {}) == "$1 $2"
