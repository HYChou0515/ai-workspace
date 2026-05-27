import json
from pathlib import Path

import httpx

from data_fetch.cli import download, load_catalog, main


def _client(body: bytes = b"a,b\n1,2\n", content_type: str = "text/csv", status: int = 200):
    """An httpx client wired to a mock transport — no real network."""
    return httpx.Client(transport=httpx.MockTransport(lambda _req: httpx.Response(
        status, headers={"content-type": content_type}, content=body
    )))


def test_download_streams_named_dataset_to_file(tmp_path: Path):
    out = tmp_path / "x.csv"
    catalog = {"ds": "https://example.invalid/ds.csv"}
    r = download("ds", catalog, str(out), client=_client(b"hello world"))
    assert out.read_bytes() == b"hello world"
    assert r.name == "ds" and r.bytes == 11 and r.content_type == "text/csv" and r.status == 200


def test_download_rejects_unknown_name(tmp_path: Path):
    try:
        download("nope", {"ds": "https://x/y"}, str(tmp_path / "o"), client=_client())
    except KeyError:
        return
    raise AssertionError("expected KeyError for an unknown dataset name")


def test_main_unknown_dataset_is_usage_error_without_network(capsys):
    # Uses the built-in catalog; an unknown name fails BEFORE any download.
    assert main(["definitely-not-a-dataset"]) == 2
    assert "unknown dataset" in capsys.readouterr().err


def test_main_list(capsys):
    assert main(["--list", "--json"]) == 0
    assert "datasets" in json.loads(capsys.readouterr().out)


def test_env_catalog_override(monkeypatch):
    monkeypatch.setenv("DATA_FETCH_CATALOG", json.dumps({"only": "https://x/y.csv"}))
    assert load_catalog() == {"only": "https://x/y.csv"}
