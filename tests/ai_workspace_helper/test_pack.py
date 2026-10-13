"""Packing the helper on the dev server (docs/plan-outside-helper.md D14,
§3.6, §3.7.2): one offline zip a Windows PC with any CPython 3.10–3.14
installs from, and the manifest the backend serves.

The commands (pnpm, uv, pip) go through an injected `run`, recorded here and
made to leave the files they would; the real run is the live check (P10).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "_pack_ai_workspace_helper", _ROOT / "scripts" / "pack_ai_workspace_helper.py"
)
assert _spec and _spec.loader
pack_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack_mod)


# §3.6: pip downloads for the Python RUNNING it unless told otherwise, and
# greenlet has one wheel per CPython minor — so each version is asked for
# explicitly, Windows, binary only, every package pinned with `==`.
def test_every_python_gets_its_own_pinned_windows_wheels():
    commands = [pack_mod.download_command(["pip"], v, Path("/wh")) for v in pack_mod.PYTHONS]

    assert pack_mod.PYTHONS == ["3.10", "3.11", "3.12", "3.13", "3.14"]
    for version, argv in zip(pack_mod.PYTHONS, commands, strict=True):
        abi = "cp" + version.replace(".", "")
        assert argv[:2] == ["pip", "download"]
        for flag in (
            ["--platform", "win_amd64"],
            ["--implementation", "cp"],
            ["--python-version", version],
            ["--abi", abi],
        ):
            assert _has(argv, flag), (version, flag)
        assert "--only-binary=:all:" in argv and "--no-deps" in argv
    assert all(pin.count("==") == 1 for pin in pack_mod.PINS)
    assert {p.split("==")[0] for p in pack_mod.PINS} >= {"playwright", "greenlet"}


# P7's acceptance: with nothing but the wheelhouse, pip resolves the helper and
# everything it needs, for Windows, on every Python the zip claims.
def test_the_wheelhouse_is_checked_to_install_offline_on_every_python():
    argv = pack_mod.verify_command(["pip"], "3.13", Path("/wh"), "0.3.0", Path("/t"))

    assert argv[:2] == ["pip", "install"]
    for flag in (["--find-links", "/wh"], ["--python-version", "3.13"], ["--abi", "cp313"]):
        assert _has(argv, flag)
    assert "--dry-run" in argv and "--no-index" in argv
    assert argv[-1] == "ai-workspace-helper==0.3.0"


class _Run:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path | None = None) -> None:
        self.commands.append(argv)
        if argv[:2] == ["uv", "build"]:
            out = Path(argv[argv.index("-o") + 1])
            version = (Path(argv[-1]) / "src/ai_workspace_helper/version.py").read_text()
            stamped = version.split('__version__ = "')[1].split('"')[0]
            (out / f"ai_workspace_helper-{stamped}-py3-none-any.whl").write_text("wheel")
        if "download" in argv:
            dest = Path(argv[argv.index("-d") + 1])
            (dest / "greenlet-3.5.6-cp312-cp312-win_amd64.whl").write_text("greenlet")


def test_the_zip_carries_the_installer_and_the_wheels_and_the_manifest_names_it(tmp_path):
    run = _Run()

    zip_path, manifest = pack_mod.pack("0.3.0", tmp_path / "out", min_version="0.2.0", run=run)

    assert zip_path == tmp_path / "out" / "ai-workspace-helper-0.3.0.zip"
    with zipfile.ZipFile(zip_path) as z:
        names = sorted(z.namelist())
        readme = z.read("README.txt").decode("utf-8")
    assert "precheck.pyw" in readme  # plan D19: check the PC before installing
    assert names == [
        "README.txt",
        "install.pyw",
        "launch.pyw",
        "precheck.pyw",  # plan D19: run it before installing
        "setup_core.py",
        "wheelhouse/ai_workspace_helper-0.3.0-py3-none-any.whl",
        "wheelhouse/greenlet-3.5.6-cp312-cp312-win_amd64.whl",
    ]
    assert json.loads(manifest.read_text()) == {
        "version": "0.3.0",
        "min_version": "0.2.0",
        "file": "ai-workspace-helper-0.3.0.zip",
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    }
    # the converter is built from the frontend before the wheel is (D11)
    assert run.commands[0][:2] == ["pnpm", "run"]


# The zip's setup_core.py IS the package's: one file, copied, never edited.
def test_the_installer_core_in_the_zip_is_the_packages_own(tmp_path):
    zip_path, _ = pack_mod.pack("0.3.0", tmp_path / "out", run=_Run())

    with zipfile.ZipFile(zip_path) as z:
        shipped = z.read("setup_core.py")
    assert (
        shipped
        == (_ROOT / "ai-workspace-helper/src/ai_workspace_helper/setup_core.py").read_bytes()
    )


def test_the_source_tree_is_not_stamped(tmp_path):
    version_py = _ROOT / "ai-workspace-helper/src/ai_workspace_helper/version.py"
    before = version_py.read_text()

    pack_mod.pack("9.9.9", tmp_path / "out", run=_Run())

    assert version_py.read_text() == before


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0rc1", "../1.0.0"])
def test_a_version_must_be_three_numbers(tmp_path, bad):
    with pytest.raises(ValueError):
        pack_mod.pack(bad, tmp_path / "out", run=_Run())


def _has(argv: list[str], pair: list[str]) -> bool:
    return any(argv[i : i + 2] == pair for i in range(len(argv) - 1))
