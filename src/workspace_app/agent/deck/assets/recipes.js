// recipes.js — the make_deck craft library (#284 follow-up).
//
// A faithful port of the proven `designed-pptx` skill's layout patterns +
// visual recipes into require-able helper functions, so the model's `build.js`
// COMPOSES tested building blocks instead of hand-writing every slide. The
// prompt (system.md) documents this API; the model never reads this file, it
// just calls these functions.
//
// Every helper takes `(pres, s, …)` — `pres` for `pres.shapes.*`, `s` the slide
// from `pres.addSlide()`. Tokens come from ./theme (C, FONT_*). All coordinates
// are absolute inches on LAYOUT_WIDE (13.3 × 7.5).
//
// Usage (the canonical build.js shape):
//   const pptxgen = require("pptxgenjs");
//   const R = require("./recipes");
//   const { C } = require("./theme");
//   (async () => {
//     const pres = new pptxgen();
//     pres.layout = "LAYOUT_WIDE";
//     let cover = pres.addSlide();
//     R.cover(pres, cover, { eyebrow: "INTERNAL · 2026", title: "My Deck",
//                            subtitle: "Subtitle", blurb: "One-line framing." });
//     let s = pres.addSlide(); s.background = { color: C.bg };
//     R.pageHeader(s, "SECTION", "A content slide", "Optional subtitle");
//     R.twoCol(pres, s, [
//       { title: "Approach A", subtitle: "tag", accent: C.red, body: "…" },
//       { title: "Approach B", subtitle: "tag", accent: C.green, body: "…" },
//     ]);
//     R.pageNum(s, 2, 8);
//     await pres.writeFile({ fileName: "./deck.pptx" });
//   })();

const { C, FONT_H, FONT_B, FONT_M } = require("./theme");

const W = 13.3;
const H = 7.5;

