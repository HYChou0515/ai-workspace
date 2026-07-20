/**
 * VSCode-shaped workspace shell. Renders all chrome (top bar, activity
 * bar, sidebar, editor, bottom panel, status bar, agent panel) and
 * owns the file/tab state shared between them.
 */

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api";
import type { AppItem, AppManifest, CloseStatus, FileInfo } from "../../api/types";
import { ItemShareDialog } from "../../components/ItemShareDialog";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { canConverse, canReadItemContent, parseItemPermission } from "../../lib/itemPermission";
import { DomainField } from "../../components/DomainField";
import { DomainFields } from "../../components/DomainFields";
import { ItemForm, pruneEmpty } from "../../components/ItemForm";
import { ymd } from "../../lib/date";
import { modCombo } from "../../lib/platform";
import { ActivityFeed } from "../../components/ActivityFeed";
import { PresenceBar } from "../../components/PresenceBar";
import { Icon, type IconName } from "../../components/Icon";
import { ModalShell } from "../../components/ModalShell";
import { Popover, PopoverDivider, PopoverItem } from "../../components/Popover";
import { CrossHandle } from "../../components/CrossHandle";
import { ResizeDivider } from "../../components/ResizeDivider";
import { ItemChatShell } from "../../components/ItemChatShell";
import { resolveUploadDir } from "./attach";
import { DialogProvider, useDialog } from "../../components/Dialog";
import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { WorkspaceSlugProvider, useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { EditModeProvider, useEditMode } from "../../hooks/editMode";
import { OpenFileProvider } from "../../hooks/openFile";
import {
  FileBufferProvider,
  FileBufferStore,
  bufferIO,
  reactQueryContentCache,
  useIsDirty,
} from "../../hooks/fileBuffer";
import {
  type EditorGroup,
  type EditorTab,
  type SplitDir,
  useEditorGroups,
} from "../../hooks/useEditorGroups";
import { useBreadcrumbs } from "../../hooks/breadcrumbs";
import { AgentProvider, useAgent } from "../../hooks/useAgent";
import { ItemCrumbChips } from "./ItemCrumbChips";
import { useCloseInvestigation } from "../../hooks/useInvestigationMutations";
import { useSetItemPermission, useUpdateItemField } from "../../hooks/useResources";
import { formatMetrics } from "./agentLog";
import { useIsNarrow } from "../../hooks/useMediaQuery";
import { usePersistentBoolean } from "../../hooks/usePersistentBoolean";
import { usePersistentDeque } from "../../hooks/usePersistentSet";
import { usePersistentNumber } from "../../hooks/usePersistentNumber";
import { useStickToBottom } from "../../hooks/useStickToBottom";
import { useOnTurnEnd } from "../../hooks/useOnTurnEnd";
import { useRefreshFiles } from "../../hooks/useRefreshFiles";
import { emitRunAll } from "../../lib/editorEvents";
import { FileView } from "../../renderers/FileView";
import { CommandPalette } from "./CommandPalette";
import { FileTree } from "./FileTree";
import { type Edge, type PaneNode, edgeForPoint } from "./paneTree";
import { basename, breadcrumbSegments, dirChildren } from "./renderer";
import { hasEditToggle, isRawEditorView, pickRenderer } from "../../renderers/registry";
import { SearchPanel } from "./SearchPanel";
import { TerminalPane } from "./TerminalPane";
import { pxToRem } from "../../lib/pxToRem";

type OpenFileFn = (path: string, opts?: { preview?: boolean }) => void;

export type ActivityMode = "evidence" | "search" | "history" | "reviewers" | "activity";

/** Close a tab through the dirty-aware path (save-on-close prompt). Provided
 * by ShellBody so the deep tab strip can request closes without prop drilling. */
const RequestCloseContext = createContext<(groupId: string, path: string) => void>(() => {});

/** #159: whether the file IDE starts collapsed (chat as the main stage) when an
 * item first opens. Chat-first Apps collapse it; ide-first Apps (RCA) open it.
 * An App with no IDE at all (`function.workspace` false) reports collapsed —
 * chat always fills the row there. This is only the first-time default; a
 * per-App preference persisted in localStorage overrides it. */
export function initialIdeCollapsed(manifest: AppManifest): boolean {
  if (!manifest.function.workspace) return true;
  return manifest.layout.primary_surface === "chat";
}

/** #464: whether the agent panel renders beside the IDE. On a wide viewport it
 * always does. On a narrow one it only shows when the chat is already filling
 * the row (the IDE is collapsed) — side-by-side, the fixed agent width would
 * force horizontal overflow, so narrow makes the IDE and the chat mutually
 * exclusive (switched via the TopBar `Workspace` toggle). */
export function showAgentPanel(isNarrow: boolean, chatFills: boolean): boolean {
  return !isNarrow || chatFills;
}

/** #419 §B5: the paths the workspace opens on entry as the main stage. A
 * "views"-first App (PM) opens its declarative `layout.views` (board / gantt /
 * roadmap …); every other App opens `default_tabs`. Both are filtered to files
 * that exist by the caller. */
export function mainSurfaceTabs(manifest: AppManifest): string[] {
  const { primary_surface, views, default_tabs } = manifest.layout;
  if (primary_surface === "views" && views && views.length > 0) return views;
  return default_tabs;
}

/** Provider shell: owns the shared file-buffer store + dialog/confirm
 * context, then renders the workspace body inside them. */
export function WorkspaceShell({
  item,
  manifest,
  files,
  dirs = [],
  onFilesChanged,
  onInvestigationChanged,
}: {
  item: AppItem;
  manifest: AppManifest;
  files: FileInfo[];
  dirs?: string[];
  onFilesChanged?: () => void;
  onInvestigationChanged?: () => void;
}) {
  const service = useMemo(
    () => investigationFileService(manifest.slug, item.resource_id),
    [manifest.slug, item.resource_id],
  );
  const queryClient = useQueryClient();
  const bufferStore = useMemo(() => {
    // The buffer reads/writes file content THROUGH the shared qk.file cache, so
    // an open editor and any other reader of the same workspace file dedupe onto
    // one entry instead of each fetching it.
    const io = bufferIO(service);
    return new FileBufferStore(io, reactQueryContentCache(queryClient, service.scopeId, io));
  }, [service, queryClient]);
  return (
    <WorkspaceSlugProvider value={manifest.slug}>
      <DialogProvider>
        <FileServiceProvider value={service}>
          <AgentProvider investigationId={item.resource_id}>
          <FileBufferProvider store={bufferStore}>
            <EditModeProvider>
              <ShellBody
                item={item}
                manifest={manifest}
                files={files}
                dirs={dirs}
                onFilesChanged={onFilesChanged}
                onInvestigationChanged={onInvestigationChanged}
                bufferStore={bufferStore}
              />
            </EditModeProvider>
          </FileBufferProvider>
          </AgentProvider>
        </FileServiceProvider>
      </DialogProvider>
    </WorkspaceSlugProvider>
  );
}

function ShellBody({
  item,
  manifest,
  files,
  dirs = [],
  onFilesChanged,
  onInvestigationChanged,
  bufferStore,
}: {
  item: AppItem;
  manifest: AppManifest;
  files: FileInfo[];
  dirs?: string[];
  onFilesChanged?: () => void;
  onInvestigationChanged?: () => void;
  bufferStore: FileBufferStore;
}) {
  const [editOpen, setEditOpen] = useState(false);
  const dialog = useDialog();
  // Inline-edit of domain fields (breadcrumb/statusbar) goes through the generic
  // per-App item update (read-modify-PUT), driven by the manifest's field schema.
  const { setField, setFields } = useUpdateItemField(
    manifest.slug,
    manifest.resource_route,
    item as unknown as AppItem,
  );
  useBreadcrumbs([
    { label: "Home", to: "/" },
    { label: manifest.title, to: `/a/${manifest.slug}` },
    { label: item.title },
  ]);
  // The initial open tabs come from the App's manifest (#89 P7b), filtered to
  // those that actually exist — not a hardcoded RCA design-view list. A
  // "views"-first App (#419 §B5) opens its `layout.views` instead of default_tabs.
  const surfaceTabs = mainSurfaceTabs(manifest);
  const initialPaths = useMemo(
    () => surfaceTabs.filter((p) => files.some((f) => f.path === p)),
    [surfaceTabs, files],
  );
  const groups = useEditorGroups(initialPaths);
  const [activityMode, setActivityMode] = useState<ActivityMode>("evidence");
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Permission-disclosure graceful-degrade: lock the panels the user lacks the
  // verb for, so a limited-access member sees a clean locked state instead of a
  // raw 403 from the file / chat sub-route (the backend still enforces). Owner
  // for access is `created_by`, not the display `owner` field.
  const me = useCurrentUser();
  const _perm = parseItemPermission(item.permission);
  const _canSeeFiles = canReadItemContent(_perm, me, item.created_by);
  const _canConverse = canConverse(_perm, me, item.created_by);

  // Resizable + collapsible panels (VSCode-style). Sizes persist; ⌘B/⌘J
  // toggle the sidebar / bottom panel.
  const [sidebarW, setSidebarW] = usePersistentNumber("rca:layout:sidebar", 260, 180, 560);
  // #108: the chat panel must be draggable to (near) full width. The editor area
  // is `minWidth: 0`, so it yields, and the divider physically stops at the row's
  // left edge — making the viewport width the only real ceiling. The old hard 680
  // cap stopped the drag long before that. (Server-render guard: jsdom defines
  // window, so tests get its default width; SSR — which we don't use — falls back.)
  const agentMaxW = typeof window === "undefined" ? 2000 : window.innerWidth;
  const [agentW, setAgentW] = usePersistentNumber("rca:layout:agent", 380, 280, agentMaxW);
  const [bottomH, setBottomH] = usePersistentNumber("rca:layout:bottom", 200, 80, 600);
  // Snapshot panel sizes at drag start so each pointermove computes
  // `start + delta` (anchored). See ResizeDivider docs.
  const sidebarStart = useRef(sidebarW);
  const agentStart = useRef(agentW);
  const bottomStart = useRef(bottomH);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [bottomOpen, setBottomOpen] = useState(true);
  // #159: chat is the main stage. When the IDE is collapsed, the whole
  // workspace (activity bar + tree + editor + bottom panel) folds away so the
  // chat fills the row — toggled by the TopBar `Workspace` button (and the
  // chevron on the editor/chat divider). The editor keeps a min width while
  // open, so dragging the divider can't squeeze it into a broken sliver;
  // full-chat is this explicit fold, not a drag.
  //
  // The first-time default comes from the App's `layout.primary_surface`
  // (chat-first Apps open collapsed; RCA's ide-first opens the workspace), then
  // the user's choice persists per-App so it survives reloads.
  const [ideCollapsed, setIdeCollapsed] = usePersistentBoolean(
    `layout:ide-collapsed:${manifest.slug}`,
    initialIdeCollapsed(manifest),
  );
  // Cap the chat width so the editor always keeps a usable minimum. The chat is
  // fixed-width (flexShrink:0), so an over-wide agentW would otherwise squeeze
  // the editor into a broken sliver (the #108 regression). Dragging stops at
  // this cap; truly full chat is the explicit fold, not an unbounded drag.
  const EDITOR_MIN_W = 360;
  const ACTIVITY_BAR_W = 50;
  const viewportW = typeof window === "undefined" ? 1440 : window.innerWidth;
  const chromeW = ACTIVITY_BAR_W + (sidebarOpen ? sidebarW : 0);
  const maxChatW = Math.max(280, viewportW - chromeW - EDITOR_MIN_W);
  const effectiveAgentW = Math.min(agentW, maxChatW);
  // #200: the chat fills the whole row when there's no IDE beside it — a
  // workspace=false App has none, and collapsing the IDE unmounts it. Otherwise
  // the chat sits at its resizable width next to the editor.
  const chatFills = !manifest.function.workspace || ideCollapsed;

  // #464: below 768px the 50 + sidebar + editor + agent columns can't coexist.
  // The shell goes single-column — the agent panel and the IDE become mutually
  // exclusive (toggled by the TopBar `Workspace` button), and the file-tree
  // sidebar becomes a tap-to-open overlay so the editor keeps the full width.
  const isNarrow = useIsNarrow();
  useEffect(() => {
    // Track the breakpoint symmetrically: narrow starts editor-first (the sidebar
    // is a tap-to-open overlay), wide restores the persistent tree column. One-way
    // (only closing on narrow) would strand a pointer-only user with the desktop
    // sidebar collapsed after a wide→narrow→wide resize, since the only wide reopen
    // is ⌘B and the ActivityBar reopen is gated to narrow (#464).
    setSidebarOpen(!isNarrow);
  }, [isNarrow]);
  const agentVisible = showAgentPanel(isNarrow, chatFills);

  const recentFiles = usePersistentDeque(
    `rca:recent-files:${item.resource_id}`,
    10,
  );

  // Sidebar / palette open into the active editor group.
  const openFile = useCallback<OpenFileFn>(
    (path, opts) => {
      groups.openInActive(path, opts);
      recentFiles.push(path);
    },
    [groups, recentFiles],
  );

  // Latest group state for the keyboard handler (bound once via a ref).
  const gRef = useRef(groups);
  gRef.current = groups;

  // Full refresh: sandbox flush → invalidate file list + dirs + every open
  // file's content + reload editor buffers. Called from the refresh button,
  // after agent turns, and after terminal exec — see useRefreshFiles for the
  // four caches it busts.
  const refreshFiles = useRefreshFiles(item.resource_id);
  // When an agent turn finishes it may have created/edited/deleted files via
  // its tools — refresh everything so the tree, viewers, and editor catch up.
  useOnTurnEnd(useAgent().log.streaming, () => {
    void refreshFiles();
    onFilesChanged?.();
  });

  // VSCode-style delete-open-file handling: when a file disappears from the
  // listing (deleted in the tree), auto-close its CLEAN tabs; keep dirty
  // ones open so ⌘S can re-create the file.
  const filePaths = useMemo(() => new Set(files.map((f) => f.path)), [files]);
  useEffect(() => {
    const g = gRef.current;
    for (const [gid, grp] of Object.entries(g.groups)) {
      for (const t of grp.tabs) {
        if (!filePaths.has(t.path) && !bufferStore.isDirty(t.path)) {
          g.closeTab(gid, t.path);
        }
      }
    }
  }, [filePaths, bufferStore]);

  // Close a tab, prompting to save when it's the LAST open view of a dirty
  // file (a sibling pane still showing it means no data is at risk).
  const requestCloseTab = useCallback(
    async (groupId: string, path: string) => {
      const g = gRef.current;
      const openElsewhere = Object.entries(g.groups).some(
        ([gid, grp]) => gid !== groupId && grp.tabs.some((t) => t.path === path),
      );
      if (bufferStore.isDirty(path) && !openElsewhere) {
        const choice = await dialog.confirm({
          title: `Save changes to ${basename(path)}?`,
          body: "Your changes will be lost if you don't save them.",
          actions: [
            { id: "save", label: "Save", variant: "primary" },
            { id: "discard", label: "Don't Save", variant: "danger" },
            { id: "cancel", label: "Cancel" },
          ],
        });
        if (choice === null || choice === "cancel") return;
        if (choice === "save") await bufferStore.save(path);
        if (choice === "discard") bufferStore.discard(path);
      }
      gRef.current.closeTab(groupId, path);
    },
    [bufferStore, dialog],
  );
  const requestCloseRef = useRef(requestCloseTab);
  requestCloseRef.current = requestCloseTab;

  // Global keyboard: ⌘P palette · ⌘B sidebar · ⌘J bottom panel ·
  // ⌘W close the active group's active tab · ⌘1-9 jump to its Nth tab.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const k = e.key.toLowerCase();
      const g = gRef.current;
      if (k === "p") {
        e.preventDefault();
        // #159: the palette jumps to files, so a collapsed IDE auto-expands —
        // the picked file lands in a visible editor, not behind the chat.
        setIdeCollapsed(false);
        setPaletteOpen(true);
      } else if (k === "b") {
        e.preventDefault();
        setSidebarOpen((v) => !v);
      } else if (k === "j") {
        e.preventDefault();
        setBottomOpen((v) => !v);
      } else if (k === "s") {
        const active = g.activeGroup?.activePath;
        if (active) {
          e.preventDefault();
          void bufferStore.save(active);
        }
      } else if (k === "w") {
        const active = g.activeGroup?.activePath;
        if (active) {
          e.preventDefault();
          void requestCloseRef.current(g.activeGroupId, active);
        }
      } else if (k >= "1" && k <= "9") {
        const idx = Number.parseInt(k, 10) - 1;
        const target = g.activeGroup?.tabs[idx];
        if (target) {
          e.preventDefault();
          g.selectTab(g.activeGroupId, target.path);
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <OpenFileProvider value={openFile}>
    <RequestCloseContext.Provider value={requestCloseTab}>
      <div
        data-testid="page-item"
        style={{
          // Fill the global layout's content area (#158), not the whole viewport
          // — the global bar takes the top 40px.
          height: "100%",
          display: "flex",
          flexDirection: "column",
          background: "var(--paper)",
          overflow: "hidden",
        }}
      >
        <TopBar
          item={item}
          manifest={manifest}
          onEditField={setField}
          ideCollapsed={ideCollapsed}
          onToggleIde={() => setIdeCollapsed((v) => !v)}
          onCommandPalette={() => setPaletteOpen(true)}
          onEdit={() => setEditOpen(true)}
        />
        {editOpen && (
          <EditItemModal
            manifest={manifest}
            item={item as unknown as AppItem}
            onClose={() => setEditOpen(false)}
            onSubmit={(patch) => {
              setFields(patch);
              setEditOpen(false);
              onInvestigationChanged?.();
            }}
          />
        )}
        <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
          {/* #89: the file IDE (activity bar + file tree + editor + bottom
              panel) renders only when the App enables `function.workspace`; a
              workspace=false App (chat-only) shows just the agent panel. The
              terminal tab inside is further gated on `function.terminal` —
              sandbox's only human UI surface (exec/package are backend tools,
              gated by allowed_tools), so there is no separate sandbox pane. */}
          {manifest.function.workspace && !ideCollapsed && _canSeeFiles && (
            <>
          <ActivityBar
            mode={activityMode}
            onMode={(m) => {
              setActivityMode(m);
              // On narrow the sidebar is a closed overlay; tapping an activity
              // icon opens it (there's no persistent tree column to reveal).
              if (isNarrow) setSidebarOpen(true);
            }}
          />
          {isNarrow && sidebarOpen && (
            // Backdrop behind the overlay sidebar — tap to dismiss (starts after
            // the 50px activity bar so it stays tappable).
            <button
              type="button"
              aria-label="Close file panel"
              onClick={() => setSidebarOpen(false)}
              style={{ position: "absolute", inset: "0 0 0 50px", zIndex: 15, background: "rgba(20,22,28,0.28)", border: "none", cursor: "pointer" }}
            />
          )}
          {sidebarOpen && (
            <>
              <div
                style={
                  isNarrow
                    ? { position: "absolute", left: 50, top: 0, bottom: 0, zIndex: 20, width: "min(280px, 78vw)", display: "flex", minWidth: 0, background: "var(--paper)", borderRight: "1px solid var(--paper-3)", boxShadow: "6px 0 24px rgba(20,22,28,0.18)" }
                    : { width: sidebarW, flexShrink: 0, display: "flex", minWidth: 0 }
                }
              >
                <ActivitySidebar
                  mode={activityMode}
                  item={item}
                  manifest={manifest}
                  files={files}
                  dirs={dirs}
                  activePath={groups.activeFile}
                  recentFiles={recentFiles.values}
                  onOpenFile={openFile}
                  // Full refresh (sandbox flush + every cache busted) on
                  // top of the parent's lightweight list-refetch. The button
                  // routes here via `ActivitySidebar → FileTree.onChanged`.
                  onFilesChanged={() => {
                    void refreshFiles();
                    onFilesChanged?.();
                  }}
                />
              </div>
              {!isNarrow && (
                <ResizeDivider
                  orientation="vertical"
                  ariaLabel="resize sidebar"
                  onResizeStart={() => {
                    sidebarStart.current = sidebarW;
                  }}
                  onResize={(d) => setSidebarW(sidebarStart.current + d)}
                />
              )}
            </>
          )}
          <EditorArea
            investigationId={item.resource_id}
            showTerminal={manifest.function.terminal}
            groups={groups}
            files={files}
            bottomHeight={bottomH}
            bottomOpen={bottomOpen}
            onResizeBottomStart={() => {
              bottomStart.current = bottomH;
            }}
            onResizeBottom={(d) => setBottomH(bottomStart.current - d)}
            onToggleBottom={() => setBottomOpen((v) => !v)}
          />
          {!isNarrow && (
            <ResizeDivider
              orientation="vertical"
              ariaLabel="resize agent panel"
              onResizeStart={() => {
                agentStart.current = effectiveAgentW;
              }}
              onResize={(d) => setAgentW(Math.min(maxChatW, agentStart.current - d))}
              collapse={{
                label: "Collapse workspace",
                icon: "chev_l",
                onToggle: () => setIdeCollapsed(true),
              }}
            />
          )}
            </>
          )}
          {/* #159: the old 16px collapsed-edge handle is gone — the discoverable
              TopBar `Workspace` button is now the canonical way to bring the IDE
              back, so a near-invisible edge sliver is just noise. */}
          <div
            style={{
              // #464: on a narrow viewport the agent hides while the IDE is up
              // (mutually exclusive, toggled via `Workspace`) so the fixed agent
              // width can't force overflow. Hidden, not unmounted — the chat and
              // its live stream survive the toggle. `display:none` also drops it
              // from the flex row so it consumes no width.
              display: agentVisible ? "flex" : "none",
              flexDirection: "column",
              height: "100%",
              minHeight: 0,
              // The chat fills the row when there's no IDE beside it (a workspace=false
              // App, or the IDE collapsed); otherwise it sits at its resizable width.
              // The multi-chat shell takes no width prop, so the wrapper owns it.
              ...(chatFills ? { flex: 1, minWidth: 0 } : {}),
              width: chatFills ? undefined : effectiveAgentW,
            }}
          >
            {/* #200: every App workspace is the per-item multi-chat shell — no slug
                fork. It leans single-chat by manifest (`layout.chat_switcher`): the
                switcher stays hidden until a second chat exists, and the lone
                "+ New chat" escape lives in the chat header, so a wedged chat is
                never a dead end. The shell carries workflow launches as chats too,
                so the old single-chat WorkflowRunSection is retired. */}
            <ItemChatShell
              readOnly={!_canConverse}
              slug={manifest.slug}
              itemId={item.resource_id}
              profile={String(item.profile ?? manifest.default_profile)}
              // #198: where the composer's attach stages files — the item's profile's
              // upload_dir (default uploads/), the same folder its workflows glob.
              uploadDir={resolveUploadDir(
                manifest.profiles,
                String(item.profile ?? manifest.default_profile),
              )}
              chatSwitcher={manifest.layout.chat_switcher}
              // Derived, not a flag: an App manages a collection set iff its agent
              // injects collections.json each turn (Topic Hub §5).
              showCollections={!!manifest.agent.context_files?.includes("collections.json")}
              // Same manifest-derived chat chrome the RCA <AgentPanel> got — the
              // shell threads it through to each chat tab's panel.
              picker={manifest.agent.picker}
              suggestions={manifest.agent.suggestions}
              appTitle={manifest.title}
              appIcon={manifest.icon}
              appColor={manifest.color}
              attachedPreset={String(item.attached_preset ?? "")}
              onAttachPreset={(preset) => setField("attached_preset", preset)}
              onSaveToolPrefs={(prefs) => setField("attached_tool_prefs", prefs)}
              onSaveSkillPrefs={(prefs) => setField("attached_skill_prefs", prefs)}
            />
          </div>
        </div>

        <CommandPalette
          open={paletteOpen}
          files={files}
          onClose={() => setPaletteOpen(false)}
          onPick={openFile}
        />
      </div>
    </RequestCloseContext.Provider>
    </OpenFileProvider>
  );
}

/** "Edit details" modal — a schema-driven {@link ItemForm} for the App item
 * (#89 P7b), replacing the RCA-specific NewInvestigationModal. Submits the
 * non-empty values as a patch; the shell PUTs the merged item. */
export function EditItemModal({
  manifest,
  item,
  onClose,
  onSubmit,
}: {
  manifest: AppManifest;
  item: AppItem;
  onClose: () => void;
  onSubmit: (patch: Record<string, unknown>) => void;
}) {
  const me = useCurrentUser();
  const [sharing, setSharing] = useState(false);
  const owner = (item.created_by as string) || (item.owner as string) || "";
  // #306 PR3: the sharing control (grill D2). Owner-only in the UI; the backend
  // additionally honours change_permission delegates (it enforces regardless).
  const canManageAccess = me === owner;
  const perm = parseItemPermission((item as Record<string, unknown>).permission);
  const access = useSetItemPermission(manifest.slug, item.resource_id);
  return (
    <ModalShell
      onClose={onClose}
      labelledBy="edit-item-title"
      width={460}
      panelStyle={{ padding: 20 }}
      data-testid="edit-item"
    >
      <h2 id="edit-item-title" style={{ marginTop: 0, fontSize: pxToRem(18) }}>
        Edit {manifest.item.noun}
      </h2>
      <ItemForm
        manifest={manifest}
        initialValues={item as Record<string, unknown>}
        submitLabel="Save"
        onSubmit={(values) => onSubmit(pruneEmpty(values))}
      />
      {canManageAccess && (
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="manage-access"
          style={{ marginTop: 12 }}
          onClick={() => setSharing(true)}
        >
          Manage access…
        </button>
      )}
      {sharing && (
        <ItemShareDialog
          itemName={(item.title as string) || manifest.item.noun}
          owner={owner}
          value={perm ?? { visibility: "private" }}
          busy={access.isPending}
          error={access.error}
          // Close ONLY on success: a 403 (e.g. a delegate whose grant was just
          // revoked) keeps the dialog up with the reason, instead of the old
          // `await` inside a `() => void` prop, which turned the rejection into
          // an unhandled promise and left the dialog hanging silently.
          onSubmit={(next) => {
            void access.setPermissionAsync(next).then(
              () => setSharing(false),
              () => {},
            );
          }}
          onClose={() => setSharing(false)}
        />
      )}
    </ModalShell>
  );
}

/* ------------------------------ Top bar ------------------------------ */

export function TopBar({
  item,
  manifest,
  onEditField,
  ideCollapsed,
  onToggleIde,
  onCommandPalette,
  onEdit,
}: {
  item: AppItem;
  manifest: AppManifest;
  onEditField: (name: string, value: string) => void;
  /** #159: whether the file IDE is currently folded away (chat is the main
   * stage). Drives the `Workspace` toggle's pressed state + hides IDE-only
   * chrome (the command palette) while collapsed. */
  ideCollapsed: boolean;
  onToggleIde: () => void;
  onCommandPalette: () => void;
  onEdit: () => void;
}) {
  const isNarrow = useIsNarrow();
  return (
    <div
      style={{
        // Narrow: the trailing control cluster (Workspace toggle + palette +
        // Members + Close + Notifications + avatar) can't fit one 360px row, so
        // wrap instead of overflowing — page-item clips overflow with no scroll,
        // which would strand the Close (the only resolve entry point) (#464).
        height: isNarrow ? "auto" : 52,
        minHeight: 52,
        flexShrink: 0,
        background: "var(--white)",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "center",
        flexWrap: isNarrow ? "wrap" : "nowrap",
        rowGap: isNarrow ? 8 : undefined,
        padding: isNarrow ? "8px 12px" : "0 16px",
        gap: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          minWidth: 0,
          flexShrink: 1,
          flexWrap: isNarrow ? "wrap" : "nowrap",
          rowGap: isNarrow ? 6 : undefined,
          fontSize: "var(--text-body-sm)",
          color: "var(--text-paper-d)",
        }}
      >
        {/* topic/product are item attributes, not hierarchy — they stay here in
            the page-local header; the global bar owns Home › App › item (#158). */}
        <ItemCrumbChips item={item} manifest={manifest} />
        <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />
        <span style={{ color: "var(--text-paper)", fontWeight: 600 }}>
          {item.title}
        </span>
        {/* Domain fields (severity/status) are manifest/layout-driven + inline-
            editable now, not RCA-hardcoded chips (#89 P7b). */}
        <DomainFields
          surface="breadcrumb"
          manifest={manifest}
          item={item as unknown as AppItem}
          onEditField={onEditField}
        />
        <IdChip resourceId={item.resource_id} />
        <button
          type="button"
          onClick={onEdit}
          title="Edit item details"
          aria-label="Edit item details"
          style={{ color: "var(--text-paper-d)", display: "inline-flex", alignItems: "center" }}
        >
          <Icon name="settings" size={13} />
        </button>
      </div>
      <span style={{ flex: 1 }} />

      {/* #159: chat is the main stage; the file IDE (tree + editor + terminal)
          folds behind this discoverable toggle. Only shown when the App has an
          IDE at all (`function.workspace`); pressed = the workspace is open. */}
      {manifest.function.workspace && (
        <button
          type="button"
          onClick={onToggleIde}
          aria-pressed={!ideCollapsed}
          title={
            ideCollapsed
              ? "Show the file workspace"
              : "Hide the file workspace — the chat expands to fill"
          }
          style={{
            height: 28,
            // Pressed (workspace open) = the accent-soft active fill used across
            // the app's toggles, so the on/off direction is unmistakable rather
            // than a faint bg swap read only via the title/aria (#466 ④).
            border: `1px solid ${ideCollapsed ? "var(--paper-3)" : "var(--accent)"}`,
            borderRadius: "var(--radius-btn)",
            background: ideCollapsed ? "transparent" : "var(--accent-soft)",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "0 10px",
            color: ideCollapsed ? "var(--text-paper-d)" : "var(--accent-h)",
            fontSize: pxToRem(12),
            cursor: "pointer",
          }}
        >
          <Icon name="panel_left" size={13} />
          <span>Workspace</span>
        </button>
      )}

      {/* #159: the command palette jumps to files/symbols — IDE-only chrome.
          Hidden while the workspace is collapsed (and for chat-only Apps); ⌘P
          still works and auto-expands the workspace. */}
      {manifest.function.workspace && !ideCollapsed && (
        <button
          type="button"
          onClick={onCommandPalette}
          title={`Go to file (${modCombo("P")})`}
          style={{
            // 320px fixed would overflow a 360px viewport; on narrow give it its
            // own full-width row (flex-basis 100%) so it fits and stays usable.
            width: isNarrow ? "auto" : 320,
            flex: isNarrow ? "1 1 100%" : "0 0 auto",
            height: 28,
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            background: "var(--paper)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "0 10px",
            color: "var(--text-paper-d)",
            fontSize: pxToRem(12),
          }}
        >
          <Icon name="search" size={13} />
          <span>Go to file, symbol, command…</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11) }}>{modCombo("P")}</span>
        </button>
      )}


      {/* #455: live viewers of this item (who else is here right now) — distinct
          from the declared Members count beside it. */}
      <PresenceBar slug={manifest.slug} itemId={item.resource_id} />

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title="Members"
            style={{
              ...iconBtn,
              display: "inline-flex",
              gap: 4,
              padding: "0 8px",
              width: "auto",
              background: open ? "var(--paper-2)" : "transparent",
            }}
          >
            <Icon name="users" size={15} />
            <span style={{ fontSize: pxToRem(12) }}>
              {((item.members as string[] | undefined)?.length ?? 0) + 1}
            </span>
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 200, padding: "6px 0" }}>
            <MemberLine name={`${item.owner} (owner)`} />
            {((item.members as string[] | undefined) ?? []).map((m) => (
              <MemberLine key={m} name={m} />
            ))}
          </div>
        )}
      </Popover>

      {/* Close is shown only when the App declares a lifecycle (#89 P7b); its
          resolve states come from the manifest, not hardcoded RCA statuses. */}
      {manifest.lifecycle && (
        <CloseInvestigationButton
          slug={manifest.slug}
          item={item}
          lifecycle={manifest.lifecycle}
        />
      )}

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title="Notifications"
            style={{ ...iconBtn, background: open ? "var(--paper-2)" : "transparent" }}
          >
            <Icon name="bell" size={15} />
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 240, padding: "6px 0" }}>
            <div className="caps" style={{ padding: "4px 10px" }}>Notifications</div>
            <div style={{ padding: "4px 10px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
              No new notifications.
            </div>
          </div>
        )}
      </Popover>

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title={item.owner}
            style={{
              width: 24,
              height: 24,
              borderRadius: "50%",
              background: open ? "var(--paper-3)" : "var(--paper-2)",
              border: "1px solid var(--paper-3)",
              fontSize: pxToRem(11),
              fontWeight: 600,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {item.owner.slice(0, 2).toUpperCase()}
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 160 }}>
            <div style={{ padding: "8px 10px", fontWeight: 600, fontSize: pxToRem(12) }}>
              {item.owner}
            </div>
          </div>
        )}
      </Popover>
    </div>
  );
}

