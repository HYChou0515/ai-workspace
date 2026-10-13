/**
 * 小幫手 (docs/plan-outside-helper.md §3.6, D16): where a person downloads
 * the helper the operator published and reads how to install it, and sees
 * whether their own helper is connected.
 *
 * The download is a link that names the file, its type and its size — what a
 * link to a non-web file should say before it is followed (NN/g, "Avoid PDF
 * for On-Screen Reading": link text states file type and size; GOV.UK Design
 * System, attachment pattern).
 */

import { useQuery } from "@tanstack/react-query";

import { API_BASE, API_PREFIX } from "../api/http";
import { type HelperRelease, outsideLookupApi } from "../api/outsideLookup";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

const MUTED = { fontSize: pxToRem(12), color: "var(--text-paper-d)" } as const;
const MONO = { fontFamily: "var(--font-mono, ui-monospace, monospace)" } as const;

export type AiWorkspacePageClient = {
  release: () => Promise<HelperRelease | null>;
  helperStatus: () => Promise<{ online: boolean }>;
};

/** Bytes as people read them: whole megabytes past one. */
export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function AiWorkspacePage({
  client = outsideLookupApi,
}: {
  client?: AiWorkspacePageClient;
}) {
  const t = useT();
  const release = useQuery({ queryKey: qk.aiWorkspaceRelease(), queryFn: () => client.release() });
  const status = useQuery({
    queryKey: qk.aiWorkspace(),
    queryFn: () => client.helperStatus(),
    refetchInterval: 15_000,
  });

  return (
    <div className="page">
      <h1>{t("aiWorkspace.title")}</h1>
      <p style={MUTED}>{t("aiWorkspace.desc")}</p>
      {status.isSuccess && (
        <p data-testid="ai-workspace-status">
          {t(status.data.online ? "aiWorkspace.online" : "aiWorkspace.offline")}
        </p>
      )}

      {release.isPending ? (
        <p style={MUTED}>…</p>
      ) : !release.data ? (
        <p data-testid="ai-workspace-unpublished" style={MUTED}>
          {t("aiWorkspace.unpublished")}
        </p>
      ) : (
        <section aria-labelledby="ai-workspace-install" style={{ display: "grid", gap: 8 }}>
          <h2 id="ai-workspace-install">{t("aiWorkspace.install")}</h2>
          <p data-testid="ai-workspace-version" style={MUTED}>
            {t("aiWorkspace.version", { version: release.data.version })}
          </p>
          <p>
            {/* Getting a file is an action, so it is drawn as the page's one
                primary button (Material 3, buttons; the app's own download in
                KbDocViewer); still an <a download> for what it does. */}
            <a
              className="btn"
              data-variant="primary"
              href={`${API_PREFIX}/ai-workspace/download/${encodeURIComponent(release.data.file)}`}
              download={release.data.file}
            >
              <Icon name="download" size={14} />{" "}
              {t("aiWorkspace.download", {
                file: release.data.file,
                size: fileSize(release.data.size),
              })}
            </a>
          </p>
          <ol style={{ display: "grid", gap: 4, paddingLeft: pxToRem(20) }}>
            <li>{t("aiWorkspace.step1")}</li>
            <li>{t("aiWorkspace.step2")}</li>
            <li>
              {/* With the deploy's base path: the helper asks `<this>/api/…`. */}
              {t("aiWorkspace.step3")}{" "}
              <code style={MONO}>{`${window.location.origin}${API_BASE}`}</code>
            </li>
            <li>{t("aiWorkspace.step4")}</li>
          </ol>
        </section>
      )}
    </div>
  );
}
