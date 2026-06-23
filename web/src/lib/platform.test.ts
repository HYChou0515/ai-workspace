// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";

import { isMac, modCombo, modLabel } from "./platform";

const mac = { platform: "MacIntel" };
const win = { platform: "Win32" };
const linux = { platform: "Linux x86_64" };

describe("platform modifier-key helpers", () => {
  it("detects macOS from navigator.platform", () => {
    expect(isMac(mac)).toBe(true);
    expect(isMac(win)).toBe(false);
    expect(isMac(linux)).toBe(false);
  });

  it("prefers the modern userAgentData.platform when present", () => {
    expect(isMac({ userAgentData: { platform: "macOS" }, platform: "Win32" })).toBe(true);
    expect(isMac({ userAgentData: { platform: "Windows" }, platform: "MacIntel" })).toBe(false);
  });

  it("labels the modifier ⌘ on Mac and Ctrl elsewhere", () => {
    expect(modLabel(mac)).toBe("⌘");
    expect(modLabel(win)).toBe("Ctrl");
  });

  it("composes a shortcut: tight on Mac, plus-joined elsewhere", () => {
    expect(modCombo("P", mac)).toBe("⌘P");
    expect(modCombo("P", win)).toBe("Ctrl+P");
    expect(modCombo("↵", linux)).toBe("Ctrl+↵");
  });
});
