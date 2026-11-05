/**
 * What a view file's `publish:` says (`docs/plan-wui-publish-and-agent-hub.md`
 * W5/A4): the choices the WUI or agent skill wrote after asking the person.
 * They pre-fill the publish dialog and take effect only when someone presses
 * Publish there (C9) — so anything malformed is simply not pre-filled.
 */

import type { Published, PublishChoices, Reach, Role, Sharing } from "../api/publish";

const SHARINGS: readonly Sharing[] = ["fresh", "per_person", "shared", "direct", "per_conversation"];
const REACHES: readonly Reach[] = ["me", "subjects", "everyone"];
const ROLES: readonly Role[] = ["reader", "participant", "collaborator"];

const GB = 1024 ** 3;

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

function positive(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : undefined;
}

export function choicesFromSpec(publish: unknown): Partial<PublishChoices> {
  if (publish === null || typeof publish !== "object" || Array.isArray(publish)) return {};
  const raw = publish as Record<string, unknown>;
  const out: Partial<PublishChoices> = {};
  const sharing = oneOf(raw.sharing, SHARINGS);
  if (sharing) out.sharing = sharing;
  const reach = oneOf(raw.reach, REACHES);
  if (reach) out.reach = reach;
  if (Array.isArray(raw.subjects)) {
    out.subjects = raw.subjects.filter(
      (s): s is string => typeof s === "string" && /^(user|group):.+/.test(s),
    );
  }
  const role = oneOf(raw.role, ROLES);
  if (role) out.role = role;
  const cpu = positive(raw.cpu_cores);
  if (cpu !== undefined) out.cpu_cores = cpu;
  const memoryGb = positive(raw.memory_gb);
  if (memoryGb !== undefined) out.memory_bytes = Math.round(memoryGb * GB);
  return out;
}

/** What a re-publish starts from: the publication as it is in effect now
 * (C9/C10). Everything the dialog asks, so pressing Publish again changes only
 * what the person changed — never resets who may use it or the copies' size. */
export function choicesFromPublished(
  row: Pick<Published, "sharing" | "reach" | "subjects" | "role" | "cpu_cores" | "memory_bytes">,
): Partial<PublishChoices> {
  return {
    sharing: row.sharing,
    reach: row.reach,
    subjects: [...row.subjects],
    role: row.role,
    cpu_cores: row.cpu_cores ?? null,
    memory_bytes: row.memory_bytes ?? null,
  };
}
