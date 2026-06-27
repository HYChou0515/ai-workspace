// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import type { KbApi, KbCollection } from "../api/kb";
import type { FileContent } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { CollectionsPickerModal } from "./CollectionsPickerModal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  ...over,
});

const COLLECTIONS = [
  coll({ resource_id: "a", name: "Alpha", doc_count: 3 }),
  coll({ resource_id: "b", name: "Beta", doc_count: 7 }),
  coll({ resource_id: "c", name: "Gamma", doc_count: 0 }),
];

/** A fake FileService whose collections.json read/write we control + observe. */
function fakeFileService(content: string | { notFound: true }): {
  svc: FileService;
  writes: string[];
} {
  const writes: string[] = [];
  const readFile = vi.fn(async (path: string): Promise<FileContent> => {
    if (typeof content === "object") {
      const err = new Error("read failed: 404") as Error & { status: number };
      err.status = 404;
      throw err;
    }
    return { kind: "text", path, size: content.length, text: content, encoding: "utf-8" };
  });
  const writeFile = vi.fn(async (_path: string, body: string | Blob | ArrayBuffer) => {
    writes.push(String(body));
  });
  const svc = {
    scopeId: "it",
    caps: {
      write: true,
      create: true,
      upload: true,
      delete: true,
      move: true,
      copy: true,
      folders: true,
      download: true,
    },
    listFiles: async () => [],
    listDirs: async () => [],
    readFile,
    writeFile,
    deleteFile: async () => {},
    moveFile: async () => {},
    copyFile: async () => {},
    mkdir: async () => {},
    refreshFiles: async () => {},
    fileUrl: () => "",
    fileDownloadUrl: () => "",
    prepareDirDownload: async () => ({ download_id: "d", filename: "f.zip", size: 0 }),
    dirDownloadUrl: () => "",
  } satisfies FileService;
  return { svc, writes };
}

function fakeClient(): KbApi {
  return { listCollections: vi.fn(async () => COLLECTIONS) } as unknown as KbApi;
}

const render = (svc: FileService, onClose = vi.fn()) =>
  renderWithQuery(
    <CollectionsPickerModal fileService={svc} client={fakeClient()} onClose={onClose} />,
  );

