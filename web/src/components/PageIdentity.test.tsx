/**
 * The page's identity controls (`docs/plan-wui-viewer-login.md` Q11/Q12): the
 * strip the platform draws ABOVE a page's frame (never over it — the line of
 * death), naming what the viewer must sign in to, opening the platform's own
 * sign-in, and listing the page's schedules with who each runs as.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BindingRow } from "../api/scheduleBindings";
import type { EnvProvider, ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { PageIdentityBar } from "./PageIdentity";

afterEach(cleanup);

const ERP_TOOL: ItemToolState = {
  key: "erp",
  group: "erp",
  label: "erp",
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  env_needs: [{ name: "ERP_TOKEN", description: "", required: true }],
};
const ERP_LOGIN: EnvProvider = { id: "erp", label: "ERP", produces: ["ERP_TOKEN"], inputs: [] };

function open({
  policy = {},
  shared = {},
  mine = {},
  providers = [] as EnvProvider[],
  rows = [] as BindingRow[],
} = {}) {
  const bindings = {
    list: vi.fn(async () => rows),
    bind: vi.fn(async () => {}),
    unbind: vi.fn(async () => {}),
  };
  renderWithQuery(
    <PageIdentityBar
      slug="rca"
      itemId="i1"
      folder="/report"
      title="Weekly report"
      client={{
        getItemTools: vi.fn(async () => [ERP_TOOL]),
        getEnvProviders: vi.fn(async () => providers),
        resolveEnvProvider: vi.fn(),
      }}
      privateClient={{
        layers: vi.fn(async () => ({ shared, policy })),
        get: vi.fn(async () => mine),
        put: vi.fn(async () => {}),
        clear: vi.fn(async () => {}),
      }}
      bindingsClient={bindings}
    />,
  );
  return { bindings };
}

describe("PageIdentityBar", () => {
  it("draws nothing on a page nobody signs in to", async () => {
    open({ shared: { ERP_TOKEN: "x" } });
    // Settled, and still nothing: the page keeps the whole window.
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("page-identity-bar")).not.toBeInTheDocument();
  });

  it("names the system to sign in to, not a count", async () => {
    open({ policy: { ERP_TOKEN: "private_only" }, providers: [ERP_LOGIN] });

    const key = await screen.findByTestId("page-identity-key");
    expect(key).toHaveTextContent("ERP");
    expect(key).not.toHaveTextContent(/\d/);
  });

  it("opens the platform's own sign-in, not anything the page draws", async () => {
    open({ policy: { ERP_TOKEN: "private_only" } });

    fireEvent.click(await screen.findByTestId("page-identity-key"));

    expect(await screen.findByTestId("env-modal")).toBeInTheDocument();
  });

  it("lists the page's schedules with who each runs as", async () => {
    open({
      rows: [
        { trigger_id: "a", run: "weekly", describe: "every Monday", bound_to: "", mine: false },
        { trigger_id: "b", run: "nightly", describe: "daily 02:00", bound_to: "bob", mine: false },
      ],
    });

    fireEvent.click(await screen.findByTestId("page-schedules"));

    expect(screen.getByTestId("page-schedule-a")).toHaveAttribute("data-bound", "");
    expect(screen.getByTestId("page-schedule-b")).toHaveTextContent("bob");
  });

  it("binds an unbound schedule to the viewer at once", async () => {
    const { bindings } = open({
      rows: [{ trigger_id: "a", run: "weekly", describe: "", bound_to: "", mine: false }],
    });
    fireEvent.click(await screen.findByTestId("page-schedules"));

    fireEvent.click(within(screen.getByTestId("page-schedule-a")).getByRole("button"));

    await waitFor(() =>
      expect(bindings.bind).toHaveBeenCalledWith("rca", "i1", "/report/schedules.json", "a"),
    );
  });

  it("asks before taking a schedule over from someone else", async () => {
    // Q12: replacing a binding stops another person's values being used — they
    // are told afterwards, and the one replacing them is told BEFORE.
    const { bindings } = open({
      rows: [{ trigger_id: "b", run: "nightly", describe: "", bound_to: "bob", mine: false }],
    });
    fireEvent.click(await screen.findByTestId("page-schedules"));

    fireEvent.click(within(screen.getByTestId("page-schedule-b")).getByRole("button"));

    // The confirm's own action, then its sentence naming who is replaced.
    const replace = await screen.findByTestId("dialog-action-replace");
    expect(replace.closest("[role=dialog]")).toHaveTextContent("bob");
    expect(bindings.bind).not.toHaveBeenCalled();

    fireEvent.click(replace);

    await waitFor(() =>
      expect(bindings.bind).toHaveBeenCalledWith("rca", "i1", "/report/schedules.json", "b"),
    );
  });

  it("lets the viewer take their own name off", async () => {
    const { bindings } = open({
      rows: [{ trigger_id: "c", run: "r", describe: "", bound_to: "me", mine: true }],
    });
    fireEvent.click(await screen.findByTestId("page-schedules"));

    fireEvent.click(within(screen.getByTestId("page-schedule-c")).getByRole("button"));

    await waitFor(() =>
      expect(bindings.unbind).toHaveBeenCalledWith("rca", "i1", "/report/schedules.json", "c"),
    );
  });
});
