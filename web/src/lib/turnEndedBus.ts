/**
 * "A turn in this item may have changed its files", for the panes that show
 * them.
 *
 * The backend says so once per turn, on the ITEM's stream, whichever chat ran
 * it (`turn_ended`). The chat session that receives it is not an ancestor of
 * the file tree or the editor, so it is handed over here — see `scopedBus`.
 *
 * A reconnect announces too: a turn that ended while the stream was down is
 * never replayed to a viewer whose stream landed on another pod. What a
 * listener does is a reload of what is on screen (`useReloadFileViews` — the
 * tree, open files, clean buffers), never a server flush, and one at a time.
 */

import { createScopedBus } from "./scopedBus";

const bus = createScopedBus<void>();

/** Subscribe to turn ends in one item. Returns the unsubscribe. */
export const subscribeTurnEnded = bus.subscribe;

/** Announce one. Called where the item's stream is handled. */
export const publishTurnEnded = (scopeId: string) => bus.publish(scopeId, undefined);
