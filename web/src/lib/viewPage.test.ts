import { describe, expect, it } from "vitest";

import type { LayoutNode } from "../pages/investigation/paneTree";
import { readViewPageTarget, viewPageHref } from "./viewPage";

const layout: LayoutNode = {
  type: "split",
  dir: "col",
  ratio: 0.3,
  a: { type: "leaf", path: "/v/a b.ai.yaml" },
  b: { type: "leaf", path: "/v/测试.csv" },
};

function roundTrip(href: string) {
  const url = new URL(href, "http://x");
  return { pathname: url.pathname, target: readViewPageTarget(url.searchParams) };
}

describe("viewPageHref / readViewPageTarget", () => {
  it("carries a layout there and back", () => {
    const { pathname, target } = roundTrip(viewPageHref("pm", "PG-1", { layout }));
    expect(pathname).toBe("/a/pm/PG-1/view");
    expect(target).toEqual({ layout });
  });

  it("carries a single path there and back", () => {
    expect(roundTrip(viewPageHref("pm", "PG-1", { path: "/v/a b.ai.yaml" })).target).toEqual({
      path: "/v/a b.ai.yaml",
    });
  });

  it("encodes an item id the way the workspace route decodes it", () => {
    expect(viewPageHref("pm", "a/b", { path: "/x" })).toMatch(/^\/a\/pm\/a%2Fb\/view\?/);
  });

  it("reads nothing from a missing or malformed query", () => {
    expect(readViewPageTarget(new URLSearchParams(""))).toBeNull();
    expect(readViewPageTarget(new URLSearchParams("layout=not-json"))).toBeNull();
    expect(
      readViewPageTarget(new URLSearchParams({ layout: JSON.stringify({ type: "split" }) })),
    ).toBeNull();
    expect(readViewPageTarget(new URLSearchParams("path="))).toBeNull();
  });
});
