/**
 * The chat's request card (docs/plan-env-request-card.md): one button per
 * variable the AI asked for — sign in, or set — and a Retry once they are all
 * set. The turn has stopped at the card; nothing retries on its own (N3).
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EnvProvider } from "../api/types";
import { type ChatEnv, ChatItemProvider } from "../hooks/chatItem";
import { renderWithQuery } from "../test/queryWrapper";

import { EnvRequestCard } from "./EnvRequestCard";

const erp: EnvProvider = { id: "erp", label: "ERP", produces: ["ERP_TOKEN"], inputs: [] };

function draw({
  names,
  mine = {},
  shared = {},
  policy = {},
  personal = {},
  providers = [erp],
  answered = false,
  sends = true,
}: {
  names: string[];
  mine?: Record<string, string>;
  shared?: Record<string, string>;
  policy?: Record<string, string>;
  personal?: Record<string, string>;
  providers?: EnvProvider[];
  answered?: boolean;
  sends?: boolean;
}) {
  const host: ChatEnv = {
    shared,
    policy,
    open: vi.fn(),
    retry: vi.fn(() => sends),
    answered: () => answered,
  };
  renderWithQuery(
    <ChatItemProvider value={{ slug: "rca", itemId: "i1", env: host }}>
      <EnvRequestCard
        callId="c1"
        request={{ tool: "lookup", names, reason: "The ERP lookup needs you to sign in" }}
        client={{ getEnvProviders: async () => providers }}
        privateClient={{ get: async () => ({ values: mine, auto: {} }) }}
        personalClient={{ get: async () => ({ values: personal, updated: {} }) }}
      />
    </ChatItemProvider>,
  );
  return host;
}

afterEach(cleanup);

describe("EnvRequestCard", () => {
  it("says why, in the AI's words", async () => {
    draw({ names: ["ERP_TOKEN"] });

    expect(await screen.findByText("The ERP lookup needs you to sign in")).toBeInTheDocument();
  });

  it("signs in when a login produces the name, straight into that login", async () => {
    const host = draw({ names: ["ERP_TOKEN"] });

    fireEvent.click(await screen.findByRole("button", { name: /登入 ERP|Sign in to ERP/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "ERP_TOKEN", login: "erp" });
  });

  it("opens the panel at the field when nothing signs in for it", async () => {
    const host = draw({ names: ["MAP_KEY"] });

    fireEvent.click(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "MAP_KEY", login: null });
  });

  it("asks for the person's own value even where a blank shared copy exists", async () => {
    const host = draw({ names: ["MAP_KEY"], shared: { MAP_KEY: "" } });

    fireEvent.click(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "MAP_KEY", login: null });
  });

  it("offers no retry while something is still missing", async () => {
    draw({ names: ["ERP_TOKEN", "MAP_KEY"], mine: { ERP_TOKEN: "t" } });

    await screen.findByRole("button", { name: /MAP_KEY/ });
    expect(screen.queryByRole("button", { name: /重試|Retry/ })).toBeNull();
  });

  it("retries once everything is set, as the user's own message", async () => {
    const host = draw({ names: ["ERP_TOKEN"], mine: { ERP_TOKEN: "t" } });

    fireEvent.click(await screen.findByRole("button", { name: /重試|Retry/ }));

    expect(host.retry).toHaveBeenCalledTimes(1);
    const [callId, text] = vi.mocked(host.retry).mock.calls[0];
    expect(callId).toBe("c1");
    expect(text).toContain("ERP_TOKEN");
    expect(text).toContain("lookup");
    // Retiring is the thread's job (`answered`), pinned in the panel and
    // `useChatSession` tests — this host's thread never changes.
  });

  it("counts the person's values across workspaces where the item uses them", async () => {
    draw({
      names: ["MAP_KEY"],
      policy: { MAP_KEY: "private_first" },
      personal: { MAP_KEY: "k" },
    });

    expect(await screen.findByRole("button", { name: /重試|Retry/ })).toBeEnabled();
  });

  it("keeps Retry when the send was refused, so it can be pressed again", async () => {
    const host = draw({ names: ["ERP_TOKEN"], mine: { ERP_TOKEN: "t" }, sends: false });

    fireEvent.click(await screen.findByRole("button", { name: /重試|Retry/ }));

    expect(host.retry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /重試|Retry/ })).toBeEnabled();
  });

  it("offers no Retry for a card the thread already answered — after a reload too", async () => {
    draw({ names: ["ERP_TOKEN"], mine: { ERP_TOKEN: "t" }, answered: true });

    expect(await screen.findByText(/已請 AI 重試|Asked the AI to retry/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重試|Retry/ })).toBeNull();
  });

  it("still offers a field when the list of logins could not be read", async () => {
    const host: ChatEnv = {
      shared: {},
      policy: {},
      open: vi.fn(),
      retry: vi.fn(() => true),
      answered: () => false,
    };
    renderWithQuery(
      <ChatItemProvider value={{ slug: "rca", itemId: "i1", env: host }}>
        <EnvRequestCard
          callId="c1"
          request={{ tool: "lookup", names: ["MAP_KEY"], reason: "r" }}
          client={{ getEnvProviders: async () => Promise.reject(new Error("down")) }}
          privateClient={{ get: async () => ({ values: {}, auto: {} }) }}
        />
      </ChatItemProvider>,
    );

    expect(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ })).toBeInTheDocument();
  });
});