function CloseInvestigationButton({
  slug,
  item,
  lifecycle,
}: {
  slug: string;
  item: AppItem;
  lifecycle: { status_field: string; closing_states: string[] };
}) {
  const navigate = useNavigate();
  const closeInvestigation = useCloseInvestigation(slug, item.resource_id);
  // "pure" = leave-as-is teardown (status untouched); the others flip status.
  const [pending, setPending] = useState<string | "pure" | null>(null);
  const currentStatus = String((item as unknown as AppItem)[lifecycle.status_field] ?? "");
  const alreadyClosed = lifecycle.closing_states.includes(currentStatus);

  const close = async (status: string | null, dismiss: () => void) => {
    if (pending) return;
    setPending(status ?? "pure");
    try {
      await closeInvestigation.mutateAsync(status as CloseStatus | null);
      dismiss();
      // Back to this App's dashboard (its item list), not the all-apps launcher.
      navigate(`/a/${slug}`);
    } catch (e) {
      console.error("closeInvestigation failed", e);
      alert(`Close failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPending(null);
    }
  };

  return (
    <Popover
      align="end"
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={onClick}
          title="Close workspace"
          style={{
            height: 28,
            padding: "0 10px",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            fontSize: pxToRem(12),
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: open ? "var(--paper-2)" : "transparent",
            color: "var(--text-paper-d)",
            cursor: "pointer",
          }}
        >
          <Icon name="x" size={12} /> Close
        </button>
      )}
    >
      {(dismiss) => (
        <div style={{ minWidth: 240 }}>
          {/* Pure close — leave the workspace without changing status, so
              an unattended item can free its sandbox. */}
          <PopoverItem
            onClick={() => {
              void close(null, dismiss);
            }}
          >
            <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
              <span>{pending === "pure" ? "Closing…" : "Close (leave open)"}</span>
              <span style={{ fontSize: pxToRem(10), color: "var(--text-paper-d2)" }}>
                Tear down the session, keep status
              </span>
            </span>
          </PopoverItem>
          <PopoverDivider />
          <div className="caps" style={{ padding: "6px 10px" }}>Resolve as…</div>
          {lifecycle.closing_states.map((state) => (
            <PopoverItem
              key={state}
              disabled={alreadyClosed}
              onClick={() => {
                if (!alreadyClosed) void close(state, dismiss);
              }}
            >
              {pending === state ? "Closing…" : state.charAt(0).toUpperCase() + state.slice(1)}
            </PopoverItem>
          ))}
          {alreadyClosed && (
            <div style={{ padding: "4px 10px", fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
              Already {currentStatus}.
            </div>
          )}
        </div>
      )}
    </Popover>
  );
}

function IdChip({ resourceId }: { resourceId: string }) {
  const [copied, setCopied] = useState(false);
  const short = (resourceId.split(":").pop() ?? resourceId).slice(0, 8);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(resourceId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked — ignore */
    }
  };
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={`Copy full id: ${resourceId}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 8px",
        borderRadius: "var(--radius-chip)",
        border: "1px solid var(--paper-3)",
        background: "var(--paper-2)",
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(11),
        color: "var(--text-paper)",
      }}
    >
      {copied ? <Icon name="check" size={11} color="var(--ok)" /> : null}
      {copied ? "copied" : short}
    </button>
  );
}

