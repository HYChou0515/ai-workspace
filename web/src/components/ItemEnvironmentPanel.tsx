/**
 * The item page's environment panel — two halves with different preconditions.
 *
 * "Is my environment running, and close it" is about a MACHINE. It is worth
 * having on a deploy that caps nobody, so it is always drawn.
 *
 * "How much of my budget is this spending, and how big may it be" is about a
 * BUDGET, and only means anything where one exists. On a deploy with no
 * per-person limits — which is the shipped default, and therefore the state
 * every deployment is in until someone configures it — drawing `0 / 0` would be
 * noise and offering a dial with an unlimited ceiling would be worse. So that
 * half is simply absent, and the useful half still shows up.
 *
 * The item leads and the person's total sits beside it: you are on this item's
 * page, so it is the subject, and the total is the context that makes a refusal
 * explicable.
 */

import { useState } from "react";

import type { ItemEnvironment } from "../api/itemEnvironment";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";

/** The owner's own ceiling and what they currently hold. `null` when this
 *  deploy caps nobody — which is a different thing from "zero used". */
export type EnvBudget = {
  cpu: number;
  memoryBytes: number;
  cpuInUse: number;
  memoryInUse: number;
};

export type ItemEnvironmentPanelProps = {
  env: ItemEnvironment;
  budget: EnvBudget | null;
  /** Whether this viewer may spend the owner's quota — `change_permission`.
   *  Read-only for everyone else, deliberately: a collaborator who gets refused
   *  needs to SEE the number that refused them. */
  canEdit: boolean;
  onClose?: () => void;
  /** One dimension at a time. An absent key means "not touched", which the
   *  caller turns into "keep what is stored" — the distinction that stops a cpu
   *  edit from clearing memory. */
  onSave?: (edit: { cpuCores?: number | null; memory?: string | null }) => void;
};

function Meter({ used, limit }: { used: number; limit: number }) {
  if (!limit) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return (
    <div
      className="meter"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="meter-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ItemEnvironmentPanel({
  env,
  budget,
  canEdit,
  onClose,
  onSave,
}: ItemEnvironmentPanelProps) {
  const t = useT();
  const [draft, setDraft] = useState<string>(
    env.statedCpuCores === null ? "" : String(env.statedCpuCores),
  );
  const [memoryDraft, setMemoryDraft] = useState<string>(
    env.statedMemoryBytes === null ? "" : String(env.statedMemoryBytes),
  );

  const stated = env.statedCpuCores;
  const effective = env.effectiveCpuCores;
  // A dial this deploy will not honour is a promise, not a control — #712's
  // lesson one layer up, and worse here because a PERSON set the number.
  // `null` covers "caps nothing" and "could not ask" alike, because the backend
  // reports an unreachable host identically to one that caps nothing.
  const enforced = env.enforcedCpuCores !== null;
  // Held down when somebody asked for more than they may have. Both numbers are
  // shown, and which limit bound is named: showing the smaller one alone makes
  // the panel disagree with what the person typed, with nothing to explain it.
  const clamped = stated !== null && effective !== null && effective < stated;
  // Which limit bound comes from the SERVER. Deriving it here meant comparing
  // the effective figure — clamped against the OWNER's quota — with the
  // viewer's own, and for a `change_permission` delegate those are different
  // people: the comparison was normally false, so the panel blamed the App and
  // sent them to change a setting that was not holding them.
  const boundByQuota = env.boundBy === "quota";

  return (
    <section className="item-environment" aria-label={t("itemenv.heading")}>
      <h3>{t("itemenv.heading")}</h3>

      {/* ── the machine half: always drawn ── */}
      <p data-testid="environment-status" className="summary">
        <span className="gauge-label">
          {env.running ? t("itemenv.status.running") : t("itemenv.status.idle")}
        </span>
        {env.running ? (
          <span data-testid="this-item-usage" className="gauge-value">
            {effective === null ? "—" : effective}
            {env.effectiveMemoryBytes ? ` · ${formatBytes(env.effectiveMemoryBytes)}` : ""}
          </span>
        ) : null}
      </p>
      {env.running && canEdit ? (
        <>
          <button type="button" data-testid="close-environment" onClick={onClose}>
            {t("itemenv.close")}
          </button>
          <p className="detail">{t("itemenv.close.hint")}</p>
        </>
      ) : null}

      {/* ── the budget half: only where a budget exists ── */}
      {budget === null ? null : (
        <>
          <h4>{t("itemenv.size.heading")}</h4>
          <p className="summary">
            {/* Never a bare number: an unset value shows what it resolves to AND
                that it is a default, or it reads as something the person chose. */}
            <span data-testid="cpu-value" className="gauge-value">
              {effective === null ? "—" : String(effective)}
            </span>
            <span data-testid="cpu-origin" className="detail">
              {stated === null ? t("itemenv.size.default") : t("itemenv.size.stated")}
            </span>
            {stated === null ? null : (
              <button
                type="button"
                data-testid="reset-cpu"
                disabled={!canEdit || env.running}
                onClick={() => {
                  setDraft("");
                  onSave?.({ cpuCores: null });
                }}
              >
                {t("itemenv.size.reset")}
              </button>
            )}
          </p>

          {clamped ? (
            <p data-testid="cpu-clamped" className="detail">
              {t(boundByQuota ? "itemenv.size.clamped.quota" : "itemenv.size.clamped.app", {
                stated: String(stated),
                effective: String(effective),
              })}
            </p>
          ) : null}

          {enforced ? null : (
            <p data-testid="cpu-unenforced" className="detail">
              {t("itemenv.unenforced")}
            </p>
          )}
          {!enforced ? null : (
          <input
            data-testid="cpu-input"
            type="number"
            min={0}
            step={0.5}
            value={draft}
            // Locked while it runs, because there is no resize: the size is
            // applied when the sandbox is created. A field that accepted a
            // change now would be promising something the protocol cannot do.
            disabled={!canEdit || env.running}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => onSave?.({ cpuCores: draft === "" ? null : Number(draft) })}
          />
          )}
          {canEdit ? null : <p className="detail">{t("itemenv.readonly")}</p>}

          {/* Memory had no control at all — the field existed on the item, the
              route accepted it, and nothing could set it. P9's SIGKILL note
              even tells people to come here and look at it. */}
          <p className="summary">
            <span data-testid="memory-value" className="gauge-value">
              {env.effectiveMemoryBytes === null ? "—" : formatBytes(env.effectiveMemoryBytes)}
            </span>
            <span data-testid="memory-origin" className="detail">
              {env.statedMemoryBytes === null ? t("itemenv.size.default") : t("itemenv.size.stated")}
            </span>
          </p>
          {env.enforcedMemoryBytes === null ? null : (
            <input
              data-testid="memory-input"
              value={memoryDraft}
              placeholder="512M"
              aria-label={t("resources.memory")}
              disabled={!canEdit || env.running}
              onChange={(e) => setMemoryDraft(e.target.value)}
              onBlur={() => onSave?.({ memory: memoryDraft === "" ? null : memoryDraft })}
            />
          )}

          <div data-testid="budget-gauge" className="gauge">
            <p className="summary">
              <span className="gauge-label">{t("itemenv.usage.total")}</span>
              <span className="gauge-value">
                {budget.cpuInUse} / {budget.cpu}
              </span>
            </p>
            <Meter used={budget.cpuInUse} limit={budget.cpu} />
          </div>
        </>
      )}
    </section>
  );
}
