/**
 * Central query-key registry. One source of truth so a `useQuery` read and
 * the `invalidateQueries` after a mutation always agree on the key — a typo
 * here is a silent stale-cache bug, so never inline raw key arrays elsewhere.
 *
 * Keys are hierarchical: invalidating `["kb"]` clears every KB query;
 * `qk.kb.documents(id)` is a leaf under it.
 */
export const qk = {
  currentUser: ["currentUser"] as const,
  users: ["users"] as const,
  notifications: ["notifications"] as const,

  investigations: ["investigations"] as const,
  investigation: (id: string) => ["investigation", id] as const,
  files: (id: string) => ["files", id] as const,
  dirs: (id: string) => ["dirs", id] as const,
  // #245: a workspace's storage usage vs quota (the upload usage bar). Invalidate
  // after any upload — success grows `used`, a 507 leaves it at the ceiling.
  workspaceUsage: (slug: string, itemId: string) =>
    ["workspaceUsage", slug, itemId] as const,
  file: (id: string, path: string) => ["file", id, path] as const,
  activity: ["activity"] as const,
  conversation: (id: string) => ["conversation", id] as const,

  apps: ["apps"] as const,
  appManifest: (slug: string) => ["appManifest", slug] as const,
  appItems: (slug: string) => ["appItems", slug] as const,
  appItem: (slug: string, id: string) => ["appItem", slug, id] as const,

  // topic-hub §5 (#142): the parsed `collections.json` of one Hub item — the
  // collection-set picker's badge + modal read it; saving the picker invalidates
  // this AND `qk.file(id, "/collections.json")` (so an open Monaco tab refreshes).
  itemCollections: (scopeId: string) => ["itemCollections", scopeId] as const,

  // #298: a workspace's co-created skills (`.skill/`). The Skills panel reads this;
  // importing a skill invalidates it (a new `.skill/<name>/` appears).
  workspaceSkills: (slug: string, itemId: string) =>
    ["workspaceSkills", slug, itemId] as const,

  // topic-hub §3: an item's chats (free + workflow) + one chat's hydrated thread.
  itemChats: (slug: string, itemId: string) => ["itemChats", slug, itemId] as const,
  itemChat: (slug: string, itemId: string, chatId: string) =>
    ["itemChat", slug, itemId, chatId] as const,

  // #100 workflows: per-App profiles (which carry a workflow) + an item's runs.
  workflowProfiles: (slug: string) => ["workflowProfiles", slug] as const,
  workflowRuns: (slug: string, itemId: string) =>
    ["workflowRuns", slug, itemId] as const,
  workflowRun: (slug: string, itemId: string, runId: string) =>
    ["workflowRun", slug, itemId, runId] as const,
  // #283: the launch dialog's pre-flight preview for one workflow.
  workflowPreview: (slug: string, itemId: string, workflowId: string) =>
    ["workflowPreview", slug, itemId, workflowId] as const,
  health: ["health"] as const,
  monitor: ["monitor"] as const,

  sanity: {
    meta: ["sanity", "meta"] as const,
    results: (model: string) => ["sanity", "results", model] as const,
    verdicts: ["sanity", "verdicts"] as const,
    custom: ["sanity", "custom"] as const,
  },

  // #230: the platform Help page payload (Help collection id + its documents).
  help: ["help"] as const,

  kb: {
    all: ["kb"] as const,
    collections: ["kb", "collections"] as const,
    documents: (collectionId: string) =>
      ["kb", "documents", collectionId] as const,
    // Per-page key for the paged endpoint — `invalidateQueries({queryKey:
    // qk.kb.documents(id)})` still matches every page through React Query's
    // prefix-match, so callers don't need to know which pages are loaded.
    documentsPage: (collectionId: string, offset: number, limit: number) =>
      ["kb", "documents", collectionId, offset, limit] as const,
    chats: ["kb", "chats"] as const,
    chat: (id: string) => ["kb", "chat", id] as const,
    agent: ["kb", "agent"] as const,
    doc: (id: string) => ["kb", "doc", id] as const,
    docChunks: (id: string) => ["kb", "doc-chunks", id] as const,
    // Issue #50: the LLM wiki browser.
    wikiPages: (collectionId: string) => ["kb", "wiki-pages", collectionId] as const,
    wikiPage: (collectionId: string, path: string) =>
      ["kb", "wiki-page", collectionId, path] as const,
    wikiStatus: (collectionId: string) => ["kb", "wiki-status", collectionId] as const,
    // #106: a collection's context cards (the lightweight glossary).
    contextCards: (collectionId: string) => ["kb", "context-cards", collectionId] as const,
    // #175: a 自動 context card generation run (status + proposals, polled).
    cardGen: (jobId: string) => ["kb", "card-gen", jobId] as const,
    // #325: browser-runnable upload-check descriptors (platform-wide, rarely
    // changes — fetched once and reused to pre-block encrypted uploads).
    uploadChecks: ["kb", "upload-checks"] as const,
  },
} as const;
