// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ItemEnvironmentModal } from "./ItemEnvironmentModal";
import { renderWithQuery } from "../test/queryWrapper";

/**
 * The modal owns the two drafts and the one Save that commits them. That is
 * the change the blur-to-save panel could not make: with a Save button,
 * leaving means what it means everywhere else in the app (#779 — a dirty
 * modal asks once, a clean one just closes), and nothing is written by a
 * keystroke that only moved focus.
 */

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

const ENVIRONMENT = {
  running: false,
  stated_cpu_cores: null,
  stated_memory_bytes: null,
  effective_cpu_cores: 2,
  effective_memory_bytes: 512 * 1024 * 1024,
  enforced_cpu_cores: 2,
  enforced_memory_bytes: 512 * 1024 * 1024,
  cpu_bound_by: null,
  memory_bound_by: null,
};
const RUNNING = { ...ENVIRONMENT, running: true };
const STATED = { ...ENVIRONMENT, stated_cpu_cores: 1, stated_memory_bytes: 256 * 1024 * 1024 };

const CAPPED = {
  limits: { count: 0, cpu: 4, memory_bytes: 8 * 1024 ** 3, disk_bytes: 0 },
  cpu_in_use: 2,
  memory_in_use: 512 * 1024 * 1024,
  live: [],
  workspaces: [],
  disk_in_use: 0,
  disk_tracked: false,
  owner: "alice",
};
const UNCAPPED = { ...CAPPED, limits: { count: 0, cpu: 0, memory_bytes: 0, disk_bytes: 0 } };

function route(
  resources: unknown,
  environment: unknown = ENVIRONMENT,
  { refuseSave = false, hangLoad = false } = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") {
      return refuseSave
        ? new Response(JSON.stringify({ detail: "sandbox_quota_exceeded" }), { status: 507 })
        : json({});
    }
    if (init?.method === "POST" && url.includes("/close")) return json({});
    if (url.includes("/environment")) {
      if (hangLoad) return new Promise<Response>(() => {});
      return json(environment);
    }
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

const puts = (f: ReturnType<typeof route>) =>
  f.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT")
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as { cpu_cores: unknown; memory: unknown });

function open(props: Partial<React.ComponentProps<typeof ItemEnvironmentModal>> = {}) {
  const onClose = vi.fn();
  const { client } = renderWithQuery(
    <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} {...props} />,
  );
  return Object.assign(onClose, { client });
}

beforeEach(() => {
  vi.stubGlobal("fetch", route(CAPPED));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ItemEnvironmentModal — frame", () => {
  it("draws the size half where the deploy caps somebody", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("budget-gauge")).toBeTruthy());
    expect(screen.getByTestId("cpu-input")).toBeTruthy();
    expect(screen.getByTestId("memory-input")).toBeTruthy();
  });

  it("omits the size half where it caps nobody, and keeps the status half", async () => {
    vi.stubGlobal("fetch", route(UNCAPPED));
    open();
    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.queryByTestId("budget-gauge")).toBeNull();
    expect(screen.queryByTestId("cpu-input")).toBeNull();
    // Nothing to save → no Save; the one way out is Close.
    expect(screen.queryByTestId("itemenv-save")).toBeNull();
    expect(screen.getByTestId("itemenv-close-panel")).toBeTruthy();
  });

  it("is a real modal — it dims what is behind it and answers Escape", async () => {
    const onClose = open();
    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.getByTestId("item-environment-modal-backdrop")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("is a modal from the moment it opens, not once the fetch lands", () => {
    vi.stubGlobal("fetch", route(CAPPED, ENVIRONMENT, { hangLoad: true }));
    open();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByTestId("item-environment-pending")).toBeTruthy();
  });

  it("asks the item's route for the item, and the person's for the total", async () => {
    const f = route(CAPPED);
    vi.stubGlobal("fetch", f);
    open();
    await waitFor(() => expect(screen.getByTestId("budget-gauge")).toBeTruthy());
    const urls = f.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/items/i-1/environment"))).toBe(true);
    expect(urls.some((u) => u.includes("/me/resources"))).toBe(true);
  });

  it("names both totals — CPU and memory — as tiles, not one unnamed bar", async () => {
    open();
    const gauge = await screen.findByTestId("budget-gauge");
    expect(gauge.querySelectorAll('[role="progressbar"]')).toHaveLength(2);
    expect(gauge).toHaveTextContent("2 / 4");
    expect(gauge).toHaveTextContent("512.0 MB / 8.0 GB");
  });
});

