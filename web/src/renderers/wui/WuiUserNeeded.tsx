/**
 * A page someone pressed but may not use (docs/plan-wui-user.md D7): said ABOVE
 * the frame, by the platform, so it reads the same whatever the page's own error
 * panel does with the refusal. Drawn once a page route answered
 * `needs_use_wui`; the page still gets its answer.
 *
 * Who can fix it is the owner — named, as #968's reader side does (D4 there),
 * because nobody who sees this can change the item's access.
 *
 * Says what happened and what fixes it, next to where it happened (NN/g,
 * "Error-Message Guidelines": constructive advice, close to the error), as a
 * passive, conditional indicator rather than a dialog (NN/g, "Indicators,
 * Validations, and Notifications") — the page behind it still works for reading.
 */
import { useAppItem, useAppManifest } from "../../hooks/useResources";
import { useUser } from "../../hooks/useUsers";
import { useT } from "../../lib/i18n";
import { RequestAccess } from "./RequestAccess";

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
      {slug && (
        <span style={{ marginLeft: 8 }}>
          <RequestAccess
            slug={slug}
            itemId={itemId}
            inside="wui.needsWuiUser.inside"
            insideVars={{ owner: ownerName || owner || t("wui.needsWuiUser.theOwner") }}
          />
        </span>
      )}
    </div>
  );
}
