// @vitest-environment happy-dom
/**
 * The 🔗 choice is per VIEW. The IDE keeps one view panel mounted and swaps the
 * file under it, so the hook must follow its key — the class
 * `usePersistentSet` fixed once already ("item A's folders became item B's").
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkingProvider } from "../../hooks/useMarking";
import { LocaleProvider, setStoredLocale } from "../../lib/i18n";
import { MarkingStore } from "../../lib/markings";
import { MarkingControl, useViewMarking } from "./MarkingControl";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

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

describe("MarkingControl names the columns its marking marks by (P27)", () => {
  function control(store: MarkingStore, locale: "en" | "zh-TW") {
    setStoredLocale(locale);
    return render(
      <LocaleProvider>
        <MarkingProvider store={store}>
          <MarkingControl value="fail" onChange={() => {}} />
        </MarkingProvider>
      </LocaleProvider>,
    );
  }

  it("says 'by lot, wafer' beside the picker while the marking holds a set", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L1", "L2"]), wafer: new Set(["3"]) }, "/v/g.ai.yaml");
    control(store, "en");
    expect(screen.getByTestId("marking-by")).toHaveTextContent(/^by lot, wafer$/);
  });

  it("in Chinese too", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L1"]), wafer: new Set(["3"]) }, "/v/g.ai.yaml");
    control(store, "zh-TW");
    expect(screen.getByTestId("marking-by")).toHaveTextContent(/^依 lot、wafer$/);
  });

  it("says nothing while the marking is empty", () => {
    control(new MarkingStore(), "en");
    expect(screen.queryByTestId("marking-by")).not.toBeInTheDocument();
  });
});
