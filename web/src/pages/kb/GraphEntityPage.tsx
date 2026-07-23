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

type Entity = {
  id: string;
  name: string;
  aliases: string[];
  kind: string;
  occurrences: number;
  mentions: Mention[];
  related: Related[];
};

async function fetchEntity(id: string): Promise<Entity | null> {
  const resp = await apiFetch(`/kb/graph/entities/${encodeURIComponent(id)}`);
  if (resp.status === 404) return null; // unknown OR unreadable — same face
  if (!resp.ok) throw new Error(`entity ${resp.status}`);
  return resp.json();
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
