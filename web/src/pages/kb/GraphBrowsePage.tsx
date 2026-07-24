/**
 * #636 — browse what the knowledge graph actually built.
 *
 * Until now an entity could only be reached by knowing its id: the merges tab
 * (which lists only the ones with a pending proposal), a neighbour node on a
 * page you were already on, or a typed uuid. The thing users asked for is
 * inspection — "show me what the system extracted from our decks" — so this is
 * a list you can search, narrow and page through.
 *
 * Two limits are visible in the design because they are real:
 *
 * - **there is no total and no page count.** Counting means permission-checking
 *   every row (measured 4.3 s over 20k), so the API's contract is "here is a
 *   page, and whether more follow". A "next" button can honour that; "page 3 of
 *   200" cannot.
 * - **kind is typed, not picked.** Listing the kinds in use would be its own
 *   aggregate query; the cheap version filters on what you type, and the kinds
 *   are visible in the rows themselves.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "../../api/http";
import { kbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Skeleton } from "../../components/Skeleton";
import { useT } from "../../lib/i18n";

type Row = { id: string; name: string; kind: string; aliases: string[] };
type Page = { items: Row[]; has_more: boolean; next_offset: number };

const LIMIT = 50;

async function fetchPage(params: {
  q: string;
  kind: string;
  collection: string;
  offset: number;
}): Promise<Page> {
  const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(params.offset) });
  if (params.q) qs.set("q", params.q);
  if (params.kind) qs.set("kind", params.kind);
  if (params.collection) qs.set("collection", params.collection);
  const resp = await apiFetch(`/kb/graph/entities?${qs}`);
  if (!resp.ok) throw new Error(`graph entities ${resp.status}`);
  return resp.json();
}

export function GraphBrowsePage() {
  const t = useT();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [collection, setCollection] = useState("");
  const [offset, setOffset] = useState(0);

  // Any narrowing returns to the first page: an offset carried over from a
  // wider result set points into a list that no longer exists.
  const narrow = (set: (v: string) => void) => (v: string) => {
    set(v);
    setOffset(0);
  };

  const page = useQuery({
    queryKey: qk.kb.graphEntities({ q, kind, collection, offset }),
    queryFn: () => fetchPage({ q, kind, collection, offset }),
  });
  const collections = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => kbApi.listCollections(),
  });

  const rows = page.data?.items ?? [];

  return (
    <div className="gbr" data-testid="graph-browse">
      <header className="gbr__head">
        <p className="gbr__eyebrow">{t("graph.browse.eyebrow")}</p>
        <h1 className="gbr__title">{t("graph.browse.title")}</h1>
        <p className="gbr__blurb">{t("graph.browse.blurb")}</p>
      </header>

      <div className="gbr__filters">
        <input
          type="search"
          className="gbr__search"
          placeholder={t("graph.browse.searchPlaceholder")}
          value={q}
          onChange={(e) => narrow(setQ)(e.target.value)}
        />
        <input
          className="gbr__kind"
          placeholder={t("graph.browse.kindPlaceholder")}
          value={kind}
          onChange={(e) => narrow(setKind)(e.target.value)}
        />
        <select
          className="gbr__collection"
          value={collection}
          onChange={(e) => narrow(setCollection)(e.target.value)}
          aria-label={t("graph.browse.collectionLabel")}
        >
          <option value="">{t("graph.browse.allCollections")}</option>
          {(collections.data ?? []).map((c) => (
            <option key={c.resource_id} value={c.resource_id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      {page.isPending ? (
        <Skeleton style={{ height: 320 }} />
      ) : rows.length === 0 ? (
        <p className="rvw__empty" data-testid="graph-browse-empty">
          {t("graph.browse.empty")}
        </p>
      ) : (
        <ul className="gbr__list" data-testid="graph-browse-list">
          {rows.map((r) => (
            <li className="gbr__row" key={r.id}>
              <Link className="gbr__name" to={`/kb/graph/entities/${r.id}`}>
                {r.name}
              </Link>
              {r.kind ? <span className="gbr__kind-chip">{r.kind}</span> : null}
              {r.aliases.length > 0 && (
                <span className="gbr__aliases">
                  {t("graph.browse.alsoWritten")}
                  {r.aliases.slice(0, 3).join(", ")}
                  {r.aliases.length > 3 ? ` +${r.aliases.length - 3}` : ""}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="gbr__pager">
        {offset > 0 && (
          <button
            type="button"
            className="gbr__page-btn"
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
          >
            {t("graph.browse.prev")}
          </button>
        )}
        {page.data?.has_more && (
          <button
            type="button"
            className="gbr__page-btn"
            onClick={() => setOffset(page.data.next_offset)}
          >
            {t("graph.browse.next")}
          </button>
        )}
      </div>
    </div>
  );
}