function MemberLine({ name }: { name: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 10px",
        fontSize: pxToRem(12),
      }}
    >
      <span
        style={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          background: "var(--paper-2)",
          border: "1px solid var(--paper-3)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: pxToRem(10),
          fontWeight: 600,
        }}
      >
        {name.slice(0, 2).toUpperCase()}
      </span>
      {name}
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  width: 32,
  height: 28,
  borderRadius: "var(--radius-btn)",
  border: "1px solid transparent",
  color: "var(--text-paper-d)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
};

/* ----------------------------- Activity bar ----------------------------- */

function ActivityBar({
  mode,
  onMode,
}: {
  mode: ActivityMode;
  onMode: (m: ActivityMode) => void;
}) {
  const items: {
    name: IconName;
    label: string;
    onClick: () => void;
    active: boolean;
  }[] = [
    { name: "folder", label: "Files", onClick: () => onMode("evidence"), active: mode === "evidence" },
    { name: "search", label: "Search files", onClick: () => onMode("search"), active: mode === "search" },
    { name: "clock", label: "History", onClick: () => onMode("history"), active: mode === "history" },
    { name: "users", label: "Reviewers", onClick: () => onMode("reviewers"), active: mode === "reviewers" },
    { name: "bell", label: "Activity", onClick: () => onMode("activity"), active: mode === "activity" },
  ];
  return (
    <div
      style={{
        width: 50,
        flexShrink: 0,
        background: "var(--paper)",
        borderRight: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "8px 0",
      }}
    >
      {items.map((it) => (
        <button
          key={it.label}
          type="button"
          title={it.label}
          onClick={it.onClick}
          style={{
            width: 50,
            height: 44,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            color: it.active ? "var(--accent)" : "var(--text-paper-d)",
            borderLeft: it.active ? "2px solid var(--accent)" : "2px solid transparent",
            background: it.active ? "var(--accent-soft)" : "transparent",
          }}
        >
          <Icon name={it.name} size={18} />
        </button>
      ))}
    </div>
  );
}