describe("CollectionsPickerModal", () => {
  it("lists every live collection with its doc count, and pre-checks the ones in collections.json", async () => {
    const { svc } = fakeFileService('[{"id":"b","name":"Beta"}]');
    render(svc);
    await waitFor(() => expect(screen.getByTestId("collection-row-a")).toBeInTheDocument());
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Gamma")).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument(); // Beta doc_count
    expect(screen.getByTestId("collection-check-b")).toBeChecked();
    expect(screen.getByTestId("collection-check-a")).not.toBeChecked();
  });

  it("filters the list by the search box (case-insensitive)", async () => {
    const { svc } = fakeFileService("[]");
    render(svc);
    await screen.findByTestId("collection-row-a");
    fireEvent.change(screen.getByTestId("collections-search"), { target: { value: "bet" } });
    expect(screen.getByTestId("collection-row-b")).toBeInTheDocument();
    expect(screen.queryByTestId("collection-row-a")).not.toBeInTheDocument();
  });

  it("writes the checked set as 2-space JSON with LIVE names, then invalidates + closes", async () => {
    const { svc, writes } = fakeFileService('[{"id":"b","name":"stale old name"}]');
    const onClose = vi.fn();
    render(svc, onClose);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("collection-check-a")); // add Alpha
    fireEvent.click(screen.getByTestId("collection-check-b")); // remove Beta
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(writes).toHaveLength(1);
    expect(writes[0]).toBe('[\n  {\n    "id": "a",\n    "name": "Alpha"\n  }\n]');
  });

  it("save is disabled until something changes (no-op guard)", async () => {
    const { svc } = fakeFileService('[{"id":"a","name":"Alpha"}]');
    render(svc);
    await screen.findByTestId("collection-row-a");
    expect(screen.getByTestId("collections-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("collection-check-b"));
    expect(screen.getByTestId("collections-save")).toBeEnabled();
  });

  it("treats a missing collections.json as an empty selection and shows a first-time hint", async () => {
    const { svc } = fakeFileService({ notFound: true });
    render(svc);
    await screen.findByTestId("collection-row-a");
    expect(screen.getByTestId("collection-check-a")).not.toBeChecked();
    expect(screen.getByTestId("collections-empty-hint")).toBeInTheDocument();
  });

  it("warns before overwriting an unparseable file but still allows save", async () => {
    const { svc, writes } = fakeFileService("{not json");
    render(svc);
    await screen.findByTestId("collection-row-a");
    expect(screen.getByTestId("collections-invalid-banner")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("collection-check-a"));
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toBe('[\n  {\n    "id": "a",\n    "name": "Alpha"\n  }\n]');
  });

  it("notes how many malformed entries were ignored", async () => {
    const { svc } = fakeFileService('[{"id":"a","name":"Alpha"},{"oops":1},5]');
    render(svc);
    await screen.findByTestId("collection-row-a");
    expect(screen.getByTestId("collections-ignored-note")).toHaveTextContent("2");
  });

  it("surfaces an orphan id (collection deleted) with a one-click remove, preserving it on save until removed", async () => {
    const { svc, writes } = fakeFileService('[{"id":"gone","name":"Old"},{"id":"a","name":"Alpha"}]');
    render(svc);
    await screen.findByTestId("collection-row-a");
    // The orphan is not a normal row (not in the live list) but shown in its own area.
    expect(screen.getByTestId("orphan-gone")).toBeInTheDocument();
    expect(screen.queryByTestId("collection-row-gone")).not.toBeInTheDocument();
    // Remove it, then save → only the live-checked Alpha remains.
    fireEvent.click(screen.getByTestId("orphan-remove-gone"));
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toBe('[\n  {\n    "id": "a",\n    "name": "Alpha"\n  }\n]');
  });

  it("keeps an un-removed orphan in the file verbatim on save", async () => {
    const { svc, writes } = fakeFileService('[{"id":"gone","name":"Old"}]');
    render(svc);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("collection-check-a")); // add a live one
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(writes).toHaveLength(1));
    // Alpha (live) first, then the untouched orphan preserved with its stored name.
    expect(writes[0]).toBe(
      '[\n  {\n    "id": "a",\n    "name": "Alpha"\n  },\n  {\n    "id": "gone",\n    "name": "Old"\n  }\n]',
    );
  });

  it("guards against discarding unsaved edits on cancel", async () => {
    const { svc } = fakeFileService("[]");
    const onClose = vi.fn();
    render(svc, onClose);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("collection-check-a")); // make it dirty
    fireEvent.click(screen.getByTestId("collections-cancel"));
    // Does not close immediately — asks first.
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("collections-discard-confirm")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("discard-yes"));
    expect(onClose).toHaveBeenCalled();
  });

  it("closes straight away on cancel when nothing changed", async () => {
    const { svc } = fakeFileService('[{"id":"a","name":"Alpha"}]');
    const onClose = vi.fn();
    render(svc, onClose);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("collections-cancel"));
    expect(onClose).toHaveBeenCalled();
  });

  // #280: priority tiers — move selected collections into ordered priority groups
  // the RCA agent walks by rank. A single tier stays the flat file it always was.
  it("moves a collection into a lower priority tier and saves it with a tier int", async () => {
    const { svc, writes } = fakeFileService("[]");
    render(svc);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("collection-check-a"));
    fireEvent.click(screen.getByTestId("collection-check-b"));
    // Both start in the top tier; push Beta down into a second tier.
    fireEvent.click(screen.getByTestId("tier-down-b"));
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0])).toEqual([
      { id: "a", name: "Alpha" }, // top tier ⇒ tier 0 omitted (stays flat)
      { id: "b", name: "Beta", tier: 10 },
    ]);
  });

  it("pre-fills the priority tiers from an existing tiered collections.json", async () => {
    const { svc } = fakeFileService(
      '[{"id":"a","name":"Alpha","tier":0},{"id":"b","name":"Beta","tier":10}]',
    );
    render(svc);
    await screen.findByTestId("collection-row-a");
    // Alpha in the first priority group, Beta in the second.
    expect(screen.getByTestId("tier-group-0")).toHaveTextContent("Alpha");
    expect(screen.getByTestId("tier-group-1")).toHaveTextContent("Beta");
  });

  it("raising a collection back to the top tier saves a flat file again", async () => {
    const { svc, writes } = fakeFileService(
      '[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta","tier":10}]',
    );
    render(svc);
    await screen.findByTestId("collection-row-a");
    fireEvent.click(screen.getByTestId("tier-up-b")); // Beta: second tier → top tier
    fireEvent.click(screen.getByTestId("collections-save"));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0])).toEqual([
      { id: "a", name: "Alpha" },
      { id: "b", name: "Beta" },
    ]);
  });

  it("changing only a tier (no selection change) enables save", async () => {
    const { svc } = fakeFileService('[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]');
    render(svc);
    await screen.findByTestId("collection-row-a");
    expect(screen.getByTestId("collections-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("tier-down-b"));
    expect(screen.getByTestId("collections-save")).toBeEnabled();
  });
});