describe("ItemEnvironmentModal — Save", () => {
  it("enables Save once a field differs from what the modal opened with, and sends BOTH dimensions in one PUT", async () => {
    const f = route(CAPPED, STATED);
    vi.stubGlobal("fetch", f);
    open();
    const cpu = await screen.findByTestId("cpu-input");
    const save = screen.getByTestId("itemenv-save");
    expect(save).toBeDisabled();

    fireEvent.change(cpu, { target: { value: "2" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(puts(f)).toHaveLength(1));
    // The untouched dimension travels with its stated value — the route
    // REPLACES both, so sending only the edit would have cleared memory.
    expect(puts(f)[0]).toEqual({ cpu_cores: 2, memory: "256M" });
  });

  it("a clean Cancel (and a clean Escape) closes without a prompt and writes nothing", async () => {
    const f = route(CAPPED);
    vi.stubGlobal("fetch", f);
    const onClose = open();
    await screen.findByTestId("cpu-input");
    fireEvent.click(screen.getByTestId("itemenv-cancel"));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("dialog-action-discard")).toBeNull();
    expect(puts(f)).toHaveLength(0);
  });

  it("a dirty Cancel asks once; keeping stays open, discarding closes — and nothing is sent either way", async () => {
    const f = route(CAPPED);
    vi.stubGlobal("fetch", f);
    const onClose = open();
    const cpu = await screen.findByTestId("cpu-input");
    fireEvent.change(cpu, { target: { value: "3" } });

    fireEvent.click(screen.getByTestId("itemenv-cancel"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("cpu-input")).toHaveValue(3);

    fireEvent.click(screen.getByTestId("itemenv-cancel"));
    fireEvent.click(await screen.findByTestId("dialog-action-discard"));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(puts(f)).toHaveLength(0);
  });

  it("a dirty Escape asks the same question — every deliberate exit goes through the guard", async () => {
    const onClose = open();
    const cpu = await screen.findByTestId("cpu-input");
    fireEvent.change(cpu, { target: { value: "3" } });
    await userEvent.keyboard("{Escape}");
    await screen.findByTestId("dialog-action-discard");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("'Back to default' is an edit like any other: it dirties the modal and Save sends null", async () => {
    const f = route(CAPPED, STATED);
    vi.stubGlobal("fetch", f);
    open();
    await screen.findByTestId("cpu-input");
    fireEvent.click(screen.getByTestId("reset-cpu"));
    expect(screen.getByTestId("cpu-input")).toHaveValue(null);
    const save = screen.getByTestId("itemenv-save");
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(puts(f)).toHaveLength(1));
    expect(puts(f)[0]).toEqual({ cpu_cores: null, memory: "256M" });
  });

  it("locks the size while the sandbox runs — inputs and Save disabled, 'Close sandbox' offered", async () => {
    const f = route(CAPPED, RUNNING);
    vi.stubGlobal("fetch", f);
    open();
    const cpu = await screen.findByTestId("cpu-input");
    expect(cpu).toBeDisabled();
    expect(screen.getByTestId("memory-input")).toBeDisabled();
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
    expect(screen.getByTestId("close-environment")).toBeTruthy();
  });

  it("disables Save when the sandbox starts under a draft — the size cannot be applied to a running one", async () => {
    // The only way to a dirty draft while running: type while idle, then the
    // sandbox starts (a turn's exec, another pod) and the record refetches.
    // Save must go grey with the draft kept, not send a size the protocol
    // cannot apply.
    let environment: unknown = ENVIRONMENT;
    const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return json({});
      if (url.includes("/environment")) return json(environment);
      if (url.includes("/me/resources")) return json(CAPPED);
      return json({});
    });
    vi.stubGlobal("fetch", f);
    const onClose = open();
    const cpu = await screen.findByTestId("cpu-input");
    fireEvent.change(cpu, { target: { value: "3" } });
    expect(screen.getByTestId("itemenv-save")).toBeEnabled();

    environment = RUNNING;
    await onClose.client.invalidateQueries({ queryKey: ["item-environment", "rca", "i-1"] });
    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeDisabled());
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
    expect(screen.getByTestId("cpu-input")).toHaveValue(3);
    expect(puts(f)).toHaveLength(0);
  });

  it("is read-only for someone who may look but not spend — no Save, no Cancel, a Close, and says why", async () => {
    open({ canEdit: false });
    const cpu = await screen.findByTestId("cpu-input");
    expect(cpu).toBeDisabled();
    expect(screen.queryByTestId("itemenv-save")).toBeNull();
    expect(screen.queryByTestId("itemenv-cancel")).toBeNull();
    expect(screen.getByTestId("itemenv-close-panel")).toBeTruthy();
    expect(screen.getByText(/擁有者的額度|owner's quota/)).toBeTruthy();
  });

  it("says so when the server refuses the size, and stays open with the draft", async () => {
    const f = route(CAPPED, ENVIRONMENT, { refuseSave: true });
    vi.stubGlobal("fetch", f);
    const onClose = open();
    const cpu = await screen.findByTestId("cpu-input");
    fireEvent.change(cpu, { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("itemenv-save"));
    const alert = await screen.findByTestId("save-failed");
    expect(alert).toHaveAttribute("role", "alert");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("cpu-input")).toHaveValue(3);
  });
});
