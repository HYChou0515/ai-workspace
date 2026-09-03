/**
 * "Someone else edited a file in this workspace", for consumers the chat stream
 * cannot reach with a prop.
 *
 * The `file_changed` broadcast (#43) already arrives on the chat's SSE
 * subscription, where it invalidates the file-tree query. That is enough for a
 * tree that re-reads on demand, but not for a WUI: a page holding half-entered
 * state has to be TOLD, or it saves over the other person's work with neither of
 * them noticing. The path travels with it — the tree's own refetch would have
 * lost exactly the part that matters.
 */

import { createScopedBus } from "./scopedBus";

const bus = createScopedBus<string>();

/** Subscribe to edits in one workspace. Returns the unsubscribe. */
export const subscribeFileChanged = bus.subscribe;

/** Announce an edit. Called where the broadcast is already handled. */
export const publishFileChanged = bus.publish;
