// theme.js — the default design tokens for make_deck (#284).
// Ported from the `designed-pptx` skill's navy theme. `require('./theme')`
// at the top of build.js and reference C.navy / FONT_H etc. — never inline
// a hex or a font name in slide code; that is what keeps a deck coherent.
//
// Fonts default to "Noto Sans CJK TC": it is installed in the deck sandbox
// image, so what LibreOffice renders == what the user exports == what the
// review model sees. Swap to "Microsoft JhengHei" etc. for a PowerPoint
// audience that has it.

const C = {
  // Primary
  navy: "1A1B41",
  navyLight: "2A2D5F",

  // Accent — use sparingly (emphasis, eyebrows, links).
  blue: "4A7CFE",
  blueSoft: "DCE4FA",

  // Semantic
  red: "E63946",
  redSoft: "FEF2F2",
  green: "10B981",
  greenSoft: "ECFDF5",
  amber: "D97706",
  amberSoft: "FEF3C7",
  gold: "D4A933",

  // Neutrals
  bg: "FAFAFA", // page bg — off-white, not pure white
  card: "FFFFFF",
  text: "1F2937", // body — warm near-black, not pure black
  textMute: "6B7280",
  textDim: "9CA3AF",
  border: "E5E7EB",
  borderDark: "D1D5DB",

  // Code blocks (Material Theme Palenight)
  codeBg: "1E1F3A",
  codeText: "ECEFF4",
  codeKw: "C792EA",
  codeStr: "C3E88D",
  codeNum: "F78C6C",
  codeCom: "676E95",
  codeFn: "82AAFF",
};

const FONT_H = "Noto Sans CJK TC"; // headings (CJK-safe, render-fidelity)
const FONT_B = "Noto Sans CJK TC"; // body
const FONT_M = "Noto Sans Mono CJK TC"; // mono / code / labels

module.exports = { C, FONT_H, FONT_B, FONT_M };
