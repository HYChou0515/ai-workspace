"""Pre-flight preview (#283) — the launch dialog's pre-run checklist + summary.

A workflow author writes ``async def preflight(wf, inputs) -> PreflightReport`` that
verifies preconditions and describes (in human words) what the run is about to do.
The launch dialog renders it before the run starts and blocks 'Run' when a REQUIRED
check fails (a missing precondition that would make the run no-op or error)."""

from workspace_app.workflow.preflight import PreflightItem, PreflightReport, Severity, can_run


def test_can_run_true_when_no_checks():
    # A report that's pure description (no checks) never blocks.
    assert can_run(PreflightReport(summary="will ingest 3 files into a, b")) is True


def test_required_failure_blocks_run():
    report = PreflightReport(
        checks=[PreflightItem(label="uploads has files", ok=False, severity=Severity.REQUIRED)]
    )
    assert can_run(report) is False


def test_advisory_failure_does_not_block_run():
    report = PreflightReport(
        checks=[PreflightItem(label="odd files", ok=False, severity=Severity.ADVISORY)]
    )
    assert can_run(report) is True


def test_unmarked_failure_blocks_by_default():
    # severity defaults to REQUIRED, so an author who forgets to mark it still blocks
    # (the safe default — better to over-block than launch a doomed run).
    assert can_run(PreflightReport(checks=[PreflightItem(label="x", ok=False)])) is False


def test_passing_required_with_failing_advisory_runs():
    report = PreflightReport(
        checks=[
            PreflightItem(label="uploads has files", ok=True),
            PreflightItem(label="heads up", ok=False, severity=Severity.ADVISORY),
        ]
    )
    assert can_run(report) is True
