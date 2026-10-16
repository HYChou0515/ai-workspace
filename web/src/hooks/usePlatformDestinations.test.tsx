// @vitest-environment happy-dom
/**
 * The platform's destinations: the capacity dashboard is offered to superusers
 * only (docs/plan-capacity-usage.md D4) — anyone else would get a 403 from it.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";

const SUPER = vi.hoisted(() => ({ current: false }));
vi.mock("./useIsSuperuser", () => ({ useIsSuperuser: () => SUPER.current }));
vi.mock("../api/groups", () => ({ groupsApi: { listGroups: async () => [] } }));

import { usePlatformDestinations } from "./usePlatformDestinations";

function destinations(superuser: boolean) {
  SUPER.current = superuser;
  return renderHook(() => usePlatformDestinations(), { wrapper: QueryWrap }).result;
}

describe("usePlatformDestinations", () => {
  it("offers the capacity dashboard to a superuser", async () => {
    const result = destinations(true);
    await waitFor(() => expect(result.current.map((d) => d.to)).toContain("/capacity"));
  });

  it("does not offer it to anyone else", async () => {
    const result = destinations(false);
    await waitFor(() => expect(result.current.map((d) => d.to)).toContain("/help"));
    expect(result.current.map((d) => d.to)).not.toContain("/capacity");
  });
});
