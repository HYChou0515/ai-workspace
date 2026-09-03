/**
 * What a save sends, given what is stored and what the person just changed.
 *
 * `PUT .../resources` replaces the whole value — both dimensions, every time.
 * The modal used to hard-code `memory: null` on every save, so editing CPU (or
 * clicking "back to default") silently destroyed a stored memory setting, with
 * no way to restore it because there was no memory control at all.
 *
 * The api client's own comment claimed the opposite — "omitting one would read
 * as 'leave that dimension alone'" — describing an intention the server never
 * had. A replace endpoint means the client owns the whole state, and this is
 * the one function that assembles it.
 */

import type { ItemSize } from "../api/itemEnvironment";

/** The two dimensions as the environment route reports them. */
export type StatedSize = {
  statedCpuCores: number | null;
  statedMemoryBytes: number | null;
};

/** What the person just changed. An absent key means "they did not touch this
 *  one" — which is NOT the same as `null`, the value that clears it. */
export type SizeEdit = {
  cpuCores?: number | null;
  memory?: string | null;
};

/** Bytes in the spelling the server parses, so the panel and `config.yaml`
 *  describe the same thing in the same words. Exact powers of two only —
 *  anything else stays a byte count rather than being rounded into a lie. */
function toSizeString(bytes: number | null): string | null {
  if (bytes === null) return null;
  for (const [unit, size] of [
    ["G", 1024 ** 3],
    ["M", 1024 ** 2],
    ["K", 1024],
  ] as const) {
    if (bytes >= size && bytes % size === 0) return `${bytes / size}${unit}`;
  }
  return String(bytes);
}

export function sizeToSave(stated: StatedSize, edit: SizeEdit): ItemSize {
  return {
    cpuCores: "cpuCores" in edit ? (edit.cpuCores ?? null) : stated.statedCpuCores,
    memory:
      "memory" in edit ? (edit.memory ?? null) : toSizeString(stated.statedMemoryBytes),
  };
}
