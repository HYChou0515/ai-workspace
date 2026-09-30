/**
 * What the `/w/` page's platform bar says (`plan-wui-viewer-login` Q11):
 * whether it is drawn at all, and — when the viewer is missing something they
 * can supply — WHICH system to sign in to or which variable to set, never
 * "N missing" (a count says nothing about what to do).
 */
import { describe, expect, it } from "vitest";

import type { EnvProvider, ItemToolState } from "../api/types";

import { identityState } from "./identityState";

const tool = (needs: ItemToolState["env_needs"]): ItemToolState => ({
  key: "t",
  group: "t",
  label: "t",
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  env_needs: needs,
});
const req = (name: string) => ({ name, description: "", required: true });
const provider = (label: string, produces: string[]): EnvProvider => ({
  id: label.toLowerCase(),
  label,
  produces,
  inputs: [],
});

const base = { shared: {}, policy: {}, mine: {}, providers: [] as EnvProvider[], hasSchedules: false };

describe("identityState", () => {
  it("draws nothing for an item nobody signs in to", () => {
    expect(identityState({ ...base, tools: [tool([req("A")])], shared: { A: "x" } }).show).toBe(false);
  });

  it("is drawn once any variable is each person's to provide", () => {
    const s = identityState({ ...base, tools: [], policy: { A: "private_only" } });
    expect(s.show).toBe(true);
  });

  it("is drawn when the deploy can sign someone in for a tool here", () => {
    const s = identityState({
      ...base,
      tools: [tool([req("ERP_TOKEN")])],
      shared: { ERP_TOKEN: "x" },
      providers: [provider("ERP", ["ERP_TOKEN"])],
    });
    expect(s.show).toBe(true);
  });

  it("is drawn when the page has schedules to run as oneself", () => {
    expect(identityState({ ...base, tools: [], hasSchedules: true }).show).toBe(true);
  });

  it("names the system to sign in to", () => {
    const s = identityState({
      ...base,
      tools: [tool([req("ERP_TOKEN")])],
      policy: { ERP_TOKEN: "private_only" },
      providers: [provider("ERP", ["ERP_TOKEN"])],
    });
    expect(s.missing).toEqual([{ kind: "login", name: "ERP" }]);
  });

  it("names a variable nobody can sign in for", () => {
    const s = identityState({
      ...base,
      tools: [tool([req("MAP_KEY")])],
      policy: { MAP_KEY: "private_only" },
    });
    expect(s.missing).toEqual([{ kind: "set", name: "MAP_KEY" }]);
  });

  it("counts one system once however many of its variables are missing", () => {
    const s = identityState({
      ...base,
      tools: [tool([req("A"), req("B")])],
      policy: { A: "private_only", B: "private_only" },
      providers: [provider("ERP", ["A", "B"])],
    });
    expect(s.missing).toEqual([{ kind: "login", name: "ERP" }]);
  });

  it("does not ask the viewer for what they cannot supply", () => {
    // Shared-first with no shared value: the viewer's own value WOULD be used,
    // so it is theirs to fill. Pinned to a set shared value: not missing.
    const s = identityState({
      ...base,
      tools: [tool([req("A"), req("B")])],
      shared: { B: "set" },
      policy: { A: "private_only" },
    });
    expect(s.missing.map((m) => m.name)).toEqual(["A"]);
  });

  it("says whether the viewer holds anything of their own", () => {
    // "Signed in" on a key that opens an empty panel was a false sentence
    // (seen in a real browser): nothing was MISSING, but nothing was theirs.
    expect(identityState({ ...base, tools: [], policy: { A: "private_only" } }).holdsOwn).toBe(false);
    expect(
      identityState({ ...base, tools: [], policy: { A: "private_only" }, mine: { A: "v" } }).holdsOwn,
    ).toBe(true);
  });

  it("is satisfied once the viewer's own value is there", () => {
    const s = identityState({
      ...base,
      tools: [tool([req("A")])],
      policy: { A: "private_only" },
      mine: { A: "v" },
    });
    expect(s.missing).toEqual([]);
  });

  it("does not count an optional variable as missing", () => {
    const s = identityState({
      ...base,
      tools: [tool([{ name: "A", description: "", required: false }])],
      policy: { A: "private_only" },
    });
    expect(s.missing).toEqual([]);
  });
});

describe("keyLabel (review round 1, V7)", () => {
  it("calls three sign-ins systems, but never calls plain variables systems", async () => {
    const { keyLabelParts } = await import("./identityState");
    const login = (name: string) => ({ kind: "login" as const, name });
    const set = (name: string) => ({ kind: "set" as const, name });

    expect(keyLabelParts([login("A"), login("B"), login("C")])).toEqual({ kind: "manySystems", count: 3 });
    expect(keyLabelParts([login("A"), set("B"), set("C")])).toEqual({ kind: "manyItems", count: 3 });
    expect(keyLabelParts([login("A"), set("B")])).toEqual({ kind: "list", logins: ["A"], sets: ["B"] });
  });
});
