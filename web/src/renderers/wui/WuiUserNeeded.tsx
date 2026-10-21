/**
 * A page someone pressed but may not use (docs/plan-wui-user.md D7): said ABOVE
 * the frame, by the platform, so it reads the same whatever the page's own error
 * panel does with the refusal. Drawn once a page route answered
 * `needs_use_wui`; the page still gets its answer.
 *
 * Who can fix it is the owner — named, as #968's reader side does (D4 there),
 * because nobody who sees this can change the item's access.
 */
import { useAppItem, useAppManifest } from "../../hooks/useResources";
import { useUser } from "../../hooks/useUsers";
import { useT } from "../../lib/i18n";

export function WuiUserNeeded({ slug, itemId }: { slug: string | null; itemId: string }) {
  const t = useT();
  const manifest = useAppManifest(slug ?? "");
  const item = useAppItem(slug ?? "", manifest?.resource_route, itemId);
  const owner = item ? (item.created_by as string) || (item.owner as string) || "" : "";
  const ownerName = useUser(owner).name;
  return (
    <div
      role="status"
      data-testid="wui-needs-wui-user"
      style={{ padding: "6px 8px", borderBottom: "1px solid var(--paper-3)", color: "var(--text-paper)" }}
    >
      {t("wui.needsWuiUser")}
      {owner ? ` ${t("wui.needsWuiUser.owner", { owner: ownerName || owner })}` : ""}
    </div>
  );
}
