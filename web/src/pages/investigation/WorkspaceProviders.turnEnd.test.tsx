// @vitest-environment happy-dom
/**
 * The file panes reload when a turn in ANY of the item's chats ends — not on
 * the default chat's `streaming` edge, which a turn in another chat or a
 * workflow never produced (each streams on its own key). Both pages an item
 * opens on (the workspace and the editor-area page) mount `WorkspaceProviders`,
 * so the reload lives there.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { qk } from "../../api/queryKeys";
import { publishTurnEnded } from "../../lib/turnEndedBus";
import { makeTestQueryClient, renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceProviders } from "./WorkspaceShell";

vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const EMPTY = { items: [], dirs: [], unwalked: [], truncated: false };

describe("WorkspaceProviders — reload on a turn's end", () => {
  it("re-reads this item's tree when one of its turns ends", async () => {
    const client = makeTestQueryClient();
    client.setQueryData(qk.files("PG-1"), EMPTY);
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1">
        {() => null}
      </WorkspaceProviders>,
      client,
    );

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(client.getQueryState(qk.files("PG-1"))?.isInvalidated).toBe(true));
  });

  // The turn's end already flushed on the server (an app chat), or the tree
  // reads the live sandbox (#345). A viewer's own flush is a whole-workspace
  // write-back with deletions, once per viewer per turn — mid-turn for a
  // sibling chat or lane. The refresh button still flushes; a turn's end does not.
  it("does not ask the server to flush", async () => {
    const flush = vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    const client = makeTestQueryClient();
    client.setQueryData(qk.files("PG-1"), EMPTY);
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1">
        {() => null}
      </WorkspaceProviders>,
      client,
    );

    act(() => publishTurnEnded("PG-1"));
    await waitFor(() => expect(client.getQueryState(qk.files("PG-1"))?.isInvalidated).toBe(true));

    expect(flush).not.toHaveBeenCalled();
  });

  // The page's own follow-ups (the usage bar) run after the reload.
  it("then tells the page", async () => {
    const onTurnEnd = vi.fn();
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(onTurnEnd).toHaveBeenCalledTimes(1));
  });

  // A workflow map ends one turn per element. A burst is one reload in flight
  // and one after it — never one per turn.
  it("folds a burst of turn ends into at most two reloads", async () => {
    const onTurnEnd = vi.fn();
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => {
      for (let i = 0; i < 10; i++) publishTurnEnded("PG-1");
    });
    await waitFor(() => expect(onTurnEnd).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 100));

    expect(onTurnEnd.mock.calls.length).toBeLessThanOrEqual(2);
    expect(onTurnEnd.mock.calls.length).toBeGreaterThanOrEqual(1);
  });

  // …and a turn that ends WHILE a reload is running still gets one after it,
  // or what it wrote stays unseen.
  it("reloads again for a turn that ended during a reload", async () => {
    const onTurnEnd = vi.fn();
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => {
      publishTurnEnded("PG-1");
      publishTurnEnded("PG-1");
    });

    await waitFor(() => expect(onTurnEnd).toHaveBeenCalledTimes(2));
  });

  it("does nothing for another item's turn", async () => {
    const onTurnEnd = vi.fn();
    renderWithQuery(
      <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
        {() => null}
      </WorkspaceProviders>,
    );

    act(() => publishTurnEnded("PG-2"));
    await new Promise((r) => setTimeout(r, 50));

    expect(onTurnEnd).not.toHaveBeenCalled();
  });
});
