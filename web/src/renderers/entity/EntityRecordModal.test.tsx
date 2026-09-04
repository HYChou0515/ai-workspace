// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../components/MonacoEditor", () => ({
  MonacoEditor: ({
    value,
    onChange,
    ariaLabel,
  }: {
    value: string;
    onChange?: (next: string) => void;
    ariaLabel?: string;
  }) => <textarea aria-label={ariaLabel} value={value} onChange={(e) => onChange?.(e.target.value)} />,
}));

import type { EntityInstance, EntityType } from "../../api/entities";
import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { OpenFileProvider } from "../../hooks/openFile";
import { DialogProvider } from "../../components/Dialog";
import { EntityRecordModal } from "./EntityRecordModal";

// The confirm dialog is at the app root (#779); the modal asks through it before
// a deliberate exit drops an in-progress edit.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: DialogProvider });

afterEach(cleanup);

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
  ],
  form: [],
};

const record: EntityInstance = {
  number: 7,
  type_name: "issue",
  fields: { title: "The bar stops a day short", status: "open" },
  body: "## Repro\n",
  diagnostics: [],
};

function modal(
  overrides: Partial<React.ComponentProps<typeof EntityRecordModal>> = {},
  openFile?: (path: string) => void,
) {
  const onClose = vi.fn();
  const onSave = vi.fn();
  const inner = (
    <FileServiceProvider value={investigationFileService("rca", "item-1")}>
      <EntityRecordModal type={type} record={record} canWrite onSave={onSave} onClose={onClose} {...overrides} />
    </FileServiceProvider>
  );
  const utils = render(openFile ? <OpenFileProvider value={openFile}>{inner}</OpenFileProvider> : inner);
  return { ...utils, onClose, onSave };
}

describe("EntityRecordModal", () => {
  it("opens the record in a labelled dialog, in the reading state", () => {
    modal();
    const dialog = screen.getByRole("dialog");
    // The accessible name has to say WHICH record — several can be opened in a
    // session and "Record" alone tells a screen-reader user nothing.
    expect(dialog).toHaveAccessibleName(expect.stringContaining("#7"));
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("hands the record's own path to the workspace opener, then gets out of the way", () => {
    const openFile = vi.fn();
    const { onClose } = modal({}, openFile);

    fireEvent.click(screen.getByRole("button", { name: /open file/i }));

    // `{records_path}/{number}.md` — the same path the file tab renders, so the
    // raw whole-file escape hatch is one click away from the modal.
    expect(openFile).toHaveBeenCalledWith("issues/7.md");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not offer the file route when there is no workspace to open into", () => {
    modal(); // no OpenFileProvider → useOpenFile() is null
    expect(screen.queryByRole("button", { name: /open file/i })).not.toBeInTheDocument();
  });

  it("shows a 409 as a non-blocking banner instead of silently dropping the edit", () => {
    const onDismissConflict = vi.fn();
    modal({ conflicts: [7], onDismissConflict });

    expect(screen.getByRole("alert")).toHaveTextContent(/someone else changed/i);
    // Reading the record is still possible — the banner is not a blocker.
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "dismiss conflict 7" }));
    expect(onDismissConflict).toHaveBeenCalledWith(7);
  });

  // An in-progress edit lives in the pane's own state, so anything that unmounts
  // the modal drops it. Both exits below did exactly that, silently.
  it("withdraws the file route while an edit is in progress", () => {
    const openFile = vi.fn();
    modal({}, openFile);
    expect(screen.getByRole("button", { name: /open file/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    // Leaving for the file tab mid-edit would have thrown the typing away.
    expect(screen.queryByRole("button", { name: /open file/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /open file/i })).toBeInTheDocument();
  });

  it("stops a stray backdrop click from discarding an in-progress edit", () => {
    const { onClose } = modal();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    fireEvent.click(screen.getByTestId("entity-record-modal-backdrop"));

    expect(onClose).not.toHaveBeenCalled();
  });

  // #779: Escape stays a working exit — a modal you cannot dismiss is worse than
  // a lost draft — but while the form is open it asks once rather than dropping
  // the edit silently. In here that matters more than most: the pane is a text
  // editor, and Escape is what you press to dismiss its own popups.
  it("asks before Escape drops an in-progress edit, and stays open when told to", async () => {
    const { onClose } = modal();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("closes on Escape once the edit is confirmed discarded", async () => {
    const { onClose } = modal();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(await screen.findByTestId("dialog-action-discard"));

    // The confirm resolves a promise, so the close lands a tick later.
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("gives a read-only member no way in", () => {
    modal({ canWrite: false });
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const { onClose } = modal();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("saves through the caller's write path and lands back on the reading view", () => {
    const { onSave } = modal();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({ status: "done" });
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();
  });
});
