"""CLI: download a *named* dataset into the workspace.

The agent never supplies a URL — it picks a NAME from a configured catalog
(name → URL). A wrong/hallucinated URL is therefore impossible; the worst the
model can do is name a dataset that doesn't exist, which fails cleanly.

    data-fetch --list                 # show available dataset names
    data-fetch reflow-incidents       # download that dataset into the workspace
    data-fetch reflow-incidents --json

The catalog defaults to a small built-in map; override it per deployment with
DATA_FETCH_CATALOG = inline JSON  *or*  a path to a JSON file ({name: url}).
Exit 0 on success, 2 on a usage / download error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Example catalog — real deployments override via DATA_FETCH_CATALOG. The point:
# the URLs live HERE (config), not in the model's output.
_DEFAULT_CATALOG: dict[str, str] = {
    "reflow-incidents": "https://example.invalid/data/reflow-incidents.csv",
    "void-rate-baseline": "https://example.invalid/data/void-rate-baseline.csv",
}


def load_catalog() -> dict[str, str]:
    raw = os.environ.get("DATA_FETCH_CATALOG")
    if not raw:
        return dict(_DEFAULT_CATALOG)
    text = Path(raw).read_text() if Path(raw).exists() else raw
    data = json.loads(text)
    return {str(k): str(v) for k, v in data.items()}


@dataclass
class Result:
    name: str
    url: str
    path: str
    bytes: int
    content_type: str
    status: int


def _default_out(name: str, url: str) -> str:
    # Filename from the URL path, falling back to the dataset name.
    return Path(urlsplit(url).path).name or name


def download(
    name: str,
    catalog: dict[str, str],
    out: str | None = None,
    *,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> Result:
    """Stream the named dataset to `out` (default: filename from the URL).
    Raises KeyError for an unknown name, httpx.HTTPError on a transport/HTTP
    failure. Streaming keeps a large download off the heap."""
    if name not in catalog:
        raise KeyError(name)
    url = catalog[name]
    out_path = Path(out or _default_out(name, url))
    if out_path.parent != Path(""):
        out_path.parent.mkdir(parents=True, exist_ok=True)

    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        size = 0
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            status = resp.status_code
            with out_path.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
                    size += len(chunk)
    finally:
        if owns:
            client.close()
    return Result(name, url, str(out_path), size, content_type, status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="data-fetch",
        description="Download a named dataset (from the configured catalog) into the workspace.",
    )
    parser.add_argument("name", nargs="?", help="dataset name (see --list)")
    parser.add_argument("--out", help="output path (default: filename from the source URL)")
    parser.add_argument("--list", action="store_true", help="list available dataset names")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    catalog = load_catalog()

    # No name (or --list) → enumerate what the agent may pick.
    if args.list or not args.name:
        names = sorted(catalog)
        if args.json:
            print(json.dumps({"datasets": names}, indent=2))
        else:
            print("available datasets:")
            for n in names:
                print(f"  - {n}")
        return 0

    if args.name not in catalog:
        print(
            f"error: unknown dataset {args.name!r}. available: {', '.join(sorted(catalog))}",
            file=sys.stderr,
        )
        return 2

    try:
        result = download(args.name, catalog, args.out, timeout=args.timeout)
    except httpx.HTTPError as exc:
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(
            f"downloaded {result.name} → {result.path} "
            f"({result.bytes} bytes, {result.content_type})"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
