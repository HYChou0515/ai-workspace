// starter.js — copy this as your build.js starting point, then adapt (#284).
// Tokens come from ./theme.js (require it; never inline hex/fonts). Includes
// helpers + cover / 2-column content / closer. Add content slides between.
// IMPORTANT: set the writeFile fileName to the out_path you were told to use.

const pptxgen = require("pptxgenjs");
const { C, FONT_H, FONT_B, FONT_M } = require("./theme");

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.3 × 7.5 inches
  pres.title = "My Deck";

  const W = 13.3, H = 7.5;
  let TOTAL = 3; // adjust as you add slides

  // Header: eyebrow (mono, charSpacing) + title (large navy) + subtitle (mute).
  function pageHeader(s, eyebrow, title, subtitle) {
    s.addText(eyebrow, {
      x: 0.6, y: 0.4, w: 12, h: 0.4,
      fontSize: 13, fontFace: FONT_M, bold: true,
      color: C.blue, charSpacing: 4, margin: 0,
    });
    s.addText(title, {
      x: 0.6, y: 0.85, w: 12.5, h: 0.7,
      fontSize: 30, fontFace: FONT_H, bold: true, color: C.navy, margin: 0,
    });
    if (subtitle) {
      s.addText(subtitle, {
        x: 0.6, y: 1.55, w: 12, h: 0.4,
        fontSize: 14, fontFace: FONT_B, color: C.textMute, margin: 0,
      });
    }
  }

  function addPageNum(s, n, lightBg = true) {
    s.addText(`${n} / ${TOTAL}`, {
      x: W - 1.2, y: H - 0.4, w: 0.9, h: 0.25,
      fontSize: 9, fontFace: FONT_B,
      color: lightBg ? C.textMute : "9999AA", align: "right",
    });
  }

  // SLIDE 1 — COVER (dark, brand-led)
  {
    const s = pres.addSlide();
    s.background = { color: C.navy };
    s.addText("INTERNAL · 2026", {
      x: 0.8, y: 2.4, w: 8, h: 0.4,
      fontSize: 14, fontFace: FONT_B, bold: true, color: C.blue, charSpacing: 4,
    });
    s.addText("Deck Title Goes Here", {
      x: 0.8, y: 2.9, w: 11, h: 1.2,
      fontSize: 60, fontFace: FONT_H, bold: true, color: "FFFFFF", margin: 0,
    });
    s.addText("Supporting subtitle in soft accent color", {
      x: 0.8, y: 4.1, w: 11, h: 0.7,
      fontSize: 26, fontFace: FONT_H, color: C.blueSoft, margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.8, y: 5.1, w: 0.8, h: 0.04,
      fill: { color: C.blue }, line: { color: C.blue, width: 0 },
    });
  }

  // SLIDE 2 — CONTENT (eyebrow + title + 2-column comparison cards)
  {
    const s = pres.addSlide();
    s.background = { color: C.bg };
    pageHeader(s, "COMPARISON", "Approach A vs Approach B", "One-sentence framing.");
    [
      { x: 0.6, accent: C.red, accentBg: C.redSoft, title: "Approach A", verdict: "✗ rejected",
        body: "Mechanism, characteristics, when it applies." },
      { x: 6.95, accent: C.green, accentBg: C.greenSoft, title: "Approach B", verdict: "✓ accepted",
        body: "Mechanism, characteristics, when it applies." },
    ].forEach(c => {
      s.addShape(pres.shapes.RECTANGLE, {
        x: c.x, y: 2.3, w: 5.75, h: 4.7, fill: { color: C.card }, line: { color: C.border, width: 1 },
        shadow: { type: "outer", color: "000000", blur: 8, offset: 2, angle: 90, opacity: 0.06 },
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x: c.x, y: 2.3, w: 5.75, h: 0.12, fill: { color: c.accent }, line: { color: c.accent, width: 0 },
      });
      s.addText(c.title, {
        x: c.x + 0.4, y: 2.55, w: 4, h: 0.5,
        fontSize: 24, fontFace: FONT_H, bold: true, color: C.navy, margin: 0,
      });
      s.addText(c.body, {
        x: c.x + 0.4, y: 3.4, w: 5.0, h: 2.5,
        fontSize: 13, fontFace: FONT_B, color: C.text, valign: "top", margin: 0, // body → valign:"top"
      });
      s.addText(c.verdict, {
        x: c.x + 0.4, y: 6.25, w: 5.0, h: 0.55,
        fontSize: 16, fontFace: FONT_H, bold: true, color: c.accent,
        align: "center", valign: "middle", margin: 0, fill: { color: c.accentBg },
      });
    });
    addPageNum(s, 2);
  }

  // SLIDE 3 — CLOSER (dark, mirrors cover)
  {
    const s = pres.addSlide();
    s.background = { color: C.navy };
    s.addText("CONCLUSION", {
      x: 0.8, y: 0.5, w: 12, h: 0.4,
      fontSize: 14, fontFace: FONT_B, bold: true, color: C.blue, charSpacing: 4, margin: 0,
    });
    s.addText("Our Direction", {
      x: 0.8, y: 0.95, w: 12, h: 0.85,
      fontSize: 42, fontFace: FONT_H, bold: true, color: "FFFFFF", margin: 0,
    });
    let yy = 3.45;
    [
      { n: "01", text: "Takeaway one — the primary point." },
      { n: "02", text: "Takeaway two — supporting evidence." },
      { n: "03", text: "Takeaway three — the action / next step." },
    ].forEach(g => {
      s.addText(g.n, {
        x: 0.8, y: yy, w: 0.8, h: 0.55,
        fontSize: 20, fontFace: FONT_H, bold: true, color: C.blue, margin: 0,
      });
      s.addText(g.text, {
        x: 1.5, y: yy, w: 11, h: 0.55,
        fontSize: 18, fontFace: FONT_H, color: "FFFFFF", valign: "middle", margin: 0,
      });
      yy += 0.65;
    });
    addPageNum(s, 3, false);
  }

  // SAVE — replace with the out_path you were told to write.
  await pres.writeFile({ fileName: "./deck.pptx" });
  console.log("OK — wrote ./deck.pptx");
})();
