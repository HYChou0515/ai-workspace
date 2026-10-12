// @vitest-environment happy-dom
/**
 * The file panes refresh when a turn in ANY of the item's chats ends — not on
 * the default chat's `streaming` edge, which a second chat, a workflow, a queued
 * follow-up or a tab opened mid-turn never produced. Both pages an item opens
 * on (the workspace and the editor-area page) mount `WorkspaceProviders`, so the
 * refresh lives there.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { publishTurnEnded } from "../../lib/turnEndedBus";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceProviders } from "./WorkspaceShell";

vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WorkspaceProviders — refresh on a turn's end", () => {
  it("refreshes this item's files when one of its turns ends", async () => {
    const refresh = vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1">
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(refresh).toHaveBeenCalledWith("pm", "PG-1"));
  });

  // The page's own follow-ups (the usage bar) run after the refresh.
  it("then tells the page", async () => {
    vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    const onTurnEnd = vi.fn();
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(onTurnEnd).toHaveBeenCalledTimes(1));
  });

  it("does nothing for another item's turn", async () => {
    const refresh = vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1">
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => publishTurnEnded("PG-2"));
    await new Promise((r) => setTimeout(r, 50));

    expect(refresh).not.toHaveBeenCalled();
  });
});
