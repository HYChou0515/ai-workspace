// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbCollection, WikiStatus } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { CodeSyncStatus } from "./CodeSyncStatus";

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
    git_last_sha: null,
    git_last_pulled_at: null,
    wiki_maintainer_guidance: "",
    wiki_reader_guidance: "",
    ...over,
  };
}

function mkClient(status: Partial<WikiStatus>, sync = vi.fn().mockResolvedValue({})): KbApi {
  return {
    getWikiStatus: vi.fn().mockResolvedValue({
      building: false,
      total: 0,
      done: 0,
      current: null,
      phase: null,
      errors: 0,
      last_error: null,
      ...status,
    }),
    syncCollection: sync,
  } as unknown as KbApi;
}

describe("CodeSyncStatus (#355)", () => {
  afterEach(cleanup);

  it("renders nothing for a non-code collection", () => {
    const { container } = renderWithQuery(
      <CodeSyncStatus collection={mkColl({ git_url: null })} client={mkClient({})} />,
    );
    expect(container.querySelector('[data-testid="kb-sync-status"]')).toBeNull();
  });

  it("shows the cloning phase while building", async () => {
    renderWithQuery(
      <CodeSyncStatus
        collection={mkColl()}
        client={mkClient({ building: true, phase: "cloning" })}
      />,
    );
    expect(await screen.findByText("Cloning repository…")).toBeInTheDocument();
    // No Sync action while a build is in flight.
    expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
  });

  it("shows the synced commit + Sync now, and re-syncs on click", async () => {
    const sync = vi.fn().mockResolvedValue({ status: "queued", git_last_sha: "abc" });
    renderWithQuery(
      <CodeSyncStatus
        collection={mkColl({ git_last_sha: "abcdef1234567890", git_last_pulled_at: 1 })}
        client={mkClient({}, sync)}
      />,
    );
    expect(await screen.findByText(/Synced to abcdef1/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(sync).toHaveBeenCalledTimes(1);
  });

  it("shows the failure + Retry, and re-syncs on click", async () => {
    const sync = vi.fn().mockResolvedValue({});
    renderWithQuery(
      <CodeSyncStatus
        collection={mkColl()}
        client={mkClient({ last_error: "git failed: repository not found" }, sync)}
      />,
    );
    expect(await screen.findByText(/Sync failed: git failed: repository not found/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(sync).toHaveBeenCalledTimes(1);
  });

  it("shows 'Not synced yet' when there is no commit and no error", async () => {
    renderWithQuery(<CodeSyncStatus collection={mkColl()} client={mkClient({})} />);
    expect(await screen.findByText("Not synced yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync now" })).toBeInTheDocument();
  });
});
