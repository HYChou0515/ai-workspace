"""A hand-rolled multi-page TEXT-LAYER PDF for tests (Helvetica, one `Tj` per
line, no images) — `pypdf` extracts each page's lines verbatim, so a fixture
can give every page DIFFERENT text. The blank pages `test_read_tools` builds
cannot: a text-less page makes every page's text layer the same (empty), which
is exactly how the page-relative offset defect hid for three review rounds."""

from __future__ import annotations


def text_pdf(pages: list[list[str]]) -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_id = len(objs) + 1 + 2 * len(pages)  # reserved after pages+contents
    for lines in pages:
        ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for i, line in enumerate(lines):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj" + (" T*" if i < len(lines) - 1 else ""))
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        pid = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        page_ids.append(pid)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    real_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    assert real_pages_id == pages_id, (real_pages_id, pages_id)
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    )
    out += trailer.encode()
    return bytes(out)
