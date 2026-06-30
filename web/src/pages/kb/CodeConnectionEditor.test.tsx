// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbCollection } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { CodeConnectionEditor } from "./CodeConnectionEditor";

function mkColl(over: Partial<KbCollection> = {}): KbCollection {
  return {
    resource_id: "c1",
    name: "repo",
    description: "",
    icon: "layers",
    cited: 0,
    doc_count: 0,
    size: 0,
    tokens: 0,
    updated_at: 0,
    owner: "me",
    use_rag: true,
    use_wiki: true,
    git_url: "https://github.com/o/r.git",
    git_branch: "main",
    wiki_maintainer_guidance: "",
    wiki_reader_guidance: "",
    ...over,
  };
}

function mkClient(update = vi.fn().mockResolvedValue(undefined)): KbApi {
  return { updateCollection: update } as unknown as KbApi;
}

describe("CodeConnectionEditor (#355)", () => {
  afterEach(cleanup);

  it("prefills the branch but never the token", () => {
    renderWithQuery(
      <CodeConnectionEditor collection={mkColl()} client={mkClient()} onClose={() => {}} />,
    );
    expect(screen.getByPlaceholderText("(default branch)")).toHaveValue("main");
    expect(screen.getByPlaceholderText("leave blank to keep the current token")).toHaveValue("");
  });

  it("saves the branch only when the token is left blank (no rotation)", async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    renderWithQuery(
      <CodeConnectionEditor collection={mkColl()} client={mkClient(update)} onClose={onClose} />,
    );
    const branch = screen.getByPlaceholderText("(default branch)");
    await userEvent.clear(branch);
    await userEvent.type(branch, "develop");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(update).toHaveBeenCalledWith("c1", { git_branch: "develop" });
    expect(onClose).toHaveBeenCalled();
  });

  it("rotates the token when one is typed", async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    renderWithQuery(
      <CodeConnectionEditor collection={mkColl()} client={mkClient(update)} onClose={() => {}} />,
    );
    await userEvent.type(
      screen.getByPlaceholderText("leave blank to keep the current token"),
      "ghp_new",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(update).toHaveBeenCalledWith("c1", { git_branch: "main", git_token: "ghp_new" });
  });

  it("clears the branch to the remote default (null) when emptied", async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    renderWithQuery(
      <CodeConnectionEditor collection={mkColl()} client={mkClient(update)} onClose={() => {}} />,
    );
    await userEvent.clear(screen.getByPlaceholderText("(default branch)"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(update).toHaveBeenCalledWith("c1", { git_branch: null });
  });
});
