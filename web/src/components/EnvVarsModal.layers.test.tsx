/**
 * `docs/plan-wui-viewer-login.md` — the Env panel's two layers.
 *
 * "Only me" (the default) holds the person's PRIVATE values for this item;
 * "Everyone" holds the item's SHARED values plus a per-variable policy saying
 * which layer a tool gets. One layer is edited at a time (Postman retired
 * editing both side by side), each tab saves on its own, and every row says
 * whose value is in use and why.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

const tool = (key: string, env_needs: ItemToolState["env_needs"]): ItemToolState => ({
  key,
  group: key,
  label: key,
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  env_needs,
});

const ERP = tool("erp", [
  { name: "ERP_TOKEN", description: "ERP access token", required: true },
  { name: "DB_HOST", description: "", required: null },
]);
const MAPS = tool("maps", [{ name: "DB_HOST", description: "", required: null }]);
const SILENT = tool("silent", null);

function open({
  tools = [ERP, MAPS, SILENT],
  envVars = {},
  envPolicy = {},
  mine = {},
  auto = {},
  canEdit = true,
}: {
  tools?: ItemToolState[];
  envVars?: Record<string, string>;
  envPolicy?: Record<string, string>;
  mine?: Record<string, string>;
  auto?: Record<string, string>;
  canEdit?: boolean;
} = {}) {
  const onSave = vi.fn();
  const privateClient = {
    get: vi.fn(async () => ({ values: mine, auto })),
    put: vi.fn(async () => {}),
    clear: vi.fn(async () => {}),
  };
  renderWithQuery(
    <EnvVarsModal
      envVars={envVars}
      envPolicy={envPolicy}
      onSave={canEdit ? onSave : undefined}
      onClose={vi.fn()}
      slug="rca"
      itemId="i1"
      client={{
        getItemTools: vi.fn(async () => tools),
        getEnvProviders: vi.fn(async () => []),
        resolveEnvProvider: vi.fn(),
      }}
      privateClient={privateClient}
    />,
  );
  return { onSave, privateClient };
}

const everyone = () => fireEvent.click(screen.getByTestId("env-tab-shared"));
/** The tool list arrives asynchronously; until it does, a declared variable is
 * drawn under "Other variables" and re-mounts under its tool afterwards. */
const toolsLoaded = () => screen.findByTestId("env-section-head-erp");
/** Unfold a section whatever its default (a ready one starts folded). */
const unfold = (key: string) => {
  const head = screen.getByTestId(`env-section-head-${key}`);
  if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
};

describe("the two tabs", () => {
  it("opens on the person's own values", async () => {
    open();
    expect(screen.getByTestId("env-tab-mine")).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("env-mine-ERP_TOKEN")).toBeInTheDocument();
  });

  it("edits one layer at a time", async () => {
    open();
    await screen.findByTestId("env-mine-ERP_TOKEN");
    expect(screen.queryByTestId("env-field-ERP_TOKEN")).not.toBeInTheDocument();

    everyone();

    expect(screen.getByTestId("env-field-ERP_TOKEN")).toBeInTheDocument();
    expect(screen.queryByTestId("env-mine-ERP_TOKEN")).not.toBeInTheDocument();
  });
});

