/**
 * Which layer a tool gets a variable from (`docs/plan-wui-viewer-login.md`).
 *
 * The FE's copy of `api.env_layers.resolve_env`, used ONLY to label the panel
 * ("in use: yours / the shared value"). The backend decides what a tool
 * receives; this is held to it by a shared table (`envLayers.test.ts`).
 */

export type EnvPolicy = "shared_first" | "private_first" | "private_only";

export const POLICIES: EnvPolicy[] = ["shared_first", "private_first", "private_only"];

/** A name with no policy — or one this build does not know — is `shared_first`,
 * the merge every turn did before policies existed. */
export function policyOf(name: string, policy: Record<string, string>): EnvPolicy {
  const p = policy[name];
  return p === "private_first" || p === "private_only" ? p : "shared_first";
}

export type Layer = "shared" | "private" | "none";

export function layerInUse(
  name: string,
  shared: Record<string, string>,
  mine: Record<string, string>,
  policy: Record<string, string>,
): Layer {
  const has = (layer: Record<string, string>) => Object.hasOwn(layer, name);
  switch (policyOf(name, policy)) {
    case "private_only":
      return has(mine) ? "private" : "none";
    case "private_first":
      return has(mine) ? "private" : has(shared) ? "shared" : "none";
    default:
      return has(shared) ? "shared" : has(mine) ? "private" : "none";
  }
}

/** A person's private layer: what they typed, with what the deploy filled in
 * over it (the automatic value wins a name). The FE's copy of the backend's
 * `own_layer`, held to it by `tests/fixtures/private_layer_cases.json`. */
export function ownLayer(
  typed: Record<string, string>,
  auto: Record<string, string>,
): Record<string, string> {
  return { ...typed, ...auto };
}