// ─── universal page header (every content slide opens with this) ──────────
// Three tiers: eyebrow (mono uppercase accent) → title (large navy) → subtitle.
function pageHeader(s, eyebrow, title, subtitle) {
  s.addText(eyebrow || "", {
    x: 0.6, y: 0.4, w: 12, h: 0.4,
    fontSize: 13, fontFace: FONT_M, bold: true,
    color: C.blue, charSpacing: 4, margin: 0,
  });
  s.addText(title || "", {
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

// ─── cover slide (dark, hero) ─────────────────────────────────────────────
// o: { eyebrow, title, subtitle, blurb }
function cover(pres, s, o) {
  s.background = { color: C.navy };
  s.addText(o.eyebrow || "", {
    x: 0.8, y: 2.4, w: 8, h: 0.4,
    fontSize: 14, fontFace: FONT_B, bold: true, color: C.blue, charSpacing: 4,
  });
  s.addText(o.title || "", {
    x: 0.8, y: 2.9, w: 11, h: 1.2,
    fontSize: 60, fontFace: FONT_H, bold: true, color: "FFFFFF", margin: 0,
  });
  if (o.subtitle) {
    s.addText(o.subtitle, {
      x: 0.8, y: 4.1, w: 11, h: 0.7,
      fontSize: 26, fontFace: FONT_H, color: C.blueSoft, margin: 0,
    });
  }
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.8, y: 5.1, w: 0.8, h: 0.04,
    fill: { color: C.blue }, line: { color: C.blue, width: 0 },
  });
  if (o.blurb) {
    s.addText(o.blurb, {
      x: 0.8, y: 5.3, w: 11, h: 0.5,
      fontSize: 16, fontFace: FONT_B, color: "B8C0E0", italic: true, margin: 0,
    });
  }
}

// ─── closing slide (mirrors the cover) ────────────────────────────────────
// o: { eyebrow, title, takeaways: [{ n, text }] }
function closer(pres, s, o) {
  s.background = { color: C.navy };
  s.addText(o.eyebrow || "CONCLUSION", {
    x: 0.8, y: 0.5, w: 12, h: 0.4,
    fontSize: 14, fontFace: FONT_B, bold: true, color: C.blue, charSpacing: 4, margin: 0,
  });
  s.addText(o.title || "", {
    x: 0.8, y: 0.95, w: 12, h: 0.85,
    fontSize: 42, fontFace: FONT_H, bold: true, color: "FFFFFF", margin: 0,
  });
  let yy = 3.45;
  (o.takeaways || []).forEach((g) => {
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
}

// ─── two-column comparison (A vs B) ───────────────────────────────────────
// cards: exactly 2 × { title, subtitle, accent, body }. Call pageHeader first.
function twoCol(pres, s, cards) {
  const xs = [0.6, 6.95];
  cards.slice(0, 2).forEach((c, i) => {
    const x = xs[i];
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: 2.3, w: 5.75, h: 4.7,
      fill: { color: C.card }, line: { color: C.border, width: 1 },
      shadow: { type: "outer", color: "000000", blur: 8, offset: 2, angle: 90, opacity: 0.06 },
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: 2.3, w: 5.75, h: 0.12,
      fill: { color: c.accent || C.blue }, line: { color: c.accent || C.blue, width: 0 },
    });
    s.addText(c.title || "", {
      x: x + 0.4, y: 2.55, w: 5, h: 0.5,
      fontSize: 24, fontFace: FONT_H, bold: true, color: C.navy, margin: 0,
    });
    if (c.subtitle) {
      s.addText(c.subtitle, {
        x: x + 0.4, y: 3.05, w: 5, h: 0.35,
        fontSize: 12, fontFace: FONT_M, color: c.accent || C.textMute, charSpacing: 1, margin: 0,
      });
    }
    s.addText(c.body || "", {
      x: x + 0.4, y: 3.5, w: 5, h: 3.3,
      fontSize: 13, fontFace: FONT_B, color: C.text, valign: "top", margin: 0,
    });
  });
}

// ─── three-column comparison ──────────────────────────────────────────────
// columns: up to 3 × { label, highlight?, body }. Highlight the "winner".
function threeCol(pres, s, columns) {
  const colXs = [0.5, 4.625, 8.75];
  const colW = 4.05, colY = 2.0, colH = 5.0;
  columns.slice(0, 3).forEach((col, i) => {
    const x = colXs[i];
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: colY, w: colW, h: colH,
      fill: { color: col.highlight ? "F0F4FE" : C.card },
      line: { color: col.highlight ? C.blue : C.border, width: col.highlight ? 1.5 : 1 },
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: colY, w: colW, h: 0.6,
      fill: { color: C.navy }, line: { color: C.navy, width: 0 },
    });
    s.addText(col.label || "", {
      x: x + 0.2, y: colY, w: colW - 0.3, h: 0.6,
      fontSize: 13, fontFace: FONT_M, bold: true, color: C.blue, charSpacing: 2,
      valign: "middle", margin: 0,
    });
    s.addText(col.body || "", {
      x: x + 0.25, y: colY + 0.8, w: colW - 0.5, h: colH - 1.0,
      fontSize: 12, fontFace: FONT_B, color: C.text, valign: "top", margin: 0,
    });
  });
}

