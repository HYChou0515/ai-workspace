/**
 * KB document viewer — an overlay that renders a source document so a citation
 * can be inspected in place. kb:// links swap the viewed document in place; the
 * "open in new tab" button opens the same document on its dedicated page.
 */

import { useEffect, useState } from "react";

import { kbApi, type KbApi } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { KbDocBody } from "./KbDocBody";
import { docHref } from "./kbLinks";

export function KbDocViewer({
  documentId,
  snippet,
  onClose,
  client = kbApi,
}: {
  documentId: string;
  /** The cited passage text, shown as a callout when opened from a citation. */
  snippet?: string;
  onClose: () => void;
  client?: KbApi;
}) {
  const [docId, setDocId] = useState(documentId);
  const [filename, setFilename] = useState<string | null>(null);

  useEffect(() => setDocId(documentId), [documentId]);

  return (
    <>
      <div className="kb-drawer-backdrop" onClick={onClose} aria-hidden />
      <aside className="kb-docviewer" role="dialog" aria-label="Document">
        <header className="kb-docviewer__head">
          <Icon name="file" size={16} color="var(--text-paper-d)" />
          <span className="kb-docviewer__name">{filename ?? "Document"}</span>
          <a
            className="kb-iconbtn"
            href={docHref(docId, snippet)}
            target="_blank"
            rel="noreferrer"
            title="Open in new tab"
            aria-label="Open in new tab"
          >
            <Icon name="arrow_u" size={15} />
          </a>
          <button type="button" className="kb-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>
        <div className="kb-docviewer__body">
          <KbDocBody
            documentId={docId}
            snippet={snippet}
            onNavigate={setDocId}
            onLoaded={(d) => setFilename(d.filename)}
            client={client}
          />
        </div>
      </aside>
    </>
  );
}