/* ----------------------------- Sidebar wrapper ----------------------------- */

function ActivitySidebar(props: {
  mode: ActivityMode;
  item: AppItem;
  manifest: AppManifest;
  files: FileInfo[];
  dirs: string[];
  activePath: string | null;
  recentFiles: string[];
  onOpenFile: OpenFileFn;
  onFilesChanged?: () => void;
}) {
  switch (props.mode) {
    case "evidence":
      return <EvidenceSidebar {...props} />;
    case "search":
      return (
        <SearchPanel
          investigationId={props.item.resource_id}
          onOpenFile={props.onOpenFile}
        />
      );
    case "history":
      return <HistorySidebar files={props.files} recentFiles={props.recentFiles} onOpenFile={props.onOpenFile} />;
    case "reviewers":
      return <ReviewersSidebar item={props.item} />;
    case "activity":
      return <ActivityFeed slug={props.manifest.slug} itemId={props.item.resource_id} onOpenFile={props.onOpenFile} />;
  }
}

/* ----------------------------- Evidence sidebar ----------------------------- */

function EvidenceSidebar({
  item,
  manifest,
  files,
  dirs,
  activePath,
  onOpenFile,
  onFilesChanged,
}: {
  item: AppItem;
  manifest: AppManifest;
  files: FileInfo[];
  dirs: string[];
  activePath: string | null;
  onOpenFile: OpenFileFn;
  onFilesChanged?: () => void;
}) {
  return (
    <SidebarFrame item={item} manifest={manifest}>
      <FileTree
        files={files}
        dirs={dirs}
        activePath={activePath}
        onOpen={onOpenFile}
        onChanged={onFilesChanged}
      />
    </SidebarFrame>
  );
}

/** Extract atx headings — exported for the outline unit test + reuse. */
export function extractHeadings(md: string): { level: number; text: string }[] {
  const out: { level: number; text: string }[] = [];
  for (const line of md.split("\n")) {
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (m && m[1] && m[2]) {
      out.push({ level: m[1].length, text: m[2] });
    }
  }
  return out;
}

/* ----------------------------- History sidebar ----------------------------- */

function HistorySidebar({
  files,
  recentFiles,
  onOpenFile,
}: {
  files: FileInfo[];
  recentFiles: string[];
  onOpenFile: OpenFileFn;
}) {
  // Filter recentFiles to those still present in the file listing.
  const items = recentFiles.filter((p) => files.some((f) => f.path === p));
  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeader}>
        <span className="caps">Recently opened</span>
      </div>
      <div className="scrollable" style={{ flex: 1, overflowY: "auto" }}>
        {items.length === 0 && (
          <div style={{ padding: "8px 14px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
            History is empty.
          </div>
        )}
        {items.map((p) => (
          <TreeRow
            key={p}
            label={basename(p)}
            path={p}
            active={false}
            onOpen={onOpenFile}
          />
        ))}
      </div>
    </aside>
  );
}

/* ----------------------------- Reviewers sidebar ----------------------------- */