// ─── numbered step rows (process flow) ────────────────────────────────────
// items: [{ n, title, body, highlight? }]
function steps(pres, s, items) {
  let sy = 2.2;
  const sH = 1.05;
  items.forEach((st) => {
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.6, y: sy, w: 12.1, h: sH,
      fill: { color: st.highlight ? "F0F4FE" : C.card },
      line: { color: st.highlight ? C.blue : C.border, width: st.highlight ? 1.5 : 1 },
    });
    s.addShape(pres.shapes.OVAL, {
      x: 0.85, y: sy + 0.22, w: 0.6, h: 0.6,
      fill: { color: st.highlight ? C.blue : C.navy },
      line: { color: st.highlight ? C.blue : C.navy, width: 0 },
    });
    s.addText(String(st.n), {
      x: 0.85, y: sy + 0.22, w: 0.6, h: 0.6,
      fontSize: 22, fontFace: FONT_H, bold: true, color: "FFFFFF",
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(st.title || "", {
      x: 1.65, y: sy + 0.15, w: 10.8, h: 0.45,
      fontSize: 17, fontFace: FONT_H, bold: true, color: C.navy, margin: 0,
    });
    s.addText(st.body || "", {
      x: 1.65, y: sy + 0.6, w: 10.8, h: 0.4,
      fontSize: 12, fontFace: FONT_B, color: C.text, valign: "top", margin: 0,
    });
    sy += sH + 0.1;
  });
}

// ─── card grid 2×2 (categorical, equal weight) ────────────────────────────
// cards: up to 4 × { title, body }
function cardGrid(pres, s, cards) {
  const cW = 5.95, cH = 2.4, cGap = 0.13;
  cards.slice(0, 4).forEach((c, i) => {
    const row = Math.floor(i / 2), col = i % 2;
    const cx = 0.6 + col * (cW + cGap);
    const cy = 2.2 + row * (cH + cGap);
    s.addShape(pres.shapes.RECTANGLE, {
      x: cx, y: cy, w: cW, h: cH,
      fill: { color: C.card }, line: { color: C.border, width: 1 },
    });
    s.addText(c.title || "", {
      x: cx + 0.35, y: cy + 0.25, w: cW - 0.7, h: 0.5,
      fontSize: 18, fontFace: FONT_H, bold: true, color: C.navy, margin: 0,
    });
    s.addText(c.body || "", {
      x: cx + 0.35, y: cy + 0.85, w: cW - 0.7, h: cH - 1.1,
      fontSize: 12, fontFace: FONT_B, color: C.text, valign: "top", margin: 0,
    });
  });
}

// ─── big callout / pull statement ─────────────────────────────────────────
// o: { parts: [{ text, color }], caption }. Call pageHeader first.
function callout(pres, s, o) {
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 1.9, w: 12.1, h: 1.6,
    fill: { color: C.navy }, line: { color: C.navy, width: 0 },
  });
  s.addText(
    (o.parts || []).map((p) => ({
      text: p.text,
      options: { fontSize: 56, bold: true, color: p.color || "FFFFFF", fontFace: FONT_H },
    })),
    { x: 0.6, y: 2.0, w: 12.1, h: 1.0, align: "center", valign: "middle", margin: 0 }
  );
  if (o.caption) {
    s.addText(o.caption, {
      x: 0.6, y: 3.0, w: 12.1, h: 0.45,
      fontSize: 14, fontFace: FONT_B, italic: true, color: C.blueSoft,
      align: "center", margin: 0,
    });
  }
}

// ─── decision matrix table (color-by-content ✓/✗/△) ───────────────────────
// matrix: array of rows (row 0 = header), each a string[]. colW optional.
function decisionMatrix(s, matrix, opts) {
  opts = opts || {};
  function cellStyle(r, c, cell) {
    if (r === 0)
      return { fill: { color: C.navy }, color: "FFFFFF", bold: true, fontSize: 11, align: c === 0 ? "left" : "center" };
    if (c === 0)
      return { fill: { color: "F1F2F8" }, color: C.text, bold: true, fontSize: 10.5, align: "left" };
    let bg = "FFFFFF", fg = C.text;
    if (cell.startsWith("✓")) { bg = C.greenSoft; fg = "065F46"; }
    else if (cell.startsWith("✗")) { bg = C.redSoft; fg = "991B1B"; }
    else if (cell.startsWith("△")) { bg = C.amberSoft; fg = "92400E"; }
    return { fill: { color: bg }, color: fg, fontSize: 10.5, fontFace: FONT_M, align: "center" };
  }
  const rows = matrix.map((row, i) =>
    row.map((cell, j) => ({
      text: cell,
      options: { ...cellStyle(i, j, cell), valign: "middle", margin: 4 },
    }))
  );
  s.addTable(rows, {
    x: 0.5, y: opts.y || 1.4, w: 12.3,
    colW: opts.colW,
    rowH: opts.rowH || 0.36,
    border: { type: "solid", color: C.border, pt: 0.75 },
  });
}

