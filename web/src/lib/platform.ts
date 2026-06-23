// Platform-aware keyboard shortcut hints (#161). The codebase hardcoded ⌘
// everywhere, so Windows/Linux users saw the Mac symbol. These helpers pick the
// right modifier for the *user-visible* hint; code comments keep ⌘ for brevity.

type NavLike = {
  platform?: string;
  userAgentData?: { platform?: string };
};

/** True on macOS / iOS — where the command key (⌘) is the shortcut modifier. */
export function isMac(nav: NavLike = navigator): boolean {
  const p = nav.userAgentData?.platform ?? nav.platform ?? "";
  return /mac|iphone|ipad|ipod/i.test(p);
}

/** The modifier label for this platform: "⌘" on Mac, "Ctrl" elsewhere. */
export function modLabel(nav?: NavLike): string {
  return isMac(nav) ? "⌘" : "Ctrl";
}

/** A full shortcut hint, e.g. `modCombo("P")` → "⌘P" on Mac, "Ctrl+P" elsewhere.
 * Mac convention writes the keys tight; Windows/Linux join with a "+". */
export function modCombo(key: string, nav?: NavLike): string {
  return isMac(nav) ? `⌘${key}` : `Ctrl+${key}`;
}