describe("Everyone: tools as sections", () => {
  it("lists every tool, most urgent first, each with a status in words", async () => {
    open({ envVars: { DB_HOST: "db" } });
    everyone();

    await toolsLoaded();
    const heads = screen
      .getAllByTestId(/^env-section-head-/)
      .filter((h) => h.dataset.testid !== "env-section-head-__other");
    // `silent` declared nothing: no section (it needs nothing to show).
    expect(heads.map((h) => h.dataset.testid)).toEqual([
      "env-section-head-erp",
      "env-section-head-maps",
    ]);
    // Symbol + colour + WORDS: any one missing, the other two still read.
    expect(heads[0]).toHaveAttribute("data-status", "missingRequired");
    expect(heads[1]).toHaveAttribute("data-status", "ready"); // DB_HOST is set
  });

  it("opens the sections that need something and leaves the rest folded", async () => {
    open({ envVars: { DB_HOST: "db" } });
    everyone();

    expect(await screen.findByTestId("env-section-head-erp")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("env-section-head-maps")).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByTestId("env-section-head-maps"));

    expect(screen.getByTestId("env-section-head-maps")).toHaveAttribute("aria-expanded", "true");
  });

  it("does not fold a section under the person as they fill it in", async () => {
    open({ envVars: { DB_HOST: "db" } });
    everyone();
    await toolsLoaded();

    fireEvent.change(screen.getByTestId("env-field-ERP_TOKEN"), { target: { value: "t" } });

    // Now "ready" — and still open, with the value they just typed in view.
    expect(screen.getByTestId("env-section-head-erp")).toHaveAttribute("data-status", "ready");
    expect(screen.getByTestId("env-section-head-erp")).toHaveAttribute("aria-expanded", "true");
  });

  it("filters sections by what is typed", async () => {
    open();
    everyone();
    await screen.findByTestId("env-section-head-erp");

    fireEvent.change(screen.getByTestId("env-search"), { target: { value: "maps" } });

    expect(screen.queryByTestId("env-section-head-erp")).not.toBeInTheDocument();
    expect(screen.getByTestId("env-section-head-maps")).toBeInTheDocument();
  });

  it("saves each variable's policy with the shared values", async () => {
    const { onSave } = open({ envVars: { DB_HOST: "db" } });
    everyone();
    await screen.findByTestId("env-section-head-erp");

    fireEvent.click(screen.getByTestId("env-policy-ERP_TOKEN-private_only"));
    fireEvent.click(screen.getByTestId("env-save"));

    expect(onSave).toHaveBeenCalledWith({ DB_HOST: "db" }, { ERP_TOKEN: "private_only" });
  });

  it("links a variable two tools share: one value, one policy", async () => {
    open();
    everyone();
    await screen.findByTestId("env-section-head-erp");
    fireEvent.click(screen.getByTestId("env-section-head-maps")); // unfold the second

    const radios = screen.getAllByTestId("env-policy-DB_HOST-private_first");
    expect(radios).toHaveLength(2);
    fireEvent.click(radios[0]);

    for (const r of screen.getAllByTestId("env-policy-DB_HOST-private_first")) {
      expect(r).toBeChecked();
    }
    expect(screen.getAllByTestId("env-shared-DB_HOST")[0]).toHaveTextContent("maps");
    // A browser allows ONE checked radio per `name`, so two copies of this
    // choice sharing a name would un-check one another and the second section
    // would show no policy at all. happy-dom does not enforce that rule, so the
    // property the browser groups by is asserted directly.
    const names = screen.getAllByTestId("env-policy-DB_HOST-private_first").map(
      (r) => (r as HTMLInputElement).name,
    );
    expect(new Set(names).size).toBe(2);
  });

  it("is read-only, and says why, for someone who may not change it", async () => {
    open({ canEdit: false });
    everyone();
    await toolsLoaded();

    expect(screen.getByTestId("env-readonly")).toBeInTheDocument();
    expect(screen.queryByTestId("env-save")).not.toBeInTheDocument();
    expect(screen.getByTestId("env-policy-ERP_TOKEN-private_only")).toBeDisabled();
  });

  it("adds a variable only each person fills, without writing an empty shared value", async () => {
    const { onSave } = open();
    everyone();
    await screen.findByTestId("env-section-head-erp");
    fireEvent.click(screen.getByTestId("env-section-head-__other")); // folded by default

    fireEvent.change(screen.getByTestId("env-add-private-name"), { target: { value: "VPN_KEY" } });
    fireEvent.click(screen.getByTestId("env-add-private"));
    fireEvent.click(screen.getByTestId("env-save"));

    // An empty shared VALUE would be a value: under `shared_first` it would
    // override everybody's own.
    expect(onSave).toHaveBeenCalledWith({}, { VPN_KEY: "private_only" });
  });
});

