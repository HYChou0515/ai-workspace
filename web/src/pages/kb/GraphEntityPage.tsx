/**
 * #534 — one identity, and everything the corpus said about it.
 *
 * The backend (`GET /kb/graph/entities/{id}`) existed with no consumer: the
 * Merges tab could ask "are these the same thing?" but nobody could OPEN an
 * identity to see its documents, aliases and relations. This page is that
 * missing face; the merge cards' entity names link here.
 *
 * Permission note: the endpoint reads AS the caller — an identity nothing
 * readable vouches for is a 404, which renders as "not found" (not an empty
 * page), so a bare name can't leak.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch } from "../../api/http";
import { qk } from "../../api/queryKeys";
import { Skeleton } from "../../components/Skeleton";
import { useT } from "../../lib/i18n";
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
  return (
    <div className="ent-page" data-testid="entity-page">
      <header className="ent-page__head">
        <h1 className="ent-page__name">
          {e.name}
          {e.kind ? <span className="mrg__kind">{e.kind}</span> : null}
        </h1>
        <p className="ent-page__meta">
          {t("entity.occurrences", { n: String(e.occurrences) })}
          {e.aliases.filter((a) => a !== e.name).length > 0
            ? ` · ${t("entity.aliases")}: ${e.aliases.filter((a) => a !== e.name).join("、")}`
            : null}
        </p>
      </header>

      <section>
        <h2 className="ent-page__h2">{t("entity.mentions")}</h2>
        {e.mentions.length === 0 ? (
          <p className="rvw__empty">{t("entity.noMentions")}</p>
        ) : (
          <ul className="mrg__ev" data-testid="entity-mentions">
            {e.mentions.map((m) => (
              <li key={`${m.source_doc_id}:${m.surface}`}>
                <a
                  className="mrg__doc"
                  href={docHref(m.source_doc_id, m.surface)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {m.source_doc_id}
                </a>
                <span className="mrg__quote">
                  {m.surface}
                  {m.occurrences > 1 ? ` ×${m.occurrences}` : ""}
                  {m.basis ? ` — ${m.basis}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {e.related.length > 0 && (
        <section>
          <h2 className="ent-page__h2">{t("entity.related")}</h2>
          <ul className="mrg__ev" data-testid="entity-related">
            {e.related.map((r, i) => (
              <li key={`${r.predicate}:${r.other_entity_id}:${i}`}>
                <span className="mrg__quote">
                  {r.direction === "in" ? `${r.other_name} ${r.predicate}` : `${r.predicate} `}
                </span>
                {r.other_entity_id ? (
                  <Link className="mrg__doc" to={`/kb/graph/entities/${r.other_entity_id}`}>
                    {r.direction === "in" ? e.name : r.other_name}
                  </Link>
                ) : (
                  <span>{r.other_name}</span>
                )}
                {r.quote ? <span className="mrg__quote">「{r.quote}」</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
