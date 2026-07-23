/**
 * #534 — one identity, and everything the corpus said about it.
 *
 * The backend (`GET /kb/graph/entities/{id}`) existed with no consumer: the
 * Merges tab could ask "are these the same thing?" but nobody could OPEN an
 * identity to see its documents, aliases and relations. This page is that face:
 * a hero (name · kind · how often the corpus talks about it), then the
 * evidence — each document that named it, in that document's own words — and
 * the relations. Internal vocabulary (link bases, raw doc ids) is translated
 * at this boundary: bases become plain words, doc ids become filenames.
 *
 * Permission note: the endpoint reads AS the caller — an identity nothing
 * readable vouches for is a 404, rendered as "not found" (unknown and
 * unreadable look the same), so a bare name can't leak.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch } from "../../api/http";
import { qk } from "../../api/queryKeys";
import { Skeleton } from "../../components/Skeleton";
import { useT } from "../../lib/i18n";
import type { MsgKey } from "../../lib/i18n";
import { docLabel } from "./CardAttachments";
import { EntityGraph } from "./EntityGraph";
import { docHref } from "./kbLinks";

type Mention = {
  surface: string;
  source_doc_id: string;
  occurrences: number;
  basis: string;
  evidence: string;
};

type Related = {
  direction: string;
  predicate: string;
  other_name: string;
  other_entity_id: string;
  quote: string;
  source_doc_id: string;
};

// #628: a number stated on a slide that names this entity — co-located, so it
// arrives with the slide it came from.
type Claim = {
  metric: string;
  norm_metric: string;
  value: string;
  unit: string;
  period: string;
  norm_period: string;
  source_doc_id: string;
  chunk_id: string;
};

type Entity = {
  id: string;
  name: string;
  aliases: string[];
  kind: string;
  occurrences: number;
  mentions: Mention[];
  related: Related[];
  claims: Claim[];
};

async function fetchEntity(id: string): Promise<Entity | null> {
  const resp = await apiFetch(`/kb/graph/entities/${encodeURIComponent(id)}`);
  if (resp.status === 404) return null; // unknown OR unreadable — same face
  if (!resp.ok) throw new Error(`entity ${resp.status}`);
  return resp.json();
}

/** #628: two decks giving different figures for the same metric in the same
 * period is exactly what a reader must not miss — group on the normalised
 * keys, flag every group with more than one distinct value. */
function conflictKeys(claims: Claim[]): Set<string> {
  const values = new Map<string, Set<string>>();
  for (const c of claims) {
    const k = `${c.norm_metric} ${c.norm_period}`;
    const vals = values.get(k) ?? new Set<string>();
    vals.add(c.value);
    values.set(k, vals);
  }
  return new Set(
    [...values].filter(([, vals]) => vals.size > 1).map(([k]) => k),
  );
}

/** Link bases are engine vocabulary — the page speaks the reader's. */
function basisKey(basis: string): MsgKey {
  switch (basis) {
    case "identical":
      return "entity.basis.identical";
    case "resembles":
      return "entity.basis.resembles";
    case "declared":
      return "entity.basis.declared";
    case "approved":
      return "entity.basis.approved";
    default:
      return "entity.basis.other";
  }
}

