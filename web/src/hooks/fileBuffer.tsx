/**
 * Shared per-path file buffers — the model layer behind every renderer.
 *
 * One buffer per file path, shared by all panes/renderers viewing it, so
 * the same file opened in a split shows edits live on both sides (VSCode's
 * one-TextModel-per-file behaviour). Holds the editable text and tracks
 * dirty state against the last-saved baseline. Saving is EXPLICIT — edits
 * never autosave; ⌘S (or a notebook run, or the close-prompt) calls save().
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
import { type FileEncoding, encodeText } from "../api/encoding";
import type { FileContent } from "../api/types";

export type SaveStatus = "clean" | "dirty" | "saving" | "saved" | "error";

export type BufferEntry = {
  status: "loading" | "ready" | "error";
  kind: "text" | "binary" | null;
  text: string; // editable text body (empty for binary / not-yet-loaded)
  savedText: string; // last-persisted baseline; dirty = text !== savedText
  encoding: FileEncoding; // how text re-encodes to bytes on save
  size: number;
  error: string | null;
  save: SaveStatus;
};

const LOADING: BufferEntry = {
  status: "loading",
  kind: null,
  text: "",
  savedText: "",
  encoding: "utf-8",
  size: 0,
  error: null,
  save: "clean",
};

type IO = {
  readFile: (id: string, path: string) => Promise<FileContent>;
  writeFile: (id: string, path: string, body: string | ArrayBuffer | Blob) => Promise<void>;
};

export class FileBufferStore {
  private entries = new Map<string, BufferEntry>();
  private listeners = new Map<string, Set<() => void>>();
  private inflight = new Set<string>();

  constructor(
    public readonly investigationId: string,
    private readonly io: IO = api,
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
        const text = content.kind === "text" ? content.text : "";
        this.set(path, {
          status: "ready",
          kind: content.kind,
          text,
          savedText: text,
          encoding: content.kind === "text" ? content.encoding : "utf-8",
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

  /** All paths that currently have a buffer (loaded or in-flight). The
   * refresh-files chain calls `reload(path)` on each clean one after a
   * sandbox state change so the open editor never shows stale content;
   * the caller should skip dirty paths (reload would clobber the user's
   * unsaved edits silently — they can save first then refresh). */
  bufferedPaths(): string[] {
    return [...this.entries.keys()];
  }

  /** Update the in-memory text (live across all panes). Marks dirty unless
   * the text matches the last-saved baseline. Never autosaves. */
  setText(path: string, text: string): void {
    const prev = this.entries.get(path) ?? LOADING;
    this.set(path, {
      status: "ready",
      kind: "text",
      text,
      save: text === prev.savedText ? "clean" : "dirty",
    });
  }

  isDirty(path: string): boolean {
    const e = this.entries.get(path);
    return !!e && (e.save === "dirty" || e.save === "error");
  }

  dirtyPaths(): string[] {
    return [...this.entries.keys()].filter((p) => this.isDirty(p));
  }

  /** Revert unsaved edits back to the last-saved content (close → Don't Save). */
  discard(path: string): void {
    const e = this.entries.get(path);
    if (!e) return;
    this.set(path, { text: e.savedText, save: "clean" });
  }

  /** Persist the buffer to the backend now (⌘S, notebook run, close→Save).
   * No-op when clean. Updates the baseline so dirty clears. */
  async save(path: string): Promise<void> {
    const entry = this.entries.get(path);
    if (!entry || entry.save === "saving" || !this.isDirty(path)) return;
    const text = entry.text;
    this.set(path, { save: "saving" });
    try {
      // UTF-8 sends the string as-is; "binary" re-encodes byte-exact so a
      // file opened losslessly (latin1) saves without corrupting bytes.
      const body: string | ArrayBuffer =
        entry.encoding === "binary"
          ? (encodeText(text, "binary").buffer as ArrayBuffer)
          : text;
      await this.io.writeFile(this.investigationId, path, body);
      const after = this.entries.get(path);
      // Keep dirty if the user typed more while the write was in flight.
      const save = after && after.text !== text ? "dirty" : "saved";
      this.set(path, { savedText: text, save });
    } catch {
      this.set(path, { save: "error" });
    }
  }

  /** Back-compat alias — persist if dirty. */
  flush(path: string): Promise<void> {
    return this.save(path);
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

/** Subscribe to a path's dirty flag WITHOUT triggering a load — for the
 * tab strip, which must not pre-fetch every open tab's content. */
export function useIsDirty(path: string): boolean {
  const store = useFileBufferStore();
  const subscribe = useCallback((cb: () => void) => store.subscribe(path, cb), [store, path]);
  const getSnapshot = useCallback(() => store.isDirty(path), [store, path]);
  return useSyncExternalStore(subscribe, getSnapshot);
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
    save: useCallback(() => store.save(path), [store, path]),
    flush: useCallback(() => store.flush(path), [store, path]),
    reload: useCallback(() => store.reload(path), [store, path]),
  };
}