// ─── code block with syntax highlighting ──────────────────────────────────
// o: { x, y, w, h, label, segments: [{ text, color }] }. Colors: theme codeKw/
// codeFn/codeStr/codeNum/codeCom/codeText.
function codeBlock(pres, s, o) {
  const x = o.x ?? 0.6, y = o.y ?? 2.2, w = o.w ?? 6.0, h = o.h ?? 5.05;
  s.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: C.codeBg }, line: { color: C.codeBg, width: 0 },
  });
  if (o.label) {
    s.addText(o.label, {
      x: x + 0.25, y: y + 0.15, w: w - 0.5, h: 0.3,
      fontSize: 10, fontFace: FONT_M, color: "8A93B8", margin: 0,
    });
  }
  s.addText(
    (o.segments || []).map((seg) => ({
      text: seg.text,
      options: { color: seg.color || C.codeText, fontSize: 13, fontFace: FONT_M },
    })),
    { x: x + 0.25, y: y + 0.5, w: w - 0.5, h: h - 0.6, valign: "top", margin: 0 }
  );
}

// ─── knowledge graph (orthogonal nodes + masked edge labels) ──────────────
// o: { frame?: {x,y,w,h}, nodes: [{x,y,label,type,color}],
//      edges: [{from,to,label,lx,ly}] }. Spread nodes across directions so
//      edge labels can't collide.
function graph(pres, s, o) {
  if (o.frame) {
    s.addShape(pres.shapes.RECTANGLE, {
      x: o.frame.x, y: o.frame.y, w: o.frame.w, h: o.frame.h,
      fill: { color: C.card }, line: { color: C.border, width: 1 },
    });
  }
  const nodes = o.nodes || [];
  (o.edges || []).forEach((e) => {
    const A = nodes[e.from], B = nodes[e.to];
    const ax = A.x + 0.85, ay = A.y + 0.225;
    const bx = B.x + 0.85, by = B.y + 0.225;
    s.addShape(pres.shapes.LINE, {
      x: ax, y: ay, w: bx - ax, h: by - ay,
      line: { color: C.textMute, width: 1.5 },
    });
    if (e.label) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: e.lx, y: e.ly, w: 1.5, h: 0.25,
        fill: { color: C.card }, line: { color: C.card, width: 0 },
      });
      s.addText(e.label, {
        x: e.lx, y: e.ly, w: 1.5, h: 0.25,
        fontSize: 9.5, fontFace: FONT_M, italic: true, color: C.textMute,
        align: "center", valign: "middle", margin: 0,
      });
    }
  });
  nodes.forEach((n, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: n.x, y: n.y, w: 1.7, h: 0.45,
      fill: { color: n.color || C.blue }, line: { color: n.color || C.blue, width: 0 },
      rectRadius: 0.06,
    });
    s.addText(n.label, {
      x: n.x, y: n.y, w: 1.7, h: 0.45,
      fontSize: 11.5, fontFace: FONT_H, bold: true, color: "FFFFFF",
      align: "center", valign: "middle", margin: 0,
    });
    if (n.type) {
      const typeY = n.below ? n.y + 0.5 : n.y - 0.25;
      s.addText(n.type, {
        x: n.x, y: typeY, w: 1.7, h: 0.22,
        fontSize: 8.5, fontFace: FONT_M, color: C.textMute, align: "center", margin: 0,
      });
    }
  });
}

