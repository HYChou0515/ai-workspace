"""Make this deployment's key pair for third-party tools' sealed credentials
(docs/plan-tool-identity-secrets.md P4).

    python -m sandbox_host.tool_secret_key /path/to/tool-secret.key

Writes the PRIVATE key (base64 of the raw X25519 bytes) to that file, readable
by its owner only — turn it into the k8s secret the host mounts and point
`SANDBOX_HOST_TOOL_SECRET_KEY_FILE` at it. Prints the PUBLIC key, which goes
into `TOOL_SECRET_RECIPIENTS` on the GitLab group (beside any other
deployment's), so every tool built from then on can be opened here.

Refuses to overwrite an existing file: replacing a live key strands every tool
sealed to it until each one is rebuilt.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .tool_secrets import public_key_text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sandbox_host.tool_secret_key")
    parser.add_argument("path", help="where to write the private key")
    args = parser.parse_args(argv)

    key = X25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    try:
        # O_EXCL: never replace a key that exists; 0600 from the first byte,
        # rather than written world-readable and chmod-ed after.
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(
            f"{args.path} exists — not replacing a key tools may already be sealed to",
            file=sys.stderr,
        )
        return 1
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(raw).decode() + "\n")
    print(public_key_text(key.public_key()))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    raise SystemExit(main())
