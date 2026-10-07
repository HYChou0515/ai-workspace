/**
 * `docs/plan-personal-env.md` — my environment variables in an item's Env panel.
 *
 * A person's values for every item fill a name only where the item asks for a
 * personal value (Private first / Private only, D2), below their value for this
 * item (D4). The "Only me" tab says which one is in use. Signing in there writes
 * my environment variables (D3, D6), not this item's values.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

const ERP: ItemToolState = {
  key: "erp",
  group: "erp",
  label: "erp",
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  env_needs: [{ name: "ERP_TOKEN", description: "", required: true }],
};

const SAP = { id: "sap", label: "SAP login", produces: ["ERP_TOKEN"], inputs: [] };

function open({
  envVars = {},
  envPolicy = { ERP_TOKEN: "private_first" },
  mine = {},
  personal = {},
}: {
  envVars?: Record<string, string>;
  envPolicy?: Record<string, string>;
  mine?: Record<string, string>;
  personal?: Record<string, string>;
} = {}) {
  const privateClient = {
    get: vi.fn(async () => ({ values: mine, auto: {} })),
    put: vi.fn(async () => {}),
    clear: vi.fn(async () => {}),
  };
  const personalClient = {
    get: vi.fn(async () => ({ values: personal, updated: {} })),
    put: vi.fn(async (values: Record<string, string>) => ({ values, updated: {} })),
  };
  const resolveEnvProvider = vi.fn(async () => ({ ERP_TOKEN: "fresh" }));
  renderWithQuery(
    <EnvVarsModal
      envVars={envVars}
      envPolicy={envPolicy}
      onSave={vi.fn()}
      onClose={vi.fn()}
      slug="rca"
      itemId="i1"
      client={{
        getItemTools: vi.fn(async () => ({ tools: [ERP], updateNeedsClose: false, canClose: false })),
        getEnvProviders: vi.fn(async () => [SAP]),
        resolveEnvProvider,
      }}
      privateClient={privateClient}
      personalClient={personalClient}
    />,
  );
  return { privateClient, personalClient, resolveEnvProvider };
}

const row = () => screen.findByTestId("env-mine-row-ERP_TOKEN");

/** A section whose values are all there turns ready and folds — so the value
 * from my environment variables counting as present is itself what makes it
 * ready. Unfold it to read the row. */
async function readyThenUnfold() {
  await waitFor(() =>
    expect(screen.getByTestId("env-section-head-erp")).toHaveAttribute("data-status", "ready"),
  );
  const head = screen.getByTestId("env-section-head-erp");
  if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
}

describe("my environment variables in an item's Env panel", () => {
  it("names my value for every item when it is the one in use", async () => {
    open({ envVars: { ERP_TOKEN: "shared" }, personal: { ERP_TOKEN: "everywhere" } });
    await readyThenUnfold();

    expect(await row()).toHaveAttribute("data-in-use", "personal");
    expect(await row()).toHaveTextContent("所有 item");
  });

  it("puts this item's own value above it", async () => {
    open({ mine: { ERP_TOKEN: "here" }, personal: { ERP_TOKEN: "everywhere" } });
    await readyThenUnfold();

    expect(await row()).toHaveAttribute("data-in-use", "mine");
    expect(await row()).toHaveTextContent("這個 item");
  });

  it("does not use it for an item that uses the shared value", async () => {
    open({ envPolicy: { ERP_TOKEN: "shared_first" }, personal: { ERP_TOKEN: "everywhere" } });

    await waitFor(async () => expect(await row()).toHaveAttribute("data-in-use", "none"));
  });

  it("signs in to my environment variables, not to this item", async () => {
    const { privateClient, personalClient, resolveEnvProvider } = open({
      personal: { OTHER: "kept" },
    });

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept", ERP_TOKEN: "fresh" }),
    );
    expect(resolveEnvProvider).toHaveBeenCalled();
    expect(privateClient.put).not.toHaveBeenCalled();
  });
});

describe("the policy choices", () => {
  it("are named Shared, Private first and Private only", async () => {
    open();
    fireEvent.click(screen.getByTestId("env-tab-shared"));

    const label = async (p: string) =>
      (await screen.findByTestId(`env-policy-ERP_TOKEN-${p}`)).closest("label");
    expect(await label("shared_first")).toHaveTextContent("Shared");
    expect(await label("private_first")).toHaveTextContent("Private first");
    expect(await label("private_only")).toHaveTextContent("Private only");
  });
});
