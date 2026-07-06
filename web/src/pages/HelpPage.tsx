/**
 * HelpPage (route /help, #230) — the platform's introduction / help surface.
 *
 * Reuses the seeded "Platform Help" KB collection: it renders that collection's
 * documents (usage guides + release notes) as links into the existing KB
 * document viewer, and embeds a KB chat permanently scoped to the collection so
 * users can ask how-to questions and get cited answers. Thin by design — the
 * doc bodies, search and chat are all the existing KB machinery. #281 will later
 * feed source-code-derived wiki into the same collection.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";

import { helpApi, type HelpApi, type HelpDocument } from "../api/help";
import { kbApi, type KbApi } from "../api/kb";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { KbChatPanel } from "./kb/KbChatPanel";
import { docPath } from "./kb/kbLinks";

function DocList({ title, docs }: { title: string; docs: HelpDocument[] }) {
  if (docs.length === 0) return null;
  return (
    <section style={{ marginBottom: 20 }}>
      <h2 style={{ fontSize: pxToRem(13), fontWeight: 700, color: "var(--text-paper-d)", margin: "0 0 8px" }}>
        {title}
      </h2>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
        {docs.map((d) => (
          <li key={d.id}>
            <Link
              to={docPath(d.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 12px",
                border: "1px solid var(--paper-3)",
                borderRadius: "var(--radius-card)",
                background: "var(--white)",
                color: "inherit",
                textDecoration: "none",
              }}
            >
              <Icon name="file" size={14} color="var(--text-paper-d)" />
              {d.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function HelpPage({
  client = helpApi,
  chatClient = kbApi,
}: {
  client?: HelpApi;
  chatClient?: KbApi;
}) {
  const t = useT();
  const navigate = useNavigate();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("help.title") }]);

  const { data } = useQuery({ queryKey: qk.help, queryFn: () => client.getHelpInfo() });
  const docs = useMemo(() => data?.documents ?? [], [data]);
  const guides = useMemo(() => docs.filter((d) => d.kind !== "release_notes"), [docs]);

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: "24px 20px", width: "100%" }}>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: pxToRem(22), fontWeight: 800, margin: "0 0 6px" }}>{t("help.title")}</h1>
        <p style={{ color: "var(--text-paper-d)", margin: 0 }}>{t("help.intro")}</p>
      </header>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 24,
          alignItems: "flex-start",
        }}
      >
        <div style={{ flex: "1 1 280px", minWidth: 240 }}>
          <DocList title={t("help.guides")} docs={guides} />
          <section style={{ marginBottom: 20 }}>
            <h2 style={{ fontSize: pxToRem(13), fontWeight: 700, color: "var(--text-paper-d)", margin: "0 0 8px" }}>
              {t("help.releaseNotes")}
            </h2>
            <Link
              to="/help/releases"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 12px",
                border: "1px solid var(--paper-3)",
                borderRadius: "var(--radius-card)",
                background: "var(--white)",
                color: "inherit",
                textDecoration: "none",
              }}
            >
              <Icon name="file" size={14} color="var(--text-paper-d)" />
              {t("help.releaseNotes.view")}
            </Link>
          </section>
          {data && docs.length === 0 && (
            <p style={{ color: "var(--text-paper-d)" }}>{t("help.empty")}</p>
          )}
        </div>

        <div style={{ flex: "2 1 420px", minWidth: 320, display: "flex", flexDirection: "column" }}>
          <h2 style={{ fontSize: pxToRem(13), fontWeight: 700, color: "var(--text-paper-d)", margin: "0 0 4px" }}>
            {t("help.ask")}
          </h2>
          <p style={{ color: "var(--text-paper-d)", margin: "0 0 10px", fontSize: pxToRem(13) }}>
            {t("help.ask.note")}
          </p>
          <div
            style={{
              height: 540,
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-card)",
              overflow: "hidden",
              display: "flex",
            }}
          >
            {data && (
              <KbChatPanel
                collectionIds={[data.collection_id]}
                hideCollectionPicker
                client={chatClient}
                onOpenCitation={(c) => navigate(docPath(c.document_id, c.snippet))}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
