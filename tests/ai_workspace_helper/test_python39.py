"""The helper runs on the person's own Python, 3.9 among them (the person:
「有些人的python是3.9」), while this repo's tests run on 3.12 — so nothing
here would notice a 3.10-only line until a 3.9 PC crashed on it. This reads
every file the helper ships the way a 3.9 interpreter would and names what it
would refuse: syntax newer than 3.9, and the newer library calls this code is
likely to reach for. Syntax is checked by the parser itself; the calls are a
list, so a newer API outside it is still a 3.9 PC's to find."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parents[2] / "ai-workspace-helper"
# …and the external AI's site-file template, which a company packs in as is
# (docs/plan-ext-ai.md D7) and runs on the same 3.9
_SHIPPED = sorted(
    [
        *_HELPER.glob("*.pyw"),
        *(_HELPER / "src" / "ai_workspace_helper").glob("*.py"),
        _HELPER / "ext_ai_site.template.py",
    ]
)

_spec = importlib.util.spec_from_file_location(
    "_python39_guard", Path(__file__).resolve().parents[2] / "scripts" / "python39_guard.py"
)
assert _spec and _spec.loader
_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guard)


def _problems(path: Path) -> list[str]:
    return _guard.problems(path, _HELPER)


@pytest.mark.parametrize("path", _SHIPPED, ids=lambda p: str(p.relative_to(_HELPER)))
def test_a_python_39_can_run_it(path):
    assert _problems(path) == []


# The guard itself: each kind of line it exists for is named, so a scan that
# silently stopped looking would redden here.
@pytest.mark.parametrize(
    ("line", "named"),
    [
        ("match x:\n    case 1:\n        pass\n", "syntax 3.9 refuses"),
        ("for a, b in zip(x, y, strict=True):\n    pass\n", "zip(strict=…)"),
        ("import tomllib\n", "import tomllib"),
        ("from itertools import pairwise\n", "from itertools import pairwise"),
        ("import datetime\nnow = datetime.UTC\n", "datetime.UTC"),
        ("def f(x: int | None) -> None:\n    pass\n", "`X | Y` annotation"),
        ("x: int | None = None\n", "`X | Y` annotation"),
        ("def f(*a: int | None) -> None:\n    pass\n", "`X | Y` annotation"),
        ("def f(a: int | None, /) -> None:\n    pass\n", "`X | Y` annotation"),
        ("Opt = int | None\n", "`X | Y` evaluated"),
        (
            "from __future__ import annotations\nok = isinstance(1, int | str)\n",
            "`X | Y` evaluated",
        ),
    ],
)
def test_the_guard_names_what_39_refuses(tmp_path, line, named):
    # in tmp_path, never the shipped folder: under xdist every case runs at once
    probe = tmp_path / "probe.py"
    probe.write_text(line, encoding="utf-8")

    assert any(named in p for p in _problems(probe)), _problems(probe)


# …and only those: a 3.9 Python runs these, so naming them would teach people
# to wave the guard through.
@pytest.mark.parametrize(
    "line",
    [
        "from __future__ import annotations\ndef f(x: int | None) -> None:\n    pass\n",
        "from __future__ import annotations\nx: int | None = None\n",
        "flags = 1 | 2\n",
        "both = {1} | {2}\n",
    ],
)
def test_the_guard_lets_through_what_39_runs(tmp_path, line):
    probe = tmp_path / "probe.py"
    probe.write_text(line, encoding="utf-8")

    assert _problems(probe) == []


#: What a too-old Python runs first: the installer, the precheck and the
#: setup_core.py they import — before `python_too_old` can say a word.
_FIRST_RUN = [
    _HELPER / "install.pyw",
    _HELPER / "precheck.pyw",
    _HELPER / "src" / "ai_workspace_helper" / "setup_core.py",
]
_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _import_time_generics(path: Path) -> list[str]:
    """`list[str]` and the like evaluated when the module is imported: a 3.8
    Python stops there, so the person never hears it is too old."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # bodies run later; decorators and bases are rare enough here
        if isinstance(stmt, ast.If) and "TYPE_CHECKING" in ast.unparse(stmt.test):
            continue
        if isinstance(stmt, ast.AnnAssign):
            stmt = ast.Expr(stmt.value) if stmt.value is not None else None
        if stmt is None:
            continue
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in _GENERICS
            ):
                found.append(f"{path.name}:{node.lineno}: {ast.unparse(node)}")
    return found


@pytest.mark.parametrize("path", _FIRST_RUN, ids=lambda p: p.name)
def test_a_too_old_python_gets_as_far_as_being_told(path):
    assert _import_time_generics(path) == []


def test_the_too_old_guard_names_an_import_time_generic(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("from typing import Callable\nRun = Callable[[list[str]], None]\n")

    assert _import_time_generics(probe) == ["probe.py:2: list[str]"]


# install.pyw itself, laid out as the zip lays it out (setup_core.py beside
# it), with the version it reads faked: a 3.9 is asked to go ahead, a 3.8 is
# told it is too old. (A real 3.8 gets that far only because nothing it runs
# first evaluates `list[str]` — the guard above.)
@pytest.mark.parametrize(
    ("version", "refused"), [((3, 8, 10, "final", 0), True), ((3, 9, 13, "final", 0), False)]
)
def test_install_pyw_takes_python_39(tmp_path, monkeypatch, version, refused):
    import importlib.machinery
    import importlib.util
    import shutil
    import sys
    import types

    shutil.copy2(_HELPER / "install.pyw", tmp_path / "install.pyw")
    shutil.copy2(_HELPER / "src" / "ai_workspace_helper" / "setup_core.py", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    # recorded as absent so monkeypatch removes the copy imported below
    monkeypatch.setitem(sys.modules, "setup_core", None)
    del sys.modules["setup_core"]
    # A tkinter of the test's own: the window is not what is tested, and a CI
    # runner's Python may have no tkinter at all (it did: the first CI run).
    said: list[tuple[str, str]] = []

    class _Tk:
        def withdraw(self) -> None:
            pass

    box = types.ModuleType("tkinter.messagebox")
    box.showerror = lambda title, text: said.append(("error", text))  # ty: ignore[unresolved-attribute]
    box.askokcancel = lambda title, text: said.append(("ask", text)) or False  # ty: ignore[unresolved-attribute]
    tk = types.ModuleType("tkinter")
    tk.Tk = _Tk  # ty: ignore[unresolved-attribute]
    tk.messagebox = box  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", box)

    loader = importlib.machinery.SourceFileLoader("_install_pyw", str(tmp_path / "install.pyw"))
    spec = importlib.util.spec_from_loader("_install_pyw", loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(sys, "version_info", version)

    code = module.main()

    if refused:
        assert code == 1 and said[0][0] == "error"
        assert "3.9" in said[0][1] and "3.8.10" in said[0][1]
    else:
        assert code == 0 and said[0][0] == "ask" and "3.9.13" in said[0][1]
