/** `turndown-plugin-gfm` ships no types. Only what `lib/htmlToMarkdown.ts` uses. */
declare module "turndown-plugin-gfm" {
  import type TurndownService from "turndown";

  export const gfm: TurndownService.Plugin;
}
