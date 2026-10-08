/**
 * The panel opened from a `request_env` card (docs/plan-env-request-card.md
 * N3, N4, D5): at the tab the card chose, at the variable it asked for — with
 * a field for it even when no tool declared it — or straight into the login
 * that produces it.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EnvProvider, ItemToolState } from "../api/types";
import type { EnvTarget } from "../hooks/chatItem";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

const erp: EnvProvider = {
  id: "erp",
  label: "ERP",
  produces: ["ERP_TOKEN"],
  inputs: [{ name: "user", label: "User", secret: false }],
};

const optional: ItemToolState = {
  key: "maps",
  group: "maps",
  label: "Maps",
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  env_needs: [{ name: "MAP_KEY", description: "", required: false }],
};

function openAt(target: EnvTarget, { tools = [] as ItemToolState[], shared = {} } = {}) {
  renderWithQuery(
    <EnvVarsModal
      envVars={shared}
      onSave={vi.fn()}
      onClose={vi.fn()}
      slug="rca"
      itemId="i1"
      target={target}
      client={{
        getItemTools: vi.fn(async () => ({ tools, updateNeedsClose: false, canClose: false })),
        getEnvProviders: vi.fn(async () => [erp]),
        resolveEnvProvider: vi.fn(),
      }}
      privateClient={{
        get: vi.fn(async () => ({ values: {}, auto: {} })),
        put: vi.fn(),
        clear: vi.fn(),
      }}
    />,
  );
}

describe("EnvVarsModal opened at a target", () => {
  it("draws and focuses a field for a name no tool declared (D5)", async () => {
    openAt({ name: "MAP_KEY", login: null });

    const field = await screen.findByTestId("env-mine-MAP_KEY");
    await waitFor(() => expect(field).toHaveFocus());
  });

  it("unfolds the tool section the name is in and focuses it", async () => {
    openAt({ name: "MAP_KEY", login: null }, { tools: [optional] });

    const field = await screen.findByTestId("env-mine-MAP_KEY");
    await waitFor(() => expect(field).toHaveFocus());
  });

  it("opens on the person's own tab", async () => {
    openAt({ name: "MAP_KEY", login: null });

    expect(screen.getByTestId("env-tab-mine")).toHaveAttribute("aria-selected", "true");
  });

  it("goes straight into the login, even when no tool declared what it produces", async () => {
    openAt({ name: "ERP_TOKEN", login: "erp" });

    expect(await screen.findByTestId("env-cred-dialog")).toBeInTheDocument();
  });
});