function ReviewersSidebar({ item }: { item: AppItem }) {
  const members = (item.members as string[] | undefined) ?? [];
  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeader}>
        <span className="caps">Reviewers</span>
      </div>
      <div style={{ padding: 12, fontSize: pxToRem(12), color: "var(--text-paper)" }}>
        <div style={{ marginBottom: 4 }}>
          <strong>{item.owner}</strong>{" "}
          <span style={{ color: "var(--text-paper-d)" }}>(owner)</span>
        </div>
        {members.map((m) => (
          <div key={m}>{m}</div>
        ))}
        {members.length === 0 && (
          <div style={{ color: "var(--text-paper-d)" }}>No additional members.</div>
        )}
      </div>
    </aside>
  );
}

/* ----------------------------- Shared sidebar frame ----------------------------- */

const sidebarStyle: React.CSSProperties = {
  // Fills the resizable wrapper in WorkspaceShell (width lives there).
  width: "100%",
  flex: 1,
  minWidth: 0,
  background: "var(--paper)",
  borderRight: "1px solid var(--paper-3)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const sidebarHeader: React.CSSProperties = {
  padding: "12px 14px",
  borderBottom: "1px solid var(--paper-3)",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

function SidebarFrame({
  item,
  manifest,
  header,
  children,
}: {
  item: AppItem;
  manifest: AppManifest;
  header?: React.ReactNode;
  children: React.ReactNode;
}) {
  const byName = new Map(manifest.fields.map((f) => [f.name, f]));
  return (
    <aside className="scrollable" style={{ ...sidebarStyle, overflowY: "auto" }}>
      {header && <div style={sidebarHeader}>{header}</div>}
      {children}
      <div style={{ flex: 1 }} />
      <footer
        style={{
          padding: "12px 14px",
          borderTop: "1px solid var(--paper-3)",
          background: "var(--paper-2)",
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          rowGap: 4,
          columnGap: 8,
          fontSize: pxToRem(11),
        }}
      >
        {/* Domain fields follow the App's statusbar layout + schema (#89 P7b),
            not RCA-hardcoded rows. Owner/Opened are cross-App item metadata. */}
        {manifest.layout.statusbar.map((name) => {
          const field = byName.get(name);
          if (!field) return null;
          const value = item[name];
          const tone = manifest.field_styles?.[name]?.[String(value)];
          return (
            <FootMeta key={name} label={manifest.labels[name] ?? name}>
              <DomainField field={field} value={value} tone={tone} />
            </FootMeta>
          );
        })}
        <FootMeta label="Owner">{item.created_by}</FootMeta>
        <FootMeta label="Opened">{ymd(item.created_time)}</FootMeta>
      </footer>
    </aside>
  );
}

function TreeRow({
  label,
  path,
  active,
  indent = 14,
  onOpen,
}: {
  label: string;
  path: string;
  active: boolean;
  indent?: number;
  onOpen: OpenFileFn;
}) {
  return (
    <button
      type="button"
      // Single click peeks (preview tab); double click opens for keeps.
      onClick={() => onOpen(path, { preview: true })}
      onDoubleClick={() => onOpen(path, { preview: false })}
      title="Click to preview · double-click to keep open"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        padding: `4px 14px 4px ${indent}px`,
        textAlign: "left",
        background: active ? "var(--accent-soft)" : "transparent",
        borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
        color: active ? "var(--accent-h)" : "var(--text-paper)",
        fontSize: pxToRem(12),
      }}
    >
      <Icon name="file" size={13} color="var(--text-paper-d)" />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
    </button>
  );
}

function FootMeta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span style={{ color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)", fontSize: pxToRem(10) }}>
        {label}
      </span>
      <span style={{ color: "var(--text-paper)" }}>{children}</span>
    </>
  );
}

/* ----------------------------- Editor area ----------------------------- */

type Groups = ReturnType<typeof useEditorGroups>;

function EditorArea({
  investigationId,
  showTerminal,
  groups,
  files,
  bottomHeight,
  bottomOpen,
  onResizeBottom,
  onResizeBottomStart,
  onToggleBottom,
}: {
  investigationId: string;
  showTerminal: boolean;
  groups: Groups;
  files: FileInfo[];
  bottomHeight: number;
  bottomOpen: boolean;
  onResizeBottom: (deltaFromStart: number) => void;
  onResizeBottomStart: () => void;
  onToggleBottom: () => void;
}) {
  const [bottomTab, setBottomTab] = useState<"problems" | "output" | "terminal" | "agent_log" | "run_history">("agent_log");

  return (
    <section style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ flex: 1, display: "flex", minHeight: 0, background: "var(--white)" }}>
        <GroupTreeView
          node={groups.tree}
          groups={groups}
          investigationId={investigationId}
          files={files}
        />
      </div>

      {bottomOpen && (
        <ResizeDivider
          orientation="horizontal"
          ariaLabel="resize bottom panel"
          onResizeStart={onResizeBottomStart}
          onResize={onResizeBottom}
        />
      )}
      <BottomPanel
        tab={bottomTab}
        onTab={setBottomTab}
        investigationId={investigationId}
        showTerminal={showTerminal}
        height={bottomHeight}
        open={bottomOpen}
        onToggle={onToggleBottom}
      />
      <StatusBar activeTab={groups.activeFile} investigationId={investigationId} />
    </section>
  );
}

/** Recursively lay out the structural pane tree; leaves render a group.
 * Split nodes use flex-grow proportional to `node.ratio` so the divider
 * between A and B can adjust their share by calling groups.setSplitRatio
 * with this node's path from root. */
function GroupTreeView({
  node,
  groups,
  investigationId,
  files,
  path = [],
}: {
  node: PaneNode;
  groups: Groups;
  investigationId: string;
  files: FileInfo[];
  path?: ("a" | "b")[];
}) {
  if (node.type === "leaf") {
    const group = groups.groups[node.id];
    if (!group) return null;
    return (
      <GroupPane group={group} groups={groups} files={files} />
    );
  }
  return (
    <SplitView
      split={node}
      path={path}
      groups={groups}
      investigationId={investigationId}
      files={files}
    />
  );
}

/** One A/B split with a draggable divider in between. */
function SplitView({
  split,
  path,
  groups,
  investigationId,
  files,
}: {
  split: Extract<PaneNode, { type: "split" }>;
  path: ("a" | "b")[];
  groups: Groups;
  investigationId: string;
  files: FileInfo[];
}) {
  const row = split.dir === "row";
  const containerRef = useRef<HTMLDivElement>(null);
  // Snapshotted on drag start: ratio + container size at the moment the
  // drag began. Each pointermove reports its delta from the start cursor;
  // we apply it against the anchor for stable 1:1 tracking.
  const startRatio = useRef(split.ratio);
  const startSize = useRef({ w: 0, h: 0 });
  const onResizeStart = () => {
    startRatio.current = split.ratio;
    const el = containerRef.current;
    startSize.current = el ? { w: el.clientWidth, h: el.clientHeight } : { w: 0, h: 0 };
  };
  const onResize = (deltaFromStart: number) => {
    const size = row ? startSize.current.w : startSize.current.h;
    if (size <= 0) return;
    groups.setSplitRatio(path, startRatio.current + deltaFromStart / size);
  };

  // Find the inner perpendicular split (if any) — its divider's endpoint on
  // OUR divider is where we drop a cross/T handle that drags both axes.
  // When BOTH children are perpendicular splits, their ratios are linked
  // (see useEditorGroups.setSplitRatio), so it doesn't matter which we
  // address — updating one updates the other. We prefer A by convention.
  const aPerp = split.a.type === "split" && split.a.dir !== split.dir;
  const bPerp = split.b.type === "split" && split.b.dir !== split.dir;
  const innerSeg: "a" | "b" | null = aPerp ? "a" : bPerp ? "b" : null;
  const innerSplit =
    innerSeg === "a" && split.a.type === "split"
      ? split.a
      : innerSeg === "b" && split.b.type === "split"
        ? split.b
        : null;
  // Inner snapshots — separate from outer because the cross is one drag
  // that updates two ratios; each needs its own anchor.
  const innerStartRatio = useRef(innerSplit?.ratio ?? 0.5);
  const onCrossStart = () => {
    onResizeStart();
    innerStartRatio.current = innerSplit?.ratio ?? 0.5;
  };
  const onCross = (dx: number, dy: number) => {
    if (!innerSplit || !innerSeg) return;
    const outerDelta = row ? dx : dy;
    const innerDelta = row ? dy : dx;
    const outerSize = row ? startSize.current.w : startSize.current.h;
    const innerSize = row ? startSize.current.h : startSize.current.w;
    if (outerSize > 0) {
      groups.setSplitRatio(path, startRatio.current + outerDelta / outerSize);
    }
    if (innerSize > 0) {
      groups.setSplitRatio(
        [...path, innerSeg],
        innerStartRatio.current + innerDelta / innerSize,
      );
    }
  };

  return (
    <div
      ref={containerRef}
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        position: "relative", // anchor the absolute CrossHandle
        display: "flex",
        flexDirection: row ? "row" : "column",
      }}
    >
      <div
        style={{
          flexGrow: split.ratio,
          flexShrink: 1,
          flexBasis: 0,
          minWidth: 0,
          minHeight: 0,
          display: "flex",
        }}
      >
        <GroupTreeView
          node={split.a}
          groups={groups}
          investigationId={investigationId}
          files={files}
          path={[...path, "a"]}
        />
      </div>
      <ResizeDivider
        orientation={row ? "vertical" : "horizontal"}
        ariaLabel={row ? "resize split column" : "resize split row"}
        onResizeStart={onResizeStart}
        onResize={onResize}
      />
      <div
        style={{
          flexGrow: 1 - split.ratio,
          flexShrink: 1,
          flexBasis: 0,
          minWidth: 0,
          minHeight: 0,
          display: "flex",
        }}
      >
        <GroupTreeView
          node={split.b}
          groups={groups}
          investigationId={investigationId}
          files={files}
          path={[...path, "b"]}
        />
      </div>
      {innerSplit && (
        <CrossHandle
          leftPct={row ? split.ratio : innerSplit.ratio}
          topPct={row ? innerSplit.ratio : split.ratio}
          onResizeStart={onCrossStart}
          onResize={onCross}
        />
      )}
    </div>
  );
}

/** One editor group: its own tab strip + breadcrumb + the active file,
 * with VSCode-style edge drop zones for incoming tab/file drags. */
