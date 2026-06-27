#!/usr/bin/env bash
# render_deck.sh — .pptx → per-slide JPGs for the make_deck review loop (#284).
# Usage: bash render_deck.sh <deck.pptx>
# Emits <dir>/slide-<n>.jpg next to the .pptx, for the model to inspect.
set -euo pipefail

PPTX="${1:?usage: render_deck.sh <deck.pptx>}"
DIR="$(dirname "$PPTX")"
NAME="$(basename "$PPTX" .pptx)"

# Clear any prior render so a now-fewer-slides deck doesn't leave stale images.
rm -f "$DIR/slide-"*.jpg

# .pptx → PDF (LibreOffice headless). soffice prints to stdout; keep it terse.
soffice --headless --convert-to pdf --outdir "$DIR" "$PPTX" >/dev/null

# PDF → one JPG per page at 130 DPI (legible for the VLM, small enough to send).
pdftoppm -jpeg -r 130 "$DIR/$NAME.pdf" "$DIR/slide"

ls "$DIR/slide-"*.jpg
