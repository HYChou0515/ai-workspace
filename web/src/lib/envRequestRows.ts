/**
 * The rows of the chat's request card (docs/plan-env-request-card.md): one
 * per variable the AI asked for, judged NOW from the viewer's own values (D4)
 * — so a reload, another day, or another person reading the same thread all
 * see where things stand for them, not what was true when it was asked.
 */
import type { EnvProvider } from "../api/types";

import { type ViewerStatus, viewerStatus } from "./identityState";

export type EnvRequestRow = {
  name: string;
  status: ViewerStatus;
  /** The login that produces this name, or `null` — then it is a field. */
  login: { id: string; label: string } | null;
};

export function envRequestRows(
  names: string[],
  {
    shared,
    mine,
    policy,
    providers,
  }: {
    shared: Record<string, string>;
    mine: Record<string, string>;
    policy: Record<string, string>;
    providers: EnvProvider[];
  },
): EnvRequestRow[] {
  return names.map((name) => {
    const via = providers.find((p) => p.produces.includes(name));
    return {
      name,
      status: viewerStatus(name, shared, mine, policy),
      login: via ? { id: via.id, label: via.label } : null,
    };
  });
}
