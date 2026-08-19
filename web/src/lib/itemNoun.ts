/**
 * What an App calls its items, resolved from the manifest.
 *
 * Every surface that names an item — the dashboard, the chat-first rail, an
 * empty state — needs the same three strings and the same fallback rule, and
 * `create_label` is optional so the rule is not merely a lookup. It lived inline
 * in `AppDashboard`; a second copy is how two surfaces start disagreeing, which
 * is exactly what happened to the platform menu next door.
 *
 * The defaults are deliberately generic rather than "chat": the rail lists
 * ITEMS, and in an App where one item holds many conversations (a PM project
 * does) calling it a chat names the wrong level.
 */
import type { AppManifest } from "../api/types";

export type ItemNouns = {
  /** Singular, as declared — "Project", "Investigation". */
  noun: string;
  /** Plural, for list headings — "Projects". */
  plural: string;
  /** The create action's label; falls back to "New <noun>". */
  createLabel: string;
};

export function itemNouns(manifest: AppManifest | undefined): ItemNouns {
  const noun = manifest?.item?.noun ?? "Item";
  const plural = manifest?.item?.noun_plural ?? "Items";
  return { noun, plural, createLabel: manifest?.item?.create_label ?? `New ${noun}` };
}
