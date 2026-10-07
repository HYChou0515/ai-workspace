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
import { makeQueryClient } from "../api/queryClient";
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
  providers = [SAP] as (typeof SAP)[],
  tools = [ERP],
}: {
  envVars?: Record<string, string>;
  envPolicy?: Record<string, string>;
  mine?: Record<string, string>;
  personal?: Record<string, string>;
  providers?: (typeof SAP)[];
  tools?: ItemToolState[];
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
  const resolveEnvProvider = vi.fn(
    async (_slug: string, _item: string, _id: string): Promise<Record<string, string>> => ({
      ERP_TOKEN: "fresh",
    }),
  );
  renderWithQuery(
    <EnvVarsModal
      envVars={envVars}
      envPolicy={envPolicy}
      onSave={vi.fn()}
      onClose={vi.fn()}
      slug="rca"
      itemId="i1"
      client={{
        getItemTools: vi.fn(async () => ({ tools, updateNeedsClose: false, canClose: false })),
        getEnvProviders: vi.fn(async () => providers),
        resolveEnvProvider,
      }}
      privateClient={privateClient}
      personalClient={personalClient}
    />,
    // The app's own client: its 30s staleTime is what a stale re-read would hit.
    makeQueryClient(),
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

  it("in an item that uses the shared value, fills this item's own value as before", async () => {
    // Round 1, F1: a Shared item never reads my environment variables, so a
    // sign-in stored there would leave the tool without its token. It goes
    // where THIS item reads it: the item's own form, saved with this tab.
    const { privateClient, personalClient } = open({ envPolicy: {} });

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect((screen.getByTestId("env-mine-ERP_TOKEN") as HTMLInputElement).value).toBe("fresh"),
    );
    fireEvent.click(screen.getByTestId("env-mine-save"));
    await waitFor(() =>
      expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "fresh" }),
    );
    expect(personalClient.put).not.toHaveBeenCalled();
  });

  it("routes a sign-in by the policy the panel shows, saved or not", async () => {
    // Round 2, F3: routed by the SAVED policy, a token signed in after the
    // owner switched a name to Private first (not yet saved) went into this
    // item's own values — where, once saved, it would hide my environment
    // variables for good.
    const { privateClient, personalClient } = open({ envPolicy: {} });
    fireEvent.click(screen.getByTestId("env-tab-shared"));
    fireEvent.click(await screen.findByTestId("env-policy-ERP_TOKEN-private_first"));
    fireEvent.click(screen.getByTestId("env-tab-mine"));

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() => expect(personalClient.put).toHaveBeenCalledWith({ ERP_TOKEN: "fresh" }));
    expect(privateClient.put).not.toHaveBeenCalled();
  });

  it("keeps both of two sign-ins made one right after the other", async () => {
    // Round 2, F2: each sign-in re-reads the row and writes all of it; two at
    // once read the same row, and the second write dropped the first's token.
    const MES = { id: "mes", label: "MES login", produces: ["MES_TOKEN"], inputs: [] };
    const { personalClient, resolveEnvProvider } = open({
      envPolicy: { ERP_TOKEN: "private_first", MES_TOKEN: "private_first" },
      providers: [SAP, MES],
      tools: [
        ERP,
        { ...ERP, key: "mes", group: "mes", label: "mes", env_needs: [{ name: "MES_TOKEN", description: "", required: true }] },
      ],
    });
    let stored: Record<string, string> = {};
    personalClient.get.mockImplementation(async () => ({ values: { ...stored }, updated: {} }));
    personalClient.put.mockImplementation(async (next: Record<string, string>) => {
      await new Promise((r) => setTimeout(r, 20));
      stored = next;
      return { values: next, updated: {} };
    });
    resolveEnvProvider.mockImplementation(
      async (_s: string, _i: string, id: string): Promise<Record<string, string>> =>
        id === "mes" ? { MES_TOKEN: "m" } : { ERP_TOKEN: "e" },
    );

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));
    fireEvent.click(await screen.findByTestId("env-provider-mes"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() => expect(personalClient.put).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(stored).toEqual({ ERP_TOKEN: "e", MES_TOKEN: "m" }));
  });

  it("keeps a value saved from another tab since the panel opened", async () => {
    // Round 1, F2: the sign-in re-reads the row before writing the whole of it.
    const { personalClient } = open({ personal: { OTHER: "kept" } });
    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    await waitFor(() => expect(personalClient.get).toHaveBeenCalled());
    personalClient.get.mockResolvedValue({ values: { OTHER: "kept", LATER: "l" }, updated: {} });

    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept", LATER: "l", ERP_TOKEN: "fresh" }),
    );
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
