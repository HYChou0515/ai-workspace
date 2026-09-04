// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "../components/Dialog";
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
});
