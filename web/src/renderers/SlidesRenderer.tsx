/**
 * A slide deck (pptx / ppt / odp) as a PDF (docs/plan-pptx-preview.md): the
 * item's sandbox converts it, and the browser's own PDF viewer shows it, the
 * way `PdfRenderer` shows a .pdf. A big deck is converted only after the person
 * says yes (N6); a failure says so and offers the original. The Edit toggle
 * shows the file as it was shown before this existed (N3).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { useFileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { Btn } from "../components/Btn";
import { Icon } from "../components/Icon";
import { useEditMode } from "../hooks/editMode";
import { subscribeFileChanged } from "../lib/fileChangedBus";
import { type MsgKey, useT } from "../lib/i18n";
import { type QuotaKind, quotaMessage } from "../lib/quotaFailure";
import { relPath } from "../lib/relPath";
import { TextRenderer } from "./TextRenderer";

/** A preview writes no file, so only the sandbox limit can refuse it; the other
 * two kinds fall back to the plain "try again in a moment". */
const SLIDES_QUOTA_KEY = {
  workspace: "slides.unreachableBody",
  user: "slides.unreachableBody",
  environment: "slides.envFull",
} as const satisfies Record<NonNullable<QuotaKind>, MsgKey>;

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

/**
 * Everything the viewer shows that is not the PDF: one block, centred in the
 * pane — a heading saying what is going on, a line of detail, then the actions
 * (Polaris "Empty state": graphic, heading, detail, one primary action and a
 * secondary beside it). `busy` swaps the graphic for an indeterminate bar.
 */
function SlidesNotice({
  title,
  body,
  busy,
  actions,
}: {
  title: string;
  body?: ReactNode;
  busy?: boolean;
  actions: ReactNode;
}) {
  return (
    <div className="slides-notice">
      {busy ? (
        <div
          className="slides-notice__progress"
          role="progressbar"
          aria-label={title}
          aria-busy="true"
        >
          <div className="slides-notice__bar" />
        </div>
      ) : (
        <Icon name="file" size={32} color="var(--text-paper-d)" />
      )}
      <h3 className="slides-notice__title">{title}</h3>
      {body && <p className="slides-notice__body">{body}</p>}
      <div className="slides-notice__actions">{actions}</div>
    </div>
  );
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
  // A link that downloads, drawn as the outlined (secondary) button it acts as.
  const download = (
    <a
      className="btn"
      data-variant="secondary"
      data-size="md"
      href={svc.fileDownloadUrl(path)}
      download
    >
      <Icon name="download" size={14} />
      {t("slides.download")}
    </a>
  );
  if (!preview) {
    return (
      <SlidesNotice
        title={t("slides.unavailable")}
        body={t("slides.unavailableBody")}
        actions={download}
      />
    );
  }
  if (q.isError) {
    return (
      <SlidesNotice
        title={t("slides.unreachable")}
        body={t("slides.unreachableBody")}
        actions={download}
      />
    );
  }
  if (!data)
    return (
      <SlidesNotice busy title={t("slides.converting")} actions={download} />
    );
  if (data.kind === "confirm") {
    return (
      <SlidesNotice
        title={t("slides.confirm", { size: megabytes(data.size) })}
        body={t("slides.confirmBody")}
        actions={
          <>
            <Btn variant="primary" onClick={() => setConfirmedPath(path)}>
              {t("slides.confirmButton")}
            </Btn>
            {download}
          </>
        }
      />
    );
  }
  if (data.kind === "refused") {
    return (
      <SlidesNotice
        title={t("slides.unreachable")}
        body={
          quotaMessage(t, SLIDES_QUOTA_KEY, {
            status: 507,
            detail: data.detail,
          }) ?? t("slides.unreachableBody")
        }
        actions={download}
      />
    );
  }
  if (data.kind === "failed") {
    return (
      <SlidesNotice
        title={t("slides.failed")}
        body={t("slides.failedBody")}
        actions={download}
      />
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
