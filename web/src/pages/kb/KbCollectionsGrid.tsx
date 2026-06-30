/**
 * KB management landing — a grid of collection cards (icon, description,
 * doc/size/cited chips, owner, last-updated; pinnable), matching the design
 * handoff. Clicking a card routes to that collection's page (#93:
 * /kb/collections/:cid). The All/Mine/Pinned tab, owner filter, and name query
 * live in the URL (`?view/owner/q`) so a filtered view is shareable.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon, type IconName } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { Skeleton } from "../../components/Skeleton";
import { UserAvatar } from "../../components/UserChip";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useT } from "../../lib/i18n";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { fmtBytes, fmtCount, fmtDate } from "./collectionFormat";
import { NewCollectionModal, type NewCollectionOpts } from "./NewCollectionModal";
import { WikiBadge } from "./RetrievalToggles";

type Tab = "all" | "mine" | "pinned";

export function KbCollectionsGrid({ client = kbApi }: { client?: KbApi }) {
  const qc = useQueryClient();
  const t = useT();
  const me = useCurrentUser();
  const navigate = useNavigate();
  const [newOpen, setNewOpen] = useState(false);
  // Grid filters live in the URL (#93) so a filtered/searched view is shareable.
  const [searchParams, setSearchParams] = useSearchParams();
  const viewParam = searchParams.get("view");
  const tab: Tab = viewParam === "mine" || viewParam === "pinned" ? viewParam : "all";
  const ownerFilter = searchParams.get("owner");
  const colQuery = searchParams.get("q") ?? "";
  // Set/clear one grid-filter param, preserving the others. The free-text query
  // replaces history (one entry per search session, not one per keystroke).
  const setFilter = (key: "view" | "owner" | "q", value: string | null) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: key === "q" },
    );
  const pinned = usePersistentSet("kb:pinned-collections");
  const importNewRef = useRef<HTMLInputElement>(null);

  const { data: collections = [], isPending: collectionsLoading } = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });

  const createMut = useMutation({
    mutationFn: (v: { name: string; description: string; opts: NewCollectionOpts }) =>
      client.createCollection(v.name, v.description, v.opts),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  // #101: import a zip as a NEW collection. On success open it at its URL.
  const importNewMut = useMutation({
    mutationFn: (file: File) => client.importCollectionNew(file),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      navigate(`/kb/collections/${encodeURIComponent(res.collection_id)}`);
    },
  });
  const pickImportNew = (files: FileList | null) => {
    const file = files?.[0];
    if (file) importNewMut.mutate(file);
    // Clear so re-picking the same file fires onChange again.
    if (importNewRef.current) importNewRef.current.value = "";
  };

  const mostCited = collections.reduce<(typeof collections)[number] | null>(
    (best, c) => (c.cited > (best?.cited ?? 0) ? c : best),
    null,
  );
  const sorted = [...collections].sort(
    (a, b) =>
      Number(pinned.has(b.resource_id)) - Number(pinned.has(a.resource_id)) ||
      a.name.localeCompare(b.name),
  );
  // Library-wide aggregates for the landing header.
  const totalDocs = collections.reduce((s, c) => s + c.doc_count, 0);
  const totalSize = collections.reduce((s, c) => s + c.size, 0);
  // #88: a chunk-based token estimate summed from the BE (each doc's CJK-aware
  // token_count of the extracted text), replacing the old raw-blob `size / 4`.
  const totalTokens = collections.reduce((s, c) => s + c.tokens, 0);
  const mineCount = collections.filter((c) => c.owner === me).length;
  const sharedCount = collections.length - mineCount;
  const pinnedCount = collections.filter((c) => pinned.has(c.resource_id)).length;
  const owners = [...new Set(collections.map((c) => c.owner))].sort();
  // Tab + owner + name filters compose over the pinned-first sorted list.
  const cq = colQuery.trim().toLowerCase();
  const shownCols = sorted.filter((c) => {
    if (tab === "mine" && c.owner !== me) return false;
    if (tab === "pinned" && !pinned.has(c.resource_id)) return false;
    if (ownerFilter && c.owner !== ownerFilter) return false;
    if (cq && !c.name.toLowerCase().includes(cq)) return false;
    return true;
  });

  const tabs: [Tab, string, number][] = [
    ["all", "All", collections.length],
    ["mine", "Mine", mineCount],
    ["pinned", "Pinned", pinnedCount],
  ];
  return (
    <section className="kb-grid-page" aria-label="Collections">
      <header className="kb-libhead">
        <div className="kb-libhead__intro">
          <div className="caps">Knowledge base</div>
          <h1 className="kb-libhead__title">
            {collections.length} collections <span className="kb-libhead__dot">·</span> {totalDocs}{" "}
            documents
          </h1>
          <p className="kb-libhead__lead">{t("kb.lead")}</p>
        </div>
        <div className="kb-libhead__metrics">
          <div className="kb-metric">
            <span className="kb-metric__label">My collections</span>
            <span className="kb-metric__value">{mineCount}</span>
            <span className="kb-metric__sub">plus {sharedCount} shared</span>
          </div>
          <div className="kb-metric">
            <span className="kb-metric__label">Total size</span>
            <span className="kb-metric__value">{fmtBytes(totalSize)}</span>
            <span className="kb-metric__sub">≈ {fmtCount(totalTokens)} tokens</span>
          </div>
          <div className="kb-metric">
            <span className="kb-metric__label">Most cited</span>
            <span className="kb-metric__value" title={mostCited?.name}>
              {mostCited && mostCited.cited > 0 ? mostCited.name : "—"}
            </span>
            <span className="kb-metric__sub">
              {mostCited && mostCited.cited > 0 ? `${mostCited.cited} citations` : "no citations yet"}
            </span>
          </div>
        </div>
      </header>

      <div className="kb-tabs">
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            className={`kb-tab${tab === id ? " is-active" : ""}`}
            aria-pressed={tab === id}
            onClick={() => setFilter("view", id === "all" ? null : id)}
          >
            {label} <span className="kb-tab__count">{count}</span>
          </button>
        ))}
      </div>

      <div className="kb-cols__actions">
        <label className="kb-docsearch kb-docsearch--inline">
          <Icon name="search" size={14} color="var(--text-paper-d)" />
          <input
            type="search"
            placeholder="Filter collections…"
            value={colQuery}
            onChange={(e) => setFilter("q", e.target.value)}
          />
        </label>
        <Popover
          align="start"
          trigger={({ onClick, open }) => (
            <button type="button" className="kb-btn" aria-haspopup="menu" aria-expanded={open} onClick={onClick}>
              <Icon name="user" size={13} /> Owner · {ownerFilter ?? "any"} <Icon name="chev_d" size={11} />
            </button>
          )}
        >
          {(close) => (
            <div className="kb-menu" role="menu">
              <button type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setFilter("owner", null); }}>
                Any owner
              </button>
              {owners.map((o) => (
                <button key={o} type="button" role="menuitem" className="kb-menu__item" onClick={() => { close(); setFilter("owner", o); }}>
                  {o}
                </button>
              ))}
            </div>
          )}
        </Popover>
        <span style={{ flex: 1 }} />
        <input
          ref={importNewRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          aria-label="Import collection from file"
          onChange={(e) => pickImportNew(e.target.files)}
        />
        <button type="button" className="kb-btn" disabled={importNewMut.isPending} onClick={() => importNewRef.current?.click()}>
          <Icon name="upload" size={13} /> Import
        </button>
        <button type="button" className="kb-btn kb-btn--primary" onClick={() => setNewOpen(true)}>
          <Icon name="plus" size={13} /> New collection
        </button>
      </div>

      <NewCollectionModal
        open={newOpen}
        busy={createMut.isPending}
        onClose={() => setNewOpen(false)}
        onCreate={(name, description, opts) =>
          createMut.mutate(
            { name, description, opts },
            {
              onSuccess: (created) => {
                setNewOpen(false);
                // #355: a code collection kicks its first sync immediately and
                // opens its page so the user watches the clone/build progress.
                if (created.git_url) {
                  void client.syncCollection(created.resource_id);
                  navigate(`/kb/collections/${created.resource_id}`);
                }
              },
            },
          )
        }
      />

      {collectionsLoading ? (
        <div className="kb-grid" aria-busy="true" data-testid="kb-cols-loading">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="kb-card-wrap">
              <Skeleton className="kb-skel--card" />
            </div>
          ))}
        </div>
      ) : collections.length === 0 ? (
        <p className="kb-cols__empty">No collections yet — create one to start adding documents.</p>
      ) : shownCols.length === 0 ? (
        <p className="kb-cols__empty">No collections match the current filters.</p>
      ) : (
        <div className="kb-grid">
          {shownCols.map((c) => (
            <div key={c.resource_id} className="kb-card-wrap">
              <button
                type="button"
                className="kb-card"
                aria-label={`Open ${c.name}`}
                onClick={() => navigate(`/kb/collections/${encodeURIComponent(c.resource_id)}`)}
              >
                <div className="kb-card__icon">
                  <Icon name={c.icon as IconName} size={18} color="var(--accent-h)" />
                </div>
                <div className="kb-card__name">{c.name}</div>
                <div className="kb-card__desc">{c.description}</div>
                <div className="kb-card__chips">
                  <span className="kb-chip">
                    <Icon name="file" size={10} color="var(--text-paper-d2)" /> {c.doc_count} docs
                  </span>
                  <span className="kb-chip">{fmtBytes(c.size)}</span>
                  {c.use_wiki && <WikiBadge />}
                  {c.cited > 0 && (
                    <span className="kb-chip kb-chip--accent">
                      <Icon name="quote" size={10} color="var(--accent-h)" /> cited {c.cited}×
                    </span>
                  )}
                </div>
                <div className="kb-card__foot">
                  <UserAvatar userId={c.owner} size={20} />
                  <span className="kb-card__owner">{c.owner}</span>
                  <span style={{ flex: 1 }} />
                  <span className="kb-card__updated">{fmtDate(c.updated_at)}</span>
                </div>
              </button>
              <button
                type="button"
                className={`kb-card__pin${pinned.has(c.resource_id) ? " is-pinned" : ""}`}
                aria-label={`${pinned.has(c.resource_id) ? "Unpin" : "Pin"} ${c.name}`}
                aria-pressed={pinned.has(c.resource_id)}
                onClick={() => pinned.toggle(c.resource_id)}
              >
                <Icon name="pin" size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
