/**
 * The chat's request card (docs/plan-env-request-card.md): one button per
 * variable the AI asked for — sign in, or set — and a Retry once they are all
 * set. The turn has stopped at the card; nothing retries on its own (N3).
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
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
  providers = [erp],
}: {
  names: string[];
  mine?: Record<string, string>;
  shared?: Record<string, string>;
  providers?: EnvProvider[];
}) {
  const host: ChatEnv = { shared, policy: {}, open: vi.fn(), retry: vi.fn() };
  renderWithQuery(
    <ChatItemProvider value={{ slug: "rca", itemId: "i1", env: host }}>
      <EnvRequestCard
        request={{ tool: "lookup", names, reason: "The ERP lookup needs you to sign in" }}
        client={{ getEnvProviders: async () => providers }}
        privateClient={{ get: async () => ({ values: mine, auto: {} }) }}
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

    expect(host.open).toHaveBeenCalledWith({ name: "ERP_TOKEN", login: "erp", tab: "mine" });
  });

  it("opens the panel at the field when nothing signs in for it", async () => {
    const host = draw({ names: ["MAP_KEY"] });

    fireEvent.click(await screen.findByRole("button", { name: /設定 MAP_KEY|Set MAP_KEY/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "MAP_KEY", login: null, tab: "mine" });
  });

  it("sends a name only the shared copy can fix to the shared tab", async () => {
    const host = draw({ names: ["MAP_KEY"], shared: { MAP_KEY: "" } });

    fireEvent.click(await screen.findByRole("button", { name: /MAP_KEY/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "MAP_KEY", login: null, tab: "shared" });
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
    const text = vi.mocked(host.retry).mock.calls[0][0];
    expect(text).toContain("ERP_TOKEN");
    expect(text).toContain("lookup");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /重試|Retry/ })).toBeDisabled(),
    );
  });
});
