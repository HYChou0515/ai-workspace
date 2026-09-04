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


/** Opens the confirm from an Enter keypress, the way a form submit does. */
function EnterHarness({ onResult }: { onResult: (r: string | null) => void }) {
  const dialog = useDialog();
  return (
    <input
      aria-label="name"
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        void dialog
          .confirm({
            title: "Save changes?",
            actions: [
              { id: "save", label: "Save" },
              { id: "discard", label: "Don't Save", variant: "danger" },
            ],
          })
          .then(onResult);
      }}
    />
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

  it("takes Escape for itself, so the modal underneath does not also act on it", async () => {
    // #779: ModalShell and this both listen on document. With the confirm open
    // it is the top layer, so Escape belongs to it alone — otherwise the modal's
    // own handler runs too and (for a dirty modal) opens a SECOND confirm. That
    // it currently cancels out is an accident of listener order, not a design.
    const user = userEvent.setup();
    const shellSawEscape = vi.fn();
    render(
      <DialogProvider>
        <Harness onResult={() => {}} />
      </DialogProvider>,
    );
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") shellSawEscape();
    });

    await user.click(screen.getByRole("button", { name: "open" }));
    await screen.findByText("Save changes?");
    await act(async () => {
      await user.keyboard("{Escape}");
    });

    expect(screen.queryByText("Save changes?")).toBeNull();
    expect(shellSawEscape).not.toHaveBeenCalled();
  });

  it("does not answer itself with the keystroke that opened it", async () => {
    // #779: raising the prompt from an Enter (submitting a form / committing a
    // rename) used to focus an action button, and the rest of that same
    // keystroke then activated it — the dialog resolved before it was readable.
    // Focus belongs on the panel; the buttons are reached by Tab.
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <DialogProvider>
        <EnterHarness onResult={onResult} />
      </DialogProvider>,
    );

    await user.click(screen.getByRole("textbox"));
    await user.keyboard("name{Enter}");

    expect(await screen.findByText("Save changes?")).toBeInTheDocument();
    expect(onResult).not.toHaveBeenCalled();
  });
});
