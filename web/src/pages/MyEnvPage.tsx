/**
 * "My environment variables" (`docs/plan-personal-env.md`): a person's values for
 * every item.
 *
 * An item uses a name from here only when its policy asks for a personal value
 * (Private first / Private only); its own value for that item wins. So this is
 * where a token goes once: sign in here, or on an item's Env panel tab
 * "Private(跨workspace)" (the same values), and every item that asks for it
 * gets the new one on its next run. The panel's Private tab is that item's
 * own values (`plan-personal-env` A20).
 *
 * A sign-in saves at once — it is the deliberate act, and a token waiting for a
 * second button is a token the next run does not have. A typed value is added
 * with its own button. Each value says when it was last set, the one hint there
 * is about when a token is due.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PERSONAL_ENV_WRITES, personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { qk } from "../api/queryKeys";
import { Logins } from "../components/EnvVarsModal";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

const DAY = 86_400_000;
const MONO = { fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: pxToRem(12) };
const MUTED = { fontSize: pxToRem(12), color: "var(--text-paper-d)" } as const;

/** What a tool can be given: the same rule `.env` parsing uses for a name. */
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function MyEnvPage({
  client = personalEnvApi,
}: {
  client?: Pick<PersonalEnvClient, "get" | "put" | "providers" | "resolve">;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const mine = useQuery({ queryKey: qk.personalEnv(), queryFn: () => client.get() });
  const providers = useQuery({ queryKey: qk.myEnvProviders(), queryFn: () => client.providers() });
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [value, setValue] = useState("");

  const values = mine.data?.values ?? {};
  const updated = mine.data?.updated ?? {};

  // Every write is the whole set, built on what the server last said — a write
  // is never based on a form that could be stale.
  const save = useMutation({
    scope: PERSONAL_ENV_WRITES,
    mutationFn: async (change: (current: Record<string, string>) => Record<string, string>) => {
      const current = (
        // `staleTime: 0`: a re-read inside the app's 30s window would otherwise
        // return the cached row, and a whole-row PUT would drop what another
        // tab saved meanwhile (round 1, F2).
        await queryClient.fetchQuery({
          queryKey: qk.personalEnv(),
          queryFn: () => client.get(),
          staleTime: 0,
        })
      ).values;
      return client.put(change(current));
    },
    onSuccess: (saved) => queryClient.setQueryData(qk.personalEnv(), saved),
  });

  const when = (n: string) => {
    const at = updated[n];
    if (!at) return "";
    const days = Math.floor((Date.now() - at) / DAY);
    return days <= 0 ? t("myEnv.updatedToday") : t("myEnv.updatedDays", { days: String(days) });
  };

  const canAdd = NAME.test(name) && value !== "" && !save.isPending;

  return (
    <div className="page">
      <h1>{t("myEnv.heading")}</h1>
      <p style={MUTED}>{t("myEnv.desc")}</p>

      <section aria-labelledby="my-env-values" style={{ display: "grid", gap: 8 }}>
        <h2 id="my-env-values">{t("myEnv.values")}</h2>
        {mine.isPending ? (
          <p style={MUTED}>…</p>
        ) : mine.isLoadingError ? (
          // Not "nothing yet": a row it could not read is not an empty one.
          // Only when it never read one — a save's re-read that fails keeps
          // what was already shown (round 3, F1).
          <p data-testid="my-env-load-failed" role="alert" style={MUTED}>
            {t("myEnv.loadFailed")}
          </p>
        ) : Object.keys(values).length === 0 ? (
          <p data-testid="my-env-empty" style={MUTED}>
            {t("myEnv.empty")}
          </p>
        ) : (
          Object.keys(values).map((n) => (
            <Row
              key={n}
              name={n}
              value={values[n]}
              when={when(n)}
              onRemove={() =>
                save.mutate((current) => Object.fromEntries(Object.entries(current).filter(([k]) => k !== n)))
              }
            />
          ))
        )}

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <input
            data-testid="my-env-new-name"
            className="input"
            placeholder={t("myEnv.name")}
            aria-label={t("myEnv.name")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            style={MONO}
          />
          <input
            data-testid="my-env-new-value"
            className="input"
            type="password"
            placeholder={t("myEnv.value")}
            aria-label={t("myEnv.value")}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            style={MONO}
          />
          <button
            type="button"
            className="btn"
            data-variant="primary"
            data-size="sm"
            data-testid="my-env-add"
            disabled={!canAdd}
            onClick={() => {
              const n = name;
              const v = value;
              save.mutate((current) => ({ ...current, [n]: v }), {
                onSuccess: () => {
                  setName("");
                  setValue("");
                },
              });
            }}
          >
            {t("myEnv.add")}
          </button>
        </div>
      </section>

      {(providers.data ?? []).length > 0 && (
        <section aria-labelledby="my-env-sign-ins" style={{ display: "grid", gap: 8, marginTop: 16 }}>
          <h2 id="my-env-sign-ins">{t("myEnv.signIns")}</h2>
          <Logins
            offered={providers.data ?? []}
            creds={creds}
            setCreds={setCreds}
            exchange={(id, credentials) => client.resolve(id, credentials)}
            onFilled={(env) => save.mutateAsync((current) => ({ ...current, ...env }))}
          />
        </section>
      )}
    </div>
  );
}

function Row({
  name,
  value,
  when,
  onRemove,
}: {
  name: string;
  value: string;
  when: string;
  onRemove: () => void;
}) {
  const t = useT();
  const [revealed, setRevealed] = useState(false);
  return (
    <div
      data-testid={`my-env-row-${name}`}
      style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
    >
      <span style={{ ...MONO, minWidth: 0, overflowWrap: "anywhere" }}>{name}</span>
      <span style={{ ...MONO, color: "var(--text-paper-d)", overflowWrap: "anywhere" }}>
        {revealed ? value : "••••••"}
      </span>
      {when && <span style={MUTED}>{when}</span>}
      <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          aria-pressed={revealed}
          onClick={() => setRevealed((r) => !r)}
        >
          {t(revealed ? "env.hide" : "env.reveal")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="my-env-remove"
          onClick={onRemove}
        >
          {t("myEnv.remove")}
        </button>
      </span>
    </div>
  );
}
