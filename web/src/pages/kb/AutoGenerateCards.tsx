/**
 * #175 自動 context card — the source picker (#415).
 *
 * A modal that picks the sources to draft cards from and kicks off a background
 * `CardGenRun` — it no longer blocks waiting for the result. Documents AND (when
 * the collection has an LLM wiki) wiki pages appear in ONE checkbox filetree
 * over two virtual roots, `Documents/` and `Wiki/`, with search + select-all /
 * invert over the visible set and folder-level selection (#415). On submit the
 * run is enqueued and the reviewer is sent to the persistent 待審核 tab (P4) to
 * accept / edit / commit — so the modal closes instead of spinning.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { FileTree } from "../investigation/FileTree";
import { buildCardGenSources, pendingIndexingCount } from "./cardGenSources";
import { fetchAllDocs } from "./useCollectionDocs";

export function AutoGenerateCards({
  collectionId,
  client = kbApi,
  onClose,
  onGenerated,
}: {
  collectionId: string;
  client?: KbApi;
  onClose: () => void;
  /** Fired once the run is enqueued (e.g. to refresh the 待審核 tab's list). */
  onGenerated?: (runId: string) => void;
}) {
  const t = useT();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [term, setTerm] = useState("");
  const [started, setStarted] = useState(false);

  // Sources: the collection's documents + (if any) its wiki pages. Both feed one
  // tree; the shared qk.kb.documents key is the same bare KbDocument[] the index
  // strip fills (#394). Stop fetching once the run is on its way.
  const { data: docList } = useQuery({
    queryKey: qk.kb.documents(collectionId),
    queryFn: () => fetchAllDocs(client, collectionId),
    enabled: !started,
  });
  const { data: wikiTree } = useQuery({
    queryKey: qk.kb.wikiPages(collectionId),
    queryFn: () => client.listWikiPages(collectionId),
    enabled: !started,
  });

  const { files, ids } = useMemo(
    () => buildCardGenSources(collectionId, docList ?? [], wikiTree?.pages ?? []),
    [collectionId, docList, wikiTree],
  );

  // How many picked documents are still indexing — card-gen defers those to the
  // index-completion hook (generate opted the collection into auto_digest), so
  // the started view says they'll be generated automatically rather than dropped.
  const pendingCount = useMemo(
    () => pendingIndexingCount(selected, docList ?? []),
    [selected, docList],
  );

  // Search filters the tree; select-all / invert act on the VISIBLE (filtered)
  // leaves so "搜尋並全選" narrows first, then selects only the matches.
  const visible = useMemo(() => {
    const q = term.trim().toLowerCase();
    return q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files;
  }, [files, term]);

  const selectAll = () => setSelected((prev) => new Set([...prev, ...visible.map((f) => f.path)]));
  const invert = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const f of visible) {
        if (next.has(f.path)) next.delete(f.path);
        else next.add(f.path);
      }
      return next;
    });

  const generateMut = useMutation({
    mutationFn: () => {
      const docIds = [...selected].map((p) => ids.get(p)).filter((id): id is string => !!id);
      return client.generateContextCards(collectionId, docIds);
    },
    onSuccess: (runId) => {
      setStarted(true);
      onGenerated?.(runId);
    },
  });

  return (
    <div className="kb-cardgen__backdrop" role="dialog" aria-label="Auto-generate context cards">
      <div className="kb-cardgen__modal">
        <header className="kb-cardgen__head">
          <strong>自動 context card</strong>
          <button type="button" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        {started ? (
          <div className="kb-cardgen__body" data-testid="cardgen-started">
            <p>已開始生成卡片，完成後可到「待審核」分頁審核。</p>
            {pendingCount > 0 && (
              <p className="kb-cardgen__pending" data-testid="cardgen-pending">
                {t("kb.cards.autogen.pending", { n: pendingCount })}
              </p>
            )}
            <footer className="kb-cardgen__foot">
              <button type="button" onClick={onClose}>
                完成
              </button>
            </footer>
          </div>
        ) : (
          <div className="kb-cardgen__body">
            <p>挑選要產生卡片的來源（文件與 wiki 頁）。系統會讀取內容、草擬卡片讓你審核。</p>
            <div className="kb-cardgen__pickbar">
              <input
                aria-label="Search sources"
                placeholder="搜尋來源…"
                value={term}
                onChange={(e) => setTerm(e.target.value)}
              />
              <button type="button" onClick={selectAll}>
                全選
              </button>
              <button type="button" onClick={invert}>
                反選
              </button>
            </div>
            <div className="kb-cardgen__tree">
              {files.length === 0 ? (
                <p className="kb-cardgen__none">沒有可選的來源。</p>
              ) : (
                <FileTree
                  files={visible}
                  dirs={[]}
                  activePath={null}
                  onOpen={() => {}}
                  scopeId={`cardgen:${collectionId}`}
                  select={{ selected, onChange: setSelected }}
                />
              )}
            </div>
            <footer className="kb-cardgen__foot">
              <button type="button" onClick={onClose}>
                取消
              </button>
              <button
                type="button"
                disabled={selected.size === 0 || generateMut.isPending}
                onClick={() => generateMut.mutate()}
              >
                {t("kb.cards.autogen.count", { n: selected.size })}
              </button>
            </footer>
          </div>
        )}
      </div>
    </div>
  );
}
