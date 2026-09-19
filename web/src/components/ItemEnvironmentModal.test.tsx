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
    if (init?.method === "DELETE" && url.includes("/me/resources/live/")) return json({});
    if (url.includes("/environment")) {
      if (hangLoad) return new Promise<Response>(() => {});
      return json(environment);
    }
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

/** Like `route`, but the environment is read from a mutable holder and a PUT
 *  can be held open — for the sequences the review found untested. */
function liveRoute(holder: { env: unknown; resources?: unknown; hangPut?: boolean; failReload?: boolean }) {
  let puts = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") {
      puts += 1;
      if (holder.hangPut) return new Promise<Response>(() => {});
      return json({});
    }
    if (init?.method === "DELETE" && url.includes("/me/resources/live/")) return json({});
    if (url.includes("/environment")) {
      if (holder.failReload && puts > 0) return new Response("nope", { status: 500 });
      return json(holder.env);
    }
    if (url.includes("/me/resources")) return json(holder.resources ?? CAPPED);
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

describe("ItemEnvironmentModal — what the review found unguarded", () => {
  it("after a successful Save the draft is gone: Save goes grey and a Cancel closes without a prompt", async () => {
    const holder = { env: STATED };
    const f = liveRoute(holder);
    vi.stubGlobal("fetch", f);
    const onClose = open();
    fireEvent.change(await screen.findByTestId("cpu-input"), { target: { value: "2" } });
    holder.env = { ...STATED, stated_cpu_cores: 2 }; // what the server now says
    fireEvent.click(screen.getByTestId("itemenv-save"));
    await waitFor(() => expect(screen.getByTestId("itemenv-save")).toBeDisabled());
    expect(screen.getByTestId("cpu-input")).toHaveValue(2);
    fireEvent.click(screen.getByTestId("itemenv-cancel"));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("dialog-action-discard")).toBeNull();
  });

  it("Save sends the OTHER dimension as the server holds it NOW, not as it was at the first keystroke", async () => {
    // Two change_permission holders: this one types a cpu value, the other
    // one saves a memory size meanwhile (the record refetches). Save must
    // carry the other's memory, not a copy taken when typing began.
    const holder = { env: STATED };
    const f = liveRoute(holder);
    vi.stubGlobal("fetch", f);
    const onClose = open();
    fireEvent.change(await screen.findByTestId("cpu-input"), { target: { value: "2" } });
    holder.env = { ...STATED, stated_memory_bytes: 1024 ** 3 };
    await onClose.client.invalidateQueries({ queryKey: ["item-environment", "rca", "i-1"] });
    await waitFor(() => expect(screen.getByTestId("memory-input")).toHaveValue("1G"));
    fireEvent.click(screen.getByTestId("itemenv-save"));
    await waitFor(() => expect(puts(f)).toHaveLength(1));
    expect(puts(f)[0]).toEqual({ cpu_cores: 2, memory: "1G" });
  });

  it("one Save is one PUT, however fast the second click comes", async () => {
    const holder = { env: STATED, hangPut: true };
    const f = liveRoute(holder);
    vi.stubGlobal("fetch", f);
    open();
    fireEvent.change(await screen.findByTestId("cpu-input"), { target: { value: "2" } });
    const save = screen.getByTestId("itemenv-save");
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(save).toBeDisabled());
    expect(puts(f)).toHaveLength(1);
  });

  it("says so when the record cannot be re-read after a Save, instead of showing the old numbers as if they were new", async () => {
    const holder = { env: STATED, failReload: true };
    vi.stubGlobal("fetch", liveRoute(holder));
    open();
    fireEvent.change(await screen.findByTestId("cpu-input"), { target: { value: "2" } });
    fireEvent.click(screen.getByTestId("itemenv-save"));
    const note = await screen.findByTestId("reload-failed");
    expect(note).toHaveAttribute("role", "alert");
  });

  it("'Close sandbox' sends the close and re-reads both the item and the person's totals", async () => {
    const f = route(CAPPED, RUNNING);
    vi.stubGlobal("fetch", f);
    open();
    const before = f.mock.calls.length;
    fireEvent.click(await screen.findByTestId("close-environment"));
    await waitFor(() =>
      expect(
        f.mock.calls.some(
          (c) => (c[1] as RequestInit | undefined)?.method === "DELETE" && String(c[0]).includes("/me/resources/live/i-1"),
        ),
      ).toBe(true),
    );
    await waitFor(() => {
      const after = f.mock.calls.slice(before).map((c) => String(c[0]));
      expect(after.some((u) => u.includes("/environment"))).toBe(true);
      expect(after.some((u) => u.includes("/me/resources"))).toBe(true);
    });
  });

  it("refuses, client-side, what the server would refuse: a memory spelling it cannot parse, or a cpu of 0", async () => {
    // A 422 only says "not saved", which leaves the person guessing at the
    // grammar; so what the server would refuse is refused here, with the
    // grammar under the field.
    open();
    const memory = await screen.findByTestId("memory-input");
    expect(memory).toHaveAttribute("placeholder", "512M");
    fireEvent.change(memory, { target: { value: "512 MiB" } });
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
    expect(memory).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("memory-hint")).toBeTruthy();
    fireEvent.change(memory, { target: { value: "512m" } });
    expect(screen.getByTestId("itemenv-save")).toBeEnabled();
    expect(memory).not.toHaveAttribute("aria-invalid", "true");
    // "MB" and "M" are both fine — likewise KB/GB/TB — and so is the
    // display format typed back. What travels is the server's spelling.
    fireEvent.change(memory, { target: { value: "512.0 MB" } });
    expect(screen.getByTestId("itemenv-save")).toBeEnabled();
    expect(memory).not.toHaveAttribute("aria-invalid", "true");
    fireEvent.change(memory, { target: { value: "1.5 GB" } });
    expect(screen.getByTestId("itemenv-save")).toBeEnabled();

    const cpu = screen.getByTestId("cpu-input");
    fireEvent.change(cpu, { target: { value: "0" } });
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
    expect(cpu).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(cpu, { target: { value: "0.5" } });
    expect(screen.getByTestId("itemenv-save")).toBeEnabled();
  });

  it("hides 'Back to default' while the size is locked — running, or a viewer who may not spend", async () => {
    vi.stubGlobal("fetch", route(CAPPED, { ...STATED, running: true }));
    open();
    await screen.findByTestId("cpu-input");
    expect(screen.queryByTestId("reset-cpu")).toBeNull();
    expect(screen.queryByTestId("reset-memory")).toBeNull();
    cleanup();
    vi.stubGlobal("fetch", route(CAPPED, STATED));
    open({ canEdit: false });
    await screen.findByTestId("cpu-input");
    expect(screen.queryByTestId("reset-cpu")).toBeNull();
  });
});

describe("ItemEnvironmentModal — memory spellings", () => {
  it("sends the server's spelling whatever the person wrote: 1.5 GB travels as 1536M", async () => {
    const f = route(CAPPED, STATED);
    vi.stubGlobal("fetch", f);
    open();
    fireEvent.change(await screen.findByTestId("memory-input"), { target: { value: "1.5 GB" } });
    fireEvent.click(screen.getByTestId("itemenv-save"));
    await waitFor(() => expect(puts(f)).toHaveLength(1));
    expect(puts(f)[0]).toEqual({ cpu_cores: 1, memory: "1536M" });
  });
});