function GroupPane({
  group,
  groups,
  files,
}: {
  group: EditorGroup;
  groups: Groups;
  files: FileInfo[];
}) {
  const [edge, setEdge] = useState<Edge | null>(null);
  const activePath = group.activePath;
  const { isEditing } = useEditMode();
  // Raw editors sit edge-to-edge (pad 0); rendered previews get breathing room.
  const rawEditor =
    activePath != null && isRawEditorView(pickRenderer(activePath), isEditing(activePath));

  const hasPayload = (e: React.DragEvent) =>
    e.dataTransfer.types.includes("application/x-rca-tab") ||
    e.dataTransfer.types.includes("application/x-rca-file");

  const handleDrop = (e: React.DragEvent, where: Edge) => {
    const tabRaw = e.dataTransfer.getData("application/x-rca-tab");
    if (tabRaw) {
      try {
        const { groupId, path } = JSON.parse(tabRaw) as { groupId: string; path: string };
        groups.dropTabOnGroup(groupId, group.id, where, path, e.ctrlKey || e.metaKey);
        return;
      } catch {
        /* ignore */
      }
    }
    const fileRaw = e.dataTransfer.getData("application/x-rca-file");
    if (fileRaw) {
      try {
        const { path } = JSON.parse(fileRaw) as { path: string };
        // file from the sidebar — no source group to clear (copy=true)
        groups.dropTabOnGroup("", group.id, where, path, true);
      } catch {
        /* ignore */
      }
    }
  };

  return (
    <div
      onMouseDown={() => groups.focusGroup(group.id)}
      onDragOver={(e) => {
        if (!hasPayload(e)) return;
        e.preventDefault();
        const r = e.currentTarget.getBoundingClientRect();
        setEdge(edgeForPoint(e.clientX, e.clientY, r));
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setEdge(null);
      }}
      onDrop={(e) => {
        const where = edge ?? "center";
        setEdge(null);
        e.preventDefault();
        handleDrop(e, where);
      }}
      style={{
        position: "relative",
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <GroupTabStrip group={group} groups={groups} />
      <Breadcrumb
        activeTab={activePath}
        files={files}
        onOpen={(p) => groups.openInGroup(group.id, p, { preview: false })}
      />
      <div
        className="scrollable"
        style={{ flex: 1, overflow: "auto", padding: rawEditor ? 0 : 20 }}
      >
        {activePath ? (
          <FileView path={activePath} />
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--text-paper-d)",
            }}
          >
            Open a file from the sidebar to view it here.
          </div>
        )}
      </div>
      {edge && <DropZoneOverlay edge={edge} />}
    </div>
  );
}

/** Translucent highlight showing where a dropped tab/file will land. */
function DropZoneOverlay({ edge }: { edge: Edge }) {
  const base: React.CSSProperties = {
    position: "absolute",
    background: "var(--accent-soft)",
    border: "2px solid var(--accent)",
    pointerEvents: "none",
    zIndex: 5,
    opacity: 0.7,
  };
  const region: React.CSSProperties =
    edge === "center"
      ? { inset: 8 }
      : edge === "left"
        ? { top: 0, bottom: 0, left: 0, width: "50%" }
        : edge === "right"
          ? { top: 0, bottom: 0, right: 0, width: "50%" }
          : edge === "top"
            ? { left: 0, right: 0, top: 0, height: "50%" }
            : { left: 0, right: 0, bottom: 0, height: "50%" };
  return <div style={{ ...base, ...region }} />;
}

function TabContextMenu({
  path,
  x,
  y,
  pinned,
  onClose,
  onCloseTab,
  onTogglePin,
  onCloseOthers,
  onCloseToRight,
  onCloseAll,
  onSplit,
}: {
  path: string;
  x: number;
  y: number;
  pinned: boolean;
  onClose: () => void;
  onCloseTab: (p: string) => void;
  onTogglePin: (p: string) => void;
  onCloseOthers: (keep: string) => void;
  onCloseToRight: (from: string) => void;
  onCloseAll: () => void;
  onSplit: (dir: SplitDir, path: string) => void;
}) {
  const item = (label: string, fn: () => void) => (
    <button
      type="button"
      onClick={() => {
        fn();
        onClose();
      }}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "5px 14px",
        fontSize: pxToRem(12),
        color: "var(--text-paper)",
        background: "transparent",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-2)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {label}
    </button>
  );
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 80 }} />
      <div
        style={{
          position: "fixed",
          top: y,
          left: x,
          zIndex: 81,
          minWidth: 180,
          background: "var(--white)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-card)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.16)",
          padding: "4px 0",
        }}
      >
        {item("Split right", () => onSplit("right", path))}
        {item("Split left", () => onSplit("left", path))}
        {item("Split up", () => onSplit("up", path))}
        {item("Split down", () => onSplit("down", path))}
        <div style={{ height: 1, background: "var(--paper-3)", margin: "4px 0" }} />
        {item(pinned ? "Unpin" : "Pin", () => onTogglePin(path))}
        {item("Copy path", () => void navigator.clipboard?.writeText(path))}
        <div style={{ height: 1, background: "var(--paper-3)", margin: "4px 0" }} />
        {item("Close", () => onCloseTab(path))}
        {item("Close others", () => onCloseOthers(path))}
        {item("Close to the right", () => onCloseToRight(path))}
        {item("Close all", () => onCloseAll())}
      </div>
    </>
  );
}

const crumbBar: React.CSSProperties = {
  height: 28,
  borderBottom: "1px solid var(--paper-3)",
  display: "flex",
  alignItems: "center",
  padding: "0 12px",
  background: "var(--white)",
  fontSize: pxToRem(12),
  color: "var(--text-paper-d)",
  gap: 2,
};

/** VSCode-style breadcrumb: each segment is clickable and drops down the
 * sibling entries at its level; selecting a file opens it, a folder drills
 * in (#6/#7). */
export function Breadcrumb({
  activeTab,
  files,
  onOpen,
}: {
  activeTab: string | null;
  files: FileInfo[];
  onOpen: (path: string) => void;
}) {
  if (!activeTab) {
    return (
      <div style={crumbBar}>
        <span style={{ color: "var(--text-paper-d2)" }}>No file open</span>
      </div>
    );
  }
  const folders = breadcrumbSegments(activeTab); // ancestor folder names
  const paths = files.map((f) => f.path);
  return (
    <div style={crumbBar}>
      {folders.map((name, i) => (
        <span key={`${name}-${i}`} style={{ display: "inline-flex", alignItems: "center" }}>
          <CrumbSegment
            label={name}
            siblingsDir={folders.slice(0, i).join("/")}
            paths={paths}
            onOpen={onOpen}
          />
          <Icon name="chev_r" size={10} color="var(--text-paper-d2)" />
        </span>
      ))}
      <CrumbSegment
        label={basename(activeTab)}
        siblingsDir={folders.join("/")}
        paths={paths}
        onOpen={onOpen}
        active
      />
    </div>
  );
}

/** One breadcrumb crumb — a button that pops a sibling browser. */
function CrumbSegment({
  label,
  siblingsDir,
  paths,
  onOpen,
  active,
}: {
  label: string;
  siblingsDir: string;
  paths: string[];
  onOpen: (path: string) => void;
  active?: boolean;
}) {
  return (
    <Popover
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={onClick}
          style={{
            padding: "2px 6px",
            borderRadius: 4,
            background: open ? "var(--paper-2)" : "transparent",
            color: active ? "var(--text-paper)" : "var(--text-paper-d)",
            fontWeight: active ? 600 : 400,
            fontSize: pxToRem(12),
          }}
        >
          {label}
        </button>
      )}
    >
      {(close) => (
        <DirBrowser startDir={siblingsDir} paths={paths} onOpen={onOpen} close={close} />
      )}
    </Popover>
  );
}

/** Drill-down listing inside a breadcrumb dropdown. Files open + close;
 * folders re-root the listing to their children. */
function DirBrowser({
  startDir,
  paths,
  onOpen,
  close,
}: {
  startDir: string;
  paths: string[];
  onOpen: (path: string) => void;
  close: () => void;
}) {
  const [dir, setDir] = useState(startDir);
  const entries = useMemo(() => dirChildren(paths, dir), [paths, dir]);
  return (
    <div style={{ minWidth: 220, maxHeight: 320, overflowY: "auto" }}>
      {dir && (
        <button
          type="button"
          onClick={() => setDir(dir.split("/").slice(0, -1).join("/"))}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            width: "100%",
            padding: "5px 10px",
            fontSize: pxToRem(12),
            color: "var(--text-paper-d2)",
            background: "transparent",
          }}
        >
          <Icon name="chev_l" size={12} /> /{dir}
        </button>
      )}
      {entries.length === 0 && (
        <div style={{ padding: "6px 10px", fontSize: pxToRem(12), color: "var(--text-paper-d2)" }}>
          Empty
        </div>
      )}
      {entries.map((e) => (
        <button
          key={e.path}
          type="button"
          onClick={() => {
            if (e.isDir) {
              setDir(e.path);
            } else {
              onOpen(e.path);
              close();
            }
          }}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            width: "100%",
            padding: "5px 10px",
            fontSize: pxToRem(12),
            textAlign: "left",
            background: "transparent",
            color: "var(--text-paper)",
          }}
          onMouseEnter={(ev) => {
            (ev.currentTarget as HTMLButtonElement).style.background = "var(--paper-2)";
          }}
          onMouseLeave={(ev) => {
            (ev.currentTarget as HTMLButtonElement).style.background = "transparent";
          }}
        >
          <Icon name={e.isDir ? "folder" : "file"} size={13} color="var(--text-paper-d)" />
          <span style={{ flex: 1 }}>{e.name}</span>
          {e.isDir && <Icon name="chev_r" size={11} color="var(--text-paper-d2)" />}
        </button>
      ))}
    </div>
  );
}

/** A single editor group's tab strip — drives only its own group. Tabs
 * carry their group id so a drag onto another group moves/copies. */
