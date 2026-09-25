// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P13 — a view header compacts in a narrow pane.
 *
 * Measured in Chromium at 1440 wide with the file tree and chat open, a
 * five-pane layout gave view panels of 215 and 88 px: the header's display-size title
 * wrapped to 2–4 lines and the marking select and Refresh to rows of their own,
 * 72–152 px of a 328 px pane. The panel now measures ITS OWN width (the
 * viewport says nothing about a pane) and marks itself `data-narrow`; the
 * stylesheet keeps its title to one truncated line and its controls on the same
 * row. happy-dom lays nothing out, so this holds the mechanism — the width that
 * flips it, and the rules it switches on; the pixels are measured in a browser.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "../../components/Dialog";
import { effective, ENTITY_VIEWS_CSS } from "../../test/cssRules";
import { EntityViewBody, NARROW_PANEL, parseViewSpec } from "./EntityViews";

const realRO = globalThis.ResizeObserver;
let emit: (width: number) => void = () => {};
beforeEach(() => {
  const callbacks: ResizeObserverCallback[] = [];
  globalThis.ResizeObserver = class {
    constructor(cb: ResizeObserverCallback) {
      callbacks.push(cb);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  emit = (width) =>
    act(() => {
      for (const cb of callbacks) cb([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
    });
});
afterEach(() => {
  globalThis.ResizeObserver = realRO;
  cleanup();
});

function panel() {
  const { container } = render(
    <DialogProvider>
      <EntityViewBody
        spec={parseViewSpec("view: table\nentity: lot\ntitle: A title long enough to wrap in a pane\n")!}
        type={null}
        entities={[]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />
    </DialogProvider>,
  );
  return container.querySelector(".ev-panel") as HTMLElement;
}

describe("a view panel in a narrow pane", () => {
  it("marks itself narrow below its breakpoint, from its own width", () => {
    const el = panel();
    // unmeasured (happy-dom measures 0): not narrow — the first paint is the wide one
    expect(el).not.toHaveAttribute("data-narrow");
    emit(NARROW_PANEL - 1);
    expect(el).toHaveAttribute("data-narrow");
    emit(NARROW_PANEL);
    expect(el).not.toHaveAttribute("data-narrow");
  });

  it("keeps its title to one truncated line and its controls on the title's row", () => {
    const title = ".ev-panel[data-narrow] .ev-panel__title";
    expect(effective(ENTITY_VIEWS_CSS, title, "white-space")).toBe("nowrap");
    expect(effective(ENTITY_VIEWS_CSS, title, "overflow")).toBe("hidden");
    expect(effective(ENTITY_VIEWS_CSS, title, "text-overflow")).toBe("ellipsis");
    expect(effective(ENTITY_VIEWS_CSS, title, "min-width")).toBe("0");
    expect(effective(ENTITY_VIEWS_CSS, ".ev-panel[data-narrow] .ev-panel__head", "flex-wrap")).toBe("nowrap");
    // the marking select gives up width before the title does
    expect(effective(ENTITY_VIEWS_CSS, ".ev-panel[data-narrow] .ev-marking select", "max-width")).toBeDefined();
  });
});

describe("a view panel in any pane", () => {
  it("is at least as tall as its pane, so a chart in it can grow to the pane's height", () => {
    expect(effective(ENTITY_VIEWS_CSS, ".ev-panel", "min-height")).toBe("100%");
  });
});
