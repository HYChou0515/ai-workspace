/**
 * A skill hub entry in the chat — what `show_skill_hub_entry` draws
 * (plan-skill-hub-history §8, A3).
 *
 * Live, not a picture: the entry is read when the card is drawn, as THIS
 * item's App sees it (`missing_tools`), so a reload shows today's counts and
 * an entry since taken down says so. 〔安裝〕 is the Skills panel's own door
 * (`POST …/skills/install`) and its refusals are worded the same way.
 * "Installed" is the picker's rule — a skill of that name whose files are
 * here (`filesHere`) — read off the panel's own skills query, which an
 * install invalidates so the card flips. Drawn where there is no item (the
 * knowledge-base chat), it shows the entry with no action.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { type SkillHubApi, skillHubApi } from "../api/skillHub";
import type { ApiClient } from "../api/types";
import { useChatItem } from "../hooks/chatItem";
import { useT } from "../lib/i18n";
import { filesHere } from "../lib/skillFiles";
import { describeRefusal } from "../lib/skillHubRefusal";
import { AppTag } from "./AppTag";

export function SkillHubEntryCard({
  entryId,
  client = skillHubApi,
  skillsClient = api,
}: {
  entryId: string;
  client?: Pick<SkillHubApi, "get" | "install">;
  skillsClient?: Pick<ApiClient, "getItemSkills">;
}) {
  const t = useT();
  const qc = useQueryClient();
  const item = useChatItem();
  const slug = item?.slug ?? "";
  const itemId = item?.itemId ?? "";
  const entryQ = useQuery({
    queryKey: qk.skillHubEntry(entryId, slug),
    queryFn: () => client.get(entryId, slug),
    retry: (count, e) => !(e instanceof HttpError && e.status === 404) && count < 2,
  });
  const skillsQ = useQuery({
    queryKey: qk.itemSkills(slug, itemId),
    queryFn: () => skillsClient.getItemSkills(slug, itemId),
    enabled: item !== null,
  });
  const [refusal, setRefusal] = useState<string | null>(null);
  const install = useMutation({
    mutationFn: () => client.install(slug, itemId, entryId),
    onMutate: () => setRefusal(null),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    },
    onError: (e) => setRefusal(describeRefusal(e, t)),
    meta: { silentError: true },
  });

  if (entryQ.isError) {
    const gone = entryQ.error instanceof HttpError && entryQ.error.status === 404;
    return (
      <div className="skill-hub-card" data-testid="skill-hub-card" data-state="gone">
        <span className="skill-hub-card-muted">
          {gone ? t("skillHub.card.gone") : t("skillHub.error")}
        </span>
      </div>
    );
  }
  const entry = entryQ.data;
  if (!entry) {
    return (
      <div className="skill-hub-card" data-testid="skill-hub-card">
        <span className="skill-hub-card-muted">{t("skillHub.loading")}</span>
      </div>
    );
  }
  const installed = (skillsQ.data ?? []).some((s) => s.name === entry.name && filesHere(s));
  return (
    <div className="skill-hub-card" data-testid="skill-hub-card">
      <div className="skill-hub-card-head">
        <Link to={`/skill-hub/${encodeURIComponent(entry.id)}`} className="skill-hub-card-title">
          <span className="skill-hub-card-owner">{entry.owner}/</span>
          {entry.name}
        </Link>
        <AppTag slug={entry.source_app} />
      </div>
      <p className="skill-hub-card-desc">{entry.description}</p>
      <p className="skill-hub-card-muted">
        {t("skillHub.counts", { installs: entry.installs ?? 0, uses: entry.uses ?? 0 })}
      </p>
      {entry.missing_tools.length > 0 ? (
        <p className="skill-hub-card-warn">
          {t("skills.fromHub.missing", { tools: entry.missing_tools.join(", ") })}
        </p>
      ) : null}
      {entry.review.notes.length > 0 ? (
        <ul className="skill-hub-card-notes" aria-label={t("skillHub.review")}>
          {entry.review.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : (
        <p className="skill-hub-card-muted">{t("skillHub.card.reviewOk")}</p>
      )}
      {item !== null ? (
        <div className="skill-hub-card-actions">
          {installed ? (
            <span className="skill-hub-card-muted">{t("skillHub.card.installed")}</span>
          ) : (
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="primary"
              disabled={install.isPending || !skillsQ.isSuccess}
              onClick={() => install.mutate()}
            >
              {t("skillHub.card.install")}
            </button>
          )}
          {refusal ? (
            <span className="skill-hub-card-error" role="alert">
              {refusal}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
