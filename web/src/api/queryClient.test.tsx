// @vitest-environment happy-dom
import { QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import { HttpError } from "./http";
import { makeQueryClient } from "./queryClient";

/**
 * The rule this pins: a write that fails is never silent.
 *
 * It was. `useUpdateItemField` called `mutation.mutate` and never read
 * `mutation.error`, so a 403 from the item PATCH reached no one — the env-var
 * panel closed on click and looked exactly like a save. Reading the error at
 * each of the 135 call sites is not a rule anyone can keep; the client reports
 * it once, for all of them.
 */
function wrap(children: ReactNode) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function failing(error: unknown) {
  return renderHook(
    () =>
      useMutation({
        mutationFn: async () => {
          throw error;
        },
      }),
    { wrapper: ({ children }) => wrap(children) },
  );
}

describe("makeQueryClient — no silent write failures", () => {
  beforeEach(() => resetWriteFailures());

  it("reports a rejected mutation, carrying the status so the copy can name the cause", async () => {
    const { result } = failing(new HttpError(403, "403 Forbidden: not permitted"));
    act(() => result.current.mutate());

    await waitFor(() => expect(currentWriteFailure()).not.toBeNull());
    expect(currentWriteFailure()?.status).toBe(403);
  });

  it("reports a plain Error too — a network drop is just as silent", async () => {
    const { result } = failing(new Error("Failed to fetch"));
    act(() => result.current.mutate());

    await waitFor(() => expect(currentWriteFailure()).not.toBeNull());
    expect(currentWriteFailure()?.status).toBeNull();
    expect(currentWriteFailure()?.message).toContain("Failed to fetch");
  });

  it("stays quiet for a mutation that renders its own error inline", async () => {
    const { result } = renderHook(
      () =>
        useMutation({
          meta: { silentError: true },
          mutationFn: async () => {
            throw new HttpError(403, "403 Forbidden: not permitted");
          },
        }),
      { wrapper: ({ children }) => wrap(children) },
    );
    act(() => result.current.mutate());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(currentWriteFailure()).toBeNull();
  });

  // A failing READ already has its own empty/error states everywhere, and a
  // background refetch failing is not something to interrupt anyone about.
  it("says nothing about a failed query — this is about writes", async () => {
    const client = makeQueryClient();
    await client
      .fetchQuery({ queryKey: ["boom"], queryFn: async () => { throw new Error("read failed"); }, retry: false })
      .catch(() => {});
    expect(currentWriteFailure()).toBeNull();
  });
});
