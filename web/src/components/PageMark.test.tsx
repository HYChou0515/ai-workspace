// @vitest-environment happy-dom
/**
 * The overview row's mark (`docs/plan-wui-overview-icon-favourites.md`): one
 * circle per row — the page's own icon in it when the view file declared one
 * that resolves, the title's first letters when not.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useResources", () => ({
  useApps: () => [
    { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
    // An App that declares no colour: the mark stays neutral.
    { slug: "plain", title: "Plain", description: "", icon: "layers", color: "" },
  ],
}));

import { appTagPalette } from "../lib/appColor";
import { PageMark, markLetters } from "./PageMark";

afterEach(cleanup);

const mark = () => screen.getByTestId("page-mark");

const base = { slug: "rca", itemId: "i-1", path: "/pages/report/page.ai.yaml" };

describe("markLetters — the default when there is no icon", () => {
  it.each([
    // CJK: the first character. Latin: the initials of the first two words,
    // `UserAvatar`'s split (spaces, `_`, `-`), uppercased.
    ["出貨看板", "出"],
    ["Shipping board", "SB"],
    ["lot-tracker", "LT"],
    ["scrap_review_q4", "SR"],
    ["  Yield  ", "Y"],
    ["", "?"],
  ])("%j → %j", (title, letters) => {
    expect(markLetters(title)).toBe(letters);
  });
});

describe("PageMark", () => {
  it("draws the title's letters in the App's tint when the page declares no icon", () => {
    render(<PageMark {...base} icon="" title="Shipping board" />);

    expect(mark()).toHaveTextContent("SB");
    expect(mark().querySelector("img, svg")).toBeNull();
    // The same palette the group heading's pill resolves, published the same
    // way (custom properties, hex — happy-dom drops `oklch()` from a style).
    const palette = appTagPalette("#F0502E")!;
    expect(mark().style.getPropertyValue("--app-tint")).toBe(palette.tint);
    expect(mark().style.getPropertyValue("--app-ink")).toBe(palette.inkLight);
    expect(mark().style.getPropertyValue("--app-ink-dark")).toBe(palette.inkDark);
    // Decoration: the row is named by its title link, not by the mark.
    expect(mark()).toHaveAttribute("aria-hidden", "true");
    // A FIXED size, inline: the overview gives the mark an `auto` grid track,
    // which is safe only for a box that cannot grow with its content
    // (`my-resources.test.ts` pins the track; this pins the box).
    expect(mark().style.width).toBe("28px");
    expect(mark().style.height).toBe("28px");
  });

  it("takes the page's OWN colour over the App's when the view file declared one it can draw", () => {
    // The author: 「顏色應該可以讓 deploy 決定」. A hex the palette can read
    // wins; anything else (a word, garbage) falls back to the App's colour,
    // not to neutral — the App's colour was the answer before the field.
    const own = appTagPalette("#0EA5A4")!;
    const { unmount } = render(<PageMark {...base} icon="" title="Yield" color="#0EA5A4" />);
    expect(mark().style.getPropertyValue("--app-tint")).toBe(own.tint);
    unmount();
    const app = appTagPalette("#F0502E")!;
    render(<PageMark {...base} icon="" title="Yield" color="tomato" />);
    expect(mark().style.getPropertyValue("--app-tint")).toBe(app.tint);
  });

  it("stays neutral for an App without a colour, and for one no longer registered", () => {
    const { unmount } = render(<PageMark {...base} slug="plain" icon="" title="Yield" />);
    expect(mark().style.getPropertyValue("--app-tint")).toBe("");
    unmount();
    render(<PageMark {...base} slug="gone" icon="" title="Yield" />);
    expect(mark().style.getPropertyValue("--app-tint")).toBe("");
    expect(mark()).toHaveTextContent("Y");
  });

  it("shows an emoji as itself, with no letters beside it", () => {
    render(<PageMark {...base} icon="📦" title="Shipping board" />);
    expect(mark()).toHaveTextContent("📦");
    expect(mark()).not.toHaveTextContent("SB");
  });

  it("draws a named icon as the icon, and an unknown key as the letters", () => {
    const { unmount } = render(<PageMark {...base} icon="kanban" title="Shipping board" />);
    expect(mark().querySelector("svg")).not.toBeNull();
    expect(mark()).not.toHaveTextContent("SB");
    unmount();
    render(<PageMark {...base} icon="rocket" title="Shipping board" />);
    expect(mark().querySelector("svg")).toBeNull();
    expect(mark()).toHaveTextContent("SB");
  });

  it("fetches a file icon from the page's own folder through the item's file route, and falls back to the letters when it does not load", () => {
    render(
      <PageMark
        slug="rca"
        itemId="i-1"
        path="/報表/出貨 看板/page.ai.yaml"
        icon="logo.png"
        title="出貨看板"
      />,
    );

    const img = mark().querySelector("img");
    expect(img).not.toBeNull();
    // Beside the view file, not at the workspace root — the rule `entry:`
    // follows — and spelled segment by segment like every file URL.
    expect(img).toHaveAttribute(
      "src",
      "/api/a/rca/items/i-1/files/%E5%A0%B1%E8%A1%A8/%E5%87%BA%E8%B2%A8%20%E7%9C%8B%E6%9D%BF/logo.png",
    );
    expect(mark()).not.toHaveTextContent("出");

    fireEvent.error(img!);

    expect(mark().querySelector("img")).toBeNull();
    expect(mark()).toHaveTextContent("出");
  });

  it("treats a file icon at the workspace root as beside the file, not under a folder", () => {
    render(<PageMark {...base} path="/status.ai.yaml" icon="icon.svg" title="Status" />);
    expect(mark().querySelector("img")).toHaveAttribute("src", "/api/a/rca/items/i-1/files/icon.svg");
  });
});
