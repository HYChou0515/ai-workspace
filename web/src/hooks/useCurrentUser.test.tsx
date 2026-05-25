// @vitest-environment happy-dom
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { makeTestQueryClient, QueryWrap } from "../test/queryWrapper";
import { useCurrentUser } from "./useCurrentUser";

function Probe() {
  // Two independent consumers of the same identity in one tree.
  useCurrentUser();
  useCurrentUser();
  return null;
}

describe("useCurrentUser", () => {
  afterEach(() => vi.restoreAllMocks());

  it("dedupes to a single fetch across consumers sharing the cache", async () => {
    const spy = vi.spyOn(api, "getCurrentUser");
    const client = makeTestQueryClient();
    render(
      <QueryWrap client={client}>
        <Probe />
      </QueryWrap>,
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    // Same query key → one network call serves both hooks.
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
