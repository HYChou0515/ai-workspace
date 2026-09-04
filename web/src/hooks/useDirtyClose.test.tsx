// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "../components/Dialog";
import { ModalShell } from "../components/ModalShell";
import { useDirtyClose } from "./useDirtyClose";

afterEach(cleanup);

function Harness({ dirty, onClose }: { dirty: boolean; onClose: () => void }) {
  const attemptClose = useDirtyClose(dirty, onClose);
  return (
    <button type="button" onClick={attemptClose}>
      close
    </button>
  );
}

function open(dirty: boolean, onClose: () => void) {
  render(
    <DialogProvider>
      <Harness dirty={dirty} onClose={onClose} />
    </DialogProvider>,
  );
  return screen.getByRole("button", { name: "close" });
}

describe("useDirtyClose", () => {
  it("closes straight away when there is nothing unsaved", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    await user.click(open(false, onClose));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("asks first, and stays open, when there is something unsaved", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    await user.click(open(true, onClose));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes once the user confirms they are discarding", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    await user.click(open(true, onClose));
    await screen.findByRole("dialog");
    await user.click(screen.getByTestId("dialog-action-discard"));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the modal open when the user chooses to keep editing", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    await user.click(open(true, onClose));
    await screen.findByRole("dialog");
    await user.click(screen.getByTestId("dialog-action-keep"));

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // The whole ARIA argument for keeping Escape is that a modal must stay
  // dismissable. That fails if the prompt it raises can only be answered with a
  // mouse: ModalShell traps Tab inside its own panel, and the confirm's buttons
  // are outside it, so a keyboard user could reach "keep editing" (Escape) and
  // nothing else — an undismissable modal by a longer route.
  it("puts keyboard focus inside the prompt, so it can be answered without a mouse", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    await user.click(open(true, onClose));
    const prompt = await screen.findByRole("dialog");

    expect(prompt.contains(document.activeElement)).toBe(true);
  });

  // Through a REAL ModalShell, because the trap is the half that bites: the
  // shell pulls Tab back into its own panel whenever focus leaves it, and the
  // prompt's buttons are outside that panel.
  it("reaches the discard action by keyboard alone, through a real ModalShell", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    function Shelled() {
      const attemptClose = useDirtyClose(true, onClose);
      return (
        <ModalShell onClose={attemptClose} ariaLabel="m" data-testid="shelled">
          <button type="button">inside one</button>
          <button type="button">inside two</button>
        </ModalShell>
      );
    }
    render(
      <DialogProvider>
        <Shelled />
      </DialogProvider>,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    const discard = await screen.findByTestId("dialog-action-discard");

    for (let i = 0; i < 8 && document.activeElement !== discard; i++) await user.tab();
    expect(document.activeElement).toBe(discard);

    await user.click(discard);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
