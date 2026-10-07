/**
 * The Env panel opened from a PAGE (`plan-wui-viewer-login` Q10/Q11): from the
 * `/w/` bar or a page's `openLogin`. The page has the item's id but not its
 * record, so this loads the two layers itself; the shared half is read-only —
 * nobody stores shared values from inside a page.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
import { ItemEnvModal } from "./ItemEnvModal";

afterEach(cleanup);

describe("ItemEnvModal", () => {
  it("opens on the person's own values, with the item's layers loaded", async () => {
    const privateClient = {
      layers: vi.fn(async () => ({ shared: {}, policy: { VPN_KEY: "private_only" } })),
      get: vi.fn(async () => ({ values: {}, auto: {} })),
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
    };
    renderWithQuery(
      <ItemEnvModal
        slug="rca"
        itemId="i1"
        onClose={vi.fn()}
        privateClient={privateClient}
        client={{
          getItemTools: vi.fn(async () => ({ tools: [], updateNeedsClose: false, canClose: false })),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
      />,
    );

    // VPN_KEY is only known from the policy — proof the layers were read.
    expect(await screen.findByTestId("env-mine-row-VPN_KEY")).toBeInTheDocument();
    expect(privateClient.layers).toHaveBeenCalledWith("rca", "i1");
    screen.getByTestId("env-tab-shared").click();
    expect(await screen.findByTestId("env-readonly")).toBeInTheDocument();
  });
});
