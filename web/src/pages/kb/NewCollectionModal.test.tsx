// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "../../components/Dialog";
import { NewCollectionModal, repoNameFromUrl } from "./NewCollectionModal";

// The confirm dialog is at the app root (#779) and this modal asks through it
// before dropping a typed name, so every render needs it — as every real one has.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: DialogProvider });

describe("NewCollectionModal — Documents mode (#50)", () => {
  afterEach(cleanup);

  it("submits the retrieval modes alongside the name", async () => {
    const onCreate = vi.fn();
    render(<NewCollectionModal open onClose={() => {}} onCreate={onCreate} />);

    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    // Opt into the wiki (document search stays on by default). #171: zh-TW labels.
    await userEvent.click(screen.getByRole("switch", { name: "知識百科" }));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(onCreate).toHaveBeenCalledWith("Process SOPs", "", { useRag: true, useWiki: true });
  });

  it("disables Create when no retrieval mode is selected", async () => {
    render(<NewCollectionModal open onClose={() => {}} onCreate={() => {}} />);
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "X");
    // Turn the default (document search) off; wiki is off too → nothing left.
    await userEvent.click(screen.getByRole("switch", { name: "文件搜尋" }));
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });
});

describe("NewCollectionModal — Code repository mode (#355)", () => {
  afterEach(cleanup);

  const toCodeMode = () => userEvent.click(screen.getByRole("button", { name: "Code repository" }));

  it("repoNameFromUrl extracts the repo name", () => {
    expect(repoNameFromUrl("https://github.com/o/ai-workspace.git")).toBe("ai-workspace");
    expect(repoNameFromUrl("https://gitlab.example/g/r/")).toBe("r");
    expect(repoNameFromUrl("")).toBe("");
  });

  it("suggests a name from the Git URL and submits code opts (rag+wiki forced)", async () => {
    const onCreate = vi.fn();
    render(<NewCollectionModal open onClose={() => {}} onCreate={onCreate} />);
    await toCodeMode();
    // The retrieval toggles are hidden in code mode.
    expect(screen.queryByRole("switch", { name: "文件搜尋" })).not.toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText("https://github.com/owner/repo.git"),
      "https://github.com/o/ai-workspace.git",
    );
    // Name auto-filled from the URL.
    expect(screen.getByPlaceholderText("New collection name…")).toHaveValue("ai-workspace");

    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onCreate).toHaveBeenCalledWith("ai-workspace", "", {
      useRag: true,
      useWiki: true,
      gitUrl: "https://github.com/o/ai-workspace.git",
      gitBranch: undefined,
      gitToken: undefined,
    });
  });

  it("rejects a non-http(s) URL (file:// blocked) and disables Create", async () => {
    render(<NewCollectionModal open onClose={() => {}} onCreate={() => {}} />);
    await toCodeMode();
    await userEvent.type(
      screen.getByPlaceholderText("https://github.com/owner/repo.git"),
      "file:///home/me/repo",
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("passes branch + token from the Advanced section", async () => {
    const onCreate = vi.fn();
    render(<NewCollectionModal open onClose={() => {}} onCreate={onCreate} />);
    await toCodeMode();
    await userEvent.type(
      screen.getByPlaceholderText("https://github.com/owner/repo.git"),
      "https://github.com/o/r.git",
    );
    await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
    await userEvent.type(screen.getByPlaceholderText("(default branch)"), "develop");
    await userEvent.type(screen.getByPlaceholderText("for a private repo"), "ghp_secret");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(onCreate).toHaveBeenCalledWith("r", "", {
      useRag: true,
      useWiki: true,
      gitUrl: "https://github.com/o/r.git",
      gitBranch: "develop",
      gitToken: "ghp_secret",
    });
  });

  it("keeps a user-typed name instead of the URL suggestion", async () => {
    const onCreate = vi.fn();
    render(<NewCollectionModal open onClose={() => {}} onCreate={onCreate} />);
    await toCodeMode();
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "My Wiki");
    await userEvent.type(
      screen.getByPlaceholderText("https://github.com/owner/repo.git"),
      "https://github.com/o/r.git",
    );
    // The typed name is preserved (not overwritten by the URL suggestion).
    expect(screen.getByPlaceholderText("New collection name…")).toHaveValue("My Wiki");
  });
});

describe("NewCollectionModal — leaving with something typed (#779)", () => {
  afterEach(cleanup);

  const renderIt = () => {
    const onClose = vi.fn();
    render(<NewCollectionModal open onClose={onClose} onCreate={() => {}} />);
    return onClose;
  };

  it("ignores a click on the backdrop once something has been typed", async () => {
    const onClose = renderIt();
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");

    await userEvent.click(document.querySelector(".kb-modal") as HTMLElement);

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText("New collection name…")).toHaveValue("Process SOPs");
  });

  it("asks before Escape drops a typed name, and keeps it when told to", async () => {
    const onClose = renderIt();
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");

    await userEvent.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByPlaceholderText("New collection name…")).toHaveValue("Process SOPs");
  });

  it("still closes on a backdrop click while the form is untouched", async () => {
    const onClose = renderIt();
    await userEvent.click(document.querySelector(".kb-modal") as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });
});