describe("Only me", () => {
  it("says whose value each variable uses", async () => {
    open({
      envVars: { DB_HOST: "db", ERP_TOKEN: "shared-t" },
      envPolicy: { ERP_TOKEN: "private_first" },
      mine: { ERP_TOKEN: "mine-t" },
    });
    await toolsLoaded();
    // Their own value arrives, the section turns ready (and folds).
    await waitFor(() =>
      expect(screen.getByTestId("env-section-head-erp")).toHaveAttribute("data-status", "ready"),
    );
    unfold("erp");

    expect(screen.getByTestId("env-mine-row-ERP_TOKEN")).toHaveAttribute("data-in-use", "mine");
    expect(screen.getAllByTestId("env-mine-row-DB_HOST")[0]).toHaveAttribute("data-in-use", "shared");
  });

  it("does not offer a box for a variable pinned to the shared value", async () => {
    open({ envVars: { DB_HOST: "db" } }); // DB_HOST: no policy = shared first, and set
    await toolsLoaded();
    fireEvent.click(screen.getByTestId("env-section-head-maps"));

    expect(screen.getAllByTestId("env-mine-row-DB_HOST")[0]).toBeInTheDocument();
    expect(screen.queryByTestId("env-mine-DB_HOST")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("env-pinned-DB_HOST")[0]).toBeInTheDocument();
  });

  it("masks the person's own values until asked", async () => {
    open({ envPolicy: { ERP_TOKEN: "private_only" }, mine: { ERP_TOKEN: "s3cret" } });
    await toolsLoaded();
    await waitFor(() =>
      expect(screen.getByTestId("env-section-head-erp")).toHaveAttribute("data-status", "missingOptional"),
    );
    unfold("erp");

    const input = () => screen.getByTestId("env-mine-ERP_TOKEN") as HTMLInputElement;
    await waitFor(() => expect(input().value).toBe("s3cret"));
    expect(input().type).toBe("password");

    fireEvent.click(screen.getByTestId("env-reveal-ERP_TOKEN"));

    expect(input().type).toBe("text");
  });

  it("saves the person's values on their own, never the shared ones", async () => {
    const { onSave, privateClient } = open({ envPolicy: { ERP_TOKEN: "private_only" } });
    await toolsLoaded();

    fireEvent.change(screen.getByTestId("env-mine-ERP_TOKEN"), { target: { value: "t" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() => expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "t" }));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("logs out by forgetting every value", async () => {
    const { privateClient } = open({ mine: { ERP_TOKEN: "t" } });
    await toolsLoaded();

    fireEvent.click(screen.getByTestId("env-mine-logout"));

    await waitFor(() => expect(privateClient.clear).toHaveBeenCalledWith("rca", "i1"));
  });

  it("lists a value the deploy filled in automatically", async () => {
    open({ tools: [], mine: { SSO_TOKEN: "auto" } });

    const row = await screen.findByTestId("env-mine-row-SSO_TOKEN");
    await waitFor(() => expect(row).toHaveAttribute("data-in-use", "mine"));
    expect(within(row).getByTestId("env-mine-SSO_TOKEN")).toBeInTheDocument();
  });
});

describe("Only me: the variables that are each person's to fill", () => {
  it("unfolds them even when tools have sections of their own", async () => {
    // Seen in a real browser: opened from a page, the variables the viewer came
    // to fill sat folded under "Other variables" below every tool.
    open({ envPolicy: { VPN_KEY: "private_only" } });
    await toolsLoaded();

    expect(screen.getByTestId("env-section-head-__other")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("env-mine-row-VPN_KEY")).toBeInTheDocument();
  });

  it("claims no status for variables no tool declared", async () => {
    // Seen in a real browser: "✓ Ready" over a row saying "Not set". Nothing
    // declared these names, so there is nothing to be ready FOR.
    open({ envPolicy: { VPN_KEY: "private_only" } });
    await toolsLoaded();

    const head = screen.getByTestId("env-section-head-__other");
    expect(head).not.toHaveAttribute("data-status");
    expect(within(head).queryByText("✓")).toBeNull();
  });
});

describe("signing in from Only me", () => {
  it("puts what the login returned into the person's own values, not the shared ones", async () => {
    const onSave = vi.fn();
    const privateClient = {
      get: vi.fn(async () => ({ values: {}, auto: {} })),
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
    };
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        envPolicy={{ ERP_TOKEN: "private_only" }}
        onSave={onSave}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => [
            {
              id: "erp-login",
              label: "ERP login",
              produces: ["ERP_TOKEN"],
              inputs: [{ name: "password", label: "Password", secret: true }],
            },
          ]),
          resolveEnvProvider: vi.fn(async () => ({ ERP_TOKEN: "from-login" })),
        }}
        privateClient={privateClient}
      />,
    );

    fireEvent.click(await screen.findByTestId("env-provider-erp-login"));
    fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByTestId("env-cred-submit"));
    await waitFor(() =>
      expect((screen.getByTestId("env-mine-ERP_TOKEN") as HTMLInputElement).value).toBe(
        "from-login",
      ),
    );
    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() =>
      expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "from-login" }),
    );
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe("review round 1: saving and leaving", () => {
  it("does not let Only-me save over values it failed to load", async () => {
    // F5: the fallback was {} and PUT replaces the whole set — a transient
    // read failure, one typed value, Save, and every stored value was gone.
    const onSave = vi.fn();
    const privateClient = {
      get: vi.fn(async () => {
        throw new Error("503");
      }),
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
    };
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={onSave}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={privateClient}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("env-mine-save")).toBeDisabled());
    fireEvent.click(screen.getByTestId("env-mine-save"));
    expect(privateClient.put).not.toHaveBeenCalled();
  });

  it("clearing a value removes it rather than storing an empty one", async () => {
    // F7: "" is a value — under private_first it would override the shared
    // one the person meant to fall back to.
    const { privateClient } = open({ envPolicy: { ERP_TOKEN: "private_first" }, mine: { ERP_TOKEN: "t" } });
    await toolsLoaded();
    unfold("erp");
    await waitFor(() =>
      expect((screen.getByTestId("env-mine-ERP_TOKEN") as HTMLInputElement).value).toBe("t"),
    );

    fireEvent.change(screen.getByTestId("env-mine-ERP_TOKEN"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() => expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", {}));
  });

  it("saving Only-me keeps the panel open while Everyone has unsaved edits", async () => {
    // C5/F6: one tab's Save closed the modal and dropped the other tab's work,
    // without asking — #779 says every deliberate exit goes through the guard.
    const onClose = vi.fn();
    const privateClient = {
      get: vi.fn(async () => ({ values: {}, auto: {} })),
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
    };
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={vi.fn()}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={privateClient}
      />,
    );
    await toolsLoaded();
    everyone();
    fireEvent.click(screen.getByTestId("env-policy-ERP_TOKEN-private_only"));
    fireEvent.click(screen.getByTestId("env-tab-mine"));

    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() => expect(privateClient.put).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("env-tab-shared")).toHaveAttribute("aria-selected", "true");
  });

  it("saving Everyone keeps the panel open while Only-me has unsaved edits", async () => {
    const onClose = vi.fn();
    const onSave = vi.fn();
    const privateClient = {
      get: vi.fn(async () => ({ values: {}, auto: {} })),
      put: vi.fn(async () => {}),
      clear: vi.fn(async () => {}),
    };
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        envPolicy={{ ERP_TOKEN: "private_only" }}
        onSave={onSave}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={privateClient}
      />,
    );
    await toolsLoaded();
    fireEvent.change(screen.getByTestId("env-mine-ERP_TOKEN"), { target: { value: "t" } });
    everyone();

    fireEvent.click(screen.getByTestId("env-save"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("env-tab-mine")).toHaveAttribute("aria-selected", "true");
  });

  it("asks before Cancel drops a changed policy", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={vi.fn()}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={{
          get: vi.fn(async () => ({ values: {}, auto: {} })),
          put: vi.fn(async () => {}),
          clear: vi.fn(async () => {}),
        }}
      />,
    );
    await toolsLoaded();
    everyone();
    fireEvent.click(screen.getByTestId("env-policy-ERP_TOKEN-private_only"));

    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(await screen.findByTestId("dialog-action-discard")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("asks before Cancel drops an edited value of one's own", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        envPolicy={{ ERP_TOKEN: "private_only" }}
        onSave={vi.fn()}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={{
          get: vi.fn(async () => ({ values: {}, auto: {} })),
          put: vi.fn(async () => {}),
          clear: vi.fn(async () => {}),
        }}
      />,
    );
    await toolsLoaded();
    fireEvent.change(screen.getByTestId("env-mine-ERP_TOKEN"), { target: { value: "t" } });

    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(await screen.findByTestId("dialog-action-discard")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on Cancel without asking when neither tab was touched", async () => {
    const onClose = vi.fn();
    open({ mine: { ERP_TOKEN: "t" } });
    // `open` wires its own onClose; drive a fresh one to observe it.
    cleanup();
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={vi.fn()}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={{
          get: vi.fn(async () => ({ values: { ERP_TOKEN: "t" }, auto: {} })),
          put: vi.fn(async () => {}),
          clear: vi.fn(async () => {}),
        }}
      />,
    );
    await toolsLoaded();

    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(onClose).toHaveBeenCalled();
  });

  it("shows what the deploy filled in as in use, and not editable", async () => {
    open({ tools: [], auto: { SSO_TOKEN: "auto" } });

    const row = await screen.findByTestId("env-mine-row-SSO_TOKEN");
    expect(row).toHaveAttribute("data-in-use", "mine");
    expect(screen.getByTestId("env-auto-SSO_TOKEN")).toBeInTheDocument();
    expect(screen.queryByTestId("env-mine-SSO_TOKEN")).not.toBeInTheDocument();
  });
});

describe("review round 2", () => {
  it("stays open with the edits when saving the shared values fails", async () => {
    // A failed PATCH resolved like a success, and the panel closed over the
    // edits it had not stored.
    const onClose = vi.fn();
    const onSave = vi.fn(async () => false);
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={onSave}
        onClose={onClose}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => [ERP]),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
        privateClient={{
          get: vi.fn(async () => ({ values: {}, auto: {} })),
          put: vi.fn(async () => {}),
          clear: vi.fn(async () => {}),
        }}
      />,
    );
    await toolsLoaded();
    everyone();
    fireEvent.click(screen.getByTestId("env-policy-ERP_TOKEN-private_only"));

    fireEvent.click(screen.getByTestId("env-save"));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });
});
