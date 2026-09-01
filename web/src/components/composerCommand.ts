/**
 * Slash commands the composer runs itself (#739).
 *
 * A command is not a message: it never reaches the model and is never persisted
 * as something the user said — otherwise the literal text "/compact" ends up in
 * the transcript the summariser is about to read.
 *
 * The vocabulary is deliberately one word long. A slash command is invisible by
 * nature, so every entry needs a visible affordance somewhere too; shipping a
 * secret vocabulary is worse than shipping none. Anything else that starts with
 * a slash — a path, a question ABOUT the command — stays ordinary text, because
 * refusing to send `/etc/hosts 這個檔案在哪` would be maddening.
 */
export type ComposerCommand = "compact";

const COMMANDS: Record<string, ComposerCommand> = { "/compact": "compact" };

export function parseComposerCommand(text: string): ComposerCommand | null {
  return COMMANDS[text.trim().toLowerCase()] ?? null;
}
