// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbProbeResult } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { FindabilityModal } from "./FindabilityModal";

afterEach(cleanup);

const COLLECTIONS = [
  { resource_id: "c1", name: "kb", parser_guidance: "BASE GUIDANCE" },
];

function before(rank: number | null): KbProbeResult["before"] {
  return rank == null
    ? { passages: [], best_rank: null }
    : { passages: [{ rank, in_top_k: rank <= 5, text: "passage", location: "p.1" }], best_rank: rank };
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
    updateCollection: vi.fn(async () => {}),
    ...overrides,
  };
}

function open(client: ReturnType<typeof fakeClient>) {
  renderWithQuery(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <FindabilityModal collectionId="c1" docId="c1/u/a.pdf" docPath="a.pdf" onClose={vi.fn()} client={client as any} />,
  );
}

describe("FindabilityModal", () => {
  it("checks current ranks for a typed question (before, no guidance)", async () => {
    const client = fakeClient();
    open(client);
    fireEvent.change(screen.getByLabelText(/question/i), {
      target: { value: "solder void root cause" },
    });
    fireEvent.click(screen.getByRole("button", { name: /check ranks/i }));
    await waitFor(() => {
      expect(client.probeFindability).toHaveBeenCalledWith(
        expect.objectContaining({ doc_id: "c1/u/a.pdf", question: "solder void root cause", guidance: null }),
      );
    });
    // the doc's best rank surfaces
    expect(await screen.findByText("#8")).toBeInTheDocument();
  });

  it("previews a candidate guidance re-parse and shows the after rank", async () => {
    const client = fakeClient();
    open(client);
    fireEvent.change(screen.getByLabelText(/question/i), { target: { value: "q" } });
    // the guidance editor prefills from the collection's current parser_guidance
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.change(screen.getByLabelText(/guidance/i), {
      target: { value: "Focus on solder void root cause." },
    });
    fireEvent.click(screen.getByRole("button", { name: /re-parse/i }));
    await waitFor(() => {
      expect(client.probeFindability).toHaveBeenCalledWith(
        expect.objectContaining({ guidance: "Focus on solder void root cause." }),
      );
    });
    // the after column shows the improved rank
    expect(await screen.findByText("#2")).toBeInTheDocument();
  });

  it("applies the tuned guidance to the collection", async () => {
    const client = fakeClient();
    open(client);
    await screen.findByDisplayValue("BASE GUIDANCE");
    fireEvent.change(screen.getByLabelText(/guidance/i), {
      target: { value: "New steering." },
    });
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    await waitFor(() => {
      expect(client.updateCollection).toHaveBeenCalledWith("c1", { parser_guidance: "New steering." });
    });
  });

  it("closes via the close button", () => {
    const onClose = vi.fn();
    renderWithQuery(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <FindabilityModal collectionId="c1" docId="d" docPath="a.pdf" onClose={onClose} client={fakeClient() as any} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
