// @vitest-environment happy-dom
/**
 * The reload loop's own state, with a reload whose end the test decides: a
 * real reload is a few cache invalidations and finishes before a test can act
 * inside it (#910 review round 2 — none of these lines was pinned).
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { publishTurnEnded } from "../../lib/turnEndedBus";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceProviders } from "./WorkspaceShell";

const pending: Array<() => void> = [];
const reload = vi.fn(() => new Promise<void>((r) => pending.push(r)));

vi.mock("../../hooks/useRefreshFiles", () => ({
  useRefreshFiles: () => async () => {},
  useReloadFileViews: () => reload,
}));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

beforeEach(() => {
  pending.length = 0;
  reload.mockClear();
});
afterEach(cleanup);

const finishReload = async () => {
  await act(async () => {
    pending.shift()?.();
    await Promise.resolve();
  });
};

function mount(onTurnEnd = vi.fn()) {
  const r = renderWithQuery(
    <WorkspaceProviders slug="pm" itemId="PG-1" onTurnEnd={onTurnEnd}>
      {() => null}
    </WorkspaceProviders>,
  );
  return { ...r, onTurnEnd };
}

describe("RefreshOnTurnEnd — the loop's state", () => {
  // After one reload finishes, the NEXT turn's end must reload again — a
  // loop left "running" would only ever queue, and the tree would stop
  // following turns after the first.
  it("reloads for a turn that ends after the last reload finished", async () => {
    const { onTurnEnd } = mount();
    act(() => publishTurnEnded("PG-1"));
    await finishReload();
    await waitFor(() => expect(onTurnEnd).toHaveBeenCalledTimes(1));

    act(() => publishTurnEnded("PG-1"));

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(2));
  });

  // A page that went away is not told, and its queued reload does not run.
  it("stops when the page goes away mid-reload", async () => {
    const { onTurnEnd, unmount } = mount();
    act(() => {
      publishTurnEnded("PG-1");
      publishTurnEnded("PG-1"); // queued behind the first
    });
    expect(reload).toHaveBeenCalledTimes(1);

    unmount();
    await finishReload();
    await new Promise((r) => setTimeout(r, 20));

    expect(onTurnEnd).not.toHaveBeenCalled();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
