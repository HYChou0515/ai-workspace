/**
 * Which layer a tool gets a variable from (`docs/plan-wui-viewer-login.md`).
 *
 * The FE's copy of `api.env_layers.resolve_env`, used ONLY to label the panel
 * ("in use: yours / the shared value"). The backend decides what a tool
 * receives; this is held to it by a shared table (`web/tests/envLayersParity.test.ts`).
 */

export type EnvPolicy = "shared_first" | "private_first" | "private_only";

export const POLICIES: EnvPolicy[] = ["shared_first", "private_first", "private_only"];

/** A name with no policy — or one this build does not know — is `shared_first`,
 * the merge every turn did before policies existed. */
export function policyOf(name: string, policy: Record<string, string>): EnvPolicy {
  const p = policy[name];
  return p === "private_first" || p === "private_only" ? p : "shared_first";
}

/** Whether any name here reads "my environment variables" — the only case
 * where it is worth asking for them (`plan-personal-env` D2). */
export function asksPersonal(policy: Record<string, string>): boolean {
  return Object.keys(policy).some((n) => policyOf(n, policy) !== "shared_first");
}

/** `private` = the person's value for THIS item; `personal` = their value for
 * every item ("my environment variables", `docs/plan-personal-env.md`). */
export type Layer = "shared" | "private" | "personal" | "none";

/** A value that counts as not set (plan-env-request-card N5): only space, tab,
 * newline, carriage return, form feed and vertical tab — NOT `trim()`, which
 * strips a different set from Python's `strip()`. The backend's `is_blank`,
 * held to the same list by `tests/fixtures/env_layers_cases.json` (`blanks`). */
export function isBlank(value: string | undefined): boolean {
  return /^[ \t\n\r\f\v]*$/.test(value ?? "");
}

export function layerInUse(
  name: string,
  shared: Record<string, string>,
  mine: Record<string, string>,
  policy: Record<string, string>,
  personal: Record<string, string> = {},
): Layer {
  // A blank value is not a value (`resolve_env`): it does not hide the next layer.
  const has = (layer: Record<string, string>) => Object.hasOwn(layer, name) && !isBlank(layer[name]);
  switch (policyOf(name, policy)) {
    case "private_only":
      return has(mine) ? "private" : has(personal) ? "personal" : "none";
    case "private_first":
      return has(mine) ? "private" : has(personal) ? "personal" : has(shared) ? "shared" : "none";
    default:
      // Shared never reads the person's values for every item (D2).
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
