/**
 * The view SDK's version (#847/#848). A runtime plugin declares the MAJOR it was
 * built against in `plugin.json` (`"sdk": "1"`); the loader refuses a plugin
 * whose major differs, per panel, loudly. Bump the major when a change to
 * `renderers/entity/public.ts` would break an already-built plugin.
 */
export const SDK_VERSION = "1";
