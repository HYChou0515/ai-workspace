// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { ItemEnvironmentModal } from "./ItemEnvironmentModal";

/**
 * docs/plan-item-disk-size.md P2 — the disk field on the Sandbox modal.
 *
 * Disk is not the sandbox's: every write reads it, so it may change while the
 * environment runs (D5) and needs no per-person budget to be drawn. It is
 * saved on its own route; the cpu/memory PUT is sent only when THOSE changed.
 */

const MB = 1024 * 1024;
const GB = 1024 * MB;
const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

const ENV = {
  running: false,
  stated_cpu_cores: null,
  stated_memory_bytes: null,
  effective_cpu_cores: 2,
  effective_memory_bytes: 512 * MB,
  enforced_cpu_cores: 2,
  enforced_memory_bytes: 512 * MB,
  cpu_bound_by: null,
  memory_bound_by: null,
  max_cpu_cores: 1024,
  max_memory_bytes: 1024 ** 5,
  stated_disk_bytes: null,
  effective_disk_bytes: 1 * GB,
  disk_used_bytes: 300 * MB,
  disk_ceiling: 20 * GB,
  owner_disk_left: 40 * GB,
};

const CAPPED = {
  limits: { count: 0, cpu: 4, memory_bytes: 8 * GB, disk_bytes: 0 },
  cpu_in_use: 2,
  memory_in_use: 512 * MB,
  live: [],
  workspaces: [],
  disk_in_use: 0,
  owner: "alice",
};
const UNCAPPED = { ...CAPPED, limits: { count: 0, cpu: 0, memory_bytes: 0, disk_bytes: 0 } };

function route(environment: unknown, resources: unknown = CAPPED) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") return json({});
    if (url.includes("/environment")) return json(environment);
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

const putsTo = (f: ReturnType<typeof route>, tail: string) =>
  f.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT" && String(c[0]).endsWith(tail))
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>);

