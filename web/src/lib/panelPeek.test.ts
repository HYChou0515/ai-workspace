import { describe, expect, it } from "vitest";

import { panelPeek } from "./panelPeek";

describe("panelPeek: a single click reveals a closed panel temporarily", () => {
  it("opens a closed panel into the temporary (peeked) state", () => {
    expect(panelPeek("closed", { type: "peek" })).toBe("peeked");
  });

  // A double click delivers TWO clicks before `dblclick` fires, so every pin
  // gesture is preceded by peeks. If a peek could demote a pinned panel, the
  // second click of "double-click to collapse" would silently un-pin it and the
  // panel would flicker between states mid-gesture.
  it("leaves an already-pinned panel pinned", () => {
    expect(panelPeek("pinned", { type: "peek" })).toBe("pinned");
  });
});

describe("panelPeek: a peek lasts until you go and do something else", () => {
  it("retracts a peeked panel once the interaction moves outside it", () => {
    expect(panelPeek("peeked", { type: "outside" })).toBe("closed");
  });

  it("keeps a pinned panel open no matter where you click", () => {
    expect(panelPeek("pinned", { type: "outside" })).toBe("pinned");
  });

  it("leaves a closed panel closed", () => {
    expect(panelPeek("closed", { type: "outside" })).toBe("closed");
  });
});

describe("panelPeek: a double click pins, and pins the same target again to close", () => {
  it("promotes a peeked panel to pinned", () => {
    expect(panelPeek("peeked", { type: "pin", sameTarget: true })).toBe("pinned");
  });

  it("pins a closed panel straight open", () => {
    expect(panelPeek("closed", { type: "pin", sameTarget: false })).toBe("pinned");
  });

  // "Double-click the tab you are already reading" is the collapse gesture.
  it("collapses when the pinned panel's own visible target is pinned again", () => {
    expect(panelPeek("pinned", { type: "pin", sameTarget: true })).toBe("closed");
  });

  // Double-clicking a DIFFERENT tab/icon is a switch, not a collapse — otherwise
  // moving from "Agent log" to "Terminal" would shut the panel in your face.
  it("stays pinned when a different target is pinned", () => {
    expect(panelPeek("pinned", { type: "pin", sameTarget: false })).toBe("pinned");
  });
});

describe("panelPeek: the chevron (and the keyboard shortcut) is a plain toggle", () => {
  // Deliberately NOT "peeked". Someone who reaches for the explicit open control
  // wants the panel to stay; handing them a peek means it evaporates the moment
  // they click into the editor, which reads as the button not having worked.
  it("opens a closed panel straight to pinned", () => {
    expect(panelPeek("closed", { type: "toggle" })).toBe("pinned");
  });

  it("closes a pinned panel", () => {
    expect(panelPeek("pinned", { type: "toggle" })).toBe("closed");
  });

  it("closes a peeked panel — it is showing, so the toggle hides it", () => {
    expect(panelPeek("peeked", { type: "toggle" })).toBe("closed");
  });
});
