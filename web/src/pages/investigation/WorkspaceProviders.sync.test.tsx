// @vitest-environment happy-dom
/**
 * #847 review round 1: the chat tab and the editor-area page (a new tab) are
 * two `WorkspaceProviders` for one item. A marking made on the page must show
 * up where the composer is, or chat mode can never send it.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMarking, useMarkingNames } from "../../hooks/useMarking";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceProviders } from "./WorkspaceShell";

vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

afterEach(cleanup);

let write: ReturnType<typeof useMarking>[1] | null = null;
function Page() {
  write = useMarking("fail")[1];
  return null;
}
function Chat({ id }: { id: string }) {
  return <span data-testid={id}>{useMarkingNames().join(",")}</span>;
}

describe("WorkspaceProviders — one item's markings across tabs", () => {
  it("a marking made on the page reaches the chat tab of the same item, not another item", async () => {
    renderWithQuery(
      <>
        <WorkspaceProviders slug="pm" itemId="PG-1">
          {() => <Page />}
        </WorkspaceProviders>
        <WorkspaceProviders slug="pm" itemId="PG-1">
          {() => <Chat id="chat" />}
        </WorkspaceProviders>
        <WorkspaceProviders slug="pm" itemId="PG-2">
          {() => <Chat id="other-item" />}
        </WorkspaceProviders>
      </>,
    );
    act(() => write!({ lot: new Set(["L1"]) }, "/v/grid.ai.yaml"));
    await waitFor(() => expect(screen.getByTestId("chat")).toHaveTextContent("fail"));
    expect(screen.getByTestId("other-item")).toHaveTextContent("");
  });
});