function GroupTabStrip({ group, groups }: { group: EditorGroup; groups: Groups }) {
  const active = group.activePath;
  // In a split, only the focused group's active tab gets full emphasis;
  // other panes' active tabs read dimmer so the focus is unambiguous.
  const groupFocused = !groups.isSplit || groups.activeGroupId === group.id;
  const activeKind = active != null ? pickRenderer(active) : null;
  const activeIsNotebook = activeKind === "notebook";
  // Markdown and images have a preview ↔ edit duality (the others are
  // always editable text or always rendered).
  const activeHasEditToggle = activeKind != null && hasEditToggle(activeKind);
  const editMode = useEditMode();
  const requestClose = useContext(RequestCloseContext);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [menu, setMenu] = useState<{ path: string; x: number; y: number } | null>(null);
  const gid = group.id;

  return (
    <div
      style={{
        height: 38,
        background: "var(--paper)",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "stretch",
        flexShrink: 0,
      }}
    >
      <div className="scrollable" style={{ display: "flex", flex: 1, minWidth: 0, overflowX: "auto" }}>
        {group.tabs.map((t: EditorTab, i) => {
          const isActive = t.path === active;
          return (
            <div
              key={t.path}
              role="tab"
              aria-selected={isActive}
              draggable
              onClick={() => groups.selectTab(gid, t.path)}
              onMouseDown={(e) => {
                if (e.button === 1) {
                  // middle-click closes the tab (VSCode)
                  e.preventDefault();
                  requestClose(gid, t.path);
                }
              }}
              onDoubleClick={() => groups.togglePin(gid, t.path)}
              onContextMenu={(e) => {
                e.preventDefault();
                setMenu({ path: t.path, x: e.clientX, y: e.clientY });
              }}
              onDragStart={(e) => {
                setDragFrom(i);
                e.dataTransfer.setData(
                  "application/x-rca-tab",
                  JSON.stringify({ groupId: gid, path: t.path }),
                );
                e.dataTransfer.effectAllowed = "copyMove";
              }}
              onDragOver={(e) => {
                // reorder only when the drag is a tab from THIS group
                if (e.dataTransfer.types.includes("application/x-rca-tab")) e.preventDefault();
              }}
              onDrop={(e) => {
                e.stopPropagation();
                const raw = e.dataTransfer.getData("application/x-rca-tab");
                let sameGroup = false;
                try {
                  sameGroup = raw ? (JSON.parse(raw) as { groupId: string }).groupId === gid : false;
                } catch {
                  /* ignore */
                }
                if (sameGroup && dragFrom != null) groups.reorderTab(gid, dragFrom, i);
                setDragFrom(null);
              }}
              title={
                t.pinned
                  ? "Pinned · double-click to unpin"
                  : "Drag to reorder · drag to another pane to move (Ctrl/⌘ to copy) · drag to an edge to split"
              }
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "0 12px",
                borderTop: isActive
                  ? `2px solid ${groupFocused ? "var(--accent)" : "var(--paper-3)"}`
                  : "2px solid transparent",
                background: isActive
                  ? groupFocused
                    ? "var(--white)"
                    : "var(--paper-2)"
                  : "transparent",
                borderRight: "1px solid var(--paper-3)",
                color: isActive && groupFocused ? "var(--text-paper)" : "var(--text-paper-d)",
                cursor: "pointer",
                whiteSpace: "nowrap",
                opacity: dragFrom === i ? 0.4 : 1,
                fontStyle: t.preview ? "italic" : "normal",
              }}
            >
              <Icon name={t.pinned ? "pin" : "file"} size={12} />
              <span style={{ fontSize: pxToRem(12) }}>{basename(t.path)}</span>
              <TabClose
                path={t.path}
                onClose={() => requestClose(gid, t.path)}
              />
            </div>
          );
        })}
      </div>
      {menu && (
        <TabContextMenu
          path={menu.path}
          x={menu.x}
          y={menu.y}
          pinned={group.tabs.find((t) => t.path === menu.path)?.pinned ?? false}
          onClose={() => setMenu(null)}
          onCloseTab={(p) => requestClose(gid, p)}
          onTogglePin={(p) => groups.togglePin(gid, p)}
          onCloseOthers={(p) => groups.closeOthers(gid, p)}
          onCloseToRight={(p) => groups.closeToRight(gid, p)}
          onCloseAll={() => groups.closeGroupTabs(gid)}
          onSplit={(dir, p) => groups.splitGroup(gid, dir === "up" ? "top" : dir === "down" ? "bottom" : dir, p)}
        />
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "0 8px" }}>
        {activeHasEditToggle && active && (
          <button
            type="button"
            title={editMode.isEditing(active) ? "Preview" : "Edit"}
            onClick={() => editMode.toggle(active)}
            style={{
              ...iconBtn,
              padding: "0 8px",
              width: "auto",
              fontSize: pxToRem(12),
              display: "inline-flex",
              gap: 4,
              color: editMode.isEditing(active) ? "var(--accent)" : "var(--text-paper-d)",
              background: editMode.isEditing(active) ? "var(--accent-soft)" : "transparent",
            }}
          >
            <Icon
              name="eye"
              size={12}
              color={editMode.isEditing(active) ? "var(--accent)" : "var(--text-paper-d)"}
            />
            {editMode.isEditing(active) ? "Preview" : "Edit"}
          </button>
        )}
        <button
          type="button"
          title="Split right"
          onClick={() => groups.splitGroup(gid, "right", active)}
          style={{ ...iconBtn, color: "var(--text-paper-d)" }}
        >
          <Icon name="split" size={14} />
        </button>
        {/* Run-all only exists for notebooks */}
        {activeIsNotebook && active && (
          <button
            type="button"
            title="Run all cells"
            onClick={() => emitRunAll(active)}
            style={{
              ...iconBtn,
              padding: "0 8px",
              width: "auto",
              color: "var(--accent)",
              fontSize: pxToRem(12),
              display: "inline-flex",
              gap: 4,
            }}
          >
            <Icon name="play" size={12} color="var(--accent)" /> Run all
          </button>
        )}
      </div>
    </div>
  );
}

/** Tab close affordance: a dirty file shows a ● dot that becomes the close
 * ✕ on hover (VSCode). Reads dirty state without forcing the file to load. */
function TabClose({ path, onClose }: { path: string; onClose: () => void }) {
  const dirty = useIsDirty(path);
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-label={dirty ? `${basename(path)} (unsaved) — close` : `close ${basename(path)}`}
      style={{
        width: 16,
        height: 16,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-paper-d2)",
        borderRadius: "var(--radius-chip)",
      }}
    >
      {dirty && !hover ? (
        <span aria-hidden style={{ fontSize: pxToRem(12), lineHeight: 1, color: "var(--text-paper-d)" }}>
          ●
        </span>
      ) : (
        <Icon name="x" size={10} />
      )}
    </button>
  );
}

function BottomPanel({
  tab,
  onTab,
  investigationId,
  showTerminal,
  height,
  open,
  onToggle,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
  onTab: (t: "problems" | "output" | "terminal" | "agent_log" | "run_history") => void;
  investigationId: string;
  /** The terminal tab needs a sandbox; hidden when the App turns it off
   * (`function.terminal`). The backend already hard-errors incoherent toggles. */
  showTerminal: boolean;
  height: number;
  open: boolean;
  onToggle: () => void;
}) {
  const { log } = useAgent();
  const bodyScrollRef = useStickToBottom<HTMLDivElement>(log);
  const tabs = [
    { key: "problems" as const, label: "Problems" },
    { key: "output" as const, label: "Output" },
    ...(showTerminal ? [{ key: "terminal" as const, label: "Terminal" }] : []),
    { key: "agent_log" as const, label: "Agent log" },
    { key: "run_history" as const, label: "Run history" },
  ];

  return (
    <div
      style={{
        height: open ? height : 32,
        flexShrink: 0,
        borderTop: "1px solid var(--paper-3)",
        background: "var(--white)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          height: 32,
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "0 14px",
          borderBottom: "1px solid var(--paper-3)",
        }}
      >
        {tabs.map((t) => {
          const active = t.key === tab;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onTab(t.key)}
              style={{
                padding: "0 10px",
                height: 32,
                borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                color: active ? "var(--text-paper)" : "var(--text-paper-d)",
                fontSize: pxToRem(12),
                fontWeight: active ? 600 : 500,
                marginBottom: -1,
              }}
            >
              {t.label}
            </button>
          );
        })}
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onToggle}
          title={`${open ? "Collapse" : "Expand"} panel (${modCombo("J")})`}
          aria-label="toggle bottom panel"
          style={{ color: "var(--text-paper-d)", padding: 4 }}
        >
          <Icon name={open ? "chev_d" : "chev_r"} size={14} />
        </button>
      </div>
      {open &&
        (tab === "terminal" ? (
          <div
            style={{
              flex: 1,
              minHeight: 0,
              padding: "8px 14px",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <TerminalPane investigationId={investigationId} />
          </div>
        ) : (
          <div
            ref={bodyScrollRef}
            className="scrollable"
            style={{
              flex: 1,
              minHeight: 0,
              overflow: "auto",
              padding: "8px 14px",
              fontFamily: "var(--font-mono)",
              fontSize: pxToRem(12),
              color: "var(--text-paper)",
            }}
          >
            <PanelBody tab={tab} />
          </div>
        ))}
    </div>
  );
}

