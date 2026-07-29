/**
 * The three-state model shared by every collapsible shell panel (the bottom
 * log strip, the file-tree sidebar).
 */

export type PanelState = "closed" | "peeked" | "pinned";

export type PanelAction =
  | { type: "peek" }
  | { type: "outside" }
  /** `sameTarget`: the pinned tab/icon being double-clicked is the one already
   * on show. Only then is a second pin a collapse — pinning a different target
   * is a switch. */
  | { type: "pin"; sameTarget: boolean }
  | { type: "toggle" };

export function panelPeek(state: PanelState, action: PanelAction): PanelState {
  switch (action.type) {
    case "peek":
      // Never demotes a pin — see the flicker note in the tests.
      return state === "pinned" ? "pinned" : "peeked";
    case "outside":
      return state === "peeked" ? "closed" : state;
    case "pin":
      return state === "pinned" && action.sameTarget ? "closed" : "pinned";
    case "toggle":
      return state === "closed" ? "pinned" : "closed";
  }
}
