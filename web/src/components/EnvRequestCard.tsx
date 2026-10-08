/**
 * The `request_env` card (docs/plan-env-request-card.md): a package tool
 * needed a variable, and the AI asked the user for it here instead of in prose.
 *
 * One row per name, judged NOW from the viewer's own values (D4): a sign-in
 * when a login produces it, a field otherwise, "set" once it has a value. The
 * turn stopped at this card (N3); once every row is set, Retry sends an
 * ordinary message and the AI runs the tool again. Nothing retries by itself.
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
import type { ApiClient } from "../api/types";
import { useChatItem } from "../hooks/chatItem";
import { asksPersonal, ownLayer } from "../lib/envLayers";
import { envRequestRows } from "../lib/envRequestRows";
import { useT } from "../lib/i18n";
import type { EnvRequest } from "../renderers/envRequest";

export function EnvRequestCard({
  request,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
}: {
  request: EnvRequest;
  client?: Pick<ApiClient, "getEnvProviders">;
  privateClient?: Pick<PrivateEnvClient, "get">;
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
  const [retried, setRetried] = useState(false);
  const rows = envRequestRows(request.names, {
    shared: env?.shared ?? {},
    policy: env?.policy ?? {},
    mine: ownLayer(mine.data?.values ?? {}, mine.data?.auto ?? {}),
    personal: personal.data?.values ?? {},
    providers: providers.data ?? [],
  });
  const loaded = providers.isSuccess && mine.isSuccess && (!asks || !personal.isPending);
  const allSet = loaded && rows.every((r) => r.status === "ready");
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
                onClick={() =>
                  env.open({
                    name: r.name,
                    login: r.status === "missing" ? (r.login?.id ?? null) : null,
                    tab: r.status === "pinned" ? "shared" : "mine",
                  })
                }
              >
                {r.status === "pinned"
                  ? t("envreq.shared", { name: r.name })
                  : r.login
                    ? t("envreq.login", { label: r.login.label })
                    : t("envreq.set", { name: r.name })}
              </button>
            )}
          </li>
        ))}
      </ul>
      {env ? (
        <div className="env-request-card-foot">
          {allSet ? (
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="primary"
              disabled={retried}
              onClick={() => {
                setRetried(true);
                env.retry(
                  t("envreq.retryMessage", { names: request.names.join(", "), tool: request.tool }),
                );
              }}
            >
              {t("envreq.retry")}
            </button>
          ) : (
            <span>{t("envreq.wait", { tool: request.tool })}</span>
          )}
        </div>
      ) : null}
    </div>
  );
}
