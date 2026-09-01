/**
 * What the item's current toolset says it wants from the environment (#750).
 *
 * The panel shows this two ways at once — a tab per tool, and one field per
 * variable — and both come from here so they cannot disagree. The rules that
 * matter are about what we are entitled to claim: a tool that declared nothing
 * is never reported as needing nothing, and a variable nobody marked required
 * is never counted as missing.
 */
import { describe, expect, it } from "vitest";

import type { ItemToolState } from "../api/types";

import { deriveEnvNeeds } from "./envNeeds";

function tool(over: Partial<ItemToolState> & { key: string }): ItemToolState {
  return {
    label: over.key,
    description: "",
    default_on: true,
    pref: "follow",
    effective: true,
    ...over,
  } as ItemToolState;
}

describe("deriveEnvNeeds", () => {
  it("groups a tool's declared variables under that tool", () => {
    const view = deriveEnvNeeds(
      [
        tool({
          key: "sap-tools",
          label: "SAP Tools",
          env_needs: [{ name: "SAP_HOST", description: "server address", required: true }],
        }),
      ],
      {},
    );

    expect(view.groups).toEqual([
      {
        key: "sap-tools",
        label: "SAP Tools",
        fields: [
          {
            name: "SAP_HOST",
            description: "server address",
            required: true,
            wantedBy: ["SAP Tools"],
            filled: false,
          },
        ],
      },
    ]);
  });

  it("names a variable's other users under every tab it appears in", () => {
    // The storage is one flat dict, so this is ONE value with two consumers.
    // Someone tidying up under the Wafer tab has to be able to see that
    // clearing it also turns off SAP Tools.
    const shared = { name: "CORP_PROXY", description: "", required: null };
    const view = deriveEnvNeeds(
      [
        tool({ key: "sap-tools", label: "SAP Tools", env_needs: [shared] }),
        tool({ key: "wafer", label: "Wafer History", env_needs: [shared] }),
      ],
      {},
    );

    for (const group of view.groups) {
      expect(group.fields[0].wantedBy).toEqual(["SAP Tools", "Wafer History"]);
    }
  });

  it("names a tool that declared nothing instead of calling it satisfied", () => {
    const view = deriveEnvNeeds(
      [
        tool({ key: "legacy", label: "Legacy Tool", env_needs: null }),
        tool({ key: "exec", label: "Exec", env_needs: [] }),
      ],
      {},
    );

    // The silent one is named…
    expect(view.undeclared).toEqual(["Legacy Tool"]);
    // …and the one that looked and needs nothing is NOT repeated as a caveat,
    // or the caveat stops meaning anything.
    expect(view.groups).toEqual([]);
    expect(view.missingRequired).toEqual([]);
  });

  it("counts only what an author marked required as still missing", () => {
    const view = deriveEnvNeeds(
      [
        tool({
          key: "sap-tools",
          label: "SAP Tools",
          env_needs: [
            { name: "SAP_HOST", description: "", required: true },
            { name: "SAP_PROXY", description: "", required: false },
            { name: "SAP_HINT", description: "", required: null },
          ],
        }),
      ],
      {},
    );

    // Not SAP_PROXY (explicitly optional) and not SAP_HINT (unstated — which
    // is not the same as required). Counting unstated would make a tool with
    // two real needs and five unmarked extras report seven forever, and a
    // panel that always complains is one people stop reading.
    expect(view.missingRequired).toEqual(["SAP_HOST"]);
  });

  it("ignores tools this item is not running", () => {
    const view = deriveEnvNeeds(
      [
        tool({
          key: "off-tool",
          label: "Switched Off",
          effective: false,
          env_needs: [{ name: "NEVER_READ", description: "", required: true }],
        }),
      ],
      {},
    );

    // Asking for a variable nothing will read is worse than silence: it sends
    // someone to find a credential for a tool that is not going to run.
    expect(view.groups).toEqual([]);
    expect(view.missingRequired).toEqual([]);
    expect(view.undeclared).toEqual([]);
  });

  it("treats a whitespace-only value as unfilled", () => {
    // A stray space is what a paste leaves behind, and it reaches the tool as
    // an empty string — so reporting it as set would be agreeing with the bug.
    const view = deriveEnvNeeds(
      [
        tool({
          key: "sap-tools",
          label: "SAP Tools",
          env_needs: [{ name: "SAP_HOST", description: "", required: true }],
        }),
      ],
      { SAP_HOST: "   " },
    );

    expect(view.missingRequired).toEqual(["SAP_HOST"]);
    expect(view.groups[0].fields[0].filled).toBe(false);
  });
});