// ─── decorative graph motif for cover corners ─────────────────────────────
// o: { nodes: [{x,y,r}], edges: [[a,b]] }
function coverMotif(pres, s, o) {
  const nodes = o.nodes || [];
  (o.edges || []).forEach(([a, b]) => {
    const A = nodes[a], B = nodes[b];
    s.addShape(pres.shapes.LINE, {
      x: A.x + A.r / 2, y: A.y + A.r / 2, w: B.x - A.x, h: B.y - A.y,
      line: { color: C.blue, width: 0.75, transparency: 50 },
    });
  });
  nodes.forEach((n) => {
    s.addShape(pres.shapes.OVAL, {
      x: n.x, y: n.y, w: n.r, h: n.r,
      fill: { color: C.blue }, line: { color: C.blue, width: 0 },
    });
  });
}

// ─── linear flow (step → step → step pills with arrows) ───────────────────
// items: [{ label }]. y optional.
function flow(pres, s, items, y) {
  const flowY = y ?? 2.65, boxW = 2.55, gap = 0.35;
  let curX = 0.85;
  items.forEach((st, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: curX, y: flowY, w: boxW, h: 0.85,
      fill: { color: C.blueSoft }, line: { color: C.blueSoft, width: 0 }, rectRadius: 0.1,
    });
    s.addText(st.label, {
      x: curX, y: flowY, w: boxW, h: 0.85,
      fontSize: 12, fontFace: FONT_B, bold: true, color: C.navy,
      align: "center", valign: "middle", margin: 0,
    });
    curX += boxW;
    if (i < items.length - 1) {
      s.addText("→", {
        x: curX + 0.05, y: flowY, w: 0.25, h: 0.85,
        fontSize: 22, fontFace: FONT_H, bold: true, color: C.textMute,
        align: "center", valign: "middle", margin: 0,
      });
      curX += gap;
    }
  });
}

// ─── callout banner (the key insight bar) ─────────────────────────────────
// o: { text, variant: 'tip'|'warning'|'outcome'|'note', y? }
const _BANNER = {
  tip: { bg: "FFF7E6", border: C.gold, fg: C.navy },
  warning: { bg: C.redSoft, border: C.red, fg: C.red },
  outcome: { bg: C.greenSoft, border: C.green, fg: "065F46" },
  note: { bg: C.blueSoft, border: C.blue, fg: C.navy },
};
function calloutBanner(pres, s, o) {
  const v = _BANNER[o.variant] || _BANNER.tip;
  const y = o.y ?? 2.15;
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y, w: 12.1, h: 1.0,
    fill: { color: v.bg }, line: { color: v.border, width: 1 },
  });
  s.addText(o.text || "", {
    x: 1.1, y, w: 11.1, h: 1.0,
    fontSize: 16, fontFace: FONT_H, bold: true, color: v.fg, valign: "middle", margin: 0,
  });
}

// ─── chip / pill (status badge) ───────────────────────────────────────────
// o: { x, y, w, h, text, color }
function chip(pres, s, o) {
  const w = o.w ?? 0.95, h = o.h ?? 0.32;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: o.x, y: o.y, w, h,
    fill: { color: o.color || C.blue }, line: { color: o.color || C.blue, width: 0 },
    rectRadius: 0.04,
  });
  s.addText(o.text || "", {
    x: o.x, y: o.y, w, h,
    fontSize: 9, fontFace: FONT_M, bold: true, color: "FFFFFF", charSpacing: 1,
    align: "center", valign: "middle", margin: 0,
  });
}

