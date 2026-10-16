/**
 * The open item's profile — which of the App's profiles it was made from — for
 * what the registry renders with only `{ path }`: an execution tab names its
 * workflow and reads its declared steps from the profile's workflows
 * (plan-workflow-out-of-chat D7). Published by `WorkspaceShell`; "" outside one.
 */

import { createContext, useContext } from "react";

const ItemProfileContext = createContext<string>("");

export const ItemProfileProvider = ItemProfileContext.Provider;

export function useItemProfile(): string {
  return useContext(ItemProfileContext);
}
