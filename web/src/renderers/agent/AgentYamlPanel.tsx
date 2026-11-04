/**
 * The agent panel (`docs/plan-wui-publish-and-agent-hub.md` A6): what opening
 * an `agent.ai.yaml` in the workspace shows.
 *
 * The author sees what others will get BEFORE publishing (NN/g, *Visibility of
 * system status*): its name and look, the tools and skills a conversation with
 * it holds — asked of the server's turn path, not re-derived here — what its
 * file gets wrong, and whether and how it is published, with its usage. Then
 * the two things to do: **Publish agent** (the shared dialog, A2's one way of
 * sharing and A9's release note on a re-publish) and **Test chat**, which opens
 * a chat beside the file that runs as this agent in the author's own
 * workspace. The raw yaml is the pane's Edit switch away, like every other
 * structured preview.
 *
 * Named `AgentYamlPanel` because `AgentPanel` is the chat panel.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { agentApi, type AgentApi, type AgentSummary } from "../../api/agents";
import { useFileService } from "../../api/fileService";
import { publishApi, type PublishApi, type PublishChoices } from "../../api/publish";
import { qk } from "../../api/queryKeys";
import { exactTime, relativeTime } from "../../api/types";
import { useDialog } from "../../components/Dialog";
import { PageMark } from "../../components/PageMark";
import { PublishDialog } from "../../components/PublishDialog";
import { UserChip } from "../../components/UserChip";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { canOpenAgentTestChat, publishAgentTestChat } from "../../lib/agentTestChatBus";
import { type MsgKey, useT } from "../../lib/i18n";
import { choicesFromSpec } from "../../lib/publishSpec";
import { parseViewSpec, viewParam } from "../entity/shared";

export function AgentYamlPanel({
  path,
  text,
  client = agentApi,
  publisher = publishApi,
}: {
  path: string;
  /** The file as the pane holds it — what the summary is of. */
  text: string;
  /** Seams for tests. */
  client?: Pick<AgentApi, "summary">;
  publisher?: Pick<PublishApi, "publish" | "unpublish" | "estimate">;
}) {
  const t = useT();
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const [asking, setAsking] = useState(false);

  // Keyed on the file's text: an edit is a different agent to summarise.
  const summary = useQuery({
    queryKey: qk.agentSummary(itemId, path, text),
    queryFn: () => client.summary(slug, itemId, path),
    enabled: !!slug,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: qk.agentSummaryOf(itemId, path) });
  const publish = useMutation({
    mutationFn: (choices: PublishChoices) => publisher.publish(slug, itemId, path, choices),
    onSuccess: refresh,
    meta: { silentError: true },
  });
  const unpublish = useMutation({
    mutationFn: () => publisher.unpublish(slug, itemId, path),
    onSuccess: refresh,
    meta: { silentError: true },
  });

  if (summary.isPending) return <p style={muted}>{t("agentPanel.loading")}</p>;
  if (summary.isError) {
    return (
      <p role="alert" style={{ ...muted, color: "var(--err)" }}>
        {t("agentPanel.failed", { why: summary.error.message })}
      </p>
    );
  }
  const s = summary.data;
  const spec = parseViewSpec(text);
  const initial: Partial<PublishChoices> = s.published
    ? { reach: s.published.reach, subjects: s.published.subjects }
    : choicesFromSpec(spec ? viewParam(spec, "publish") : undefined);
  const canTest = s.valid && canOpenAgentTestChat(itemId);
  const blockedReason = !s.valid || s.problems.length > 0 ? t("agentPanel.fixFirst") : null;

  const askUnpublish = () => {
    void confirm({
      title: t("agentPanel.unpublish.title", { title: s.title }),
      body: t("agentPanel.unpublish.body"),
      actions: [
        { id: "cancel", label: t("publish.cancel") },
        { id: "go", label: t("agentPanel.unpublish"), variant: "danger" },
      ],
    }).then((choice) => {
      if (choice === "go") unpublish.mutate();
    });
  };

  return (
    <section data-testid="agent-panel" aria-labelledby="agent-panel-title" style={panel}>
      <header style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <PageMark
          slug={slug}
          itemId={itemId}
          path={path}
          icon={s.icon}
          color={s.color}
          title={s.title || path}
          size={40}
        />
        <div style={{ minWidth: 0 }}>
          <h2 id="agent-panel-title" style={{ margin: 0, fontSize: "1.15rem" }}>
            {s.title || path}
          </h2>
          {s.description && <p style={{ ...muted, margin: "2px 0 0" }}>{s.description}</p>}
        </div>
      </header>

      {s.problems.length > 0 && (
        <div role="alert" style={problemBox}>
          <strong>{t(s.valid ? "agentPanel.problems.warn" : "agentPanel.problems.fatal")}</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {s.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {s.valid && <Capabilities summary={s} />}

      <PublishState summary={s} />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          disabled={blockedReason !== null || publish.isPending}
          onClick={() => setAsking(true)}
        >
          {t(s.published ? "agentPanel.republish" : "agentPanel.publish")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          disabled={!canTest}
          title={canTest ? t("agentPanel.test.tip") : undefined}
          onClick={() =>
            publishAgentTestChat(itemId, { path, title: t("agentPanel.test.chatTitle", { title: s.title }) })
          }
        >
          {t("agentPanel.test")}
        </button>
        {s.published && (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            disabled={unpublish.isPending}
            onClick={askUnpublish}
          >
            {t("agentPanel.unpublish")}
          </button>
        )}
        {blockedReason && (
          <span role="status" style={muted}>
            {blockedReason}
          </span>
        )}
      </div>
      {(publish.isError || unpublish.isError) && (
        <p role="alert" style={{ ...muted, color: "var(--err)" }}>
          {t("agentPanel.publishFailed", {
            why: (publish.error ?? unpublish.error)?.message ?? "",
          })}
        </p>
      )}
      {publish.isSuccess && !publish.isPending && (
        <p role="status" style={muted}>
          {t("agentPanel.published")}
        </p>
      )}

      {asking && (
        <PublishDialog
          kind="agent"
          slug={slug}
          itemId={itemId}
          path={path}
          initial={initial}
          republish={!!s.published}
          client={publisher}
          onClose={() => setAsking(false)}
          onConfirm={(choices) => {
            setAsking(false);
            publish.mutate(choices);
          }}
        />
      )}
    </section>
  );
}

function Capabilities({ summary: s }: { summary: AgentSummary }) {
  const t = useT();
  return (
    <dl style={facts}>
      <dt style={term}>{t("agentPanel.tools")}</dt>
      <dd style={def} data-testid="agent-tools">
        {s.tools.length === 0 ? t("agentPanel.tools.none") : s.tools.join(", ")}
        {!s.narrows && <span style={muted}> · {t("agentPanel.tools.all")}</span>}
        {s.not_available.length > 0 && (
          <div style={muted} data-testid="agent-not-available">
            {t("agentPanel.tools.missing", { names: s.not_available.join(", ") })}
          </div>
        )}
      </dd>
      <dt style={term}>{t("agentPanel.skills")}</dt>
      <dd style={def} data-testid="agent-skills">
        {s.skills.length === 0 ? t("agentPanel.skills.none") : s.skills.join(", ")}
      </dd>
      {s.suggestions.length > 0 && (
        <>
          <dt style={term}>{t("agentPanel.suggestions")}</dt>
          <dd style={def}>{s.suggestions.map((x) => x.label).join(" · ")}</dd>
        </>
      )}
    </dl>
  );
}

function PublishState({ summary: s }: { summary: AgentSummary }) {
  const t = useT();
  const p = s.published;
  if (!p) {
    return (
      <p style={muted} data-testid="agent-publish-state">
        {t("agentPanel.notPublished")}
      </p>
    );
  }
  const iso = new Date(p.published_at).toISOString();
  return (
    <div data-testid="agent-publish-state" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span>
        {t("agentPanel.publishedBy")} <UserChip userId={p.published_by} nameOnly /> ·{" "}
        <span title={exactTime(iso)}>{relativeTime(iso)}</span> · {t(`publish.reach.${p.reach}` as MsgKey)}
      </span>
      <span style={muted}>{t("publish.usage", { opens: s.opens, people: s.people })}</span>
      {s.notes.length > 0 && (
        <ul style={{ ...muted, margin: 0, paddingLeft: 18 }} aria-label={t("agentPanel.notes")}>
          {s.notes.map((n) => (
            <li key={`${n.at}-${n.text}`}>
              <span title={exactTime(new Date(n.at).toISOString())}>{relativeTime(new Date(n.at).toISOString())}</span>
              {" · "}
              {n.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const panel: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 20,
  maxWidth: 760,
};
const muted: React.CSSProperties = { color: "var(--ink-2)", fontSize: "0.9rem", margin: 0 };
const problemBox: React.CSSProperties = {
  border: "1px solid var(--paper-3)",
  borderRadius: 8,
  padding: "10px 12px",
  color: "var(--ink)",
};
const facts: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(6rem, max-content) 1fr",
  gap: "6px 16px",
  margin: 0,
};
const term: React.CSSProperties = { fontWeight: 600, color: "var(--ink-2)" };
const def: React.CSSProperties = { margin: 0, overflowWrap: "anywhere" };
