"""Enable coverage measurement inside pytest-xdist worker subprocesses.

Python auto-imports a top-level ``sitecustomize`` module on interpreter
startup when it's importable (we put the repo root on PYTHONPATH for the
test run). ``coverage.process_startup()`` is a no-op UNLESS the
``COVERAGE_PROCESS_START`` env var points at a coverage config — so this
costs nothing for normal app/test runs and only activates when we
explicitly ask for subprocess coverage (CI's parallel ``pytest -n auto``).
"""

try:
    import coverage

    coverage.process_startup()
except ImportError:  # coverage not installed (e.g. a production image) — fine.
    pass
