/**
 * Shared per-path file buffers — the model layer behind every renderer.
 *
 * One buffer per file path, shared by all panes/renderers viewing it, so
 * the same file opened in a split shows edits live on both sides (VSCode's
 * one-TextModel-per-file behaviour). Holds the editable text, tracks dirty
 * state, and debounces autosave back to the FileStore.
 *
 * IO is injected (readFile / writeFile) so the store is unit-testable
 * without a live backend.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

import { api } from "../api";
import type { FileContent } from "../api/types";

export type SaveStatus = "clean" | "dirty" | "saving" | "saved" | "error";

export type BufferEntry = {
  status: "loading" | "ready" | "error";
  kind: "text" | "binary" | null;
  text: string; // editable text body (empty for binary / not-yet-loaded)
  size: number;
  error: string | null;
  save: SaveStatus;
};

const LOADING: BufferEntry = {
  status: "loading",
  kind: null,
  text: "",
  size: 0,
  error: null,
  save: "clean",
};

type IO = {
  readFile: (id: string, path: string) => Promise<FileContent>;
  writeFile: (id: string, path: string, body: string) => Promise<void>;
};

export class FileBufferStore {
  private entries = new Map<string, BufferEntry>();
  private listeners = new Map<string, Set<() => void>>();
  private inflight = new Set<string>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    public readonly investigationId: string,
    private readonly io: IO = api,
    private readonly debounceMs = 500,
  ) {}

  subscribe(path: string, cb: () => void): () => void {
    let set = this.listeners.get(path);
    if (!set) {
      set = new Set();
      this.listeners.set(path, set);
    }
    set.add(cb);
    return () => set.delete(cb);
  }

  snapshot(path: string): BufferEntry {
    return this.entries.get(path) ?? LOADING;
  }

  private set(path: string, patch: Partial<BufferEntry>): void {
    const prev = this.entries.get(path) ?? LOADING;
    this.entries.set(path, { ...prev, ...patch });
    this.emit(path);
  }

  private emit(path: string): void {
    this.listeners.get(path)?.forEach((cb) => cb());
  }

  ensureLoaded(path: string): void {
    if (this.entries.has(path) || this.inflight.has(path)) return;
    this.inflight.add(path);
    this.entries.set(path, LOADING);
    this.io
      .readFile(this.investigationId, path)
      .then((content) => {
        this.inflight.delete(path);
        this.set(path, {
          status: "ready",
          kind: content.kind,
          text: content.kind === "text" ? content.text : "",
          size: content.size,
          error: null,
          save: "clean",
        });
      })
      .catch((e: unknown) => {
        this.inflight.delete(path);
        this.set(path, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      });
  }

  reload(path: string): void {
    this.entries.delete(path);
    this.inflight.delete(path);
    this.ensureLoaded(path);
  }

  /** Update the in-memory text (live across all panes) + schedule save. */
  setText(path: string, text: string): void {
    this.set(path, { status: "ready", kind: "text", text, save: "dirty" });
    const existing = this.timers.get(path);
    if (existing) clearTimeout(existing);
    this.timers.set(
      path,
      setTimeout(() => void this.flush(path), this.debounceMs),
    );
  }

  /** Push the current text to the backend now (used on cell_done, etc.). */
  async flush(path: string): Promise<void> {
    const entry = this.entries.get(path);
    if (!entry || entry.save === "clean" || entry.save === "saving") return;
    this.set(path, { save: "saving" });
    try {
      await this.io.writeFile(this.investigationId, path, entry.text);
      // only flip to saved if no further edits arrived while saving
      const after = this.entries.get(path);
      if (after && after.save === "saving") this.set(path, { save: "saved" });
    } catch {
      this.set(path, { save: "error" });
    }
  }
}

const FileBufferContext = createContext<FileBufferStore | null>(null);

export function FileBufferProvider({
  investigationId,
  children,
  store,
}: {
  investigationId: string;
  children: React.ReactNode;
  store?: FileBufferStore; // test seam
}) {
  const value = useMemo(
    () => store ?? new FileBufferStore(investigationId),
    [store, investigationId],
  );
  return (
    <FileBufferContext.Provider value={value}>{children}</FileBufferContext.Provider>
  );
}

export function useFileBufferStore(): FileBufferStore {
  const store = useContext(FileBufferContext);
  if (!store) throw new Error("useFileBuffer* must be used inside <FileBufferProvider>");
  return store;
}

export function useFileBuffer(path: string) {
  const store = useFileBufferStore();
  const subscribe = useCallback((cb: () => void) => store.subscribe(path, cb), [store, path]);
  const getSnapshot = useCallback(() => store.snapshot(path), [store, path]);
  const entry = useSyncExternalStore(subscribe, getSnapshot);
  useEffect(() => {
    store.ensureLoaded(path);
  }, [store, path]);
  return {
    entry,
    setText: useCallback((t: string) => store.setText(path, t), [store, path]),
    flush: useCallback(() => store.flush(path), [store, path]),
    reload: useCallback(() => store.reload(path), [store, path]),
  };
}
