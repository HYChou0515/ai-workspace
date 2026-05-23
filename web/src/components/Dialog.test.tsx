// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DialogProvider, useDialog } from "./Dialog";

afterEach(cleanup);

function Harness({ onResult }: { onResult: (r: string | null) => void }) {
  const dialog = useDialog();
  return (
    <button
      type="button"
      onClick={async () => {
        const r = await dialog.confirm({
          title: "Save changes?",
          body: "brief.md has unsaved changes.",
          actions: [
            { id: "save", label: "Save", variant: "primary" },
            { id: "discard", label: "Don't Save", variant: "danger" },
          ],
        });
        onResult(r);
      }}
    >
      open
    </button>
  );
}

describe("<DialogProvider /> / useDialog", () => {
  it("shows the dialog and resolves with the chosen action id", async () => {
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <DialogProvider>
        <Harness onResult={onResult} />
      </DialogProvider>,
    );

    await user.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByText("Save changes?")).toBeInTheDocument();
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onResult).toHaveBeenCalledWith("save");
    // dialog dismissed
    expect(screen.queryByText("Save changes?")).toBeNull();
  });

  it("resolves null when dismissed with Escape", async () => {
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <DialogProvider>
        <Harness onResult={onResult} />
      </DialogProvider>,
    );
    await user.click(screen.getByRole("button", { name: "open" }));
    await screen.findByText("Save changes?");
    await act(async () => {
      await user.keyboard("{Escape}");
    });
    expect(onResult).toHaveBeenCalledWith(null);
  });
});
