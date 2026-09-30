/**
 * The `/w/` page's platform bar (`docs/plan-wui-viewer-login.md` Q11): whether
 * to draw it at all, and what the viewer is missing that they can supply.
 *
 * "Missing" is narrow on purpose: a variable some running tool marked
 * REQUIRED, whose value this viewer's tools would not get, and which the
 * viewer could fix — i.e. one their own value would actually be used for. A
 * shared value pinned by `shared_first` is not theirs to fix, and an optional
 * one is not missing. Each is named by the SYSTEM to sign in to when the deploy
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
  providers,
  hasSchedules,
}: {
  tools: ItemToolState[];
  shared: Record<string, string>;
  policy: Record<string, string>;
  mine: Record<string, string>;
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
    const layer = layerInUse(name, shared, mine, policy);
    const value = layer === "private" ? mine[name] : layer === "shared" ? shared[name] : "";
    if ((value ?? "").trim() !== "") continue;
    // Pinned to the shared copy: whatever the viewer typed would not be used.
    if (policyOf(name, policy) === "shared_first" && Object.hasOwn(shared, name)) continue;
    const via = offered.find((p) => p.produces.includes(name));
    const entry: Missing = via ? { kind: "login", name: via.label } : { kind: "set", name };
    const key = `${entry.kind}:${entry.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    missing.push(entry);
  }

  const personal = Object.keys(policy).some((n) => policyOf(n, policy) !== "shared_first");
  // Whether "signed in" would be TRUE: nothing missing is not the same as
  // holding anything of one's own.
  const holdsOwn = Object.values(mine).some((v) => v.trim() !== "");
  return { show: personal || offered.length > 0 || hasSchedules, missing, holdsOwn };
}
