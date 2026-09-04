// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Switch } from "./Switch";

afterEach(cleanup);

describe("Switch", () => {
  it("announces itself as a switch, not a checkbox", () => {
    // The difference is not decoration: a checkbox reads as a choice that has
    // not happened yet, and this one takes effect the moment it is flipped.
    render(<Switch checked onChange={() => {}} title="Rebuild this page whenever you open it" />);

    const it_ = screen.getByRole("switch");
    expect(it_).toBeChecked();
    expect(it_).toHaveAccessibleName("Rebuild this page whenever you open it");
  });

  it("says the same thing on hover and to a screen reader", () => {
    // A short word on screen explains nothing on its own, so the sentence has
    // to reach BOTH — and be the same sentence, or the two describe different
    // controls.
    render(
      <Switch checked={false} onChange={() => {}} title="Rebuild this page whenever you open it">
        Auto-rebuild
      </Switch>,
    );

    const label = screen.getByText("Auto-rebuild").closest("label");
    expect(label).toHaveAttribute("title", "Rebuild this page whenever you open it");
    expect(screen.getByRole("switch")).toHaveAccessibleName(
      "Rebuild this page whenever you open it",
    );
  });

  it("flips from the label, not only the control", () => {
    // The visible word is the target most people aim at; a switch you can only
    // hit on a 26px track is a switch people miss.
    const onChange = vi.fn();
    render(
      <Switch checked={false} onChange={onChange} title="On">
        Auto-rebuild
      </Switch>,
    );

    fireEvent.click(screen.getByText("Auto-rebuild"));

    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("refuses to flip when it is disabled", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} title="On" disabled />);

    fireEvent.click(screen.getByRole("switch"));

    expect(onChange).not.toHaveBeenCalled();
  });
});
