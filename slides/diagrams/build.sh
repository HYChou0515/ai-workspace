#!/usr/bin/env bash
# Rebuild every diagram SVG from its .mmd source.
#
# The deck references the SVGs, not the mermaid source, so that it renders the
# same everywhere — VS Code's Marp preview, GitHub, and the CLI exporters — none
# of which run mermaid themselves.
#
# Usage: slides/diagrams/build.sh [name ...]   (no args = all)
set -euo pipefail
cd "$(dirname "$0")"

# mermaid-cli drives a headless Chromium. Point it at one if the system has none.
if [[ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]]; then
  candidate=$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | tail -1 || true)
  [[ -n "$candidate" ]] && export PUPPETEER_EXECUTABLE_PATH="$candidate"
fi

sources=("$@")
if [[ ${#sources[@]} -eq 0 ]]; then
  sources=(*.mmd)
else
  sources=("${sources[@]/%/.mmd}")
fi

for src in "${sources[@]}"; do
  echo "→ ${src%.mmd}.svg"
  npx --yes @mermaid-js/mermaid-cli@11 \
    -i "$src" -o "${src%.mmd}.svg" \
    -c mermaid-config.json -p puppeteer.json -b transparent >/dev/null
done
echo "done: ${#sources[@]} diagram(s)"
