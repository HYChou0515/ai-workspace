/**
 * The login a `request_env` card's 登入 button opens
 * (docs/plan-env-request-card.md N6): the environment panel's own login form —
 * the same `Logins`, so it looks and behaves the same — on its own, at its
 * first field, instead of the whole panel with the form somewhere inside it.
 *
 * As in the panel (#750), what the login returns is shown and stored only when
 * the person presses Save: into THEIR values for this item, the rest of that
 * layer kept — or, with `saveTo="personal"`, into their values for every item
 * (`docs/plan-env-token-refresh.md` D6: a dead token signs in back into the
 * layer it lived in).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api as defaultApi } from "../api";
import { PERSONAL_ENV_WRITES, personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient, EnvProvider } from "../api/types";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { isBlank } from "../lib/envLayers";
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
  personalClient = personalEnvApi,
  saveTo = "private",
}: {
  slug: string;
  itemId: string;
  provider: EnvProvider;
  onClose: () => void;
  client?: Pick<ApiClient, "resolveEnvProvider">;
  privateClient?: Pick<PrivateEnvClient, "get" | "put">;
  personalClient?: Pick<PersonalEnvClient, "get" | "put">;
  /** Which of the person's layers Save writes: this item's (the default), or
   * their values for every item. */
  saveTo?: "private" | "personal";
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
  // My values for every item: one queue with every other writer of that row
  // in this tab (plan-personal-env A15), so two writes do not race.
  const savePersonal = useMutation({
    scope: PERSONAL_ENV_WRITES,
    mutationFn: async (env: Record<string, string>) => {
      const now = await personalClient.get();
      return personalClient.put({ ...now.values, ...env });
    },
    onSuccess: (stored) => {
      queryClient.setQueryData(qk.personalEnv(), stored);
      onClose();
    },
  });
  const saving = saveTo === "personal" ? savePersonal : save;
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
      // Top-anchored: the result view is shorter than the form, and centred
      // the dialog moved down under the pointer that had just pressed submit.
      align="top"
      width={420}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{title}</strong>
      {filled === null ? (
        <Logins
          offered={[provider]}
          initialDialog={provider.id}
          formOnly
          creds={creds}
          setCreds={setCreds}
          exchange={(id, values) => client.resolveEnvProvider(slug, itemId, id, values)}
          onCancel={attemptClose}
          onFilled={(env) => {
            // A blank is not a value (plan N5): not listed as obtained, not stored.
            setFilled(Object.fromEntries(Object.entries(env).filter(([, v]) => !isBlank(v))));
            setCreds({});
          }}
        />
      ) : (
        <>
          <p style={{ margin: 0, fontSize: pxToRem(12) }}>
            {t(saveTo === "personal" ? "envreq.loginGotPersonal" : "envreq.loginGot")}
          </p>
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
              disabled={saving.isPending}
              onClick={() => saving.mutate(filled)}
            >
              {t("env.save")}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}
