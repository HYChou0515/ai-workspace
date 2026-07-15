/**
 * KbChatPanel — the reusable KB conversation core (no outer chrome). Renders
 * the agent log with the SAME components as the RCA agent (foldable reasoning,
 * tool-call cards, live token metrics) so the two chats look identical; adds a
 * collection picker for a fresh thread + suggestion chips + the composer.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { EntryView } from "../../components/AgentEntryView";
import { Icon } from "../../components/Icon";
import { ModelEffortPicker } from "../../components/ModelEffortPicker";
import { ReplayDialog, type ReplayRequest } from "../../components/ReplayDialog";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useT } from "../../lib/i18n";
import { useKbAgentName } from "../../lib/kbAgent";
import { modCombo } from "../../lib/platform";
import { rankCollections } from "../../lib/rankCollections";
import { useKbChat } from "../../hooks/useKbChat";
import { useStickToBottom } from "../../hooks/useStickToBottom";
import { TurnStatus } from "../../components/TurnStatus";
import { KbCollectionsModal } from "./KbCollectionsModal";
import { WikiCorrectionDialog } from "./WikiCorrectionDialog";
import { fileToImageInput, stagedImagePreview, type StagedImage } from "./kbImage";
import type { AgentEntry } from "../investigation/agentLog";
import { extractClipboardFiles } from "../investigation/transfer";

/** How many ranked collections to surface as quick-pick pills (#271); the rest
 * live behind the "more" modal. */
const PILL_COUNT = 6;

/** #397: the nearest preceding USER message before entry `i` — the question the
 * flagged assistant answer was replying to (drafting context). "" if none. */
function nearestUserQuestion(entries: AgentEntry[], i: number): string {
  for (let j = i - 1; j >= 0; j--) {
    const e = entries[j];
    if (e.kind === "message" && e.message.role === "user") return e.message.content;
  }
  return "";
}

