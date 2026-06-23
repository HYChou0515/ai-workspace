/**
 * Dedicated document page (route /kb/doc/*) — the "open in new tab" target for
 * a document. Same body as the citation drawer, but full-page and shareable.
 * The id is the splat after /kb/doc/; an optional ?hl= highlights a passage.
 */

import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { useBreadcrumbs } from "../../hooks/breadcrumbs";
import { KbDocBody } from "./KbDocBody";
import { docPath } from "./kbLinks";

export function KbDocPage({ client = kbApi }: { client?: KbApi }) {
  const params = useParams();
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const documentId = params["*"] ?? "";
  const snippet = sp.get("hl") ?? undefined;
  const [filename, setFilename] = useState<string | null>(null);
  useBreadcrumbs([
    { label: "Home", to: "/" },
    { label: "Knowledge base", to: "/kb" },
    { label: filename ?? "Document" },
  ]);

  return (
    <div className="kb-docpage">
      <header className="kb-docpage__head">
        <button type="button" className="kb-nav__back" onClick={() => navigate("/kb")}>
          <Icon name="chev_l" size={13} /> Knowledge base
        </button>
        <Icon name="file" size={15} color="var(--text-paper-d)" />
        <span className="kb-docpage__name">{filename ?? "Document"}</span>
      </header>
      <div className="kb-docpage__body">
        <KbDocBody
          documentId={documentId}
          snippet={snippet}
          onNavigate={(id) => navigate(docPath(id))}
          onLoaded={(d) => setFilename(d.filename)}
          showChunks
          client={client}
        />
      </div>
    </div>
  );
}
