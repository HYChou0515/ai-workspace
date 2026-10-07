/**
 * `docs/plan-personal-env.md` A22 — what the Env panel looks like and says,
 * after the user read it (2026-10-08): the tabs looked like three buttons,
 * the copy used three nouns for one thing and talked about settings a viewer
 * may not be able to change, the private tabs had no `.env` box, and clearing
 * a person's values was neither marked as destructive nor confirmed.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { makeQueryClient } from "../api/queryClient";
import { LocaleProvider, setStoredLocale } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(() => {
  cleanup();
  setStoredLocale("zh-TW");
});

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

function open({
  envVars = {} as Record<string, string>,
  envPolicy = { ERP_TOKEN: "private_first" } as Record<string, string>,
  mine = {} as Record<string, string>,
  auto = {} as Record<string, string>,
  personal = {} as Record<string, string>,
  english = false,
  mineRead = undefined as undefined | (() => Promise<{ values: Record<string, string>; auto: Record<string, string> }>),
  providers = [] as { id: string; label: string; produces: string[]; inputs: [] }[],
} = {}) {
  const privateClient = {
    get: vi.fn(mineRead ?? (async () => ({ values: mine, auto }))),
    put: vi.fn(async () => {}),
    clear: vi.fn(async () => {}),
  };
  const personalClient = {
    get: vi.fn(async () => ({ values: personal, updated: {} as Record<string, number> })),
    put: vi.fn(async (values: Record<string, string>) => ({ values, updated: {} })),
  };
  if (english) setStoredLocale("en");
  const Wrap = english ? LocaleProvider : ({ children }: { children: React.ReactNode }) => <>{children}</>;
  const onClose = vi.fn();
  renderWithQuery(
    <Wrap>
    <EnvVarsModal
      envVars={envVars}
      envPolicy={envPolicy}
      onSave={vi.fn()}
      onClose={onClose}
      slug="rca"
      itemId="i1"
      client={{
        getItemTools: vi.fn(async () => ({ tools: [ERP], updateNeedsClose: false, canClose: false })),
        getEnvProviders: vi.fn(async () => providers),
        resolveEnvProvider: vi.fn(async () => ({ ERP_TOKEN: "fresh" })),
      }}
      privateClient={privateClient}
      personalClient={personalClient}
    />
    </Wrap>,
    makeQueryClient(),
  );
  return { privateClient, personalClient, onClose };
}

const tab = (id: string) => screen.findByTestId(`env-tab-${id}`);
/** The clear button waits for this workspace's values to be read (A22). */
async function enabled(testId: string) {
  const b = await screen.findByTestId(testId);
  await waitFor(() => expect(b).not.toBeDisabled());
  return b;
}

/** A ready section folds; unfold it to read the row. */
async function unfold() {
  const head = await screen.findByTestId("env-section-head-erp");
  if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
}

describe("the tabs", () => {
  it("are drawn as tabs — the share dialog's strip — not as three buttons", async () => {
    // A filled button is how this app draws its PRIMARY ACTION; a selected tab
    // drawn that way read as "press me", not "you are here".
    open();
    const t = await tab("mine");
    expect(t).not.toHaveClass("btn");
    expect(t.closest('[role="tablist"]')).not.toBeNull();
    expect(t).toHaveAttribute("aria-selected", "true");
  });
});

