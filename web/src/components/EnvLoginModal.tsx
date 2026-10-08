/**
 * The login a `request_env` card's 登入 button opens
 * (docs/plan-env-request-card.md N6): the environment panel's own login form —
 * the same `Logins`, so it looks and behaves the same — on its own, at its
 * first field, instead of the whole panel with the form somewhere inside it.
 *
 * As in the panel (#750), what the login returns is shown and stored only when
 * the person presses Save: into THEIR values for this item, the rest of that
 * layer kept.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api as defaultApi } from "../api";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient, EnvProvider } from "../api/types";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Logins } from "./EnvVarsModal";
import { ModalShell } from "./ModalShell";

export function EnvLoginModal({
  slug,
  itemId,
  provider,
  onClose,
  client = defaultApi,
  privateClient = privateEnvApi,
}: {
  slug: string;
  itemId: string;
  provider: EnvProvider;
  onClose: () => void;
  client?: Pick<ApiClient, "resolveEnvProvider">;
  privateClient?: Pick<PrivateEnvClient, "get" | "put">;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [filled, setFilled] = useState<Record<string, string> | null>(null);
  const save = useMutation({
    mutationFn: async (env: Record<string, string>) => {
      // A fresh read, then the whole layer back with these names set: a PUT
      // replaces the layer, and a value saved elsewhere since must survive.
      const now = await privateClient.get(slug, itemId);
      const values = { ...now.values, ...env };
      await privateClient.put(slug, itemId, values);
      return { values, auto: now.auto };
    },
    onSuccess: (stored) => {
      queryClient.setQueryData(qk.privateEnv(slug, itemId), stored);
      onClose();
    },
  });
  const typed = Object.values(creds).some((v) => v.trim() !== "");
  const attemptClose = useDirtyClose(typed || filled !== null, onClose);

  // At the first field, as a field target puts the person at theirs.
  useEffect(() => {
    if (filled) return;
    document.querySelector<HTMLElement>('[data-testid="env-cred-dialog"] input')?.focus();
  }, [filled]);

  const title = t("envreq.login", { label: provider.label });
  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={title}
      data-testid="env-login-modal"
      width={420}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{title}</strong>
      {filled === null ? (
        <Logins
          offered={[provider]}
          initialDialog={provider.id}
          creds={creds}
          setCreds={setCreds}
          exchange={(id, values) => client.resolveEnvProvider(slug, itemId, id, values)}
          onFilled={(env) => {
            setFilled(env);
            setCreds({});
          }}
        />
      ) : (
        <>
          <p style={{ margin: 0, fontSize: pxToRem(12) }}>{t("envreq.loginGot")}</p>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: pxToRem(12) }}>
            {Object.keys(filled).map((name) => (
              <li key={name} style={{ fontFamily: "var(--font-mono)" }}>
                {name}
              </li>
            ))}
          </ul>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button type="button" className="btn" data-variant="secondary" onClick={attemptClose}>
              {t("env.cancel")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-testid="env-login-save"
              disabled={save.isPending}
              onClick={() => save.mutate(filled)}
            >
              {t("env.save")}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}
