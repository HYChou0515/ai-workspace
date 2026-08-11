// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportModeDialog } from "./ImportModeDialog";

afterEach(cleanup);

const noop = () => {};

describe("ImportModeDialog", () => {
  it("says the choice covers cards, not documents alone", () => {
    // #701 made `mode` govern context cards too. The backend rule shipped first and
    // this sentence was the only place a person is asked — consenting to one thing
    // and getting another is the defect, so the copy is the guard.
    render(
      <ImportModeDialog
        fileName="Specs.zip"
        busy={false}
        onOverwrite={noop}
        onSkip={noop}
        onCancel={noop}
      />,
    );

    const prompt = screen.getByRole("dialog").textContent ?? "";
    expect(prompt).toMatch(/documents and cards/i);
    expect(prompt).toContain("Specs.zip");
  });

  it("routes each button to its own choice", () => {
    const overwrite = vi.fn();
    const skip = vi.fn();
    const cancel = vi.fn();
    render(
      <ImportModeDialog
        fileName="Specs.zip"
        busy={false}
        onOverwrite={overwrite}
        onSkip={skip}
        onCancel={cancel}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip existing" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(overwrite).toHaveBeenCalledOnce();
    expect(skip).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("disables the committing choices while an import is in flight, but not Cancel", () => {
    // A second Overwrite mid-import would replay a destructive merge; backing out
    // of a dialog never needs to wait for one.
    render(
      <ImportModeDialog
        fileName="Specs.zip"
        busy
        onOverwrite={noop}
        onSkip={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByRole("button", { name: "Overwrite" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Skip existing" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });
});
