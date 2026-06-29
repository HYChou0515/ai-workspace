import { Icon } from "../../components/Icon";
import type { BlockedUpload } from "../../kb/uploadChecks";
import { isMsgKey, type MsgKey, useT } from "../../lib/i18n";

export type { BlockedUpload };

/**
 * The "can't accept" section of the index-status strip (#325): files refused at
 * upload because they're encrypted/unreadable. Unlike the background-failure
 * list there's no doc to open — just the file name, why it was refused, and what
 * to do (decrypt + re-upload). Both rejection paths (the browser pre-block and
 * the server 422) feed this list; the user dismisses it once acknowledged.
 */
export function UploadBlockedList({
  items,
  onDismiss,
}: {
  items: BlockedUpload[];
  onDismiss: () => void;
}) {
  const t = useT();
  if (items.length === 0) return null;
  // A server key we don't recognise (older FE vs newer check) still shows
  // actionable copy instead of crashing translate().
  const reason = (key: string): string =>
    t(isMsgKey(key) ? (key as MsgKey) : "kb.upload.blocked.unreadable");
  return (
    <div className="kb-index-status__blocked" data-testid="kb-upload-blocked">
      <div className="kb-index-status__blocked-head">
        <Icon name="x" size={13} color="var(--err)" />
        <span>{t("kb.status.cantAccept", { n: items.length })}</span>
        <button
          type="button"
          className="kb-index-status__blocked-dismiss"
          onClick={onDismiss}
        >
          {t("kb.status.cantAcceptDismiss")}
        </button>
      </div>
      <ul className="kb-index-status__fail-list">
        {items.map((it, i) => (
          <li key={`${it.name}-${i}`} className="kb-index-status__blocked-item">
            <span className="kb-index-status__fail-name">{it.name}</span>
            <span className="kb-index-status__fail-reason">{reason(it.messageKey)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
