/** One row per variable the AI asked for (docs/plan-env-request-card.md N3, D4). */
import { describe, expect, it } from "vitest";

import type { EnvProvider } from "../api/types";

import { envRequestRows } from "./envRequestRows";

const erp: EnvProvider = { id: "erp", label: "ERP", produces: ["ERP_TOKEN"], inputs: [] };
const none = {
  shared: {},
  mine: {},
  personal: {},
  policy: {} as Record<string, string>,
  providers: [] as EnvProvider[],
};

describe("envRequestRows", () => {
  it("offers a sign-in when this deployment has a login that produces the name", () => {
    expect(envRequestRows(["ERP_TOKEN"], { ...none, providers: [erp] })).toEqual([
      { name: "ERP_TOKEN", status: "missing", login: { id: "erp", label: "ERP" } },
    ]);
  });

  it("offers a field when no login produces it", () => {
    expect(envRequestRows(["MAP_KEY"], { ...none, providers: [erp] })).toEqual([
      { name: "MAP_KEY", status: "missing", login: null },
    ]);
  });

  it("is ready once the viewer's tools would get a value — read now, not when it was asked", () => {
    const rows = envRequestRows(["ERP_TOKEN", "MAP_KEY"], {
      ...none,
      mine: { ERP_TOKEN: "t" },
      shared: { MAP_KEY: "k" },
      providers: [erp],
    });

    expect(rows.map((r) => r.status)).toEqual(["ready", "ready"]);
  });

  it("counts a value from the person's own variables across workspaces where the item uses it", () => {
    const rows = envRequestRows(["MAP_KEY"], {
      ...none,
      policy: { MAP_KEY: "private_first" },
      personal: { MAP_KEY: "k" },
    });

    expect(rows[0].status).toBe("ready");
  });

  it("says when only the shared copy can fix it", () => {
    const rows = envRequestRows(["MAP_KEY"], { ...none, shared: { MAP_KEY: "" } });

    expect(rows[0].status).toBe("pinned");
  });
});