// ─── pull-quote with decorative quote mark ────────────────────────────────
// o: { source, lines: [{ text, color, bold }] }
function pullQuote(pres, s, o) {
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 1.85, w: 12.1, h: 2.1,
    fill: { color: C.navy }, line: { color: C.navy, width: 0 },
  });
  s.addText("“", {
    x: 0.7, y: 1.7, w: 0.8, h: 1.0,
    fontSize: 80, fontFace: "Georgia", bold: true, color: C.blue, margin: 0,
  });
  if (o.source) {
    s.addText(o.source, {
      x: 1.5, y: 1.95, w: 11, h: 0.35,
      fontSize: 11, fontFace: FONT_B, bold: true, color: C.blue, charSpacing: 3, margin: 0,
    });
  }
  let qy = 2.32;
  (o.lines || []).forEach((ln) => {
    s.addText(ln.text, {
      x: 1.5, y: qy, w: 11.1, h: 0.45,
      fontSize: ln.bold ? 14 : 13, fontFace: FONT_B, italic: true, bold: !!ln.bold,
      color: ln.color || "DCE4FA", margin: 0,
    });
    qy += 0.46;
  });
}

// ─── Y-split (one source → two outcomes) ──────────────────────────────────
// o: { label }. Add two cards at y≈3.85 on left/right afterwards.
function ySplit(pres, s, o) {
  s.addShape(pres.shapes.RECTANGLE, {
    x: 5.3, y: 2.2, w: 2.7, h: 0.85,
    fill: { color: C.navy }, line: { color: C.navy, width: 0 },
  });
  s.addText(o.label || "", {
    x: 5.3, y: 2.2, w: 2.7, h: 0.85,
    fontSize: 16, fontFace: FONT_H, bold: true, color: "FFFFFF",
    align: "center", valign: "middle", margin: 0,
  });
  s.addShape(pres.shapes.LINE, { x: 5.3, y: 3.05, w: -2.0, h: 0.7, line: { color: C.blue, width: 2.5 } });
  s.addShape(pres.shapes.LINE, { x: 8.0, y: 3.05, w: 2.0, h: 0.7, line: { color: C.blue, width: 2.5 } });
}

// ─── deck-wide footer: section progress strip ─────────────────────────────
// o: { total, current } — 0-based current.
function navStrip(pres, s, o) {
  const segW = 1.2, segGap = 0.1;
  let segX = 0.5;
  for (let i = 0; i < o.total; i++) {
    const on = i === o.current;
    s.addShape(pres.shapes.RECTANGLE, {
      x: segX, y: 7.2, w: segW, h: 0.04,
      fill: { color: on ? C.blue : C.border }, line: { color: on ? C.blue : C.border, width: 0 },
    });
    segX += segW + segGap;
  }
}

// ─── small footer motif (bottom-left, unifies the deck) ───────────────────
function motif(pres, s) {
  const c = C.blueSoft;
  const dot = (x, y) =>
    s.addShape(pres.shapes.OVAL, { x, y, w: 0.08, h: 0.08, fill: { color: c }, line: { color: c, width: 0 } });
  dot(0.4, H - 0.35); dot(0.65, H - 0.35); dot(0.55, H - 0.55);
  s.addShape(pres.shapes.LINE, { x: 0.44, y: H - 0.31, w: 0.21, h: 0, line: { color: c, width: 0.75 } });
  s.addShape(pres.shapes.LINE, { x: 0.48, y: H - 0.31, w: 0.11, h: -0.16, line: { color: c, width: 0.75 } });
  s.addShape(pres.shapes.LINE, { x: 0.59, y: H - 0.47, w: 0.11, h: 0.16, line: { color: c, width: 0.75 } });
}

// ─── page number (bottom-right) ───────────────────────────────────────────
function pageNum(s, n, total, lightBg = true) {
  s.addText(`${n} / ${total}`, {
    x: W - 1.2, y: H - 0.4, w: 0.9, h: 0.25,
    fontSize: 9, fontFace: FONT_B, color: lightBg ? C.textMute : "9999AA",
    align: "right", margin: 0,
  });
}

module.exports = {
  W, H,
  pageHeader, cover, closer,
  twoCol, threeCol, steps, cardGrid, callout, decisionMatrix,
  codeBlock, graph, coverMotif, flow, calloutBanner, chip, pullQuote, ySplit,
  navStrip, motif, pageNum,
};
