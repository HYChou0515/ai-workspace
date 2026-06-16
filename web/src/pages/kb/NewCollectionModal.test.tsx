// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewCollectionModal } from "./NewCollectionModal";

describe("NewCollectionModal — retrieval toggles (#50)", () => {
  afterEach(cleanup);

  it("submits the retrieval modes alongside the name", async () => {
    const onCreate = vi.fn();
    render(<NewCollectionModal open onClose={() => {}} onCreate={onCreate} />);

    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    // Opt into the wiki (document search stays on by default).
    await userEvent.click(screen.getByRole("switch", { name: "Knowledge wiki" }));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(onCreate).toHaveBeenCalledWith("Process SOPs", "", { useRag: true, useWiki: true });
  });

  it("disables Create when no retrieval mode is selected", async () => {
    render(<NewCollectionModal open onClose={() => {}} onCreate={() => {}} />);
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "X");
    // Turn the default (document search) off; wiki is off too → nothing left.
    await userEvent.click(screen.getByRole("switch", { name: "Document search" }));
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });
});
