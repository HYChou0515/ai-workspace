/**
 * A sign-in whose refresh token the deploy reported dead
 * (`docs/plan-env-token-refresh.md` D6): told in the thread where it happened,
 * with that sign-in's own button — the remedy picked for the person, not a
 * pointer to go and find it (NN/g, Error-Message Guidelines: "offer some
 * potential remedies … guess the correct action and let users pick it").
 *
 * Signing in again writes back to the layer the dead token lived in (D3): my
 * environment variables, or this item's Private, through #892's own login page
 * (`EnvLoginModal`). A shared token is the item's, not one person's, so the
 * button opens the environment panel at it instead.
 *
 * No Retry: #892's is bound to the model's tool call, and this message is the
 * platform's — after signing in, the person asks again.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api as defaultApi } from "../api";
import { personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient } from "../api/types";
import { useChatItem } from "../hooks/chatItem";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { EnvLoginModal } from "./EnvLoginModal";

export function SignInNotice({
  provider: providerId,
  layer,
  text,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
}: {
  provider: string;
  layer: "personal" | "private" | "shared";
  /** The stored sentence — shown when the sign-in can no longer be looked up. */
  text: string;
  client?: Pick<ApiClient, "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: Pick<PrivateEnvClient, "get" | "put">;
  personalClient?: Pick<PersonalEnvClient, "get" | "put">;
}) {
  const t = useT();
  const chat = useChatItem();
  const env = chat?.env;
  const providers = useQuery({
    queryKey: qk.envProviders(chat?.slug ?? "", chat?.itemId ?? ""),
    queryFn: () => client.getEnvProviders(chat?.slug ?? "", chat?.itemId ?? ""),
    enabled: Boolean(chat),
  });
  const provider = providers.data?.find((p) => p.id === providerId) ?? null;
  const [signingIn, setSigningIn] = useState(false);

  const label = provider?.label;
  return (
    <div
      data-testid="sign-in-notice"
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 8,
        padding: "4px 8px",
        fontSize: pxToRem(12),
        color: "var(--text-paper-d)",
        borderLeft: "2px solid var(--err)",
      }}
    >
      <span>{label ? t("env.signInExpired", { label }) : text}</span>
      {chat && env && provider ? (
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          onClick={() => {
            if (layer === "shared") env.open({ name: provider.produces[0] ?? "" });
            else setSigningIn(true);
          }}
        >
          {t("envreq.login", { label: provider.label })}
        </button>
      ) : null}
      {signingIn && chat && provider ? (
        <EnvLoginModal
          slug={chat.slug}
          itemId={chat.itemId}
          provider={provider}
          saveTo={layer === "personal" ? "personal" : "private"}
          client={client}
          privateClient={privateClient}
          personalClient={personalClient}
          onClose={() => setSigningIn(false)}
        />
      ) : null}
    </div>
  );
}
