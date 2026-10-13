/**
 * What the helper runs in its Chrome to turn a fetched page into
 * Markdown (docs/plan-outside-helper.md D11, §3.4) — the card's own paste
 * converter, built from this source into the helper's package
 * (`pnpm run build:ai-workspace` → `ai-workspace/src/ai_workspace/
 * page_to_markdown.js`), never copied by hand. The build exposes this object
 * as the global `__aiWorkspace`.
 */
import { htmlToMarkdown } from "./htmlToMarkdown";

export const aiWorkspace = { htmlToMarkdown };
