// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ItemEnvironmentModal } from "./ItemEnvironmentModal";
import { renderWithQuery } from "../test/queryWrapper";

/**
 * The panel's frame: it fetches, and it decides what the two halves may say.
 *
 * The point of testing the modal separately from the panel is that the panel
 * takes its answers as props and cannot be wrong about them — everything that
 * can be wrong lives here, in what gets fetched and what gets passed down.
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
  effective_memory_bytes: null,
  // What the BACKEND says it will really apply. Omitting these made the double
  // disagree with the real payload, the dial correctly disappeared, and this
  // test went red — which is the enforcement gate proving itself end to end.
  enforced_cpu_cores: 2,
  enforced_memory_bytes: null,
  cpu_bound_by: null,
  memory_bound_by: null,
};

const CAPPED = {
  limits: { count: 0, cpu: 4, memory_bytes: 0, disk_bytes: 0 },
  cpu_in_use: 2,
  memory_in_use: 0,
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
    if (url.includes("/environment")) {
      if (hangLoad) return new Promise<Response>(() => {});
      return json(environment);
    }
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

/** What actually reached the server — the only evidence that a number the
 *  person typed was not dropped. Asserting on the absence of a prompt cannot
 *  tell a saved value from a lost one. */
const puts = (f: ReturnType<typeof route>) =>
  f.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT")
    .map((c) => String((c[1] as RequestInit).body));

beforeEach(() => {
  vi.stubGlobal("fetch", route(CAPPED));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ItemEnvironmentModal", () => {
  it("draws the size half where the deploy caps somebody", async () => {
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("budget-gauge")).toBeTruthy());
    expect(screen.getByTestId("cpu-input")).toBeTruthy();
  });

  it("omits the size half where it caps nobody, and keeps the status half", async () => {
    // The shipped default. `0 / 0` is not a reading, and a dial with an
    // unlimited ceiling is worse than no dial — but "is it running, close it"
    // is about a machine and still worth having.
    vi.stubGlobal("fetch", route(UNCAPPED));

    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.queryByTestId("budget-gauge")).toBeNull();
    expect(screen.queryByTestId("cpu-input")).toBeNull();
  });

  it("is a real modal — it dims what is behind it and answers Escape", async () => {
    // It used to be a bare `<div className="modal">`: nothing dimmed behind it,
    // focus stayed on the page underneath, and the only way out was one
    // unlabelled `×` below the panel.
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.getByTestId("item-environment-modal-backdrop")).toBeTruthy();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  /**
   * Closing does not WRITE. That is the whole of what this modal decides about
   * saving, and it is deliberately the smaller claim.
   *
   * The fields commit on blur and there is no Save button, so "what does
   * leaving mean" has no answer this component can derive. Two attempts were
   * made and both were worse than the question. A `useDirtyClose` prompt could
   * not work at all: the confirm dialog takes focus in order to be answerable,
   * taking focus blurs the field, and blurring is what saves — the question
   * committed the thing it was asking about. Committing on the way out instead
   * made Escape the only keystroke in the app that writes, spending the ITEM
   * OWNER's quota, and the refusal could not even be shown because the panel is
   * gone by the time the server answers.
   *
   * So Escape does what Escape does everywhere else here: it closes. A number
   * typed and never blurred is not sent — the same thing that happens today if
   * you type one and navigate away, and the safe direction: dropping a
   * keystroke rather than writing one nobody confirmed.
   */
  it("writes nothing when it is closed, even mid-edit", async () => {
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher)).toEqual([]);
    // Nor is anything asked: there is no prompt this panel could raise that
    // would not first commit the value it is asking about.
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
  });

  it("still commits a field that is blurred inside the panel", async () => {
    // The panel's own save model, unchanged by any of this: the value goes out
    // when the field loses focus to something else IN the panel. Closing is not
    // that, which is the distinction the test above pins.
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.tab();

    await waitFor(() =>
      expect(puts(fetcher)).toEqual([JSON.stringify({ cpu_cores: 3, memory: null })]),
    );
  });

  it("closes from its own ✕, and writes nothing on the way out either", async () => {
    // The ✕ is the exit that USED to write, and only in some browsers: clicking
    // a button moves focus in Chrome, the field saves on blur, and so the same
    // click saved in Chrome and dropped the number in Firefox and Safari. Both
    // exits now mean the same thing, everywhere — which is what a modal's exits
    // mean in the rest of this app.
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.click(screen.getByTestId("dismiss-item-environment"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher)).toEqual([]);
  });

  it("commits when the person TABS off the field, ✕ included", async () => {
    // The rule is "you moved focus off the field", not "you closed". Tabbing to
    // the ✕ is a focus move the PERSON made, so the value goes out — and then
    // Enter closes without adding anything. This pins behaviour that already
    // held; it exists because the comment above it once claimed the opposite,
    // and a sentence is not a guard.
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.tab({ shift: true });

    expect(document.activeElement).toBe(screen.getByTestId("dismiss-item-environment"));
    expect(puts(fetcher)).toEqual([JSON.stringify({ cpu_cores: 3, memory: null })]);

    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher).length).toBe(1);
  });

  it("is a modal from the moment it opens, not once the fetch lands", async () => {
    // It used to `return null` until `/environment` answered. Clicking 沙盒 then
    // did nothing visible — and because the parent already believed it was
    // open, a second click did nothing either.
    vi.stubGlobal("fetch", route(CAPPED, ENVIRONMENT, { hangLoad: true }));
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    expect(await screen.findByTestId("item-environment-modal")).toBeTruthy();
    expect(screen.getByTestId("item-environment-pending")).toBeTruthy();
  });

  it("says so when the server refuses the size", async () => {
    // Reachable precisely because no exit commits: the save is dispatched while
    // this is still on screen, so there is somewhere for the 507 to be read.
    vi.stubGlobal("fetch", route(CAPPED, ENVIRONMENT, { refuseSave: true }));
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.tab({ shift: true });

    expect(await screen.findByTestId("save-failed")).toBeTruthy();
  });

  it("asks the item's route for the item, and the person's for the total", async () => {
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);

    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    const urls = fetcher.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/a/rca/items/i-1/environment"))).toBe(true);
    expect(urls.some((u) => u.includes("/me/resources"))).toBe(true);
  });
});

// Re-exported so the suite fails loudly if the wrapper ever stops providing a
// client, rather than every test here failing with an opaque hook error.
export { render };
