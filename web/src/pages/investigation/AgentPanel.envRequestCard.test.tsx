// @vitest-environment happy-dom
/**
 * docs/plan-env-request-card.md: a `request_env` card in an item's chat opens
 * THAT chat's environment panel, and its Retry is an ordinary send. A panel
 * that cannot open the environment panel hands the card no actions.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { privateEnvApi } from "../../api/privateEnv";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

function agent(): AgentState {
  return {
    investigationId: "it1",
    log: {
      entries: [
        {
          kind: "tool_call",
          call: {
            call_id: "c1",
            name: "request_env",
            status: "done",
            args: {},
            output:
              'The user now sees a card.\n[env-request]{"tool":"lookup","names":["MAP_KEY"],"reason":"The map needs a key"}',
          },
        },
      ],
      streaming: false,
    } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel(a: AgentState, { canEditEnv }: { canEditEnv: boolean }) {
  renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <WorkspaceSlugProvider value="pm">
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={a}
            picker={[]}
            suggestions={[]}
            attachedPreset=""
            onAttachPreset={() => {}}
            uploadDir="uploads"
            envVars={{}}
            envPolicy={{}}
            onSaveEnvVars={canEditEnv ? vi.fn() : undefined}
          />
        </WorkspaceSlugProvider>
      </DialogProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
  vi.spyOn(api, "getItemTools").mockResolvedValue({ tools: [], updateNeedsClose: false, canClose: false });
  vi.spyOn(api, "getEnvProviders").mockResolvedValue([]);
});

afterEach(cleanup);

describe("AgentPanel — a request_env card in the log", () => {
  it("opens this chat's environment panel", async () => {
    vi.spyOn(privateEnvApi, "get").mockResolvedValue({ values: {}, auto: {} });
    renderPanel(agent(), { canEditEnv: true });

    fireEvent.click(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ }));

    expect(await screen.findByTestId("env-modal")).toBeInTheDocument();
    // At the variable the card asked for, though no tool declared it.
    const field = await screen.findByTestId("env-mine-MAP_KEY");
    await waitFor(() => expect(field).toHaveFocus());
  });

  it("turns to Retry once the person saves the value from the panel it opened", async () => {
    vi.spyOn(privateEnvApi, "get").mockResolvedValue({ values: {}, auto: {} });
    vi.spyOn(privateEnvApi, "put").mockResolvedValue(undefined);
    renderPanel(agent(), { canEditEnv: true });

    fireEvent.click(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ }));
    fireEvent.change(await screen.findByTestId("env-mine-MAP_KEY"), { target: { value: "k" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));

    expect(await screen.findByRole("button", { name: /重試|Retry/ })).toBeEnabled();
  });

  it("retries as an ordinary send once the variable is set", async () => {
    vi.spyOn(privateEnvApi, "get").mockResolvedValue({ values: { MAP_KEY: "k" }, auto: {} });
    const a = agent();
    renderPanel(a, { canEditEnv: true });

    fireEvent.click(await screen.findByRole("button", { name: /重試|Retry/ }));

    await waitFor(() => expect(a.send).toHaveBeenCalledTimes(1));
    expect(vi.mocked(a.send).mock.calls[0][0]).toContain("MAP_KEY");
  });

  it("shows the request without actions where the panel cannot be opened", async () => {
    vi.spyOn(privateEnvApi, "get").mockResolvedValue({ values: {}, auto: {} });
    renderPanel(agent(), { canEditEnv: false });

    expect(await screen.findByText("The map needs a key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /MAP_KEY/ })).toBeNull();
  });
});
