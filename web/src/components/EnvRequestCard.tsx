/**
 * The `request_env` card (docs/plan-env-request-card.md): a package tool
 * needed a variable, and the AI asked the user for it here instead of in prose.
 *
 * One row per name, judged NOW from the viewer's own values (D4): a sign-in
 * when a login produces it, a field otherwise, "set" once it has a value. The
 * turn stopped at this card (N3); once every row is set, Retry sends an
 * ordinary message — marked as answering this call, so the card retires,
 * reload or not — and the AI runs the tool again. Nothing retries by itself.
 *
 * Outside an item chat (`useChatItem()` has no `env`: the knowledge-base chat,
 * a replay) it shows the request without actions.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api as defaultApi } from "../api";
import { personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient, EnvProvider } from "../api/types";
import { useChatItem } from "../hooks/chatItem";
import { asksPersonal, ownLayer } from "../lib/envLayers";
import { envRequestRows } from "../lib/envRequestRows";
import { useT } from "../lib/i18n";
import type { EnvRequest } from "../renderers/envRequest";
import { EnvLoginModal } from "./EnvLoginModal";

export function EnvRequestCard({
  callId,
  request,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
}: {
  /** The `request_env` call — what the Retry message says it answers. */
  callId: string;
  request: EnvRequest;
  client?: Pick<ApiClient, "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: Pick<PrivateEnvClient, "get" | "put">;
  personalClient?: Pick<PersonalEnvClient, "get">;
}) {
  const t = useT();
  const item = useChatItem();
  const env = item?.env;
  const on = Boolean(item && env);
  const slug = item?.slug ?? "";
  const itemId = item?.itemId ?? "";
  // The same keys the header's missing-value hint reads (`useEnvMissing`), so a
  // card adds no request of its own, and a save that refreshes one refreshes both.
  const providers = useQuery({
    queryKey: qk.envProviders(slug, itemId),
    queryFn: () => client.getEnvProviders(slug, itemId),
    enabled: on,
  });
  const mine = useQuery({
    queryKey: qk.privateEnv(slug, itemId),
    queryFn: () => privateClient.get(slug, itemId),
    enabled: on,
  });
  // Read only where the item asks for personal values — like the header does.
  const asks = asksPersonal(env?.policy ?? {});
  const personal = useQuery({
    queryKey: qk.personalEnv(),
    queryFn: () => personalClient.get(),
    enabled: on && asks,
  });
  const rows = envRequestRows(request.names, {
    shared: env?.shared ?? {},
    policy: env?.policy ?? {},
    mine: ownLayer(mine.data?.values ?? {}, mine.data?.auto ?? {}),
    personal: personal.data?.values ?? {},
    providers: providers.data ?? [],
  });
  // Settled, not succeeded: a list of logins that could not be read still
  // leaves a field to fill — the card must not go inert over it.
  const loaded = !providers.isPending && !mine.isPending && (!asks || !personal.isPending);
  const allSet = loaded && rows.every((r) => r.status === "ready");
  // N6: a login opens its own page — the panel's login form on its own.
  const [signingIn, setSigningIn] = useState<EnvProvider | null>(null);
  // Retired by the thread, not by a flag of its own: the Retry message is drawn
  // marked as answering this call, and a send the server refuses is retracted —
  // so the card comes back by itself (plan D8).
  const done = Boolean(env?.answered(callId));
  return (
    <div className="env-request-card" data-testid="env-request-card">
      <p className="env-request-card-reason">{request.reason}</p>
      <ul className="env-request-card-rows">
        {rows.map((r) => (
          <li key={r.name} className="env-request-card-row">
            <span className="env-request-card-name">{r.name}</span>
            {!env || !loaded ? null : r.status === "ready" ? (
              <span className="env-request-card-ready">✓ {t("envreq.ready")}</span>
            ) : (
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant="primary"
                onClick={() => {
                  // `r.login` was read off this same list, so the provider is
                  // always found; the panel is the fallback only by type.
                  const login = r.login && providers.data?.find((p) => p.id === r.login?.id);
                  if (login) setSigningIn(login);
                  else env.open({ name: r.name });
                }}
              >
                {r.login
                  ? t("envreq.login", { label: r.login.label })
                  : t("envreq.set", { name: r.name })}
              </button>
            )}
          </li>
        ))}
      </ul>
      {env ? (
        <div className="env-request-card-foot">
          {done ? (
            <span>{t("envreq.retried")}</span>
          ) : allSet ? (
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="primary"
              onClick={() => {
                const text = t("envreq.retryMessage", {
                  names: request.names.join(", "),
                  tool: request.tool,
                });
                env.retry(callId, text);
              }}
            >
              {t("envreq.retry")}
            </button>
          ) : (
            <span>{t("envreq.wait", { tool: request.tool })}</span>
          )}
        </div>
      ) : null}
      {signingIn && env ? (
        <EnvLoginModal
          slug={slug}
          itemId={itemId}
          provider={signingIn}
          onClose={() => setSigningIn(null)}
          client={client}
          privateClient={privateClient}
        />
      ) : null}
    </div>
  );
}
