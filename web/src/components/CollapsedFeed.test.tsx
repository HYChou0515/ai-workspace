// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useRef } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { LocaleProvider, setStoredLocale } from "../lib/i18n";
import { CollapsedFeed } from "./CollapsedFeed";

/** docs/plan-chat-collapse-middle.md — a feed of `n` numbered rows. */
function Feed({ n, chat = "c1" }: { n: number; chat?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const rows = Array.from({ length: n }, (_, i) => `row ${i}`);
  return (
    <div ref={ref} data-testid="scroller">
      <CollapsedFeed
        entries={rows}
        resetKey={chat}
        scrollRef={ref}
        renderRow={(r, i) => (
          <p key={i} data-testid="row" data-index={i}>
            {r}
          </p>
        )}
      />
    </div>
  );
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const drawn = () => screen.queryAllByTestId("row").map((r) => Number(r.dataset.index));

describe("CollapsedFeed", () => {
  it("draws every row, with no bar, when the feed is short (D1)", () => {
    render(<Feed n={1100} />);
    expect(drawn()).toHaveLength(1100);
    expect(screen.queryByRole("button", { name: /再顯示/ })).toBeNull();
  });

  it("draws the first 100 and the newest 1000 and says how many are hidden (D1)", () => {
    render(<Feed n={5000} />);
    const rows = drawn();
    expect(rows).toHaveLength(1100);
    expect(rows.slice(0, 100)).toEqual(Array.from({ length: 100 }, (_, i) => i));
    expect(rows.slice(100)).toEqual(Array.from({ length: 1000 }, (_, i) => 4000 + i));
    expect(screen.getByText("中間還有 3900 則未顯示")).toBeTruthy();
  });

  it("keeps each row's place in the whole feed, so actions that find a row by index still work (D7)", () => {
    render(<Feed n={5000} />);
    const last = screen.getAllByTestId("row").at(-1);
    expect(last?.textContent).toBe("row 4999");
    expect(last?.dataset.index).toBe("4999");
  });

  it("↓ continues the top part downward by 1000 (D2/D3)", () => {
    render(<Feed n={5000} />);
    fireEvent.click(screen.getByRole("button", { name: "↓ 再顯示接下來的 1000 則" }));
    const rows = drawn();
    expect(rows.slice(0, 1100)).toEqual(Array.from({ length: 1100 }, (_, i) => i));
    expect(rows[1100]).toBe(4000);
    expect(screen.getByText("中間還有 2900 則未顯示")).toBeTruthy();
  });

  it("↑ continues the bottom part upward by 1000 (D2/D3)", () => {
    render(<Feed n={5000} />);
    fireEvent.click(screen.getByRole("button", { name: "↑ 再顯示更早的 1000 則" }));
    const rows = drawn();
    expect(rows[100]).toBe(3000);
    expect(rows).toHaveLength(2100);
    expect(screen.getByText("中間還有 2900 則未顯示")).toBeTruthy();
  });

  it("offers one 顯示其餘 button once no more than 1000 are hidden — a direction would change nothing (GitHub's expand-all)", () => {
    render(<Feed n={1500} />);
    expect(screen.queryByRole("button", { name: /↓|↑/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "顯示其餘 400 則" }));
    expect(drawn()).toHaveLength(1500);
    expect(screen.queryByText(/未顯示/)).toBeNull();
  });

  it("stays fully open once everything was shown: a new message does not bring the bar back", () => {
    const { rerender } = render(<Feed n={1500} />);
    fireEvent.click(screen.getByRole("button", { name: "顯示其餘 400 則" }));
    rerender(<Feed n={1501} />);
    expect(drawn()).toHaveLength(1501);
    expect(screen.queryByText(/未顯示/)).toBeNull();
  });

  it("speaks the reader's language", () => {
    setStoredLocale("en");
    render(
      <LocaleProvider>
        <Feed n={5000} />
      </LocaleProvider>,
    );
    expect(screen.getByText("Hidden: 3900")).toBeTruthy();
    expect(screen.getByRole("button", { name: "↓ Show the next 1000" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "↑ Show 1000 earlier" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Collapsed rows" })).toBeTruthy();
  });

  it("draws both expand controls as the house button, not as bare text", () => {
    // `className="btn"` alone has no look: the shape comes from data-variant /
    // data-size (styles/base.css), which a real browser showed missing.
    render(<Feed n={5000} />);
    for (const b of screen.getAllByRole("button", { name: /顯示/ })) {
      expect(b.className).toBe("btn");
      expect(b.dataset.variant).toBe("secondary");
      expect(b.dataset.size).toBe("sm");
    }
  });

  it("starts over at 100 / 1000 in another chat (D6)", () => {
    const { rerender } = render(<Feed n={5000} />);
    fireEvent.click(screen.getByRole("button", { name: "↓ 再顯示接下來的 1000 則" }));
    expect(drawn()).toHaveLength(2100);
    rerender(<Feed n={5000} chat="c2" />);
    expect(drawn()).toHaveLength(1100);
  });
});

describe("CollapsedFeed keeps the reader's place (D4)", () => {
  /** happy-dom has no layout: give the scroller a height that grows with its rows. */
  function withLayout(rowPx: number) {
    const el = screen.getByTestId("scroller");
    Object.defineProperty(el, "scrollHeight", {
      configurable: true,
      get: () => el.querySelectorAll("[data-testid=row]").length * rowPx,
    });
    return el;
  }

  it("↑ shifts the scroll by the height it added above the bottom part", () => {
    render(<Feed n={5000} />);
    const el = withLayout(10);
    el.scrollTop = 1234;
    fireEvent.click(screen.getByRole("button", { name: "↑ 再顯示更早的 1000 則" }));
    expect(el.scrollTop).toBe(1234 + 1000 * 10);
  });

  it("顯示其餘 keeps the bottom part where it was, like ↑", () => {
    render(<Feed n={1500} />);
    const el = withLayout(10);
    el.scrollTop = 1234;
    fireEvent.click(screen.getByRole("button", { name: "顯示其餘 400 則" }));
    expect(el.scrollTop).toBe(1234 + 400 * 10);
  });

  it("↓ leaves the scroll where it was: the top part stays put", () => {
    render(<Feed n={5000} />);
    const el = withLayout(10);
    el.scrollTop = 1234;
    fireEvent.click(screen.getByRole("button", { name: "↓ 再顯示接下來的 1000 則" }));
    expect(el.scrollTop).toBe(1234);
  });
});

describe("CollapsedFeed keeps rows mounted as they cross the bar", () => {
  /** A row that counts how often it is mounted — a remount loses an opened tool
   * card and re-parses its markdown. */
  const mounts: number[] = [];
  function Row({ i }: { i: number }) {
    useEffect(() => {
      mounts.push(i);
    }, [i]);
    return <p data-testid="row" data-index={i} />;
  }
  function Counted({ n }: { n: number }) {
    const ref = useRef<HTMLDivElement>(null);
    return (
      <div ref={ref}>
        <CollapsedFeed
          entries={Array.from({ length: n }, (_, i) => i)}
          resetKey="c1"
          scrollRef={ref}
          renderRow={(r, i) => <Row key={i} i={r} />}
        />
      </div>
    );
  }

  it.each([
    ["the feed grows past 1100", 1100, 1101, 1],
    ["an undo brings it back under (rows 100–101 were hidden)", 1102, 1050, 2],
  ])("mounts only new rows when %s", (_, from, to, fresh) => {
    const { rerender } = render(<Counted n={from} />);
    mounts.length = 0;
    rerender(<Counted n={to} />);
    expect(mounts).toHaveLength(fresh);
  });

  it("mounts only the revealed rows when the last press empties the middle", () => {
    render(<Counted n={1500} />);
    mounts.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "顯示其餘 400 則" }));
    expect(mounts).toHaveLength(400);
  });
});
