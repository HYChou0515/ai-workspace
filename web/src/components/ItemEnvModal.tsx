/**
 * The Env panel opened from a PAGE — the `/w/` platform bar, or a page's own
 * `workspace.openLogin()` (`docs/plan-wui-viewer-login.md` Q10/Q11).
 *
 * A page has the item's id but not its record, so the two layers are loaded
 * here (`GET …/env/layers`, under `read_meta`, the verb that already returns
 * them on the item). The shared half is read-only from here: storing shared
 * values is the workspace's Env button, and a page is not the place to do it.
 * Drawn by the platform, outside the page's frame — the page never sees what
 * is typed into it.
 */
import { useQuery } from "@tanstack/react-query";

import { api as defaultApi } from "../api";
import { personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient } from "../api/types";
import { EnvVarsModal } from "./EnvVarsModal";

export function ItemEnvModal({
  slug,
  itemId,
  onClose,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
}: {
  slug: string;
  itemId: string;
  onClose: () => void;
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: PrivateEnvClient;
  personalClient?: Pick<PersonalEnvClient, "get" | "put">;
}) {
  const layers = useQuery({
    queryKey: qk.envLayers(slug, itemId),
    queryFn: () => privateClient.layers(slug, itemId),
  });
  // Drawn once the layers are here: the panel seeds its form from them once.
  if (!layers.data) return null;
  return (
    <EnvVarsModal
      envVars={layers.data.shared}
      envPolicy={layers.data.policy}
      onClose={onClose}
      slug={slug}
      itemId={itemId}
      client={client}
      privateClient={privateClient}
      personalClient={personalClient}
    />
  );
}
