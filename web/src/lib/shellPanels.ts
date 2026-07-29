/**
 * Which of the shell's two main stages — the file workspace and the chat — are
 * folded away, and the one rule that governs folding them: never both.
 *
 * Collapsing everything leaves a top bar over a blank rectangle, which reads as
 * the page having broken rather than as a layout the user chose. So folding the
 * last panel standing unfolds the other one instead of emptying the screen.
 */

export type ShellPanels = { ideCollapsed: boolean; chatCollapsed: boolean };

export function toggleShellPanel(
  state: ShellPanels,
  which: "ide" | "chat",
  /** A chat-only App (`function.workspace` false) has no workspace to swap to. */
  hasWorkspace: boolean,
): ShellPanels {
  const { ideCollapsed, chatCollapsed } = state;

  if (which === "chat") {
    if (chatCollapsed) return { ideCollapsed, chatCollapsed: false };
    // Folding the chat needs a workspace to fall back on — either one that is
    // already showing, or one we can unfold in its place.
    if (!hasWorkspace) return state;
    return { ideCollapsed: false, chatCollapsed: true };
  }

  if (ideCollapsed) return { ideCollapsed: false, chatCollapsed };
  return { ideCollapsed: true, chatCollapsed: false };
}
