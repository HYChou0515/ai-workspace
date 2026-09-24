// @vitest-environment happy-dom
/**
 * The 🔗 choice is per VIEW. The IDE keeps one view panel mounted and swaps the
 * file under it, so the hook must follow its key — the class
 * `usePersistentSet` fixed once already ("item A's folders became item B's").
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useViewMarking } from "./MarkingControl";

afterEach(() => localStorage.clear());

describe("useViewMarking", () => {
  it("a choice made for view A does not follow the panel to view B", () => {
    const { result, rerender } = renderHook(
      ({ k, file }: { k: string; file: string | null }) => useViewMarking(k, file),
      { initialProps: { k: "item:/a.ai.yaml", file: "fail" } },
    );
    act(() => result.current[1](null));
    expect(result.current[0]).toBeNull();

    rerender({ k: "item:/b.ai.yaml", file: "fail" });
    expect(result.current[0]).toBe("fail");

    rerender({ k: "item:/a.ai.yaml", file: "fail" });
    expect(result.current[0]).toBeNull();
  });
});
