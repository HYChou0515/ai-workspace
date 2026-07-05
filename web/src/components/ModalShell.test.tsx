// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModalShell } from "./ModalShell";

afterEach(cleanup);

describe("ModalShell", () => {
  it("renders children inside a labelled modal dialog", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="My modal">
        <p>body</p>
      </ModalShell>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("My modal");
    expect(screen.getByText("body")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m">
        <p>x</p>
      </ModalShell>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on backdrop click but not on panel click", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m" data-testid="shell">
        <button type="button">inside</button>
      </ModalShell>,
    );
    // clicking the content (inside the dialog) must NOT close
    fireEvent.click(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
    // clicking the backdrop (the presentation wrapper) closes
    fireEvent.click(screen.getByTestId("shell-backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not close on backdrop when closeOnBackdrop is false", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m" data-testid="shell" closeOnBackdrop={false}>
        <p>x</p>
      </ModalShell>,
    );
    fireEvent.click(screen.getByTestId("shell-backdrop"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not close on Escape when closeOnEscape is false", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m" closeOnEscape={false}>
        <p>x</p>
      </ModalShell>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
