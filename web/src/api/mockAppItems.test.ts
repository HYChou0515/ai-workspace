import { describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("mockApi.listAppItems", () => {
  // #383: the mock backend is what `dev`-without-a-server and component tests
  // render against, so it must honour the same updated_time-desc `sorts` the
  // real backend does — otherwise the homepage preview looks unsorted.
  it("returns items sorted by updated_time descending when requested", async () => {
    const sorts = JSON.stringify([{ type: "meta", key: "updated_time", direction: "-" }]);
    const items = await mockApi.listAppItems("/rca-investigation", { sorts });
    const times = items.map((i) => i.updated_time ?? i.created_time);
    const descending = [...times].sort().reverse();
    expect(times).toEqual(descending);
  });
});
