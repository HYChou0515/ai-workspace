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

export type AiWorkspaceHelperPageClient = {
  release: () => Promise<HelperRelease | null>;
  helperStatus: () => Promise<{ online: boolean }>;
};

/** Bytes as people read them: whole megabytes past one. */
export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function AiWorkspaceHelperPage({
  client = outsideLookupApi,
}: {
  client?: AiWorkspaceHelperPageClient;
}) {
  const t = useT();
  const release = useQuery({ queryKey: qk.aiWorkspaceHelperRelease(), queryFn: () => client.release() });
  const status = useQuery({
    queryKey: qk.aiWorkspaceHelper(),
    queryFn: () => client.helperStatus(),
    refetchInterval: 15_000,
  });

  return (
    <div className="page">
      <h1>{t("aiWorkspaceHelper.title")}</h1>
      <p style={MUTED}>{t("aiWorkspaceHelper.desc")}</p>
      {status.isSuccess && (
        <p data-testid="ai-workspace-helper-status">
          {t(status.data.online ? "aiWorkspaceHelper.online" : "aiWorkspaceHelper.offline")}
        </p>
      )}

      {release.isPending ? (
        <p style={MUTED}>…</p>
      ) : !release.data ? (
        <p data-testid="ai-workspace-helper-unpublished" style={MUTED}>
          {t("aiWorkspaceHelper.unpublished")}
        </p>
      ) : (
        <section aria-labelledby="ai-workspace-helper-install" style={{ display: "grid", gap: 8 }}>
          <h2 id="ai-workspace-helper-install">{t("aiWorkspaceHelper.install")}</h2>
          <p data-testid="ai-workspace-helper-version" style={MUTED}>
            {t("aiWorkspaceHelper.version", { version: release.data.version })}
          </p>
          <p>
            {/* Getting a file is an action, so it is drawn as the page's one
                primary button (Material 3, buttons; the app's own download in
                KbDocViewer); still an <a download> for what it does. */}
            <a
              className="btn"
              data-variant="primary"
              href={`${API_PREFIX}/ai-workspace-helper/download/${encodeURIComponent(release.data.file)}`}
              download={release.data.file}
            >
              <Icon name="download" size={14} />{" "}
              {t("aiWorkspaceHelper.download", {
                file: release.data.file,
                size: fileSize(release.data.size),
              })}
            </a>
          </p>
          <ol style={{ display: "grid", gap: 4, paddingLeft: pxToRem(20) }}>
            <li>{t("aiWorkspaceHelper.step1")}</li>
            <li>{t("aiWorkspaceHelper.step2")}</li>
            <li>
              {/* With the deploy's base path: the helper asks `<this>/api/…`. */}
              {t("aiWorkspaceHelper.step3")}{" "}
              <code style={MONO}>{`${window.location.origin}${API_BASE}`}</code>
            </li>
            <li>{t("aiWorkspaceHelper.step4")}</li>
          </ol>
        </section>
      )}
    </div>
  );
}
