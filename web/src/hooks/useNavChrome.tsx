/**
 * Whether the global top nav bar is hidden for the current surface (#chat-private).
 * A chat-first workspace hides it so the chat reads like a clean private-chat app
 * — the platform overview it would otherwise expose lives behind the chat rail's
 * menu button instead. Provided once by GlobalLayout; a page opts in via
 * `setHidden(true)` on mount (and restores it on unmount).
 */

import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

type NavChrome = { hidden: boolean; setHidden: (hidden: boolean) => void };

const NavChromeContext = createContext<NavChrome>({ hidden: false, setHidden: () => {} });

export function NavChromeProvider({ children }: { children: ReactNode }) {
  const [hidden, setHidden] = useState(false);
  const value = useMemo(() => ({ hidden, setHidden }), [hidden]);
  return <NavChromeContext.Provider value={value}>{children}</NavChromeContext.Provider>;
}

export function useNavChrome(): NavChrome {
  return useContext(NavChromeContext);
}
