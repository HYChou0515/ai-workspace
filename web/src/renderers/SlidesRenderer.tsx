/**
 * A slide deck (pptx / ppt / odp) as a PDF (docs/plan-pptx-preview.md): the
 * item's sandbox converts it, and the browser's own PDF viewer shows it, the
 * way `PdfRenderer` shows a .pdf. A big deck is converted only after the person
 * says yes (N6); a failure gives the converter's reason and the original. The
 * Edit toggle shows the file as it was shown before this existed (N3).
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { useFileService } from "../api/fileService";
import { useEditMode } from "../hooks/editMode";
import { useT } from "../lib/i18n";
import { relPath } from "../lib/relPath";
import { TextRenderer } from "./TextRenderer";

const MUTED = { color: "var(--text-paper-d)" } as const;

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

export function SlidesRenderer({ path }: { path: string }) {
  const t = useT();
  const svc = useFileService();
  const { isEditing } = useEditMode();
  const [confirmed, setConfirmed] = useState(false);
  const preview = svc.slidePreview;
  const q = useQuery({
    queryKey: ["slidePreview", svc.scopeId, path, confirmed],
    queryFn: () => preview!(path, confirmed),
    enabled: Boolean(preview) && !isEditing(path),
    // The server answers from its cache when the deck is unchanged; asking
    // again on open is how an edited deck gets its new preview.
    staleTime: 0,
    retry: false,
  });
  const data = q.data;
  const url = useMemo(
    () => (data?.kind === "pdf" ? URL.createObjectURL(data.blob) : null),
    [data],
  );
  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);

  if (isEditing(path)) return <TextRenderer path={path} />;
  if (!preview) return <div style={MUTED}>{t("slides.unavailable")}</div>;
  const download = (
    <a href={svc.fileDownloadUrl(path)} download>
      {t("slides.download")}
    </a>
  );
  if (q.isError) {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <span>{t("slides.failed", { why: String(q.error) })}</span>
        {download}
      </div>
    );
  }
  if (!data) return <div style={MUTED}>{t("slides.converting")}</div>;
  if (data.kind === "confirm") {
    return (
      <div style={{ display: "grid", gap: 8, justifyItems: "start" }}>
        <span>{t("slides.confirm", { size: megabytes(data.size) })}</span>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          onClick={() => setConfirmed(true)}
        >
          {t("slides.confirmButton")}
        </button>
        {download}
      </div>
    );
  }
  if (data.kind === "failed") {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <span>{t("slides.failed", { why: data.why })}</span>
        {download}
      </div>
    );
  }
  return (
    <iframe
      title={relPath(path)}
      src={url ?? undefined}
      style={{
        width: "100%",
        height: "100%",
        minHeight: 0,
        border: 0,
        background: "#fff",
      }}
    />
  );
}