describe("the copy", () => {
  it("says what each private tab holds, in one noun", async () => {
    open();
    await tab("mine");
    expect(await screen.findByText("你在這個 workspace 的值")).toBeInTheDocument();

    fireEvent.click(await tab("personal"));
    expect(await screen.findByText(/你在所有 workspace 共用的值/)).toBeInTheDocument();
    // The way to the page that holds them IS the sentence's own link.
    expect(screen.getByTestId("env-personal-page")).toHaveTextContent("前往我的環境變數設定");
    expect(screen.getByTestId("env-personal-page")).toHaveAttribute("href", "/my-env");
  });

  it("puts the link inside the sentence in each language's own brackets", async () => {
    // Review A22: the brackets were literal full-width text in the JSX, so the
    // English line read "workspaces（set them in …）".
    open({ english: true });
    fireEvent.click(await tab("personal"));
    const p = (await screen.findByTestId("env-personal-page")).closest("p")!;
    expect(p.textContent).toContain("(");
    expect(p.textContent).not.toContain("（");
  });

  it("names the tab whose value is in use", async () => {
    open({ envVars: { ERP_TOKEN: "s" }, personal: { ERP_TOKEN: "p" } });
    await unfold();
    expect(await screen.findByTestId("env-mine-row-ERP_TOKEN")).toHaveTextContent("使用中：Private(跨workspace)");
  });

  it("says what typing here would override, and nothing once this tab holds the value", async () => {
    open({ envPolicy: { ERP_TOKEN: "private_first" }, envVars: { ERP_TOKEN: "s" } });
    await unfold();
    const row = await screen.findByTestId("env-mine-row-ERP_TOKEN");
    expect(row).toHaveTextContent("覆蓋 Shared");

    fireEvent.change(within(row).getByTestId("env-mine-ERP_TOKEN"), { target: { value: "mine" } });
    expect(row).not.toHaveTextContent("覆蓋");
    expect(row).toHaveTextContent("使用中：Private");
  });

  it("names the cross-workspace tab when that is what typing here would override", async () => {
    open({ envPolicy: { ERP_TOKEN: "private_first" }, personal: { ERP_TOKEN: "p" } });
    await unfold();
    expect(await screen.findByTestId("env-mine-row-ERP_TOKEN")).toHaveTextContent("覆蓋 Private(跨workspace)");
  });

  it("does not tell the cross-workspace tab's reader about a setting they may not be able to change", async () => {
    // The user, on the old per-row "uses it / doesn't (its setting is Shared)":
    // the Private tab already says which value is in use.
    open({ envPolicy: {}, personal: { ERP_TOKEN: "p" } });
    fireEvent.click(await tab("personal"));
    const row = await screen.findByTestId("env-personal-row-ERP_TOKEN");
    expect(row).not.toHaveTextContent("Shared");
    expect(row).not.toHaveTextContent("Private first");
  });
});

describe("clearing my values for this workspace", () => {
  it("cannot be pressed when there is nothing of mine to clear", async () => {
    // Review A22: it asked to delete values that were not there.
    open();
    await waitFor(() => expect(screen.getByTestId("env-mine-logout")).toBeDisabled());
  });

  it("does not promise to delete what comes back by itself", async () => {
    // What the deploy filled in automatically returns on the next use, so the
    // confirm must not say everything is gone for good (review A22).
    open({ mine: { ERP_TOKEN: "x" } });
    fireEvent.click(await enabled("env-mine-logout"));
    const body = (await screen.findByTestId("dialog-action-clear")).closest('[role="dialog"]')!;
    expect(body).toHaveTextContent("填的");
    expect(body).toHaveTextContent("登入");
  });

  it("is marked as destructive and asks first, naming what it does", async () => {
    // NN/g "Confirmation Dialogs": confirm an action that cannot be undone, and
    // label the choice with its outcome; GOV.UK "Warning button": red is for
    // serious, hard-to-undo actions, with a step to confirm.
    const { privateClient } = open({ mine: { ERP_TOKEN: "x" } });
    const clear = await enabled("env-mine-logout");
    expect(clear).toHaveAttribute("data-variant", "danger");

    fireEvent.click(clear);
    const confirm = await screen.findByTestId("dialog-action-clear");
    expect(confirm).toHaveTextContent("清除");
    expect(confirm).toHaveAttribute("data-variant", "danger");
    expect(privateClient.clear).not.toHaveBeenCalled();

    fireEvent.click(confirm);
    await waitFor(() => expect(privateClient.clear).toHaveBeenCalled());
  });

  it("does nothing when the person changes their mind", async () => {
    const { privateClient } = open({ mine: { ERP_TOKEN: "x" } });
    fireEvent.click(await enabled("env-mine-logout"));
    fireEvent.click(await screen.findByTestId("dialog-action-cancel"));
    await new Promise((r) => setTimeout(r, 20));
    expect(privateClient.clear).not.toHaveBeenCalled();
  });
});

