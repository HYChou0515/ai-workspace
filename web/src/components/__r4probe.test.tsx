// @vitest-environment happy-dom
// TEMPORARY round-4 review probe. Delete after running.
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DialogProvider, useDialog } from "./Dialog";
import { LAYER_ATTR, ModalShell } from "./ModalShell";

// inlined copy of the scanner under review (importing the test module runs its
// top-level tree walk with a bad SRC)
function onClickBodies(text: string): { body: string; line: number }[] {
  const out: { body: string; line: number }[] = [];
  const re = /onClick=\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    let depth = 1;
    let quote: string | null = null;
    let i = m.index + m[0].length;
    for (; i < text.length && depth > 0; i++) {
      const c = text[i];
      if (quote) {
        if (c === "\\") i++;
        else if (c === quote) quote = null;
        continue;
      }
      if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth--;
    }
    if (depth !== 0) throw new Error(`onClickBodies: unbalanced braces from offset ${m.index}.`);
    out.push({ body: text.slice(m.index, i), line: text.slice(0, m.index).split("\n").length });
  }
  return out;
}

describe("R4 probe", () => {
  it("Q2a: ModalShell panel actually carries the attribute in the DOM", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="m" data-testid="m">
        <p>x</p>
      </ModalShell>,
    );
    const panel = screen.getByTestId("m");
    expect(panel.hasAttribute(LAYER_ATTR)).toBe(true);
    expect(panel.getAttribute(LAYER_ATTR)).toBe("");
    // presence selector with an empty value
    expect(document.querySelectorAll(`[${LAYER_ATTR}]`).length).toBe(1);
  });

  it("Q2b: Dialog panel carries it too, and lands LAST in the stack", async () => {
    function Harness() {
      const dialog = useDialog();
      return (
        <ModalShell onClose={() => {}} ariaLabel="m" data-testid="m">
          <button type="button" onClick={() => void dialog.confirm({ title: "T", actions: [{ id: "a", label: "A" }] })}>
            open
          </button>
        </ModalShell>
      );
    }
    render(
      <DialogProvider>
        <Harness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "open" }));
    await screen.findByText("T");
    const layers = document.querySelectorAll(`[${LAYER_ATTR}]`);
    expect(layers.length).toBe(2);
    // the confirm must be last, or the modal underneath still thinks it is topmost
    expect(layers[layers.length - 1]).toBe(screen.getByRole("dialog", { name: "T" }));
    expect(layers[0]).toBe(screen.getByTestId("m"));
  });

  it("Q2c: the modal underneath stops answering Escape while the confirm is up", async () => {
    const onClose = vi.fn();
    function Harness() {
      const dialog = useDialog();
      return (
        <ModalShell onClose={onClose} ariaLabel="m" data-testid="m">
          <button type="button" onClick={() => void dialog.confirm({ title: "T", actions: [{ id: "a", label: "A" }] })}>
            open
          </button>
        </ModalShell>
      );
    }
    render(
      <DialogProvider>
        <Harness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "open" }));
    await screen.findByText("T");
    onClose.mockClear();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Q2d: ReviewDrawer-shaped element no longer joins the stack", () => {
    render(
      <>
        <ModalShell onClose={() => {}} ariaLabel="m" data-testid="m">
          <p>x</p>
        </ModalShell>
        <aside role="dialog" aria-modal="true" aria-label="drawer" data-testid="drawer">
          d
        </aside>
      </>,
    );
    const layers = document.querySelectorAll(`[${LAYER_ATTR}]`);
    expect(layers.length).toBe(1);
    expect(layers[0]).toBe(screen.getByTestId("m"));
  });

  it("Q4a: an apostrophe in a comment inside an onClick body trips the scanner", () => {
    const src = [
      "<button",
      "  onClick={() => {",
      "    // don't close while busy",
      "    onClose();",
      "  }}",
      ">x</button>",
    ].join("\n");
    let threw: unknown = null;
    let bodies: ReturnType<typeof onClickBodies> | null = null;
    try {
      bodies = onClickBodies(src);
    } catch (e) {
      threw = e;
    }
    console.log("Q4a threw:", threw ? String(threw).slice(0, 120) : "no");
    console.log("Q4a body:", bodies ? JSON.stringify(bodies[0]?.body) : "n/a");
    expect(true).toBe(true);
  });

  it("Q4b: a regex literal with a brace inside an onClick body", () => {
    const src = '<button onClick={() => setX(s.replace(/\\}/g, ""))}>x</button>';
    let threw: unknown = null;
    try {
      onClickBodies(src);
    } catch (e) {
      threw = e;
    }
    console.log("Q4b threw:", threw ? String(threw).slice(0, 140) : "no");
    expect(true).toBe(true);
  });
});
