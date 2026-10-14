"""The helper runs on the person's own Python, 3.9 among them (the person:
「有些人的python是3.9」), while this repo's tests run on 3.12 — so nothing
here would notice a 3.10-only line until a 3.9 PC crashed on it. This reads
every file the helper ships the way a 3.9 interpreter would and names what it
would refuse: syntax newer than 3.9, and the newer library calls this code is
likely to reach for. Syntax is checked by the parser itself; the calls are a
list, so a newer API outside it is still a 3.9 PC's to find."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parents[2] / "ai-workspace-helper"
_SHIPPED = sorted([*_HELPER.glob("*.pyw"), *(_HELPER / "src" / "ai_workspace_helper").glob("*.py")])

#: Modules, names and keywords that arrived after 3.9.
_NEW_MODULES = {"tomllib"}
_NEW_NAMES = {
    ("itertools", "pairwise"),
    ("contextlib", "aclosing"),
    ("datetime", "UTC"),
    ("enum", "StrEnum"),
    ("asyncio", "TaskGroup"),
    ("asyncio", "timeout"),
    ("typing", "Self"),
    ("typing", "TypeAlias"),
    ("typing", "ParamSpec"),
    ("typing", "Concatenate"),
    ("typing", "TypeGuard"),
    ("typing", "Never"),
}
_NEW_BUILTINS = {"anext", "aiter", "ExceptionGroup", "BaseExceptionGroup"}
#: call keyword -> the callables that only take it from 3.10 on
_NEW_KEYWORDS = {"strict": {"zip"}, "slots": {"dataclass"}, "kw_only": {"dataclass"}}


def _callee(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _problems(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    where = path.relative_to(_HELPER)
    try:
        tree = ast.parse(source, str(path), feature_version=(3, 9))
    except SyntaxError as exc:
        return [f"{where}:{exc.lineno}: syntax 3.9 refuses ({exc.msg})"]
    postponed = "from __future__ import annotations" in source
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            found += [
                f"{where}:{line}: import {a.name}" for a in node.names if a.name in _NEW_MODULES
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.module in _NEW_MODULES:
                found.append(f"{where}:{line}: from {node.module}")
            found += [
                f"{where}:{line}: from {node.module} import {a.name}"
                for a in node.names
                if (node.module, a.name) in _NEW_NAMES
            ]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in _NEW_NAMES:
                found.append(f"{where}:{line}: {node.value.id}.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _NEW_BUILTINS:
            found.append(f"{where}:{line}: {node.id}")
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if _callee(node.func) in _NEW_KEYWORDS.get(kw.arg or "", set()):
                    found.append(f"{where}:{line}: {_callee(node.func)}({kw.arg}=…)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not postponed:
            hints = [a.annotation for a in (*node.args.args, *node.args.kwonlyargs)]
            for hint in (*hints, node.returns):
                if hint is not None and any(
                    isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(hint)
                ):
                    found.append(f"{where}:{line}: `X | Y` annotation, not postponed")
    return found


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
    ],
)
def test_the_guard_names_what_39_refuses(tmp_path, line, named):
    probe = _HELPER / "_probe_py39.py"
    try:
        probe.write_text(line, encoding="utf-8")
        assert any(named in p for p in _problems(probe)), _problems(probe)
    finally:
        probe.unlink()


# install.pyw itself, laid out as the zip lays it out (setup_core.py beside
# it): a 3.9 Python is asked to go ahead, a 3.8 one is told it is too old.
@pytest.mark.parametrize(
    ("version", "refused"), [((3, 8, 10, "final", 0), True), ((3, 9, 13, "final", 0), False)]
)
def test_install_pyw_takes_python_39(tmp_path, monkeypatch, version, refused):
    import importlib.machinery
    import importlib.util
    import shutil
    import sys

    shutil.copy2(_HELPER / "install.pyw", tmp_path / "install.pyw")
    shutil.copy2(_HELPER / "src" / "ai_workspace_helper" / "setup_core.py", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "setup_core", raising=False)
    loader = importlib.machinery.SourceFileLoader("_install_pyw", str(tmp_path / "install.pyw"))
    spec = importlib.util.spec_from_loader("_install_pyw", loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    said: list[tuple[str, str]] = []

    class _Tk:
        def withdraw(self) -> None:
            pass

    monkeypatch.setattr(module.tkinter, "Tk", _Tk)
    monkeypatch.setattr(
        module.messagebox, "showerror", lambda title, text: said.append(("error", text))
    )
    monkeypatch.setattr(
        module.messagebox, "askokcancel", lambda title, text: said.append(("ask", text)) or False
    )
    monkeypatch.setattr(sys, "version_info", version)

    code = module.main()

    if refused:
        assert code == 1 and said[0][0] == "error"
        assert "3.9" in said[0][1] and "3.8.10" in said[0][1]
    else:
        assert code == 0 and said[0][0] == "ask" and "3.9.13" in said[0][1]