describe("the .env box on the private tabs", () => {
  it("on Private: shows my values for this workspace as .env text, and saves what is pasted", async () => {
    const { privateClient } = open({ mine: { ERP_TOKEN: "old" } });
    const box = (await screen.findByTestId("env-mine-text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("ERP_TOKEN=old"));

    fireEvent.change(box, { target: { value: "ERP_TOKEN=new\nOTHER=o\n" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() =>
      expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "new", OTHER: "o" }),
    );
  });

  it("on Private: a line taken out of the box clears that value", async () => {
    const { privateClient } = open({ mine: { ERP_TOKEN: "old", GONE: "g" } });
    const box = (await screen.findByTestId("env-mine-text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("GONE=g"));

    fireEvent.change(box, { target: { value: "ERP_TOKEN=new\n" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));

    await waitFor(() => expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "new" }));
  });

  it("keeps a line that is still being typed in the box", async () => {
    // The box shows the values as text; a half-typed line parses to nothing,
    // and redrawing from the values would erase it under the cursor.
    open({ mine: { ERP_TOKEN: "old" } });
    const box = (await screen.findByTestId("env-mine-text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("ERP_TOKEN=old"));

    fireEvent.change(box, { target: { value: "ERP_TOKEN=old\nNEW_KE" } });
    expect(box.value).toBe("ERP_TOKEN=old\nNEW_KE");
  });

  it("on Private: a field and the box are one copy", async () => {
    open({ mine: { ERP_TOKEN: "old" } });
    await unfold();
    fireEvent.change(await screen.findByTestId("env-mine-ERP_TOKEN"), { target: { value: "typed" } });
    expect(((await screen.findByTestId("env-mine-text")) as HTMLTextAreaElement).value).toContain("ERP_TOKEN=typed");
  });

  it("on Private(跨workspace): saves what is pasted, without touching names it did not change", async () => {
    const { personalClient } = open({ personal: { ERP_TOKEN: "old", KEEP: "k" } });
    fireEvent.click(await tab("personal"));
    const box = (await screen.findByTestId("env-personal-text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("ERP_TOKEN=old"));
    // Saved from elsewhere since the panel opened: kept.
    personalClient.get.mockResolvedValue({ values: { ERP_TOKEN: "old", KEEP: "k", LATER: "l" }, updated: {} });

    fireEvent.change(box, { target: { value: "ERP_TOKEN=new\nKEEP=k\nADDED=a\n" } });
    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() =>
      expect(personalClient.put).toHaveBeenCalledWith({ ERP_TOKEN: "new", KEEP: "k", LATER: "l", ADDED: "a" }),
    );
  });

  it("on Private(跨workspace): a line taken out of the box removes that value", async () => {
    const { personalClient } = open({ personal: { ERP_TOKEN: "old", GONE: "g" } });
    fireEvent.click(await tab("personal"));
    const box = (await screen.findByTestId("env-personal-text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("GONE=g"));

    fireEvent.change(box, { target: { value: "ERP_TOKEN=old\n" } });
    fireEvent.click(screen.getByTestId("env-personal-save"));

    await waitFor(() => expect(personalClient.put).toHaveBeenCalledWith({ ERP_TOKEN: "old" }));
  });
});

describe("the private tabs keep what was typed, exactly (review A22: the box as the one copy)", () => {
  // Making the `.env` text the one copy of a private tab parsed every value
  // through the text format: it trims, and cannot hold a line break.

  it("takes a space typed into a field on Private", async () => {
    open({ mine: { ERP_TOKEN: "" } });
    await unfold();
    const f = (await screen.findByTestId("env-mine-ERP_TOKEN")) as HTMLInputElement;
    fireEvent.change(f, { target: { value: "a" } });
    fireEvent.change(f, { target: { value: "a " } });
    expect(f.value).toBe("a ");
  });

  it("takes a space typed into a field on Private(跨workspace)", async () => {
    open();
    fireEvent.click(await tab("personal"));
    const f = (await screen.findByTestId("env-personal-ERP_TOKEN")) as HTMLInputElement;
    fireEvent.change(f, { target: { value: "a" } });
    fireEvent.change(f, { target: { value: "a " } });
    expect(f.value).toBe("a ");
  });

  it("does not call a stored value the text format cannot hold an unsaved change", async () => {
    const { onClose } = open({ mine: { ERP_TOKEN: "tok " } });
    await unfold();
    await screen.findByTestId("env-mine-ERP_TOKEN");
    fireEvent.click(screen.getByTestId("env-cancel"));
    expect(onClose).toHaveBeenCalled();
  });

  it("saves such a value back as it was when another field changes", async () => {
    const { privateClient } = open({
      mine: { ERP_TOKEN: "x", PEM: "line1\nline2" },
    });
    await unfold();
    fireEvent.change(await screen.findByTestId("env-mine-ERP_TOKEN"), { target: { value: "y" } });
    fireEvent.click(screen.getByTestId("env-mine-save"));
    await waitFor(() =>
      expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { ERP_TOKEN: "y", PEM: "line1\nline2" }),
    );
  });

  it("writes nothing on Private(跨workspace) for a stored value the text format cannot hold", async () => {
    const { personalClient } = open({ personal: { ERP_TOKEN: "tok " } });
    fireEvent.click(await tab("personal"));
    await screen.findByTestId("env-personal-ERP_TOKEN");
    fireEvent.click(screen.getByTestId("env-personal-save"));
    await new Promise((r) => setTimeout(r, 30));
    expect(personalClient.put).not.toHaveBeenCalled();
  });

  it("on Private(跨workspace): typing a value then clearing it, for a name never set, changes nothing", async () => {
    // Review A22, F4: it counted as unsaved and Save sent a whole-row PUT.
    const { onClose, personalClient } = open();
    fireEvent.click(await tab("personal"));
    const f = await screen.findByTestId("env-personal-ERP_TOKEN");
    fireEvent.change(f, { target: { value: "x" } });
    fireEvent.change(f, { target: { value: "" } });

    fireEvent.click(screen.getByTestId("env-cancel"));
    expect(onClose).toHaveBeenCalled();
    expect(personalClient.put).not.toHaveBeenCalled();
  });

  it("cannot save Private over values it has not read", async () => {
    const { privateClient } = open({ mineRead: () => new Promise(() => {}) });
    const box = (await screen.findByTestId("env-mine-text")) as HTMLTextAreaElement;
    expect(box).toHaveAttribute("readonly");
    expect(screen.getByTestId("env-mine-save")).toBeDisabled();
    expect(privateClient.put).not.toHaveBeenCalled();
  });

  it("keeps the stored values when a sign-in lands before they were read", async () => {
    let release: (v: { values: Record<string, string>; auto: Record<string, string> }) => void = () => {};
    const { privateClient } = open({
      mineRead: () => new Promise((r) => (release = r)),
      providers: [{ id: "sap", label: "SAP", produces: ["ERP_TOKEN"], inputs: [] }],
    });
    fireEvent.click(await screen.findByTestId("env-provider-sap"));
    fireEvent.click(screen.getByTestId("env-cred-submit"));
    await new Promise((r) => setTimeout(r, 20));
    release({ values: { KEEP: "k" }, auto: {} });

    await waitFor(() => expect(screen.getByTestId("env-mine-save")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("env-mine-save"));
    await waitFor(() =>
      expect(privateClient.put).toHaveBeenCalledWith("rca", "i1", { KEEP: "k", ERP_TOKEN: "fresh" }),
    );
  });
});
