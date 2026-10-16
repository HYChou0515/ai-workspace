/**
 * Who a Deployed page's link opens for (`docs/plan-deploy-reach.md` decision 8).
 *
 * A `/w/` page opens for whoever may read the workspace's files, and the URL
 * grants nothing (`WuiPage.tsx`). The server decides a read in two steps —
 * `read_meta` (else 404: the workspace is not theirs to see), then
 * `read_content` (`perm/authorize.py`, `item_authz.check_access`) — so the
 * reach is whoever passes BOTH. ONE function for the Deploy panel and the
 * overview's cards: two copies would describe one page two ways.
 *
 * - `everyone`: no permission (public, the back-compat rule), `public`, or
 *   `restricted` with `all` holding both verbs.
 * - `shared`: `restricted` and someone besides the owner may pass both.
 *   Group membership is not known here, so a group grant on one verb may
 *   cover any grantee of the other — counted as someone.
 * - `owner`: `private`, or `restricted` with nobody else passing both — a
 *   Discoverable grant (`read_meta` only) opens nothing.
 * - `unknown`: a permission this build cannot read — never guessed (#578).
 *
 * The owner and admins are not part of the answer: they always open it.
 */

import { parseItemPermission } from "./itemPermission";

export type PageReach = "everyone" | "shared" | "owner" | "unknown";

const isGroup = (subject: string) => subject.startsWith("group:");

export function pageReach(raw: unknown): PageReach {
  if (raw === undefined || raw === null) return "everyone";
  const perm = parseItemPermission(raw);
  if (!perm) return "unknown";
  if (perm.visibility === "public") return "everyone";
  if (perm.visibility === "private") return "owner";
  const meta = perm.read_meta ?? [];
  const content = perm.read_content ?? [];
  if (meta.includes("all") && content.includes("all")) return "everyone";
  const opens =
    (meta.includes("all") && content.length > 0) ||
    (content.includes("all") && meta.length > 0) ||
    content.some((s) => meta.includes(s)) ||
    (content.some(isGroup) && meta.length > 0) ||
    (meta.some(isGroup) && content.length > 0);
  return opens ? "shared" : "owner";
}