export function GraphEntityPage() {
  const t = useT();
  const { entityId = "" } = useParams();
  const q = useQuery({
    queryKey: qk.kb.graphEntity(entityId),
    queryFn: () => fetchEntity(entityId),
  });

  if (q.isPending) {
    return (
      <div className="ent-page">
        <Skeleton style={{ height: 220 }} />
      </div>
    );
  }
  if (q.isError || q.data === null) {
    return (
      <div className="ent-page">
        <p className="rvw__empty" data-testid="entity-missing">
          {t("entity.missing")}
        </p>
      </div>
    );
  }
  const e = q.data as Entity;
  const aliases = e.aliases.filter((a) => a !== e.name);
  const docCount = new Set(e.mentions.map((m) => m.source_doc_id)).size;
  const conflicts = conflictKeys(e.claims);

  return (
    <div className="ent-page" data-testid="entity-page">
      <header className="ent-page__hero">
        <p className="ent-page__eyebrow">{t("entity.eyebrow")}</p>
        <h1 className="ent-page__name">
          {e.name}
          {e.kind ? <span className="ent-page__kind">{e.kind}</span> : null}
        </h1>
        <div className="ent-page__stats">
          <span className="ent-page__stat">
            <strong>{e.occurrences}</strong> {t("entity.stat.occ")}
          </span>
          <span className="ent-page__stat">
            <strong>{docCount}</strong> {t("entity.stat.docs")}
          </span>
          {aliases.length > 0 && (
            <span className="ent-page__stat ent-page__stat--aliases">
              {t("entity.aliases")}
              {aliases.map((a) => (
                <span className="ent-page__alias" key={a}>
                  {a}
                </span>
              ))}
            </span>
          )}
        </div>
      </header>

      {/* The identity AS a graph — centre node, evidence on the left, relations
          on the right. The lists below stay as the receipts. */}
      <EntityGraph
        name={e.name}
        kind={e.kind}
        docs={e.mentions.map((m) => ({
          source_doc_id: m.source_doc_id,
          surface: m.surface,
          occurrences: m.occurrences,
        }))}
        rels={e.related.map((r) => ({
          direction: r.direction,
          predicate: r.predicate,
          other_name: r.other_name,
          other_entity_id: r.other_entity_id,
        }))}
      />

      {/* #628 — the numbers stated on slides that name this entity. Verbatim
          values with their slide; disagreeing figures for the same metric and
          period wear a badge instead of silently coexisting. */}
      {e.claims.length > 0 && (
        <section className="ent-page__section">
          <h2 className="ent-page__h2">{t("entity.claims")}</h2>
          <ul className="ent-page__list" data-testid="entity-claims">
            {e.claims.map((c, i) => {
              const conflicted = conflicts.has(`${c.norm_metric} ${c.norm_period}`);
              return (
                <li className="ent-page__mention" key={`${c.source_doc_id}:${c.chunk_id}:${i}`}>
                  <a
                    className="ent-page__docchip"
                    href={docHref(c.source_doc_id, c.value)}
                    target="_blank"
                    rel="noreferrer"
                    title={c.source_doc_id}
                  >
                    {docLabel(c.source_doc_id)}
                  </a>
                  <span className="ent-page__claim-metric">{c.metric}</span>
                  <span className="ent-page__claim-value">
                    {c.value}
                    {c.unit ? <span className="ent-page__claim-unit">{c.unit}</span> : null}
                  </span>
                  {c.period ? <span className="ent-page__claim-period">{c.period}</span> : null}
                  {conflicted ? (
                    <span className="ent-page__conflict">{t("entity.claimConflict")}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="ent-page__section">
        <h2 className="ent-page__h2">{t("entity.mentions")}</h2>
        {e.mentions.length === 0 ? (
          <p className="rvw__empty">{t("entity.noMentions")}</p>
        ) : (
          <ul className="ent-page__list" data-testid="entity-mentions">
            {e.mentions.map((m) => (
              <li className="ent-page__mention" key={`${m.source_doc_id}:${m.surface}`}>
                <a
                  className="ent-page__docchip"
                  href={docHref(m.source_doc_id, m.surface)}
                  target="_blank"
                  rel="noreferrer"
                  title={m.source_doc_id}
                >
                  {docLabel(m.source_doc_id)}
                </a>
                <span className="ent-page__surface">
                  「{m.surface}」
                  {m.occurrences > 1 ? (
                    <span className="ent-page__times">×{m.occurrences}</span>
                  ) : null}
                </span>
                <span className="ent-page__basis">{t(basisKey(m.basis))}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {e.related.length > 0 && (
        <section className="ent-page__section">
          <h2 className="ent-page__h2">{t("entity.related")}</h2>
          <ul className="ent-page__list" data-testid="entity-related">
            {e.related.map((r, i) => (
              <li className="ent-page__mention" key={`${r.predicate}:${r.other_entity_id}:${i}`}>
                <span className="ent-page__surface">
                  {r.direction === "in" ? (
                    <>
                      {r.other_entity_id ? (
                        <Link
                          className="ent-page__rel"
                          to={`/kb/graph/entities/${r.other_entity_id}`}
                        >
                          {r.other_name}
                        </Link>
                      ) : (
                        r.other_name
                      )}{" "}
                      {r.predicate} {e.name}
                    </>
                  ) : (
                    <>
                      {e.name} {r.predicate}{" "}
                      {r.other_entity_id ? (
                        <Link
                          className="ent-page__rel"
                          to={`/kb/graph/entities/${r.other_entity_id}`}
                        >
                          {r.other_name}
                        </Link>
                      ) : (
                        r.other_name
                      )}
                    </>
                  )}
                </span>
                {r.quote ? <span className="ent-page__quote">「{r.quote}」</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
