/**
 * The browser's copy of the server's page-write rule (`api/wui_page.py`
 * `page_may_write`, docs/plan-wui-user.md D12) — what a page's bridge refuses
 * before asking. Held to the table the backend also answers, so the browser
 * never promises a write the server would refuse, nor refuses one it allows.
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`.
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/wui_page_write_cases.json";

import { pageMayWrite } from "../src/renderers/wui/paths";

describe("pageMayWrite", () => {
  it.each(table.cases as { view: string; path: string; expected: boolean }[])(
    "agrees with page_may_write: %j",
    (c) => {
      expect(pageMayWrite(c.view, c.path)).toBe(c.expected);
    },
  );
});
