// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbProbeResult } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { TuneParsingModal } from "./TuneParsingModal";

afterEach(cleanup);

const COLLECTIONS = [{ resource_id: "c1", name: "kb", parser_guidance: "BASE GUIDANCE" }];

function before(rank: number | null): KbProbeResult["before"] {
  return rank == null
    ? { passages: [], best_rank: null }
    : {
        passages: [{ rank, in_top_k: rank <= 5, text: "passage", location: "p.1" }],
        best_rank: rank,
      };
}

function fakeClient(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    listCollections: vi.fn(async () => COLLECTIONS),
    probeFindability: vi.fn(
      async (body: { guidance?: string | null }): Promise<KbProbeResult> => ({
        top_k: 5,
        depth: 50,
        before: before(8),
        after: body.guidance == null ? null : before(2),
      }),
    ),
    answerFindability: vi.fn(async function* () {
      yield { type: "message_delta", text: "Voids form ", reasoning: false };
      yield { type: "message_delta", text: "from outgassing [1].", reasoning: false };
      yield { type: "done" };
    }),
    setDocumentGuidance: vi.fn(async () => {}),
    updateCollection: vi.fn(async () => {}),
    reindexDocument: vi.fn(async () => {}),
    ...overrides,
  };
}

function open(client: ReturnType<typeof fakeClient>, docGuidance?: string) {
  renderWithQuery(
    <TuneParsingModal
      collectionId="c1"
      docId="c1/u/a.pdf"
      docPath="a.pdf"
      docGuidance={docGuidance}
      onClose={vi.fn()}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      client={client as any}
    />,
  );
}

describe("TuneParsingModal", () => {
  it("prefills from the collection guidance and shows the inherited source", async () => {
    open(fakeClient());
    await screen.findByDisplayValue("BASE GUIDANCE");
    expect(screen.getByText(/繼承自 collection/)).toBeInTheDocument();
    // no per-doc override ⇒ no clear button
    expect(screen.queryByRole("button", { name: /清除專屬設定/ })).not.toBeInTheDocument();
  });

  it("prefills from the per-doc override and shows the custom source + clear button", async () => {
    open(fakeClient(), "DOC OVERRIDE");
    await screen.findByDisplayValue("DOC OVERRIDE");
    expect(screen.getByText(/此文件:專屬設定/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /清除專屬設定/ })).toBeInTheDocument();
  });

  it("checks current ranks for a typed question (guidance null, with k)", async () => {
    const client = fakeClient();
    open(client);
    fireEvent.change(screen.getByLabelText("問題"), { target: { value: "solder void" } });
    fireEvent.click(screen.getByRole("button", { name: /檢查排名/ }));
    await waitFor(() => {
      expect(client.probeFindability).toHaveBeenCalledWith(
        expect.objectContaining({
          doc_id: "c1/u/a.pdf",
          question: "solder void",
          guidance: null,
          k: 5,
        }),
      );
    });
  });

  it("re-parses with the candidate guidance and shows the after rank", async () => {
    const client = fakeClient();
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.change(screen.getByLabelText("問題"), { target: { value: "q" } });
    fireEvent.change(screen.getByLabelText("解析 prompt"), {
      target: { value: "Focus on solder void." },
    });
    fireEvent.click(screen.getByRole("button", { name: /用 guidance 重新解析/ }));
    await waitFor(() => {
      expect(client.probeFindability).toHaveBeenCalledWith(
        expect.objectContaining({ guidance: "Focus on solder void." }),
      );
    });
    // the After box (open by default) shows the improved best rank
    expect(await screen.findByText(/最佳 #2/)).toBeInTheDocument();
  });

  it("streams a Try-answer into the After box", async () => {
    const client = fakeClient();
    open(client);
    fireEvent.change(screen.getByLabelText("問題"), { target: { value: "why voids?" } });
    fireEvent.click(screen.getByRole("button", { name: /用 guidance 重新解析/ }));
    await screen.findByText(/最佳 #2/);
    fireEvent.click(screen.getByRole("button", { name: /試答 \(套用新 prompt 後\)/ }));
    expect(await screen.findByText(/Voids form from outgassing \[1\]\./)).toBeInTheDocument();
    expect(client.answerFindability).toHaveBeenCalledWith(
      expect.objectContaining({
        doc_id: "c1/u/a.pdf",
        question: "why voids?",
        guidance: "BASE GUIDANCE",
      }),
    );
  });

  it("saves the guidance for this document only", async () => {
    const client = fakeClient();
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.change(screen.getByLabelText("解析 prompt"), { target: { value: "doc-only steering" } });
    fireEvent.click(screen.getByRole("button", { name: /只套用到這份文件/ }));
    await waitFor(() => {
      expect(client.setDocumentGuidance).toHaveBeenCalledWith("c1/u/a.pdf", "doc-only steering");
    });
    // the saved-not-in-effect nudge + reindex button appear
    expect(await screen.findByRole("button", { name: /重新索引這份文件/ })).toBeInTheDocument();
  });

  it("applies to the whole collection only after confirming the blast radius", async () => {
    const client = fakeClient();
    const confirm = vi.fn(() => true);
    vi.stubGlobal("confirm", confirm);
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.change(screen.getByLabelText("解析 prompt"), { target: { value: "collection steering" } });
    fireEvent.click(screen.getByRole("button", { name: /套用到整個 collection/ }));
    await waitFor(() => {
      expect(client.updateCollection).toHaveBeenCalledWith("c1", {
        parser_guidance: "collection steering",
      });
    });
    expect(confirm).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("does not apply to the collection when the confirm is declined", async () => {
    const client = fakeClient();
    vi.stubGlobal("confirm", vi.fn(() => false));
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.click(screen.getByRole("button", { name: /套用到整個 collection/ }));
    expect(client.updateCollection).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("clears the per-doc override", async () => {
    const client = fakeClient();
    open(client, "DOC OVERRIDE");
    await screen.findByDisplayValue("DOC OVERRIDE");
    fireEvent.click(screen.getByRole("button", { name: /清除專屬設定/ }));
    await waitFor(() => {
      expect(client.setDocumentGuidance).toHaveBeenCalledWith("c1/u/a.pdf", "");
    });
  });

  it("re-indexes the document from the saved nudge", async () => {
    const client = fakeClient();
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.click(screen.getByRole("button", { name: /只套用到這份文件/ }));
    const reindex = await screen.findByRole("button", { name: /重新索引這份文件/ });
    fireEvent.click(reindex);
    await waitFor(() => expect(client.reindexDocument).toHaveBeenCalledWith("c1/u/a.pdf"));
  });

  it("closes via the close button", () => {
    const onClose = vi.fn();
    renderWithQuery(
      <TuneParsingModal
        collectionId="c1"
        docId="d"
        docPath="a.pdf"
        onClose={onClose}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        client={fakeClient() as any}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /關閉/ }));
    expect(onClose).toHaveBeenCalled();
  });
});
