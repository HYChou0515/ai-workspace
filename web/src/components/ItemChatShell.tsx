import { useEffect, useMemo, useRef, useState } from "react";

import { investigationFileService } from "../api/fileService";
import { type ItemChatSummary } from "../api/itemChats";
import type { Suggestion } from "../api/types";
import { useItemChat } from "../hooks/useItemChat";
import { useItemChats } from "../hooks/useItemChats";
import { useItemCollections } from "../hooks/useItemCollections";
import { AgentPanel } from "../pages/investigation/AgentPanel";
import { ChatSwitcher } from "./ChatSwitcher";
import { CollectionsButton } from "./CollectionsButton";
import { CollectionsPickerModal } from "./CollectionsPickerModal";
import { ManageChatsModal } from "./ManageChatsModal";
import { NewItemPicker } from "./NewItemPicker";
import { TodoPanel } from "./TodoPanel";

/** What ItemChatShell feeds straight through to each chat's AgentPanel — the
 * App-manifest-derived chat chrome (mirrors the props WorkspaceShell passes the
 * RCA `<AgentPanel>`). The shell adds the multi-chat switcher. */
type AgentChrome = {
  picker: { preset: string; name: string }[];
  suggestions?: Suggestion[];
  appTitle?: string;
  appIcon?: string;
  appColor?: string;
  attachedPreset: string;
  onAttachPreset: (preset: string) => void;
  /** #322: persist this item's per-tool override (`attached_tool_prefs`). Threaded
   * to the AgentPanel header's Tools picker; absent → no picker button. */
  onSaveToolPrefs?: (prefs: Record<string, boolean>) => void;
  /** #380: persist this item's per-skill override (`attached_skill_prefs`). Threaded
   * to the AgentPanel header's Skills picker; absent → its Save is a no-op. */
  onSaveSkillPrefs?: (prefs: Record<string, boolean>) => void;
  /** The item's environment variables + a way to persist them, threaded to the
   * AgentPanel header's Env panel; absent → no Env button. */
  envVars?: Record<string, string>;
  /** Which layer each variable comes from (`plan-wui-viewer-login`). */
  envPolicy?: Record<string, string>;
  /** May this viewer open the panel at all — for their OWN values, which needs
   * no `write_meta`. Saving the shared ones is `onSaveEnvVars`. */
  canOpenEnv?: boolean;
  onSaveEnvVars?: (
    envVars: Record<string, string>,
    envPolicy: Record<string, string>,
  ) => void | boolean | Promise<void | boolean>;
  /** #P4: whether this App ever opens a sandbox, and whether this viewer may
   * resize it. Absent → no Environment button. Gated on the App HAVING one
   * rather than on who may edit: everyone in the workspace needs to see whether
   * an environment is running and what it costs, because that is what explains
   * a refusal. Only `canResize` needs `change_permission`, since resizing spends
   * the OWNER's quota. */
  environment?: { canResize: boolean };
  /** plan-chat-video-export: may this viewer make a video of a chat —
   * `read_content` + `add_content`. Threaded to the AgentPanel header's Export
   * dialog, which draws its video choice locked without it. */
  canExportVideo?: boolean;
  /** plan-outside-lookup: may this viewer add files (`add_content`)? The
   * 請幫我查 card hides its attachments without it — the server would refuse
   * them, and its text goes to the AI without being saved. */
  canAddFiles?: boolean;
  /** #198: the folder the composer's attach stages files into (the item's profile's
   * upload_dir; default uploads/). Threaded straight through to the AgentPanel. */
  uploadDir: string;
};

/** The chat the address asks for (`?chat=`), or null. Read from `window`
 * rather than the router so the shell keeps working wherever it is mounted. */
function chatFromAddress(): string | null {
  return new URLSearchParams(window.location.search).get("chat") || null;
}

