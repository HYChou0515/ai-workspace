"""#284 follow-up — a REAL render of the bundled craft library.

Fake-IVlm unit tests prove the loop's control flow; this proves the actual
`recipes.js` + `theme.js` + `render_deck.sh` produce a valid, non-empty deck
through node + LibreOffice + poppler. Marked integration: runs in the full local
suite (and the deck sandbox image), skipped in CI. Mirrors what `build_deck`
does, minus the model — it composes a build.js from the documented recipe API.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_app.agent.deck.loop import CraftAssets

pytestmark = pytest.mark.integration


def _toolchain_ready() -> bool:
    if not all(shutil.which(b) for b in ("node", "soffice", "pdftoppm")):
        return False
    # pptxgenjs must be resolvable (global install + NODE_PATH in the deck image).
    return (
        subprocess.run(
            ["node", "-e", "require.resolve('pptxgenjs')"],
            capture_output=True,
        ).returncode
        == 0
    )


# A build.js that exercises a representative slice of the recipe API (cover,
# header, two-column with accents, page number, dark closer) — including CJK so
# the noto-cjk font path is real.
_BUILD_JS = """\
const pptxgen = require("pptxgenjs");
const R = require("./recipes");
const { C } = require("./theme");
(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  let s = pres.addSlide();
  R.cover(pres, s, { eyebrow: "TEST", title: "良率報告", subtitle: "Yield", blurb: "x" });
  s = pres.addSlide(); s.background = { color: C.bg };
  R.pageHeader(s, "SECTION", "方案比較", "two routes");
  R.twoCol(pres, s, [
    { title: "A", subtitle: "DET", accent: C.red, body: "固定流程。" },
    { title: "B", subtitle: "ADP", accent: C.green, body: "看圖修正。" },
  ]);
  R.pageNum(s, 2, 3);
  s = pres.addSlide();
  R.closer(pres, s, { title: "Done", takeaways: [{ n: "01", text: "ship it" }] });
  await pres.writeFile({ fileName: "./deck.pptx" });
})();
"""


@pytest.mark.skipif(not _toolchain_ready(), reason="node/libreoffice/poppler/pptxgenjs unavailable")
def test_recipes_library_renders_a_real_deck(tmp_path: Path):
    assets = CraftAssets.load()
    (tmp_path / "theme.js").write_text(assets.theme_js, encoding="utf-8")
    (tmp_path / "recipes.js").write_text(assets.recipes_js, encoding="utf-8")
    (tmp_path / "render_deck.sh").write_text(assets.render_script, encoding="utf-8")
    (tmp_path / "build.js").write_text(_BUILD_JS, encoding="utf-8")

    build = subprocess.run(["node", "build.js"], cwd=tmp_path, capture_output=True, text=True)
    assert build.returncode == 0, f"node build.js failed:\n{build.stderr}"
    deck = tmp_path / "deck.pptx"
    assert deck.is_file() and deck.stat().st_size > 10_000

    render = subprocess.run(
        ["bash", "render_deck.sh", "./deck.pptx"], cwd=tmp_path, capture_output=True, text=True
    )
    assert render.returncode == 0, f"render_deck.sh failed:\n{render.stderr}"
    slides = sorted(tmp_path.glob("slide-*.jpg"))
    assert len(slides) == 3, f"expected 3 slide images, got {[p.name for p in slides]}"
    assert all(p.stat().st_size > 2_000 for p in slides)  # non-blank renders
