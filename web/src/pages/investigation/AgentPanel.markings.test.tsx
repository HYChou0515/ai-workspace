// @vitest-environment happy-dom
/**
 * #847 P7: the item's non-empty markings sit above the composer as chips, each
 * removable, and the ones left there go with the next message.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { MarkingProvider } from "../../hooks/useMarking";
import { MarkingStore } from "../../lib/markings";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel(store: MarkingStore, agent = stubAgent()) {
  renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <MarkingProvider store={store}>
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={agent}
            picker={[]}
            suggestions={[]}
            attachedPreset=""
            onAttachPreset={() => {}}
          />
        </MarkingProvider>
      </DialogProvider>
    </MemoryRouter>,
  );
  return agent;
}

function type(text: string) {
  const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
  fireEvent.change(composer, { target: { value: text } });
  fireEvent.keyDown(composer, { key: "Enter" });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
});

afterEach(cleanup);

describe("AgentPanel — markings go with a message (#847 P7)", () => {
  it("shows each non-empty marking as a chip above the composer, live", () => {
    const store = new MarkingStore();
    renderPanel(store);
    expect(screen.queryByTestId("marking-chip")).not.toBeInTheDocument();
    act(() => store.set("fail", { lot: new Set(["L1", "L2"]) }, "/v/grid.ai.yaml"));
    expect(screen.getByTestId("marking-chip")).toHaveTextContent("lot 2");
  });

  it("sends the chips' markings with the message", async () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2", "L1"]), wafer: new Set(["3"]) }, "/v/grid.ai.yaml");
    const agent = renderPanel(store);
    type("why these?");
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(agent.send).toHaveBeenCalledWith("why these?", {
      applySkills: [],
      imagePaths: [],
      markings: [
        {
          name: "fail",
          source: "/v/grid.ai.yaml",
          columns: { lot: ["L1", "L2"], wafer: ["3"] },
        },
      ],
    });
  });

  it("a removed chip is not sent, and comes back for the next message", async () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L1"]) }, null);
    const agent = renderPanel(store);
    fireEvent.click(within(screen.getByTestId("marking-chip")).getByRole("button"));
    expect(screen.queryByTestId("marking-chip")).not.toBeInTheDocument();
    type("hi");
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(agent.send).toHaveBeenCalledWith("hi", { applySkills: [], imagePaths: [] });
    // Removal was for that message: the selection is still there.
    expect(screen.getByTestId("marking-chip")).toBeInTheDocument();
  });

  it("without markings, the send is exactly what it was", async () => {
    const agent = renderPanel(new MarkingStore());
    type("hi");
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(agent.send).toHaveBeenCalledWith("hi", { applySkills: [], imagePaths: [] });
  });
});
