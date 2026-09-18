"""The Content-Type a workspace file is served with — ONE rule, shared.

The file route (`api/file_routes.py`, `read_file`) and the chat-video player
(`chat_video/player.py`) both call this, so a picture in a rendered video is
sent to the browser under exactly the media type the chat serves it with:
same bytes, same type, same Chromium, same picture (or the same broken one).
Rounds 5–8 of PR #817 were four ways of deciding the type from the bytes
instead, and each disagreed with the browser somewhere; the chat never
looks at the bytes for this, and now neither does the video.
"""

from __future__ import annotations

import mimetypes


def media_type_for(path: str, data: bytes) -> str:
    """Issue #40: extension → MIME first so workspace markdown reports
    rendering ``![foo](./foo.png)`` get ``Content-Type: image/png`` (the
    browser inlines) instead of ``application/octet-stream`` (the browser
    offers a download). Unknown extension → the older UTF-8 sniff, so
    text-with-unknown-extension still renders in the file viewer.

    ``path`` must be the NORMALISED path: ``splitext`` sees no extension on
    a string ending in ``/``."""
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain; charset=utf-8"
