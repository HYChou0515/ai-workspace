"""What a tool's exit code means, in words the model can act on (#674).

Two kinds of code arrive here, and they are worth telling apart.

**The system's.** A timeout we imposed, a kill from the memory cap we set, a
segfault, a launcher that would not start. Nobody declares these; they happen.
They are also the most confusing failures the platform can produce, because
they reach the model as a bare number with little or no output — `exit_code=-9`
is what a tool gets for exceeding a limit it was never told about.

**The tool's.** A small published set an author opts into. It stays small on
purpose: a code that does not change what anyone does next is decoration that
costs an author a rule to remember, and a code an author gets *wrong* is worse
than none — a permanent failure labelled retryable makes a model try forever.
So the default (`1`) is the safe one, and everything unclaimed lands there.

The note is guidance, never an instruction the platform acts on by itself.
`2` in particular means *the model may call again*, not that we retry silently:
a tool can have side effects, and repeating them unasked is how a "helpful"
retry deletes something twice.
"""

from __future__ import annotations

import signal

#: The tool's own codes. `1` is deliberately absent: it means "failed", the
#: output already says how, and a note would add nothing.
RETRYABLE = 2
BLOCKED = 3

#: Ours, borrowed from GNU `timeout` and honoured by every backend.
TIMEOUT = 124

_BY_CODE = {
    RETRYABLE: (
        "The tool reports this as retryable — call it again. When the message "
        "names something wrong with the arguments, fix that first."
    ),
    BLOCKED: (
        "The tool cannot proceed until someone acts, so calling it again "
        "unchanged will fail the same way. Tell the user exactly what is "
        "needed — if it names a missing variable, they can set it in this "
        "workspace's environment-variables panel."
    ),
    TIMEOUT: (
        "The platform stopped the tool: it ran past the time limit, or produced "
        "no output for long enough to look hung. A smaller request may finish."
    ),
    126: (
        "The tool never started — its launcher is present but not executable. "
        "The bundle is broken; report it to whoever publishes the tool."
    ),
    127: (
        "The tool never started — its launcher was not found. The bundle is "
        "missing or was not mounted; report it to whoever publishes the tool."
    ),
}

_BY_SIGNAL = {
    signal.SIGKILL: (
        "The sandbox killed the tool. This is almost always the memory limit — "
        "the same request over less data usually succeeds."
    ),
    signal.SIGSEGV: (
        "The tool crashed. A bundle that segfaults was built for a different "
        "environment than the one running it; report it to whoever publishes "
        "the tool."
    ),
}


def _signal_of(exit_code: int) -> int | None:
    """The signal that killed the process, however this backend spells it.

    The host reports a raw negative return code; shells and other backends
    report `128 + signal`. An author reading either needs the same sentence."""
    if exit_code < 0:
        return -exit_code
    if 128 < exit_code < 128 + signal.NSIG:
        return exit_code - 128
    return None


def explain(exit_code: int) -> str | None:
    """One sentence about this exit code, or None when the output speaks for
    itself (success, and plain unqualified failure)."""
    if exit_code in _BY_CODE:
        return _BY_CODE[exit_code]
    sig = _signal_of(exit_code)
    if sig is not None:
        try:
            return _BY_SIGNAL[signal.Signals(sig)]
        except (ValueError, KeyError):
            return f"The sandbox stopped the tool with signal {sig}."
    return None
