/**
 * Who a just-Deployed link opens for — said to the publisher under the address
 * (`docs/plan-deploy-reach.md` decisions 2, 4, 5).
 *
 * The address is a shortcut, not a grant (`WuiPage.tsx`), and a new workspace
 * is private — so "anyone who can open this item" was a true rule that let
 * people hand out links nobody else could open. This states the FACT instead,
 * from the workspace's own query (`qk.appItem`, the one the top bar reads and
 * the share dialog's save refreshes — so the sentence follows a change made
 * from the button beside it), and puts the fix where the problem is read.
 *
 * An indicator, not a notification (NN/g, "Indicators, Validations, and
 * Notifications"): passive, conditional, next to the thing it describes. No
 * confirm before Deploy — deploying a private page to look at it yourself is a
 * real use (decision 1).
 */

import { useState } from "react";

import { ItemAccessDialog } from "../../components/ItemMembersPanel";
import { Btn } from "../../components/Btn";
import { ShareChatDialog } from "../../components/ShareChatDialog";
import { useCurrentUserState } from "../../hooks/useCurrentUser";
import { useIsSuperuserState } from "../../hooks/useIsSuperuser";
import { useAppItem, useAppManifest } from "../../hooks/useResources";
import { useUser } from "../../hooks/useUsers";
import { useT, type MsgKey } from "../../lib/i18n";
import { canChangeItemPermission, parseItemPermission } from "../../lib/itemPermission";
import { pageReach } from "../../lib/pageReach";

export function DeployReach({ slug, itemId }: { slug: string; itemId: string }) {
  const t = useT();
  const manifest = useAppManifest(slug);
  const item = useAppItem(slug, manifest?.resource_route, itemId);
  const me = useCurrentUserState();
  const { isSuperuser, groups } = useIsSuperuserState();
  const owner = item ? (item.created_by as string) || (item.owner as string) || "" : "";
  const ownerName = useUser(owner).name;
  const [sharing, setSharing] = useState(false);

  const raw = item ? (item as Record<string, unknown>).permission : undefined;
  const reach = item ? pageReach(raw) : "unknown";
  if (!manifest || !item || reach === "unknown" || !me.ready) {
    // Not read yet, or written by a newer build: the rule, never a guess
    // (#578). Nor before the viewer is known — the placeholder id is not the
    // owner, and the owner would read the non-owner's sentence.
    return <div style={{ color: "var(--text-paper-d)" }}>{t("wui.deploy.reach.unknown")}</div>;
  }

  const sentence: MsgKey =
    reach === "everyone"
      ? "wui.deploy.reach.everyone"
      : reach === "shared"
        ? "wui.deploy.reach.shared"
        : me.id === owner
          ? "wui.deploy.reach.owner"
          : "wui.deploy.reach.ownerAndAdmins";
  // The rule the top bar's access chip uses (`WorkspaceShell`), superusers and
  // delegated `change_permission` included.
  const canChange = canChangeItemPermission(parseItemPermission(raw), me.id, owner, isSuperuser, groups);
  const chatFirst = manifest.layout?.primary_surface === "chat";

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <span>{t(sentence)}</span>
      {reach !== "everyone" &&
        (canChange ? (
          <Btn size="sm" onClick={() => setSharing(true)}>
            {t("wui.deploy.share")}
          </Btn>
        ) : (
          <span style={{ color: "var(--text-paper-d)" }}>{t("wui.deploy.askOwner", { owner: ownerName })}</span>
        ))}
      {sharing &&
        (chatFirst ? (
          <ShareChatDialog slug={slug} item={item} onClose={() => setSharing(false)} />
        ) : (
          <ItemAccessDialog manifest={manifest} item={item} onClose={() => setSharing(false)} />
        ))}
    </div>
  );
}
