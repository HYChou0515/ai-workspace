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

  investigations: ["investigations"] as const,
  investigation: (id: string) => ["investigation", id] as const,
  files: (id: string) => ["files", id] as const,
  dirs: (id: string) => ["dirs", id] as const,
  file: (id: string, path: string) => ["file", id, path] as const,
  activity: (id: string) => ["activity", id] as const,
  conversation: (id: string) => ["conversation", id] as const,

  agentConfigs: ["agentConfigs"] as const,
  templates: ["templates"] as const,

  kb: {
    all: ["kb"] as const,
    collections: ["kb", "collections"] as const,
    documents: (collectionId: string) =>
      ["kb", "documents", collectionId] as const,
    chats: ["kb", "chats"] as const,
    chat: (id: string) => ["kb", "chat", id] as const,
    agent: ["kb", "agent"] as const,
    doc: (id: string) => ["kb", "doc", id] as const,
  },
} as const;
