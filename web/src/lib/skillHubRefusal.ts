import { HttpError } from "../api/http";
import type { MsgKey, Vars } from "./i18n";

/**
 * A skill hub refusal, in the viewer's language (plan-skill-hub-ui-polish
 * D16). The routes refuse with a code and the parameters a sentence needs
 * (`skill_hub_routes.py`, `FolderInTheWay.code()`); this is the one place
 * they become words. Anything else — an unknown code, a server that still
 * sends a sentence, a non-HTTP failure — reads as its message.
 */
const KEY: Record<string, MsgKey> = {
  not_found: "skillHub.refused.not_found",
  owner_only: "skillHub.refused.owner_only",
  transfer_owner_required: "skillHub.refused.transfer_owner_required",
  transfer_name_taken: "skillHub.refused.transfer_name_taken",
  folder_in_the_way: "skillHub.refused.folder_in_the_way",
};

const str = (v: unknown): string => (typeof v === "string" ? v : "");

export function describeRefusal(
  e: unknown,
  t: (key: MsgKey, vars?: Vars) => string,
): string {
  if (e instanceof HttpError && e.code && Object.hasOwn(KEY, e.code)) {
    const d = e.detail ?? {};
    const owner = str(d.owner);
    if (e.code === "folder_in_the_way") {
      // Whose copy is in the way, when it is one — "already have it" and
      // "name clash" read differently.
      return owner
        ? t("skillHub.refused.folder_in_the_way.theirs", {
            owner,
            path: str(d.path),
          })
        : t(KEY[e.code], { path: str(d.path) });
    }
    return t(KEY[e.code], { owner, name: str(d.name) });
  }
  return e instanceof Error ? e.message : String(e);
}
