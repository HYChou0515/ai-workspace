"""A real deck through real LibreOffice (docs/plan-pptx-preview.md D2): the
command the sandbox runs really produces a PDF where the cache expects it.
Integration — needs `soffice`, which CI's unit runners do not have."""

from __future__ import annotations

import shutil

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed"),
]


# A real presentation as LibreOffice's flat XML (`.fodp`) — Impress opens it
# like any deck, and it needs no library to write.
_FODP = b"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
  office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:body><office:presentation>
  <draw:page draw:name="Q3">
   <draw:frame svg:x="2cm" svg:y="2cm" svg:width="20cm" svg:height="3cm">
    <draw:text-box><text:p>Q3 review</text:p></draw:text-box>
   </draw:frame>
  </draw:page>
 </office:presentation></office:body>
</office:document>
"""


async def test_libreoffice_turns_a_deck_into_its_cached_pdf(tmp_path) -> None:  # noqa: ANN001
    sandbox = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, _FODP, "/slides/q3 review.fodp")

    sha = await sandbox.render_preview(h, "/slides/q3 review.fodp", convert=True)

    assert sha is not None
    pdf = await sandbox.get_preview(h, sha)
    assert pdf is not None and pdf.startswith(b"%PDF")
    assert [f.path for f in (await sandbox.walk(h, "/")).files] == ["/slides/q3 review.fodp"]


async def test_under_the_userns_jail_it_fails_saying_why(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The `kind: local` jail mounts no /proc, and LibreOffice will not start
    without it — a limit of that jail (`make_deck` meets it too), not of this
    feature; production's host isolates by uid + cgroup, without a jail. What
    is pinned is that it fails HONESTLY: the converter's reason, nothing cached.
    soffice does not exit there, so it is the time limit that ends it — cut
    short here, or the test waits the full PREVIEW_TIMEOUT_S."""
    import workspace_app.sandbox.local_process as lp
    from workspace_app.sandbox.local_process import _userns_supported
    from workspace_app.sandbox.protocol import PreviewFailed

    if not _userns_supported():
        pytest.skip("no unprivileged user namespaces here")
    monkeypatch.setattr(lp, "PREVIEW_TIMEOUT_S", 15.0)
    sandbox = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, _FODP, "/q3.fodp")

    with pytest.raises(PreviewFailed, match="proc"):
        await sandbox.render_preview(h, "/q3.fodp", convert=True)

    assert await sandbox.render_preview(h, "/q3.fodp", convert=False) is None
