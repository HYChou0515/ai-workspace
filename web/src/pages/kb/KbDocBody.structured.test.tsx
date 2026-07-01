// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { QueryWrap } from "../../test/queryWrapper";
import { KbDocBody } from "./KbDocBody";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

function mkDoc(over: Partial<KbRenderedDoc>): KbRenderedDoc {
  return {
    document_id: "col/u/d",
    collection_id: "col",
    file_id: "blob-1",
    content_type: "text/plain",
    size: 1,
    chunks: 0,
    cited: 0,
    created_by: "u",
    updated_at: 0,
    status: "ready",
    markdown: "",
    filename: "d",
    ...over,
  } as KbRenderedDoc;
}

function fakeClient(doc: KbRenderedDoc): KbApi {
  return { renderDocument: async () => doc, getDocChunks: async () => [] } as unknown as KbApi;
}

describe("KbDocBody structured-data viewers (#361)", () => {
  afterEach(cleanup);

  it("renders a .json doc's verbatim text as a collapsible tree", async () => {
    const doc = mkDoc({ filename: "config.json", markdown: '{"name": "widget"}' });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    expect(await screen.findByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
  });

  it("renders a .csv doc as a data grid", async () => {
    const doc = mkDoc({ filename: "data.csv", markdown: "a,b\n1,2\n" });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    expect(await screen.findByRole("columnheader", { name: "a" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
  });

  it("renders a .jsonl doc as one card per record", async () => {
    const doc = mkDoc({ filename: "events.jsonl", markdown: '{"a": 1}\n{"b": 2}\n' });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    expect(await screen.findAllByTestId("jsonl-record")).toHaveLength(2);
  });

  it("renders a .yaml doc as a tree", async () => {
    const doc = mkDoc({ filename: "conf.yaml", markdown: "name: widget\n" });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    expect(await screen.findByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
  });

  it("still renders a markdown doc through the markdown path", async () => {
    const doc = mkDoc({ filename: "notes.md", content_type: "text/markdown", markdown: "# Heading\nbody" });
    render(<KbDocBody documentId="col/u/d" onNavigate={() => {}} client={fakeClient(doc)} />);
    expect(await screen.findByRole("heading", { name: "Heading" })).toBeInTheDocument();
  });

  it("keeps the cited-passage callout for a structured doc without inline highlight", async () => {
    const doc = mkDoc({ filename: "config.json", markdown: '{"name": "widget"}' });
    render(
      <KbDocBody documentId="col/u/d" snippet="the cited bit" onNavigate={() => {}} client={fakeClient(doc)} />,
    );
    // Tree still renders (await the async doc load) alongside the callout.
    expect(await screen.findByText(/widget/)).toBeInTheDocument();
    expect(screen.getByText("the cited bit")).toBeInTheDocument();
  });
});
