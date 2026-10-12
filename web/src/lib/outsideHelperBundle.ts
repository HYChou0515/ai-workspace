/**
 * What the outside helper runs in its Chrome to turn a fetched page into
 * Markdown (docs/plan-outside-helper.md D11, §3.4) — the card's own paste
 * converter, built from this source into the helper's package
 * (`pnpm run build:outside-helper` → `outside-helper/src/outside_helper/
 * page_to_markdown.js`), never copied by hand. The build exposes this object
 * as the global `__outsideHelper`.
 */
import { htmlToMarkdown } from "./htmlToMarkdown";

export const outsideHelper = { htmlToMarkdown };
