// @vitest-environment happy-dom
/** The per-page "rebuild when I open this" toggle. */
import { beforeEach, describe, expect, it } from "vitest";

import { autoBuildScope, getWuiAutoBuild, setWuiAutoBuild } from "./wuiAutoBuild";

const PAGE = autoBuildScope("item1", "/sales");

describe("wuiAutoBuild", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to ON — which is what makes a stale page impossible", () => {
    // Off by default would leave the failure this exists to prevent in place
    // for everyone who never finds the toggle.
    expect(getWuiAutoBuild(PAGE)).toBe(true);
  });

  it("round-trips OFF and back", () => {
    setWuiAutoBuild(PAGE, false);
    expect(getWuiAutoBuild(PAGE)).toBe(false);
    setWuiAutoBuild(PAGE, true);
    expect(getWuiAutoBuild(PAGE)).toBe(true);
  });

  it("is remembered per page, not per browser", () => {
    // One page builds in two seconds and another in sixty. A single switch
    // would make the slow one's answer the fast one's too.
    setWuiAutoBuild(PAGE, false);

    expect(getWuiAutoBuild(autoBuildScope("item1", "/costs"))).toBe(true);
    expect(getWuiAutoBuild(autoBuildScope("item2", "/sales"))).toBe(true);
  });

  it("separates two items that name a folder the same", () => {
    // The folder alone is not an identity: `/page` exists in as many items as
    // people care to create it in.
    setWuiAutoBuild(autoBuildScope("item1", "/page"), false);

    expect(getWuiAutoBuild(autoBuildScope("item2", "/page"))).toBe(true);
  });

  it("treats stored garbage as the default", () => {
    localStorage.setItem(`rca.wuiAutoBuild.${PAGE}`, "banana");
    expect(getWuiAutoBuild(PAGE)).toBe(true);
  });
});
