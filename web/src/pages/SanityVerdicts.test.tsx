// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SanityApi, SanityMeta, SanityVerdict } from "../api/sanity";
import { renderWithQuery } from "../test/queryWrapper";
import { SanityVerdicts } from "./SanityVerdicts";

const meta: SanityMeta = {
  models: ["m-a", "m-b"],
  levels: [{ level: "none", label: "Off" }],
  questions: [],
};
const verdicts: SanityVerdict[] = [
  { model: "m-a", score: 82, summary: "- 適合 KB 問答\n- JSON 強" },
];

function fakeApi(over: Partial<SanityApi> = {}): SanityApi {
  return {
    getMeta: async () => meta,
    getResults: async () => [],
    run: vi.fn(async () => ({ queued: true })),
    getVerdicts: async () => verdicts,
    runMissing: vi.fn(async () => ({ count: 0 })),
    rescore: vi.fn(async () => ({ count: 1 })),
    listCustom: async () => [],
    createCustom: vi.fn(async (b) => ({ id: "x", ...b })),
    updateCustom: vi.fn(async (id, b) => ({ id, ...b })),
    deleteCustom: vi.fn(async () => {}),
    ...over,
  };
}

describe("SanityVerdicts", () => {
  afterEach(cleanup);

  it("renders a fitness card per model with score + summary", async () => {
    renderWithQuery(<SanityVerdicts client={fakeApi()} />);
    expect(await screen.findByTestId("verdict-m-a")).toBeInTheDocument();
    expect(screen.getByTestId("verdict-score-m-a")).toHaveTextContent("82");
    expect(screen.getByText(/適合 KB 問答/)).toBeInTheDocument();
  });

  it("'重新 AI 評分' triggers a rescore", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityVerdicts client={api} />);
    await userEvent.click(await screen.findByTestId("rescore"));
    await waitFor(() => expect(api.rescore).toHaveBeenCalled());
  });

  it("shows an empty hint when there are no verdicts yet", async () => {
    renderWithQuery(<SanityVerdicts client={fakeApi({ getVerdicts: async () => [] })} />);
    expect(await screen.findByTestId("verdicts-empty")).toBeInTheDocument();
  });
});
