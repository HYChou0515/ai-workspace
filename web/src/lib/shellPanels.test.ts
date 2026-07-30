import { describe, expect, it } from "vitest";

import { toggleShellPanel } from "./shellPanels";

const both = { ideCollapsed: false, chatCollapsed: false };

describe("toggleShellPanel: the ordinary case", () => {
  it("folds the chat away when the workspace is there to take the room", () => {
    expect(toggleShellPanel(both, "chat", true)).toEqual({
      ideCollapsed: false,
      chatCollapsed: true,
    });
  });

  it("folds the workspace away when the chat is there to take the room", () => {
    expect(toggleShellPanel(both, "ide", true)).toEqual({
      ideCollapsed: true,
      chatCollapsed: false,
    });
  });

  it("brings a folded panel back", () => {
    expect(toggleShellPanel({ ideCollapsed: true, chatCollapsed: false }, "ide", true)).toEqual(
      both,
    );
  });
});

/**
 * Collapsing everything leaves the item as a top bar over a blank rectangle,
 * which reads as the page having broken. The last panel standing cannot be
 * folded away on its own — folding it unfolds the other one instead, so there
 * is always something on screen and never a state to be stuck in.
 */
describe("toggleShellPanel: something is always on screen", () => {
  it("swaps to the workspace when the chat is folded and the workspace was already away", () => {
    expect(toggleShellPanel({ ideCollapsed: true, chatCollapsed: false }, "chat", true)).toEqual({
      ideCollapsed: false,
      chatCollapsed: true,
    });
  });

  it("swaps to the chat when the workspace is folded and the chat was already away", () => {
    expect(toggleShellPanel({ ideCollapsed: false, chatCollapsed: true }, "ide", true)).toEqual({
      ideCollapsed: true,
      chatCollapsed: false,
    });
  });

  // A chat-only App has no workspace to swap to, so its chat simply cannot be
  // folded — there would be nothing left at all.
  it("refuses to fold the chat of an App that has no workspace", () => {
    expect(toggleShellPanel(both, "chat", false)).toEqual(both);
  });

  it("still lets that App's chat be un-folded, if it somehow got folded", () => {
    expect(toggleShellPanel({ ideCollapsed: true, chatCollapsed: true }, "chat", false)).toEqual({
      ideCollapsed: true,
      chatCollapsed: false,
    });
  });
});