/**
 * The per-item multi-chat shell (topic-hub §3, redesigned in #132): a compact chat
 * switcher dropdown + a single `+ New` picker ([Free chat] + the seed profile's
 * workflows) + a manage-all-chats modal (rename / delete / search), with the active
 * chat rendered below as the full RCA `AgentPanel` (model picker, suggestions,
 * @mention, attach, undo, Cmd-Enter — scoped to the active chat via `useItemChat`).
 * A free chat is created on demand; a workflow launch opens a workflow chat
 * (run-driven) and selects it. A paused workflow chat surfaces a Continue affordance
 * (the human_gate decision) above the panel. The bar also carries the collection-set
 * picker (topic-hub §5, #142) — item-level, shared by every chat + the agent.
 */
export function ItemChatShell({
  slug,
  itemId,
  readOnly = false,
  chatSwitcher,
  showCollections,
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
  onSaveToolPrefs,
  envVars,
  envPolicy,
  canOpenEnv,
  onSaveEnvVars,
  environment,
  canExportVideo,
  canAddFiles,
  onSaveSkillPrefs,
  uploadDir,
}: {
  slug: string;
  itemId: string;
  /** Permission-disclosure: forwarded to the composer — true disables it (the
   * user can read the thread but lacks `converse`). */
  readOnly?: boolean;
  /** #200: how prominent the switcher is. "auto" hides it until a 2nd chat
   * exists (single-chat-leaning); "always" surfaces it up front (Topic Hub). */
  chatSwitcher: "auto" | "always";
  /** #200: whether this App manages a collection set (the topic-hub §5 picker).
   * Derived upstream from the manifest's `context_files` containing
   * `collections.json`; off for most Apps (RCA / Playground). */
  showCollections: boolean;
} & AgentChrome) {
  // The chat the address names (`?chat=`), read once — the schedules overview
  // links a schedule's last run to its own chat this way. Tied to the item it
  // was read for: the shell is not remounted between items, and an id held
  // for another item left the next one with no chat selected (review round 3).
  const [address] = useState(() => ({ itemId, chat: chatFromAddress() }));
  const addressChat = address.itemId === itemId ? address.chat : null;
  const { chats, isLoading, isFetchedAfterMount, createFreeChat, renameChat, deleteChat } =
    useItemChats(
      slug,
      itemId,
      undefined,
      // A list cached moments ago is fresh to the cache but may predate that
      // chat (Run now just made it): fetch it on mount when the address asks.
      addressChat ? "always" : undefined,
    );
  // `?chat=<id>` opens that chat — the schedules overview links a schedule's
  // last run to its own conversation this way (docs/plan-schedule-overview.md).
  // Read once, from the address the item was opened with; an id the item has
  // no chat for falls back to the most recent one like any other.
  const [activeChatId, setActiveChatId] = useState<string | null>(addressChat);
  const [managing, setManaging] = useState(false);
  const reopening = useRef(false);
  /** Why auto-opening the item's first chat failed, if it did. */
  const [openFailed, setOpenFailed] = useState<string | null>(null);

  // The item's collection set (topic-hub §5, #142) is a workspace file shared by
  // every chat + the agent, so the picker lives at the shell level, not per chat.
  const fileService = useMemo(() => investigationFileService(slug, itemId), [slug, itemId]);
  const collections = useItemCollections(fileService);
  const collectionCount = collections.data?.selectedIds.length ?? 0;
  const [pickerOpen, setPickerOpen] = useState(false);

  // Keep a valid selection: when nothing is active, or the active chat was just
  // deleted, fall back to the most-recent chat (chats are activity-sorted, §132).
  //
  // Not for the chat the ADDRESS named until the list has been fetched since
  // mount: a cached list (fresh or not) can predate a schedule's chat made
  // since, and falling back against it threw the `?chat=` choice away. An id
  // still absent from the fetched list falls back as before.
  useEffect(() => {
    if (!chats.length) return;
    if (activeChatId == null || !chats.some((c) => c.chat_id === activeChatId)) {
      if (!isFetchedAfterMount && activeChatId != null && activeChatId === addressChat) return;
      setActiveChatId(chats[0].chat_id);
    }
  }, [chats, activeChatId, isFetchedAfterMount, addressChat]);

  // A Hub with no chats (brand-new, or every chat deleted, §132) auto-opens one so
  // the item lands on a usable composer instead of an empty placeholder. `reopening`
  // suppresses a double-create across the create→refetch gap; it clears only once a
  // chat is actually listed, so a later "delete the last chat" re-arms it.
  useEffect(() => {
    if (chats.length) {
      reopening.current = false;
      return;
    }
    if (isLoading || reopening.current) return;
    reopening.current = true;
    setOpenFailed(null);
    void createFreeChat().then(
      (c) => setActiveChatId(c.chat_id),
      (err: unknown) => {
        // The latch STAYS closed. `createFreeChat` is a fresh closure each
        // render, so this effect re-runs on every render — releasing the latch
        // here would turn a failing create into one attempt per render, i.e. a
        // flood aimed at a server that is already unhappy.
        //
        // Auto-open gets exactly one try; recovery is the explicit Retry below.
        // What was actually broken was that a rejection left NO error and NO way
        // out, so the item sat on the placeholder permanently.
        setOpenFailed(err instanceof Error ? err.message : String(err));
      },
    );
  }, [isLoading, chats.length, createFreeChat]);

  const onFreeChat = async () => {
    setActiveChatId((await createFreeChat()).chat_id);
  };
  const active = chats.find((c) => c.chat_id === activeChatId) ?? null;

  // #200: the switcher leans single-chat — hidden until a second chat exists,
  // unless the App opts into an always-visible switcher (Topic Hub). The bar as a
  // whole renders only when it would carry something: the switcher, the workflow
  // picker, or the collection set. When it carries nothing (a single-chat-leaning
  // App with one chat, e.g. RCA), the lone "+ New chat" escape moves into the chat
  // header instead so the App reads as a single chat — `onNewChat` below.
  const showSwitcher = chatSwitcher === "always" || chats.length > 1;
  // Not workflows: none is started from a chat (plan-workflow-out-of-chat D3 /
  // D7) — the workspace's Workflows sidebar starts them.
  const showBar = showSwitcher || showCollections;

  return (
    <div
      className="item-chat-shell"
      data-testid="item-chat-shell"
      style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}
    >
      {showBar && (
        <div
          className="item-chat-shell__bar"
          data-testid="item-chat-shell__bar"
          style={{
            display: "flex",
            // Wrap rather than clip. With the run-in-this-chat launcher on
            // this line the bar is 379px of content at a 390px viewport, and
            // an unwrapped row cut the collections button off at the edge.
            // Measured: without wrap, scrollWidth 379 vs clientWidth 350.
            //
            // Wrapping alone was not enough: `.chat-switcher` had a zero flex
            // basis, so it absorbed the launcher's width instead of forcing a
            // wrap — at 390px the switcher was 22px wide with no title, the
            // #456 disease the header identity block already had. It has a
            // real basis now (`topic-hub.css`), so the row breaks first.
            flexWrap: "wrap",
            gap: 8,
            alignItems: "center",
            flex: "0 0 auto",
            padding: "4px 8px",
            borderBottom: "1px solid var(--paper-3)",
          }}
        >
          {showSwitcher && (
            <ChatSwitcher
              chats={chats}
              activeChatId={activeChatId}
              onSelect={setActiveChatId}
              onManage={() => setManaging(true)}
            />
          )}
          <NewItemPicker onFreeChat={onFreeChat} />
          {showCollections && (
            // `margin-left: auto`, not a spacer element. A spacer is an item:
            // on a wrapped line it still costs a `gap`, and at the workspace's
            // 280px column that gap was what tipped the bar from two rows to
            // three (en). The margin right-aligns the button on whatever line
            // it lands on and costs nothing when it wraps.
            <span style={{ marginLeft: "auto" }}>
              <CollectionsButton count={collectionCount} onClick={() => setPickerOpen(true)} />
            </span>
          )}
        </div>
      )}
      {managing && (
        <ManageChatsModal
          chats={chats}
          activeChatId={activeChatId}
          onClose={() => setManaging(false)}
          onSelect={setActiveChatId}
          onRename={(id, title) => void renameChat(id, title)}
          onDelete={(id) => void deleteChat(id)}
        />
      )}
      {pickerOpen && (
        <CollectionsPickerModal fileService={fileService} onClose={() => setPickerOpen(false)} />
      )}
      {active ? (
        <ItemChatPanel
          key={active.chat_id}
          slug={slug}
          itemId={itemId}
          readOnly={readOnly}
          chat={active}
          // #200: when the bar is hidden the chat header carries the lone escape
          // hatch; when the bar is shown its "+ New" picker already does, so the
          // header omits it (exactly one create-entry is ever visible).
          onNewChat={showBar ? undefined : onFreeChat}
          picker={picker}
          suggestions={suggestions}
          appTitle={appTitle}
          appIcon={appIcon}
          appColor={appColor}
          attachedPreset={attachedPreset}
          onAttachPreset={onAttachPreset}
          onSaveToolPrefs={onSaveToolPrefs}
          envVars={envVars}
          envPolicy={envPolicy}
          canOpenEnv={canOpenEnv}
          onSaveEnvVars={onSaveEnvVars}
          environment={environment}
          canExportVideo={canExportVideo}
          canAddFiles={canAddFiles}
          onSaveSkillPrefs={onSaveSkillPrefs}
          uploadDir={uploadDir}
        />
      ) : (
        <div className="item-chat-panel__empty" data-testid="no-chat">
          {openFailed ? (
            <>
              <p>對話開啟失敗：{openFailed}</p>
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                data-testid="retry-open-chat"
                onClick={() => {
                  setOpenFailed(null);
                  reopening.current = true;
                  void createFreeChat().then(
                    (c) => setActiveChatId(c.chat_id),
                    (err: unknown) => {
                      reopening.current = false;
                      setOpenFailed(err instanceof Error ? err.message : String(err));
                    },
                  );
                }}
              >
                重試
              </button>
            </>
          ) : (
            <p>No conversation open yet — start one from the menu above to begin.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ItemChatPanel({
  slug,
  itemId,
  readOnly = false,
  chat,
  onNewChat,
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
  onSaveToolPrefs,
  envVars,
  envPolicy,
  canOpenEnv,
  onSaveEnvVars,
  environment,
  canExportVideo,
  canAddFiles,
  onSaveSkillPrefs,
  uploadDir,
}: {
  slug: string;
  itemId: string;
  readOnly?: boolean;
  chat: ItemChatSummary;
  /** #200: the single-chat-leaning escape hatch — present only when the shell
   * bar is hidden, so the header is the sole place to start a fresh chat. */
  onNewChat?: () => void;
} & AgentChrome) {
  // The active chat drives the full RCA AgentPanel (AgentState shape) — the
  // model picker, suggestions, @mention, attach, undo and Cmd-Enter all work
  // per chat. Injected as a prop so AgentPanel needs no <AgentProvider> here.
  const agent = useItemChat({ slug, itemId, chatId: chat.chat_id });
  return (
    <div
      className="item-chat-panel"
      data-testid="item-chat-panel"
      style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
      {/* #613: the agent's todo checklist for THIS chat — pinned above the feed
          like the workflow progress. Locks editing while a turn streams. */}
      <TodoPanel
        slug={slug}
        itemId={itemId}
        chatId={chat.chat_id}
        streaming={agent.log.streaming}
        readOnly={!!readOnly}
      />

      <AgentPanel
        investigationId={itemId}
        chatId={chat.chat_id}
        readOnly={readOnly}
        agent={agent}
        fill
        onNewChat={onNewChat}
        picker={picker}
        suggestions={suggestions}
        attachedPreset={attachedPreset}
        onAttachPreset={onAttachPreset}
        onSaveToolPrefs={onSaveToolPrefs}
        envVars={envVars}
        envPolicy={envPolicy}
        canOpenEnv={canOpenEnv}
        onSaveEnvVars={onSaveEnvVars}
        environment={environment}
        canExportVideo={canExportVideo}
        canAddFiles={canAddFiles}
        onSaveSkillPrefs={onSaveSkillPrefs}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
        uploadDir={uploadDir}
      />
    </div>
  );
}