function PanelBody({
  tab,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
}) {
  const { log } = useAgent();

  if (tab === "problems") {
    const banners = log.entries.filter((e) => e.kind === "banner");
    const toolErrors = log.entries.filter(
      (e) => e.kind === "tool_call" && e.call.parseError,
    );
    if (!log.error && banners.length === 0 && toolErrors.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No problems detected.</div>;
    }
    return (
      <>
        {log.error && <LogLine ts="now" kind="warn" text={`stream error: ${log.error}`} />}
        {banners.map((b, i) =>
          b.kind === "banner" ? <LogLine key={`b-${i}`} ts="—" kind="warn" text={b.text} /> : null,
        )}
        {toolErrors.map((e, i) =>
          e.kind === "tool_call" && e.call.parseError ? (
            <LogLine
              key={`e-${i}`}
              ts="—"
              kind="warn"
              text={`${e.call.name}: parse-error → ${e.call.parseError}`}
            />
          ) : null,
        )}
      </>
    );
  }

  if (tab === "output") {
    const calls = log.entries.filter((e) => e.kind === "tool_call");
    if (calls.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No tool output yet.</div>;
    }
    return (
      <>
        {calls.map((e, i) =>
          e.kind === "tool_call" && callBody(e.call) !== undefined ? (
            <div key={i} style={{ marginBottom: 4 }}>
              <div style={{ color: "var(--accent)" }}>
                → {e.call.name}
                {e.call.status === "running" && (
                  <span style={{ color: "var(--text-paper-d)" }}> (running…)</span>
                )}
              </div>
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  color: "var(--text-paper)",
                  fontSize: pxToRem(12),
                }}
              >
                {callBody(e.call)}
              </pre>
            </div>
          ) : null,
        )}
      </>
    );
  }

  // 'terminal' is handled at the BottomPanel level (it needs to claim
  // the full panel height) — see TerminalPane.

  if (tab === "run_history") {
    const calls = log.entries.filter((e) => e.kind === "tool_call");
    if (calls.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No tool runs yet.</div>;
    }
    // Full run detail: command + args + the complete output, not just a name.
    return (
      <>
        {calls.map((e, i) =>
          e.kind === "tool_call" ? (
            <details key={i} style={{ marginBottom: 8 }}>
              <summary
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "baseline",
                  flexWrap: "wrap",
                  cursor: "pointer",
                  listStyle: "none",
                }}
              >
                <span style={{ color: e.call.status === "running" ? "var(--accent)" : "var(--text-paper-d2)" }}>
                  {exitGlyph(e.call)}
                </span>
                <span style={{ fontWeight: 600 }}>{e.call.name}</span>
                <span style={{ color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)", fontSize: pxToRem(12) }}>
                  {argsLine(e.call.args)}
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11), fontFamily: "var(--font-mono)" }}>
                  {runMeta(e.call)}
                </span>
              </summary>
              {e.call.parseError && (
                <div style={{ color: "var(--warn)", fontSize: pxToRem(12), marginLeft: 16 }}>
                  parse-error → {e.call.parseError}
                </div>
              )}
              {callBody(e.call) && <pre style={logPre}>{callBody(e.call)}</pre>}
            </details>
          ) : null,
        )}
      </>
    );
  }

  // agent_log — the full event transcript (more than the chat box: every
  // message in full, every tool start/end with args + complete output).
  if (log.entries.length === 0 && !log.streaming) {
    return <div style={{ color: "var(--text-paper-d)" }}>Idle.</div>;
  }
  return (
    <>
      {log.metrics && (
        <div
          style={{
            display: "flex",
            gap: 8,
            padding: "4px 8px",
            marginBottom: 6,
            borderRadius: 4,
            background: "var(--paper-2)",
            color: log.metrics.phase === "final" ? "var(--text-paper-d)" : "var(--accent)",
            fontFamily: "var(--font-mono)",
            fontSize: pxToRem(12),
          }}
        >
          {log.streaming && log.metrics.phase !== "final" ? "▸" : "✓"} {formatMetrics(log.metrics)}
        </div>
      )}
      {log.entries.map((e, i) => {
        // The entry currently being produced (last, while streaming) renders
        // live + expanded so it auto-scrolls; finished entries fold like the
        // chat does. Switching <div>↔<details> on the same key remounts, so a
        // just-finished entry collapses cleanly.
        const active = log.streaming && i === log.entries.length - 1;
        if (e.kind === "banner") {
          return <LogLine key={i} ts={fmtTs(e.at)} kind="warn" text={e.text} />;
        }
        if (e.kind === "tool_call") {
          const running = e.call.status === "running";
          const head = (
            <>
              <span style={logTs}>{fmtTs(e.call.startedAt)}</span>
              <span
                style={{ width: 56, flexShrink: 0, color: running ? "var(--accent)" : "var(--text-paper-d)" }}
              >
                {running ? "tool ▸" : "tool ✓"}
              </span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(12) }}>
                {e.call.name}({argsLine(e.call.args)})
              </span>
            </>
          );
          const body = callBody(e.call);
          if (active && running) {
            return (
              <div key={i} style={{ marginBottom: 6 }}>
                <div style={{ display: "flex", gap: 8 }}>{head}</div>
                {body && <pre style={logPre}>{body}</pre>}
              </div>
            );
          }
          return (
            <details key={i} style={{ marginBottom: 6 }}>
              <summary style={logSummary}>{head}</summary>
              {body && <pre style={logPre}>{body}</pre>}
            </details>
          );
        }
        if (e.kind === "mention") {
          return (
            <LogLine
              key={i}
              ts={fmtTs(e.at)}
              kind="warn"
              text={`summoned ${e.users.join(", ")}${e.note ? ` — ${e.note}` : ""}`}
            />
          );
        }
        if (e.kind === "phase") {
          // #100: a workflow phase boundary in the log view.
          return <LogLine key={i} ts={fmtTs(e.at)} kind="muted" text={`— ${e.phase} —`} />;
        }
        if (e.kind === "step") {
          // #100: a workflow step's live line (deterministic-phase movement).
          const glyph =
            e.step.status === "passed"
              ? "✓"
              : e.step.status === "failed"
                ? "✗"
                : e.step.status === "skipped"
                  ? "⤳"
                  : e.step.status === "retrying"
                    ? "↻"
                    : "▸";
          const detail = `${e.step.key ? ` · ${e.step.key}` : ""}${e.step.reason ? ` — ${e.step.reason}` : ""}`;
          return (
            <LogLine
              key={i}
              ts={fmtTs(e.at)}
              kind={e.step.status === "failed" ? "warn" : "muted"}
              text={`${glyph} ${e.step.name}${detail}`}
            />
          );
        }
        const head = (
          <>
            <span style={logTs}>{fmtTs(e.at)}</span>
            <span style={{ color: e.message.role === "user" ? "var(--info)" : "var(--text-paper-d)" }}>
              {e.message.role}: {firstLine(e.message.content || e.message.reasoning || "")}
            </span>
          </>
        );
        const body = (
          <>
            {e.message.reasoning && (
              <pre style={{ ...logPre, opacity: 0.7 }}>{e.message.reasoning}</pre>
            )}
            {e.message.content !== "" && <pre style={logPre}>{e.message.content}</pre>}
          </>
        );
        if (active && e.message.role === "assistant") {
          return (
            <div key={i} style={{ marginBottom: 6 }}>
              <div style={{ display: "flex", gap: 8 }}>{head}</div>
              {body}
            </div>
          );
        }
        return (
          <details key={i} style={{ marginBottom: 6 }}>
            <summary style={logSummary}>{head}</summary>
            {body}
          </details>
        );
      })}
      {log.streaming && <LogLine ts="" kind="accent" text="…streaming" />}
    </>
  );
}

/** First line of a message, truncated — the folded-log summary. */
function firstLine(s: string): string {
  const line = s.split("\n", 1)[0] ?? "";
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

/** Compact `k=v` arg summary for a tool call. */
function argsLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
}

type RunCall = {
  status: "running" | "done";
  output?: string;
  liveOutput?: string;
  startedAt?: number;
  endedAt?: number;
};

/** The text to show for a tool call: its live stdout while running, the
 * final formatted output once done. */
function callBody(call: RunCall): string | undefined {
  return call.status === "done" ? call.output : (call.liveOutput ?? call.output);
}

/** ✓ / ✗N exit glyph: parses `exit_code=N` that exec prepends to output. */
function exitGlyph(call: RunCall): string {
  if (call.status === "running") return "•";
  const m = call.output?.match(/exit_code=(\d+)/);
  if (m) return m[1] === "0" ? "✓ 0" : `✗ ${m[1]}`;
  return "✓";
}

/** Per-run meta line: duration + start→end clock times. */
function runMeta(call: RunCall): string {
  const parts: string[] = [];
  if (call.startedAt != null && call.endedAt != null) {
    parts.push(`${((call.endedAt - call.startedAt) / 1000).toFixed(2)}s`);
  } else if (call.status === "running") {
    parts.push("running…");
  }
  if (call.startedAt != null) {
    const fmt = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
    const t = fmt(call.startedAt);
    parts.push(call.endedAt != null ? `${t}→${fmt(call.endedAt)}` : t);
  }
  return parts.join("  ");
}

/** Wall-clock time for a log entry (blank when unknown, e.g. loaded history).
 * 24-hour HH:MM:SS — compact and locale-stable, so it never wraps the column
 * the way a 12-hour "3:53:42 PM" does. */
function fmtTs(at?: number): string {
  return at != null ? new Date(at).toLocaleTimeString([], { hour12: false }) : "";
}

const logTs: React.CSSProperties = {
  width: 64,
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "var(--text-paper-d2)",
};

const logSummary: React.CSSProperties = {
  display: "flex",
  gap: 8,
  cursor: "pointer",
  listStyle: "none",
  alignItems: "baseline",
};

const logPre: React.CSSProperties = {
  margin: "2px 0 0 16px",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontFamily: "var(--font-mono)",
  fontSize: pxToRem(12),
  color: "var(--text-paper)",
  background: "var(--paper-2)",
  padding: "6px 8px",
  borderRadius: 4,
};

function LogLine({
  ts,
  kind,
  text,
}: {
  ts: string;
  kind: "info" | "accent" | "warn" | "muted";
  text: string;
}) {
  const color =
    kind === "info"
      ? "var(--info)"
      : kind === "accent"
        ? "var(--accent)"
        : kind === "warn"
          ? "var(--warn)"
          : "var(--text-paper-d)";
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <span style={{ width: 64, color: "var(--text-paper-d2)" }}>{ts}</span>
      <span style={{ width: 60, color }}>{kind}</span>
      <span>{text}</span>
    </div>
  );
}

export function StatusBar({
  activeTab,
  investigationId,
}: {
  activeTab: string | null;
  investigationId: string;
}) {
  const kind = activeTab ? pickRenderer(activeTab) : null;
  const isNotebook = kind === "notebook" && activeTab != null;
  return (
    <div
      style={{
        height: 28,
        flexShrink: 0,
        background: "var(--ink)",
        color: "var(--text-dark)",
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 12px",
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(11),
      }}
    >
      <span style={{ flex: 1 }} />
      <span>{kind ? `lang: ${kind}` : ""}</span>
      {isNotebook && (
        <KernelStatusPill
          investigationId={investigationId}
          notebookPath={activeTab}
        />
      )}
      <span>UTF-8</span>
      <span>default-user</span>
    </div>
  );
}

function KernelStatusPill({
  investigationId,
  notebookPath,
}: {
  investigationId: string;
  notebookPath: string;
}) {
  const slug = useWorkspaceSlug();
  const [state, setState] = useState<"idle" | "restarting" | "error">("idle");
  const restart = async () => {
    if (state === "restarting") return;
    setState("restarting");
    try {
      await api.restartKernel({ slug, investigationId, notebookPath });
      setState("idle");
    } catch (e) {
      console.error("restartKernel failed", e);
      setState("error");
    }
  };
  const label =
    state === "restarting"
      ? "kernel py3.12 restarting…"
      : state === "error"
        ? "kernel py3.12 error"
        : "kernel py3.12 idle";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{label}</span>
      <button
        type="button"
        onClick={() => void restart()}
        disabled={state === "restarting"}
        title="Restart kernel"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 3,
          padding: "0 6px",
          height: 18,
          borderRadius: "var(--radius-chip)",
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.2)",
          color: "var(--text-dark)",
          fontFamily: "var(--font-mono)",
          fontSize: pxToRem(10),
          cursor: state === "restarting" ? "wait" : "pointer",
        }}
      >
        <Icon name="refresh" size={10} color="var(--text-dark)" />
        Restart
      </button>
    </span>
  );
}
