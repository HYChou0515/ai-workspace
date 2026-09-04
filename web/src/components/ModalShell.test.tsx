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

  // #779: a stray click beside the panel is the one exit the user never meant
  // to take, so the default withdraws it. A modal that wants it says so.
  it("ignores a backdrop click by default", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m" data-testid="shell">
        <button type="button">inside</button>
      </ModalShell>,
    );
    fireEvent.click(screen.getByTestId("shell-backdrop"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on backdrop click when closeOnBackdrop is asked for", () => {
    const onClose = vi.fn();
    render(
      <ModalShell onClose={onClose} ariaLabel="m" data-testid="shell" closeOnBackdrop>
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

  // #779 P5: a modal that says where focus belongs must win over "first
  // focusable". Found by opening the moved NewCollectionModal in a browser —
  // its Name field lost the caret to the segmented control above it, and no
  // unit test looks at focus placement.
  it("honours an autofocus target over the first focusable", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="m">
        <button type="button">first</button>
        <input aria-label="name" autoFocus />
      </ModalShell>,
    );
    expect(document.activeElement).toBe(screen.getByLabelText("name"));
  });

  it("moves focus into the modal on open (#467)", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="m">
        <button type="button">first</button>
        <button type="button">second</button>
      </ModalShell>,
    );
    expect(screen.getByText("first")).toHaveFocus();
  });

  it("restores focus to the trigger when it closes (#467)", () => {
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    expect(trigger).toHaveFocus();

    const { unmount } = render(
      <ModalShell onClose={() => {}} ariaLabel="m">
        <button type="button">inside</button>
      </ModalShell>,
    );
    // focus was pulled into the modal, off the trigger
    expect(trigger).not.toHaveFocus();

    unmount();
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it("traps Tab at the last focusable, wrapping to the first (#467)", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="m">
        <button type="button">first</button>
        <button type="button">last</button>
      </ModalShell>,
    );
    const first = screen.getByText("first");
    const last = screen.getByText("last");
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
  });

  it("traps Shift+Tab at the first focusable, wrapping to the last (#467)", () => {
    render(
      <ModalShell onClose={() => {}} ariaLabel="m">
        <button type="button">first</button>
        <button type="button">last</button>
      </ModalShell>,
    );
    const first = screen.getByText("first");
    const last = screen.getByText("last");
    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  // #779: two modals can be open at once (the workspace renders EditItemModal
  // and the ⌘P palette, and ⌘P is a global keydown that fires while a modal is
  // up). Escape aimed at the top one must not also reach the one underneath —
  // for a modal holding unsaved work that turns a keystroke meant for the
  // palette into a "discard your edits?" prompt, which a reflex answers
  // destructively.
  it("ignores Escape when it is not the topmost modal", () => {
    const underneath = vi.fn();
    const topmost = vi.fn();
    render(
      <>
        <ModalShell onClose={underneath} ariaLabel="under">
          <p>under</p>
        </ModalShell>
        <ModalShell onClose={topmost} ariaLabel="over">
          <p>over</p>
        </ModalShell>
      </>,
    );

    fireEvent.keyDown(document, { key: "Escape" });

    expect(topmost).toHaveBeenCalledOnce();
    expect(underneath).not.toHaveBeenCalled();
  });
});