export function KbChatPanel({
  chatId = null,
  collectionIds: fixedCollectionIds,
  hideCollectionPicker = false,
  onOpenCitation,
  onChatCreated,
  client = kbApi,
}: {
  chatId?: string | null;
  /** #230: when provided, the chat is permanently scoped to these collections
   * (the internal picker + ranked auto-selection are bypassed). Used by the
   * /help page to lock the chat to the Platform Help collection. */
  collectionIds?: string[];
  /** #230: hide the collection-picker UI (implied when `collectionIds` is set). */
  hideCollectionPicker?: boolean;
  onOpenCitation?: (c: KbCitation) => void;
  onChatCreated?: (chatId: string) => void;
  client?: KbApi;
}) {
  const locked = fixedCollectionIds !== undefined;
  const t = useT();
  const me = useCurrentUser();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  // #513 P10: one transient image staged for the next message. The server
  // VLM-describes it into the query; it's never uploaded as a KB document.
  const [image, setImage] = useState<StagedImage | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  // #51 P6: replay diagnostic for one past entry (answer / kb_search).
  const [replayReq, setReplayReq] = useState<ReplayRequest | null>(null);
  // #397: the "回報有誤" dialog for one flagged assistant answer.
  const [reportReq, setReportReq] = useState<{
    collectionId: string;
    question: string;
    answer: string;
  } | null>(null);

  const collectionsQ = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });
  const collections = useMemo(() => collectionsQ.data ?? [], [collectionsQ.data]);
  // #271: the user's past chats rank the collections (most-used first), so the
  // pill shortlist surfaces what they actually reach for.
  const chatsQ = useQuery({ queryKey: qk.kb.chats, queryFn: () => client.listChats() });
  const ranked = useMemo(
    () => rankCollections(collections, chatsQ.data ?? [], me),
    [collections, chatsQ.data, me],
  );
  const pills = useMemo(() => ranked.slice(0, PILL_COUNT), [ranked]);
  // Issue #32: /kb/agent returns an array — one entry per kb_chat
  // picker row. Suggestions come from the CURRENTLY-CHOSEN entry so
  // changing models also swaps the empty-state chips.
  const { data: agents = [] } = useQuery({
    queryKey: qk.kb.agent,
    queryFn: () => client.getAgentConfig(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  const [pickedAgent, setPickedAgent] = useKbAgentName();
  const activeAgent = agents.find((a) => a.name === pickedAgent) ?? agents[0];
  const suggestions = activeAgent?.suggestions ?? [];

  // #271 default: pre-select the pill shortlist (the user's most-used
  // collections), but only once BOTH the collections AND the chat history that
  // ranks them have settled — otherwise an early seed (cited-only cold-start
  // order) wouldn't match the pills shown after the history loads.
  const seededRef = useRef(false);
  useEffect(() => {
    // Wait for the chat history to *settle* (success OR error → `isFetched`), not
    // just succeed: if listing chats fails we still seed, treating it as no
    // history (cold-start ranking) rather than leaving the selection empty.
    if (!seededRef.current && collectionsQ.isSuccess && chatsQ.isFetched && collections.length > 0) {
      setSelected(new Set(ranked.slice(0, PILL_COUNT).map((c) => c.resource_id)));
      seededRef.current = true;
    }
  }, [collectionsQ.isSuccess, chatsQ.isFetched, collections.length, ranked]);

  const collectionIds = useMemo(
    () => (locked ? fixedCollectionIds! : [...selected]),
    [locked, fixedCollectionIds, selected],
  );
  // #397: the in-scope collection whose wiki a correction would target (Q13:
  // only when a wiki is actually on). First wiki collection when several.
  const wikiCollectionId = useMemo(
    () => collections.find((c) => collectionIds.includes(c.resource_id) && c.use_wiki)?.resource_id,
    [collections, collectionIds],
  );
  const { log, send, cancel } = useKbChat({ collectionIds, chatId, client, onChatCreated });
  // Follow the conversation as it streams; back off when the user scrolls up.
  const bodyRef = useStickToBottom<HTMLDivElement>(log);

  // #513 P10: stage an image dropped / pasted / picked into the composer.
  const stageImage = async (file: File | undefined) => {
    if (!file || !file.type.startsWith("image/")) return;
    setImage(await fileToImageInput(file));
  };
  const stageFirstImage = (dt: DataTransfer | null) => {
    const { images } = extractClipboardFiles(dt, Date.now());
    if (images.length) void stageImage(images[0]);
    return images.length > 0;
  };

  const submit = (text: string) => {
    const t = text.trim();
    // #513 P10: an image with no text is a valid turn (its VLM description is the query).
    if ((!t && !image) || log.streaming) return;
    setDraft("");
    const img = image;
    setImage(null);
    void send(t, img ? { data: img.data, mime: img.mime } : undefined);
  };

  const empty = log.entries.length === 0;
  const showPicker =
    !locked && !hideCollectionPicker && chatId == null && empty && collections.length > 0;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="kb-chatpanel">
      <div className="kb-chatpanel__body" ref={bodyRef}>
        {empty && (
          <p className="kb-drawer__hello">
            Hi — ask me anything across your knowledge base. I'll cite the sources.
          </p>
        )}
        {log.entries.map((entry, i) => (
          <EntryView
            key={i}
            entry={entry}
            onOpenCitation={onOpenCitation}
            // #51 P6: same 1:1 entry↔message mapping as the RCA panel;
            // only for persisted threads (chatId) and never mid-stream.
            onReplay={
              chatId != null &&
              !log.streaming &&
              (entry.kind === "tool_call" ||
                (entry.kind === "message" && entry.message.role === "assistant"))
                ? () =>
                    setReplayReq({ kind: "turn", source: "kb", threadId: chatId, messageIndex: i })
                : undefined
            }
            // #397: "回報有誤" on a completed assistant answer, only when the
            // scope has a wiki to correct (Q13).
            onReportWiki={
              wikiCollectionId != null &&
              !log.streaming &&
              entry.kind === "message" &&
              entry.message.role === "assistant"
                ? () =>
                    setReportReq({
                      collectionId: wikiCollectionId,
                      question: nearestUserQuestion(log.entries, i),
                      answer: entry.message.role === "assistant" ? entry.message.content : "",
                    })
                : undefined
            }
          />
        ))}
        <TurnStatus log={log} />
        {log.error && <div className="kb-drawer__error">{log.error}</div>}
      </div>

      <div className="kb-drawer__foot">
        {showPicker && (
          <div className="kb-picker" aria-label="Collections to search">
            <span className="kb-picker__label">Search in</span>
            {pills.map((c) => {
              const on = selected.has(c.resource_id);
              return (
                <button
                  key={c.resource_id}
                  type="button"
                  className={`kb-chip kb-chip--btn${on ? " is-on" : ""}`}
                  aria-pressed={on}
                  onClick={() => toggle(c.resource_id)}
                >
                  {on && <Icon name="check" size={10} />}
                  {c.name}
                </button>
              );
            })}
            {collections.length > PILL_COUNT && (
              <button
                type="button"
                className="kb-chip kb-chip--btn kb-chip--more"
                data-testid="kb-collections-more"
                onClick={() => setPickerOpen(true)}
              >
                <Icon name="layers" size={10} />
                {t("collections.more", { n: selected.size })}
              </button>
            )}
          </div>
        )}
        {empty && suggestions.length > 0 && (
          <div className="kb-suggestions">
            {suggestions.map((s) => (
              <button
                key={s.label}
                type="button"
                className="kb-suggestion"
                onClick={() => submit(s.prompt)}
              >
                <Icon name="sparkle" size={11} color="var(--accent)" />
                {s.label}
              </button>
            ))}
          </div>
        )}
        <div className="kb-composer">
          {image && (
            <div className="kb-composer__attach">
              <img className="kb-composer__thumb" src={stagedImagePreview(image)} alt="" />
              <span className="kb-composer__attach-name">{image.name}</span>
              <button
                type="button"
                className="kb-composer__attach-x"
                aria-label="Remove image"
                onClick={() => setImage(null)}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          )}
          <textarea
            className="kb-composer__input"
            rows={2}
            placeholder="Ask the knowledge base…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            // #513 P10: drop / paste an image straight into the composer (reuses the
            // #364 clipboard-parsing primitive). A plain-text paste falls through.
            onPaste={(e) => {
              if (stageFirstImage(e.clipboardData)) e.preventDefault();
            }}
            onDrop={(e) => {
              if (stageFirstImage(e.dataTransfer)) e.preventDefault();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || !e.shiftKey)) {
                e.preventDefault();
                submit(draft);
              }
            }}
          />
          <div className="kb-composer__row">
            {/* #513 P10: attach a transient image the server VLM-describes into the query. */}
            <input
              ref={imageInputRef}
              data-testid="kb-image-input"
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = ""; // allow re-picking the same file
                void stageImage(f);
              }}
            />
            <button
              type="button"
              className="kb-composer__attach-btn"
              aria-label="Attach image"
              onClick={() => imageInputRef.current?.click()}
            >
              <Icon name="paperclip" size={15} />
            </button>
            <span className="kb-composer__hint">{modCombo("↵")} to send</span>
            {/* Handoff 3.0: one combined chip replaces the three
                separate model / depth / effort controls. Model pick
                stays per-message (sticky), same semantics as before. */}
            <ModelEffortPicker
              models={agents}
              selectedName={pickedAgent}
              onSelectModel={setPickedAgent}
              retrieval
              wikiAvailable={collections.some(
                (c) => collectionIds.includes(c.resource_id) && c.use_wiki,
              )}
              // #506: KB chat searches the wiki as a budgeted in-agent tool → the
              // number picker, not the routing toggle.
              wikiBudget
            />
            {log.streaming ? (
              <button type="button" className="kb-btn kb-btn--stop" onClick={cancel}>
                <Icon name="x" size={13} /> Stop
              </button>
            ) : (
              <button
                type="button"
                className="kb-btn kb-btn--primary"
                disabled={!draft.trim() && !image}
                onClick={() => submit(draft)}
              >
                <Icon name="arrow_r" size={13} /> Send
              </button>
            )}
          </div>
        </div>
      </div>
      {showPicker && pickerOpen && (
        <KbCollectionsModal
          collections={collections}
          selected={selected}
          onChange={setSelected}
          onClose={() => setPickerOpen(false)}
        />
      )}
      {replayReq && <ReplayDialog request={replayReq} onClose={() => setReplayReq(null)} />}
      {reportReq && (
        <WikiCorrectionDialog
          collectionId={reportReq.collectionId}
          question={reportReq.question}
          answer={reportReq.answer}
          wikiPages={[]}
          client={client}
          onClose={() => setReportReq(null)}
        />
      )}
    </div>
  );
}
