/**
 * "Put this in the chat box for me."
 *
 * A WUI's whole promise is that someone who does not write software can get
 * unstuck on their own. When their page breaks they cannot open a console, and
 * asking them to retype what went wrong is asking for the one skill they were
 * promised they would not need — so the pane hands the report over with one
 * button and they add a sentence of their own.
 *
 * It lands in the composer rather than being sent, because what to say next is
 * still theirs to decide.
 */

import { createScopedBus } from "./scopedBus";

const bus = createScopedBus<string>();

/** Subscribe to text offered for this workspace's chat box. */
export const subscribeAgentDraft = bus.subscribe;

/** Offer text to this workspace's chat box. */
export const publishAgentDraft = bus.publish;
