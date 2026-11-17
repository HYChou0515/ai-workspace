"""What a caller may put in a third-party tool's environment
(docs/plan-tool-identity-secrets.md).

The caller's variables are the item's user-set ones — values a user, or an
agent reaching this host's API directly, chooses. Most are what the tool is for
(a token, a base URL). Some decide where the process loads CODE from, or where
its traffic goes and whom it trusts; passing those is a way to run someone
else's code inside the tool, beside its credentials, or to read them off the
wire. So for a tool command (`tool=<name>`) they are dropped HERE, on the host — not in
the app: this host's API can be called without the app, and a rule the caller
applies is a rule the caller can skip.

A denylist, by necessity: the names a tool legitimately reads are its author's
choice and cannot be listed. It is drawn from sudo's `env_delete` defaults and
widened to the interpreters, TLS stacks, proxies and package tools a bundle
or what it shells out to will honour. Matched case-insensitively, because
`https_proxy` is honoured as readily as `HTTPS_PROXY`.

The tool's own credentials are not caller variables and are added after this.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Built by the app: which of the caller's names are the user's own, so the
#: launcher can restore them last. Rewritten, never dropped — a name left on it
#: after its value is gone would be restored as EMPTY over the launcher's own.
USER_KEYS = "SANDBOX_USER_ENV_KEYS"

_EXACT = frozenset(
    {
        "HOME",
        "PATH",
        "TMPDIR",
        "IFS",
        "CDPATH",
        "ENV",
        "BASH_ENV",
        "SHELLOPTS",
        "BASHOPTS",
        "PS4",
        "GCONV_PATH",
        "LOCPATH",
        "NLSPATH",
        "HOSTALIASES",
        "RESOLV_HOST_CONF",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "PERLLIB",
        "PERL5LIB",
        "PERL5OPT",
        "RUBYLIB",
        "RUBYOPT",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
    }
)
_PREFIXES = (
    "PYTHON",
    "LD_",
    "DYLD_",
    "GLIBC_",
    "MALLOC_",
    "BASH_FUNC_",
    "OPENSSL_",
    "SSL_",
    "NODE_",
    "GIT_",
    "PIP_",
    "UV_",
    "SANDBOX_",
)


def is_loader_name(name: str) -> bool:
    upper = name.upper()
    return (
        upper in _EXACT
        or upper.startswith(_PREFIXES)
        or upper.endswith("_PROXY")
        or upper == USER_KEYS
    )


def scrub_tool_env(env: Mapping[str, str]) -> dict[str, str]:
    """`env` without the loader names, its user-key list rewritten to match."""
    kept = {name: value for name, value in env.items() if not is_loader_name(name)}
    if USER_KEYS in env:
        names = [n for n in env[USER_KEYS].split() if n in kept]
        if names:
            kept[USER_KEYS] = " ".join(names)
    return kept
