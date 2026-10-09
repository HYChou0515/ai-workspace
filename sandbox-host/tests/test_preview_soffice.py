"""A real deck through real LibreOffice on the host (the app's docs/plan-pptx-preview.md D2): the
command the sandbox runs really produces a PDF where the cache expects it.
Integration — needs `soffice`, which CI's unit runners do not have."""

from __future__ import annotations

import shutil

import pytest

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec

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
