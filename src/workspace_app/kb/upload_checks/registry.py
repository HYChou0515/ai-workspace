"""UploadCheckRegistry — runtime directory of ``IUploadCheck`` instances.

The Ingestor holds ONE registry and calls ``run(filename, mime, data)``
at the top of ``store()``. Every registered check whose ``applies`` is
True inspects the bytes, in registration order; the FIRST that returns
an ``UploadRejection`` aborts the upload by raising ``UploadRejected``
(no further checks run — one reason is enough to refuse the file).

``hints()`` collects the browser-runnable subset for the
``GET /kb/upload-checks`` endpoint.
"""

from __future__ import annotations

from .protocol import IUploadCheck, UploadCheckHint, UploadRejected


class UploadCheckRegistry:
    def __init__(self) -> None:
        self._checks: list[IUploadCheck] = []

    def register(self, check: IUploadCheck) -> UploadCheckRegistry:
        """Append ``check`` at the tail. Returns the registry so the
        bundled-check factory can chain registrations."""
        self._checks.append(check)
        return self

    def run(self, *, filename: str, mime: str, data: bytes) -> None:
        """Inspect ``data`` with every applicable check. Raises
        ``UploadRejected`` on the first rejection; returns ``None`` (the
        upload is accepted) when no check objects."""
        for check in self._checks:
            if check.applies(filename=filename, mime=mime):
                rejection = check.inspect(data)
                if rejection is not None:
                    raise UploadRejected(rejection)

    def hints(self) -> list[UploadCheckHint]:
        """The browser-runnable descriptors, in registration order —
        checks with no client prefilter (server-only) are omitted."""
        out: list[UploadCheckHint] = []
        for check in self._checks:
            hint = check.client_prefilter()
            if hint is not None:
                out.append(hint)
        return out

    def checks(self) -> list[IUploadCheck]:
        """A copy of the registered checks, in registration order — for
        diagnostics / startup logging."""
        return list(self._checks)
