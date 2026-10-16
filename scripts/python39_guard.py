"""What a Python 3.9 would refuse (docs/plan-outside-helper.md D23).

The helper runs on the person's own Python, 3.9 among them, while this repo
runs on 3.12 — so nothing here would notice a 3.10-only line until a 3.9 PC
crashed on it. `problems` reads a file the way a 3.9 interpreter would and
names what it would refuse: syntax newer than 3.9, an `X | Y` it would
evaluate, and the newer library calls this code is likely to reach for.
Syntax is checked by the parser itself; the calls are a list, so a newer API
outside it is still a 3.9 PC's to find.

One guard, two users: the helper's own files (tests/ai_workspace_helper/
test_python39.py) and a company's site file as it is packed
(scripts/pack_ai_workspace_helper.py, docs/plan-ext-ai.md D7).
"""

from __future__ import annotations

import ast
from pathlib import Path

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


#: Names that make an `X | Y` a union of types, not of numbers or sets.
_TYPES = {"int", "str", "float", "bytes", "bool", "list", "dict", "tuple", "set", "type", "object"}


def _is_union(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)


def _typeish(node: ast.expr) -> bool:
    return (
        (isinstance(node, ast.Constant) and node.value is None)
        or (isinstance(node, ast.Name) and node.id in _TYPES)
        or isinstance(node, ast.Subscript)
        or (_is_union(node) and isinstance(node, ast.BinOp) and _typeish(node.left))
    )


def _annotations(tree: ast.AST) -> list[ast.expr]:
    """Every annotation: each kind of parameter, returns, and variables."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            params = [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
            found += [p.annotation for p in params if p is not None and p.annotation is not None]
            if node.returns is not None:
                found.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
    return found


def problems(path: Path, root: Path | None = None) -> list[str]:
    """What a Python 3.9 would refuse in the file at `path`, each as
    `<where>:<line>: <what>` — `where` relative to `root` when it is inside it.
    Read as Python reads it: a UTF-8 BOM is not part of the source."""
    source = path.read_text(encoding="utf-8-sig")
    where = path.relative_to(root) if root is not None and path.is_relative_to(root) else path.name
    try:
        tree = ast.parse(source, str(path), feature_version=(3, 9))
    except SyntaxError as exc:
        return [f"{where}:{exc.lineno}: syntax 3.9 refuses ({exc.msg})"]
    postponed = "from __future__ import annotations" in source
    found: list[str] = []
    in_annotation: set[int] = set()
    for hint in _annotations(tree):
        nodes = list(ast.walk(hint))
        in_annotation |= {id(n) for n in nodes}
        if not postponed and any(_is_union(n) for n in nodes):
            found.append(f"{where}:{hint.lineno}: `X | Y` annotation, not postponed")
    for node in ast.walk(tree):
        runtime_union = (
            id(node) not in in_annotation and isinstance(node, ast.BinOp) and _is_union(node)
        )
        if runtime_union and (_typeish(node.left) or _typeish(node.right)):
            found.append(f"{where}:{node.lineno}: `X | Y` evaluated at run time")
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
            # isinstance(x, A | B): evaluated whatever `__future__` says
            checks_type = _callee(node.func) in ("isinstance", "issubclass") and len(node.args) == 2
            if checks_type and _is_union(node.args[1]) and not _typeish(node.args[1]):
                found.append(f"{where}:{line}: `X | Y` evaluated at run time")
    return found
