/**
 * One item's environment — is it running, what does it cost, and what size did
 * somebody set for it.
 *
 * Deliberately separate from `myResources`. That one answers "what am I holding
 * across everything I own" and is scoped to a person; this answers "what is
 * THIS item doing", and a collaborator may be entitled to the second without
 * the first. Merging them would mean a route that hands a visitor the owner's
 * whole working set to explain one refusal.
 *
 * `stated` and `effective` are kept apart all the way to the UI. They differ
 * whenever somebody asked for more than the App's ceiling or their own budget
 * allows, and showing only one of them is a choice between a panel that
 * disagrees with what the person typed and one that lies about what runs.
 */

import { apiFetch, httpErrorFrom } from "./http";

/** What the item's environment route answers. */
export type ItemEnvironment = {
  /** A probe of THIS item, not a listing — a listing answers for whichever
   *  replica took the request, so an absent item may simply be on another pod. */
  running: boolean;
  /** What somebody set. `null` = nobody has, and the size resolves fresh from
   *  `min(App ceiling, owner budget)` every time it is asked. */
  statedCpuCores: number | null;
  statedMemoryBytes: number | null;
  /** What will actually be applied, once the App's ceiling and the owner's
   *  budget have both had their say. */
  effectiveCpuCores: number | null;
  effectiveMemoryBytes: number | null;
  /** What the BACKEND says it will really enforce. `null` means no ceiling will
   *  be applied — and deliberately does not distinguish "this backend caps
   *  nothing" from "we could not ask it", because the backend itself cannot.
   *  A dial the machine will not honour is a promise, so it is not drawn. */
  enforcedCpuCores: number | null;
  enforcedMemoryBytes: number | null;
  /** Which ceiling held a stated size down — the App's or the owner's quota —
   *  or null when nothing did. Answered by the server because it is the only
   *  place that holds both: comparing the effective figure against the numbers
   *  a CLIENT can see meant comparing the viewer's quota with a clamp made
   *  against the owner's, which for a delegate are different people. */
  boundBy: "app" | "quota" | null;
};

/** A size to store. `null` in either dimension CLEARS it — which is not zero,
 *  and not "leave this one alone": it is how "nobody has said" is written. */
export type ItemSize = {
  cpuCores: number | null;
  /** The operator's spelling (`512M` / `2G`), so the wire matches what a person
   *  types and one parser owns the vocabulary. */
  memory: string | null;
};

type Wire = {
  running: boolean;
  stated_cpu_cores: number | null;
  stated_memory_bytes: number | null;
  effective_cpu_cores: number | null;
  effective_memory_bytes: number | null;
  enforced_cpu_cores: number | null;
  enforced_memory_bytes: number | null;
  bound_by: "app" | "quota" | null;
};

export type ItemEnvironmentApi = {
  get(slug: string, itemId: string): Promise<ItemEnvironment>;
  setSize(slug: string, itemId: string, size: ItemSize): Promise<void>;
};

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}`;

export const itemEnvironmentApi: ItemEnvironmentApi = {
  async get(slug, itemId) {
    const r = await apiFetch(`${base(slug, itemId)}/environment`);
    // Checked rather than assumed: a swallowed status resolves exactly like a
    // success, and the panel then renders an empty environment as though that
    // were the answer. That is the failure `closeEnvironment` was fixed for.
    if (!r.ok) throw await httpErrorFrom(r, `environment failed: ${r.status}`);
    const w = (await r.json()) as Wire;
    return {
      running: w.running,
      statedCpuCores: w.stated_cpu_cores,
      statedMemoryBytes: w.stated_memory_bytes,
      effectiveCpuCores: w.effective_cpu_cores,
      effectiveMemoryBytes: w.effective_memory_bytes,
      enforcedCpuCores: w.enforced_cpu_cores ?? null,
      enforcedMemoryBytes: w.enforced_memory_bytes ?? null,
      boundBy: w.bound_by ?? null,
    };
  },
  async setSize(slug, itemId, size) {
    const r = await apiFetch(`${base(slug, itemId)}/resources`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      // Both keys always present. Omitting one would read as "leave that
      // dimension alone", while `null` is what clears it back to the resolved
      // default — two different intents that must not share a spelling.
      body: JSON.stringify({ cpu_cores: size.cpuCores, memory: size.memory }),
    });
    if (!r.ok) throw await httpErrorFrom(r, `set environment size failed: ${r.status}`);
  },
};
