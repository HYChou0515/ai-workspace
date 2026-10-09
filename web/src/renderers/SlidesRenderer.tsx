/**
 * A slide deck (pptx / ppt / odp) as a PDF (docs/plan-pptx-preview.md): the
 * item's sandbox converts it, and the browser's own PDF viewer shows it, the
 * way `PdfRenderer` shows a .pdf. A big deck is converted only after the person
 * says yes (N6); a failure says so and offers the original. The Edit toggle
 * shows the file as it was shown before this existed (N3).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { useFileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { useEditMode } from "../hooks/editMode";
import { subscribeFileChanged } from "../lib/fileChangedBus";
import { type MsgKey, useT } from "../lib/i18n";
import { type QuotaKind, quotaMessage } from "../lib/quotaFailure";
import { relPath } from "../lib/relPath";
import { TextRenderer } from "./TextRenderer";

const MUTED = { color: "var(--text-paper-d)" } as const;

/** A preview writes no file, so only the sandbox limit can refuse it; the other
 * two kinds fall back to the plain "can't preview right now". */
const SLIDES_QUOTA_KEY = {
  workspace: "slides.unreachable",
  user: "slides.unreachable",
  environment: "slides.envFull",
} as const satisfies Record<NonNullable<QuotaKind>, MsgKey>;

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

export function SlidesRenderer({ path }: { path: string }) {
  const t = useT();
  const svc = useFileService();
  const { isEditing } = useEditMode();
  // A yes is for the deck it was given to: the next big deck asks again.
  const [confirmedPath, setConfirmedPath] = useState<string | null>(null);
  const confirmed = confirmedPath === path;
  const preview = svc.slidePreview;
  const qc = useQueryClient();
  // A person's save arrives on the bus, not through the refresh chain.
  useEffect(
    () =>
      subscribeFileChanged(svc.scopeId, (changed) => {
        if (relPath(changed) === relPath(path)) {
          void qc.invalidateQueries({ queryKey: qk.file(svc.scopeId, path) });
        }
      }),
    [qc, svc.scopeId, path],
  );
  const q = useQuery({
    queryKey: qk.slidePreview(svc.scopeId, path, confirmed),
    queryFn: () => preview!(path, confirmed),
    enabled: Boolean(preview) && !isEditing(path),
    // The server answers from its cache when the deck is unchanged; asking
    // again on open is how an edited deck gets its new preview.
    staleTime: 0,
    retry: false,
  });
  const data = q.data;
  // A yes is for the content it was given to, too: once its PDF is here the
  // server has it cached, so later asks go without the yes — the same version
  // comes straight back, and new content that is big is asked about again.
  // The no-yes key still holds the first answer ("big — preview?"); give it the
  // PDF first, or dropping the yes would flash the question back.
  useEffect(() => {
    if (!confirmed || data?.kind !== "pdf") return;
    qc.setQueryData(qk.slidePreview(svc.scopeId, path, false), data);
    setConfirmedPath(null);
  }, [confirmed, data, qc, svc.scopeId, path]);
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
        <span>{t("slides.unreachable")}</span>
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
          onClick={() => setConfirmedPath(path)}
        >
          {t("slides.confirmButton")}
        </button>
        {download}
      </div>
    );
  }
  if (data.kind === "refused") {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <span>
          {quotaMessage(t, SLIDES_QUOTA_KEY, { status: 507, detail: data.detail }) ??
            t("slides.unreachable")}
        </span>
        {download}
      </div>
    );
  }
  if (data.kind === "failed") {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <span>{t("slides.failed")}</span>
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
