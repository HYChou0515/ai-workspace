// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { RetrievalEvalPanel } from "./RetrievalEvalPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const RESULT = {
  collection_id: "c1",
  run_label: "run-1",
  n_generated: 3,
  n_kept: 3,
  recall_chunk: { "1": 0.33, "3": 1.0, "5": 1.0, "10": 1.0 },
  mrr_chunk: 0.61,
  recall_doc: { "1": 1.0 },
  mrr_doc: 1.0,
};

function stubFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/eval-result/data"))
        return new Response(JSON.stringify([RESULT]), { status: 200 });
      if (url.includes("/eval-run/data"))
        return new Response(
          JSON.stringify([
            {
              collection_id: "c1",
              run_label: "run-2",
              status: "running",
              total: 4,
              done: [0, 1],
              failed: [],
            },
          ]),
          { status: 200 },
        );
      if (url.includes("/eval-job")) return new Response("{}", { status: 201 });
      return new Response("[]", { status: 200 }); // collections list etc.
    }),
  );
  return calls;
}

describe("RetrievalEvalPanel (#535)", () => {
  it("shows the metric cards and the in-flight run", async () => {
    stubFetch();
    render(<RetrievalEvalPanel />, { wrapper: QueryWrap });

    expect(await screen.findByTestId("eval-results")).toBeInTheDocument();
    expect(screen.getByText("run-1")).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument(); // top-1 hit rate as a percentage
    expect(screen.getByText("0.61")).toBeInTheDocument(); // the chunk rank score numeral
    expect(await screen.findByTestId("eval-running")).toHaveTextContent("2/4");
  });

  it("the run button fires a dispatch job through the auto route", async () => {
    const calls = stubFetch();
    render(<RetrievalEvalPanel />, { wrapper: QueryWrap });

    await userEvent.click(await screen.findByRole("button", { name: /跑一輪評測|Run evaluation/ }));

    const post = calls.find((c) => c.url.includes("/eval-job"));
    expect(post).toBeTruthy();
    const body = JSON.parse(String(post!.init!.body));
    expect(body.payload.kind).toBe("dispatch");
    expect(body.payload.run_label).toMatch(/^run-/);
  });
});
