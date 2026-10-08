/**
 * The `/w/` page's platform bar (`docs/plan-wui-viewer-login.md` Q11): whether
 * to draw it at all, and what the viewer is missing that they can supply.
 *
 * "Missing" is narrow on purpose: a variable some running tool marked
 * REQUIRED and whose value this viewer's tools would not get — a blank value
 * counts as none in every layer, so whatever is missing, the viewer's own value
 * would be used for it. An optional one is not missing. Each is named by the SYSTEM to sign in to when the deploy
 * can sign them in for it, else by the variable; never counted ("2 missing"
 * says nothing about what to do).
 */
import type { EnvProvider, ItemToolState } from "../api/types";

import { layerInUse, policyOf } from "./envLayers";

export type Missing = { kind: "login" | "set"; name: string };

export function identityState({
  tools,
  shared,
  policy,
  mine,
  personal = {},
  providers,
  hasSchedules,
}: {
  tools: ItemToolState[];
  shared: Record<string, string>;
  policy: Record<string, string>;
  mine: Record<string, string>;
  /** The viewer's values for every item (`plan-personal-env`): used only for a
   * name the item asks for as personal (Private first / Private only). */
  personal?: Record<string, string>;
  providers: EnvProvider[];
  hasSchedules: boolean;
}): { show: boolean; missing: Missing[]; holdsOwn: boolean } {
  const live = tools.filter((t) => t.effective);
  const declared = new Set(live.flatMap((t) => (t.env_needs ?? []).map((n) => n.name)));
  const offered = providers.filter((p) => p.produces.some((n) => declared.has(n)));

  const required = [
    ...new Set(
      live.flatMap((t) => (t.env_needs ?? []).filter((n) => n.required === true).map((n) => n.name)),
    ),
  ];
  const missing: Missing[] = [];
  const seen = new Set<string>();
  for (const name of required) {
    if (viewerStatus(name, shared, mine, policy, personal) !== "missing") continue;
    const via = offered.find((p) => p.produces.includes(name));
    const entry: Missing = via ? { kind: "login", name: via.label } : { kind: "set", name };
    const key = `${entry.kind}:${entry.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    missing.push(entry);
  }

  const asksPersonal = Object.keys(policy).some((n) => policyOf(n, policy) !== "shared_first");
  // Whether "signed in" would be TRUE: nothing missing is not the same as
  // holding anything of one's own. A value from my environment variables
  // counts only where this item would use it.
  const holdsOwn =
    Object.values(mine).some((v) => v.trim() !== "") ||
    Object.entries(personal).some(
      ([n, v]) => policyOf(n, policy) !== "shared_first" && v.trim() !== "",
    );
  return { show: asksPersonal || offered.length > 0 || hasSchedules, missing, holdsOwn };
}

/** Where one variable stands for this viewer — the ONE judgement the key
 * button (`identityState`) and the chat's request card (`envRequestRows`) both
 * make: `ready` when the value their tools would get is not blank, otherwise
 * `missing`. A blank value is not a value in any layer (`layerInUse`), so a
 * blank shared copy never stands in the way of the viewer's own. */
export type ViewerStatus = "ready" | "missing";

export function viewerStatus(
  name: string,
  shared: Record<string, string>,
  mine: Record<string, string>,
  policy: Record<string, string>,
  personal: Record<string, string> = {},
): ViewerStatus {
  const layer = layerInUse(name, shared, mine, policy, personal);
  const value =
    layer === "private"
      ? mine[name]
      : layer === "personal"
        ? personal[name]
        : layer === "shared"
          ? shared[name]
          : "";
  return (value ?? "").trim() !== "" ? "ready" : "missing";
}

/** How the key button names what is missing (`plan-wui-viewer-login` Q11):
 * the names while there are one or two; beyond that the first two by name and
 * how many in all — never a bare count (plan UI §) — and "systems" only when
 * every entry is a sign-in, since a variable to type is not a system (review
 * rounds 1 V7, 2). */
export type KeyLabel =
  | { kind: "list"; logins: string[]; sets: string[] }
  | { kind: "manySystems"; count: number }
  | { kind: "manyItems"; count: number; first: string[] };

export function keyLabelParts(missing: Missing[]): KeyLabel {
  if (missing.length >= 3) {
    return missing.every((m) => m.kind === "login")
      ? { kind: "manySystems", count: missing.length }
      : { kind: "manyItems", count: missing.length, first: missing.slice(0, 2).map((m) => m.name) };
  }
  return {
    kind: "list",
    logins: missing.filter((m) => m.kind === "login").map((m) => m.name),
    sets: missing.filter((m) => m.kind === "set").map((m) => m.name),
  };
}