function open(canEdit = true) {
  renderWithQuery(<ItemEnvironmentModal slug="rca" itemId="i-1" canEdit={canEdit} onClose={vi.fn()} />);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ItemEnvironmentModal — disk", () => {
  it("draws the disk field with what is used and how far it may go, before anything is typed", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open();

    const field = await screen.findByTestId("disk-field");
    expect(screen.getByTestId("disk-input")).toBeTruthy();
    expect(field).toHaveTextContent(word("itemenv.disk.used", { used: "300.0 MB" }));
    expect(field).toHaveTextContent(word("itemenv.size.ceiling", { value: "20.0 GB" }));
    expect(field).toHaveTextContent(word("itemenv.disk.now"));
  });

  it("names an unset disk as the App's size, not the person's quota", async () => {
    // The cpu/memory default comes from the owner's quota; the disk's from
    // the App (D7) — the same words would say something false.
    vi.stubGlobal("fetch", route(ENV));
    open();

    expect(await screen.findByTestId("disk-origin")).toHaveTextContent(word("itemenv.disk.default"));
  });

  it("sits with this workspace's sizes, above the person's totals", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open();
    const disk = await screen.findByTestId("disk-field");
    const gauge = screen.getByTestId("budget-gauge");

    expect(disk.compareDocumentPosition(gauge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("while running, the hint says closing is for cpu and memory — the disk can change now", async () => {
    vi.stubGlobal("fetch", route({ ...ENV, running: true }));
    open();

    await screen.findByTestId("disk-input");
    expect(screen.getByText(word("itemenv.close.hint"))).toBeTruthy();
    expect(word("itemenv.close.hint")).toContain("CPU");
  });

  it("is drawn with no per-person budget too — disk needs none", async () => {
    vi.stubGlobal("fetch", route(ENV, UNCAPPED));
    open();

    expect(await screen.findByTestId("disk-input")).toBeEnabled();
    expect(screen.getByTestId("itemenv-save")).toBeTruthy();
  });

  it("is not drawn by an API pod that sends no disk figures", async () => {
    const { stated_disk_bytes, effective_disk_bytes, disk_used_bytes, disk_ceiling, owner_disk_left, ...old } =
      ENV;
    vi.stubGlobal("fetch", route(old));
    open();

    await screen.findByTestId("cpu-input");
    expect(screen.queryByTestId("disk-field")).toBeNull();
  });

  it("saves the disk on its own route, and sends no cpu/memory PUT for a disk-only edit", async () => {
    const f = route(ENV);
    vi.stubGlobal("fetch", f);
    open();
    fireEvent.change(await screen.findByTestId("disk-input"), { target: { value: "5 GB" } });

    fireEvent.click(screen.getByTestId("itemenv-save"));

    await waitFor(() => expect(putsTo(f, "/disk")).toEqual([{ disk: "5G" }]));
    expect(putsTo(f, "/resources")).toHaveLength(0);
  });

  it("sends no disk PUT for a cpu-only edit", async () => {
    const f = route(ENV);
    vi.stubGlobal("fetch", f);
    open();
    fireEvent.change(await screen.findByTestId("cpu-input"), { target: { value: "3" } });

    fireEvent.click(screen.getByTestId("itemenv-save"));

    await waitFor(() => expect(putsTo(f, "/resources")).toHaveLength(1));
    expect(putsTo(f, "/disk")).toHaveLength(0);
  });

  it("may change while the environment runs — cpu and memory stay locked", async () => {
    const f = route({ ...ENV, running: true });
    vi.stubGlobal("fetch", f);
    open();

    const disk = await screen.findByTestId("disk-input");
    expect(disk).toBeEnabled();
    expect(screen.getByTestId("cpu-input")).toBeDisabled();
    fireEvent.change(disk, { target: { value: "2G" } });
    fireEvent.click(screen.getByTestId("itemenv-save"));

    await waitFor(() => expect(putsTo(f, "/disk")).toEqual([{ disk: "2G" }]));
  });

  it("refuses, before Save, a size below what the workspace already holds (D1)", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open();
    fireEvent.change(await screen.findByTestId("disk-input"), { target: { value: "100M" } });

    expect(screen.getByTestId("disk-hint")).toHaveTextContent(word("itemenv.field.disk.under", { detail: "300.0 MB" }));
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
  });

  it("refuses a spelling the server cannot read", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open();
    fireEvent.change(await screen.findByTestId("disk-input"), { target: { value: "lots" } });

    expect(screen.getByTestId("disk-hint")).toBeTruthy();
    expect(screen.getByTestId("itemenv-save")).toBeDisabled();
  });

  it("'Back to default' clears it", async () => {
    const f = route({ ...ENV, stated_disk_bytes: 5 * GB, effective_disk_bytes: 5 * GB });
    vi.stubGlobal("fetch", f);
    open();
    fireEvent.click(await screen.findByTestId("reset-disk"));

    fireEvent.click(screen.getByTestId("itemenv-save"));

    await waitFor(() => expect(putsTo(f, "/disk")).toEqual([{ disk: null }]));
  });

  it("says how much of the owner's own quota is left — a ceiling is not a guarantee (D2, D6)", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open();

    expect(await screen.findByTestId("disk-owner-left")).toHaveTextContent(
      word("itemenv.disk.ownerLeft", { left: "40.0 GB" }),
    );
  });

  it("says nothing of the owner's quota when the server gave none (a collaborator, or no quota)", async () => {
    vi.stubGlobal("fetch", route({ ...ENV, owner_disk_left: null }));
    open();

    await screen.findByTestId("disk-input");
    expect(screen.queryByTestId("disk-owner-left")).toBeNull();
  });

  it("says when the App's ceiling held a stated size down", async () => {
    vi.stubGlobal("fetch", route({ ...ENV, stated_disk_bytes: 50 * GB, effective_disk_bytes: 20 * GB }));
    open();

    expect(await screen.findByTestId("disk-clamped")).toHaveTextContent(
      word("itemenv.memory.clamped.app", { stated: "50.0 GB", effective: "20.0 GB" }),
    );
  });

  it("is read-only for someone who may look but not change it", async () => {
    vi.stubGlobal("fetch", route(ENV));
    open(false);

    expect(await screen.findByTestId("disk-input")).toBeDisabled();
    expect(screen.queryByTestId("itemenv-save")).toBeNull();
  });
});
