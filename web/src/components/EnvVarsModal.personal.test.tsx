/**
 * `docs/plan-personal-env.md` — my environment variables in an item's Env panel.
 *
 * Three tabs, one per layer, named the way the user named them (A20): Shared,
 * Private (my values for THIS item) and Private(跨workspace) (my values for
 * every item — the same row as the "My environment variables" page). What you
 * do in a tab, a sign-in included, lands in that tab's layer and nowhere else.
 *
 * A value for every item fills a name only where the item asks for a personal
 * value (Private first / Private only, D2), below my value for this item (D4).
 * The Private tab says which one is in use; the cross-workspace tab says, per
 * name, whether this item uses it at all.
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
  personalFails = false,
  mineHangs = false,
}: {
  envVars?: Record<string, string>;
  envPolicy?: Record<string, string>;
  mine?: Record<string, string>;
  personal?: Record<string, string>;
  providers?: (typeof SAP)[];
  tools?: ItemToolState[];
  personalFails?: boolean;
  mineHangs?: boolean;
} = {}) {
  const privateClient = {
    get: vi.fn(() =>
      mineHangs
        ? new Promise<never>(() => {})
        : Promise.resolve({ values: mine, auto: {} as Record<string, string> }),
    ),
    put: vi.fn(async () => {}),
    clear: vi.fn(async () => {}),
  };
  const personalClient = {
    get: vi.fn(async () => {
      if (personalFails) throw new Error("down");
      return { values: personal, updated: {} as Record<string, number> };
    }),
    put: vi.fn(async (values: Record<string, string>) => ({ values, updated: {} })),
  };
  const onClose = vi.fn();
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
      onClose={onClose}
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
  return { privateClient, personalClient, resolveEnvProvider, onClose };
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

  it("on Private, a sign-in fills this item's own value, saved with this tab — whatever the policy", async () => {
    // A20: the tab is my values for THIS item, so that is where its sign-in
    // lands, even for a name this item reads from my environment variables
    // (the round-1/2 split by policy, A8, is gone).
    const { privateClient, personalClient } = open({ envPolicy: { ERP_TOKEN: "private_first" } });

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
});

const personalTab = async () => fireEvent.click(await screen.findByTestId("env-tab-personal"));

describe("the Private(跨workspace) tab", () => {
  it("sits third, after Shared and Private, under the names the user chose", async () => {
    open();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((x) => x.textContent)).toEqual(["Shared", "Private", "Private(跨workspace)"]);
    // Private stays the tab the panel opens on.
    expect(screen.getByTestId("env-tab-mine")).toHaveAttribute("aria-selected", "true");
  });

  it("lists what this item's tools need, and says whether this item uses it", async () => {
    open({ envPolicy: { ERP_TOKEN: "private_first" }, personal: { ERP_TOKEN: "t" } });
    await personalTab();

    const used = await screen.findByTestId("env-personal-row-ERP_TOKEN");
    expect(used).toHaveAttribute("data-used", "true");
    cleanup();

    open({ envPolicy: {}, personal: { ERP_TOKEN: "t" } });
    await personalTab();
    const unused = await screen.findByTestId("env-personal-row-ERP_TOKEN");
    // D2: a Shared name never reads it — said on the row, so a sign-in here
    // that this item then ignores is not a mystery.
    expect(unused).toHaveAttribute("data-used", "false");
    expect(unused).toHaveTextContent("Shared");
  });

  it("stores a sign-in at once, keeping my other values, and leaves this item's alone", async () => {
    const { privateClient, personalClient } = open({ personal: { OTHER: "kept" } });
    await personalTab();

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept", ERP_TOKEN: "fresh" }),
    );
    expect(privateClient.put).not.toHaveBeenCalled();
  });

  it("keeps a value saved from another tab since the panel opened", async () => {
    // Round 1, F2: the write re-reads the row before writing the whole of it.
    const { personalClient } = open({ personal: { OTHER: "kept" } });
    await personalTab();
    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    await waitFor(() => expect(personalClient.get).toHaveBeenCalled());
    personalClient.get.mockResolvedValue({ values: { OTHER: "kept", LATER: "l" }, updated: {} });

    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept", LATER: "l", ERP_TOKEN: "fresh" }),
    );
  });

  it("keeps both a save and a sign-in made while it was on its way", async () => {
    // Round 2, F2: each write re-reads the row and writes all of it; two at once
    // read the same row, and the second write dropped the first's value. A
    // sign-in now waits for its own store (review A20, D5), so the pair that can
    // still overlap is this tab's Save and a sign-in started during it.
    const MES = { ...ERP, key: "mes", group: "mes", label: "mes", env_needs: [{ name: "MES_TOKEN", description: "", required: true }] };
    const { personalClient } = open({
      envPolicy: { ERP_TOKEN: "private_first", MES_TOKEN: "private_first" },
      tools: [ERP, MES],
    });
    let stored: Record<string, string> = {};
    personalClient.get.mockImplementation(async () => ({ values: { ...stored }, updated: {} }));
    personalClient.put.mockImplementation(async (next: Record<string, string>) => {
      await new Promise((r) => setTimeout(r, 20));
      stored = next;
      return { values: next, updated: {} };
    });
    await personalTab();
    fireEvent.change(await screen.findByTestId("env-personal-MES_TOKEN"), { target: { value: "m" } });

    fireEvent.click(screen.getByTestId("env-personal-save"));
    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() => expect(personalClient.put).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(stored).toEqual({ MES_TOKEN: "m", ERP_TOKEN: "fresh" }));
  });

  it("saves what I typed with this tab, changing only the names I edited", async () => {
    const { personalClient, privateClient } = open({ personal: { OTHER: "kept", ERP_TOKEN: "old" } });
    await personalTab();
    const box = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    await waitFor(() => expect(box.value).toBe("old"));
    fireEvent.change(box, { target: { value: "new" } });
    // Saved elsewhere meanwhile: kept, because only the edited name is written.
    personalClient.get.mockResolvedValue({ values: { OTHER: "kept", ERP_TOKEN: "old", LATER: "l" }, updated: {} });

    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept", ERP_TOKEN: "new", LATER: "l" }),
    );
    expect(privateClient.put).not.toHaveBeenCalled();
  });

  it("shows the signed-in value over what I had typed for that name, and no longer counts it unsaved", async () => {
    const { onClose } = open({ personal: { ERP_TOKEN: "old" } });
    await personalTab();
    const box = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    await waitFor(() => expect(box.value).toBe("old"));
    fireEvent.change(box, { target: { value: "typed" } });

    fireEvent.click(screen.getByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() => expect((screen.getByTestId("env-personal-ERP_TOKEN") as HTMLInputElement).value).toBe("fresh"));
    fireEvent.click(screen.getByTestId("env-cancel"));
    expect(onClose).toHaveBeenCalled();
  });

  it("removes a value I cleared", async () => {
    const { personalClient } = open({ personal: { OTHER: "kept", ERP_TOKEN: "old" } });
    await personalTab();
    const box = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    await waitFor(() => expect(box.value).toBe("old"));
    fireEvent.change(box, { target: { value: "" } });

    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() => expect(personalClient.put).toHaveBeenCalledWith({ OTHER: "kept" }));
  });

  it("asks before Cancel drops a value typed here", async () => {
    const { onClose, personalClient } = open({ personal: { ERP_TOKEN: "old" } });
    await personalTab();
    const box = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    await waitFor(() => expect(box.value).toBe("old"));
    fireEvent.change(box, { target: { value: "typed" } });

    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-discard"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(personalClient.put).not.toHaveBeenCalled();
  });

  it("closes on Cancel without asking when nothing was typed here", async () => {
    const { onClose } = open({ personal: { ERP_TOKEN: "old" } });
    await personalTab();
    await screen.findByTestId("env-personal-ERP_TOKEN");

    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(onClose).toHaveBeenCalled();
  });

  it("does not let Save write the row over a read that failed", async () => {
    // A save re-reads, but a person who never saw their values would be
    // editing blind: the button waits for a successful read.
    open({ personalFails: true });
    await personalTab();

    await waitFor(() => expect(screen.getByTestId("env-personal-save")).toBeDisabled(), { timeout: 3000 });
  });

  it("finds a variable by the tool that needs it, as the other tabs do", async () => {
    // Review A20, D1: searching "ledger" showed the tool on Private and nothing here.
    open({ tools: [{ ...ERP, label: "Ledger" }] });
    fireEvent.change(await screen.findByTestId("env-search"), { target: { value: "ledger" } });
    await personalTab();

    expect(await screen.findByTestId("env-personal-row-ERP_TOKEN")).toBeInTheDocument();
  });

  it("says there is nothing to fill, not that a search matched nothing, when nothing is needed", async () => {
    // Review A20, D2.
    open({ tools: [], envPolicy: {} });
    await personalTab();

    expect(await screen.findByTestId("env-personal-none")).toBeInTheDocument();
    expect(screen.queryByText("沒有符合的工具或變數")).toBeNull();
  });

  it("does not claim this item uses the value before it knows this item's own", async () => {
    // Review A20, D3: with the Private read still out, the row said "uses this
    // value" — and an own value, once read, wins (D4).
    open({ personal: { ERP_TOKEN: "t" }, mineHangs: true });
    await personalTab();
    await new Promise((r) => setTimeout(r, 30));

    expect(screen.queryByText("這個 item 會用這個值")).toBeNull();
  });

  it("does not take typing while a save is on its way", async () => {
    // Review A20, D4: what was typed during the save was dropped as it landed.
    const { personalClient } = open({ personal: { ERP_TOKEN: "old" } });
    personalClient.put.mockImplementation(async (values: Record<string, string>) => {
      await new Promise((r) => setTimeout(r, 30));
      return { values, updated: {} };
    });
    await personalTab();
    const box = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    await waitFor(() => expect(box.value).toBe("old"));
    fireEvent.change(box, { target: { value: "a" } });

    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() => expect(screen.getByTestId("env-personal-ERP_TOKEN")).toBeDisabled());
  });

  it("keeps the sign-in open and says so when its value could not be stored", async () => {
    // Review A20, D5: the dialog closed and the token was gone.
    const { personalClient } = open();
    personalClient.put.mockRejectedValue(new Error("down"));
    await personalTab();

    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    expect(await screen.findByTestId("env-cred-error")).toBeInTheDocument();
    expect(screen.getByTestId("env-cred-dialog")).toBeInTheDocument();
  });

  it("writes nothing when Save is pressed with nothing typed", async () => {
    // Review A20, D6: a whole-row PUT for no change can only race another tab.
    const { personalClient, onClose } = open({ personal: { ERP_TOKEN: "old" } });
    await personalTab();
    await screen.findByTestId("env-personal-ERP_TOKEN");

    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(personalClient.put).not.toHaveBeenCalled();
  });

  it("links to the page that holds all of them", async () => {
    open();
    await personalTab();
    expect(await screen.findByTestId("env-personal-page")).toHaveAttribute("href", "/my-env");
  });
});

describe("the policy choices", () => {
  it("are named Shared, Private first and Private only", async () => {
    // D9 — restored after A20 dropped it by accident (review A20, C11).
    open();
    fireEvent.click(screen.getByTestId("env-tab-shared"));

    const label = async (p: string) =>
      (await screen.findByTestId(`env-policy-ERP_TOKEN-${p}`)).closest("label");
    expect(await label("shared_first")).toHaveTextContent("Shared");
    expect(await label("private_first")).toHaveTextContent("Private first");
    expect(await label("private_only")).toHaveTextContent("Private only");
  });
});
