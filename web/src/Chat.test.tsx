// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted so vi.mock can reference it (vi.mock is lifted to the top of the file).
const harness = vi.hoisted(() => {
  type Resolve = (value: void) => void;
  const state: { resume: Resolve | null } = { resume: null };

  function streamAgentEvents({ signal }: { signal?: AbortSignal }) {
    return (async function* () {
      yield { type: "message_delta", text: "thinking..." };
      // Park until either the test resumes us OR the signal aborts.
      await new Promise<void>((resolve) => {
        state.resume = resolve;
        signal?.addEventListener("abort", () => resolve());
      });
      if (signal?.aborted) {
        yield { type: "run_cancelled" };
        return;
      }
      yield { type: "done" };
    })();
  }

  return { state, streamAgentEvents };
});

vi.mock("./api", () => ({
  api: {
    getConversationByWorkspace: vi.fn().mockResolvedValue(null),
    streamAgentEvents: harness.streamAgentEvents,
    listWorkspaces: vi.fn().mockResolvedValue([]),
    createWorkspace: vi.fn(),
    listFiles: vi.fn().mockResolvedValue([]),
    readFile: vi.fn(),
  },
}));

// Import AFTER vi.mock so Chat picks up the mocked module.
import { Chat } from "./Chat";

beforeEach(() => {
  harness.state.resume = null;
});

afterEach(() => {
  cleanup();
});

async function startTurn() {
  const user = userEvent.setup();
  render(<Chat workspaceId="ws-test" />);
  const composer = await screen.findByPlaceholderText(
    "Ask the agent to do something…",
  );
  await user.type(composer, "hello");
  await user.click(screen.getByRole("button", { name: /^send$/i }));
  // Wait until the first event has been rendered so we know the stream is in flight.
  await screen.findByText("thinking...");
  return user;
}

describe("Chat — F4 Stop button", () => {
  it("renders Stop while a turn is running", async () => {
    await startTurn();
    expect(screen.getByRole("button", { name: /^stop$/i })).toBeInTheDocument();
  });

  it("does not render Stop when idle", async () => {
    render(<Chat workspaceId="ws-test" />);
    await screen.findByPlaceholderText("Ask the agent to do something…");
    expect(screen.queryByRole("button", { name: /^stop$/i })).not.toBeInTheDocument();
  });

  it("clicking Stop renders the cancelled row", async () => {
    const user = await startTurn();
    await user.click(screen.getByRole("button", { name: /^stop$/i }));
    expect(await screen.findByText("— cancelled —")).toBeInTheDocument();
  });

  it("composer re-enables after cancellation", async () => {
    const user = await startTurn();
    const composer = screen.getByPlaceholderText("Ask the agent to do something…");
    expect(composer).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /^stop$/i }));
    await screen.findByText("— cancelled —");
    expect(composer).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: /^stop$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument();
  });
});
