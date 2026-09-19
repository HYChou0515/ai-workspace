/**
 * The body of an item's Sandbox modal — presentational. It draws three
 * things and decides none of them:
 *
 *  1. the status row — running or not, the size it is running at, and the one
 *     action that changes that (Close sandbox), in the shape `/my-resources`
 *     draws its live rows;
 *  2. the size fields — CPU and memory, side by side, each with its label, its
 *     input, and a helper line saying what is in effect and where the number
 *     came from (a default resolved from the owner's quota, or something a
 *     person set — and if set, why less is in effect: the App's ceiling or the
 *     owner's quota held it down);
 *  3. the owner's totals — CPU and memory as the same tiles `/my-resources`
 *     draws, so a number here looks like the same number there.
 *
 * The DRAFTS belong to the modal, which also owns Save. This panel only
 * reports edits (`onDraft`); nothing here writes. The earlier panel saved on
 * blur, and the modal around it spent thirty lines explaining why that made
 * Escape and ✕ behave differently across browsers. A field that only edits a
 * draft has no such question to answer.
 *
 * Fields are LOCKED while the sandbox runs: there is no resize, the size is
 * applied when the sandbox is created, and a field that accepted a change now
 * would promise something the protocol cannot do. The hint under the status
 * row says so. `canEdit` is the viewer's `change_permission` — the verb that
 * decides who may spend the OWNER's quota; everyone else sees the numbers,
 * greyed, and a sentence saying why.
 *
 * Which ceiling held a stated size down is answered by the SERVER
 * (`cpuBoundBy`), not by comparing figures here: the effective number is
 * clamped against the owner's quota, and for a delegate the viewer's quota is
 * somebody else's — the comparison was normally false, so the panel blamed
 * the App and sent people to change a setting that was not holding them.
 */

import type { ItemEnvironment } from "../api/itemEnvironment";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";
import { Gauge } from "./Gauge";

export type EnvBudget = {
  cpu: number;
  memoryBytes: number;
  cpuInUse: number;
  memoryInUse: number;
};

/** The two fields as the person is typing them. `""` = "use the default". */
export type SizeDraft = { cpu: string; memory: string };

export type ItemEnvironmentPanelProps = {
  env: ItemEnvironment;
  budget: EnvBudget | null;
  canEdit: boolean;
  draft: SizeDraft;
  onDraft: (draft: SizeDraft) => void;
  /** Close the RUNNING sandbox (not the panel). */
  onCloseSandbox?: () => void;
};

export function ItemEnvironmentPanel({
  env,
  budget,
  canEdit,
  draft,
  onDraft,
  onCloseSandbox,
}: ItemEnvironmentPanelProps) {
  const t = useT();
  const locked = !canEdit || env.running;

  const cpuStated = env.statedCpuCores;
  const cpuEffective = env.effectiveCpuCores;
  const cpuEnforced = env.enforcedCpuCores !== null;
  const cpuClamped = cpuStated !== null && cpuEffective !== null && cpuEffective < cpuStated;

  const memStated = env.statedMemoryBytes;
  const memEffective = env.effectiveMemoryBytes;
  const memEnforced = env.enforcedMemoryBytes !== null;
  const memClamped = memStated !== null && memEffective !== null && memEffective < memStated;

  const cores = (n: number) =>
    t(n === 1 ? "resources.live.cores_one" : "resources.live.cores", { n });

  return (
    <div className="item-environment">
      {/* ── status row: always drawn ── */}
      <div className="env-status" data-testid="environment-status" data-running={env.running}>
        <span className={`live-dot${env.running ? "" : " live-dot--idle"}`} aria-hidden="true" />
        <span className="env-status__label">
          {env.running ? t("itemenv.status.running") : t("itemenv.status.idle")}
        </span>
        {env.running ? (
          <span data-testid="this-item-usage" className="env-status__detail detail">
            {cpuEffective === null ? "" : cores(cpuEffective)}
            {cpuEffective !== null && memEffective ? " · " : ""}
            {memEffective ? formatBytes(memEffective) : ""}
          </span>
        ) : null}
        {env.running && canEdit ? (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="close-environment"
            onClick={onCloseSandbox}
          >
            {t("itemenv.close")}
          </button>
        ) : null}
      </div>
      {env.running && canEdit ? <p className="detail env-hint">{t("itemenv.close.hint")}</p> : null}

      {/* ── size + totals: only where a budget exists ── */}
      {budget === null ? null : (
        <>
          <h4 className="env-heading">{t("itemenv.size.heading")}</h4>
          <div className="env-fields">
            <div className="env-field">
              <label htmlFor="itemenv-cpu">{t("itemenv.field.cpu")}</label>
              {cpuEnforced ? (
                <input
                  className="input"
                  id="itemenv-cpu"
                  data-testid="cpu-input"
                  type="number"
                  min={0}
                  step={0.5}
                  inputMode="decimal"
                  value={draft.cpu}
                  placeholder={cpuEffective === null ? "" : String(cpuEffective)}
                  disabled={locked}
                  onChange={(e) => onDraft({ ...draft, cpu: e.target.value })}
                />
              ) : (
                <p data-testid="cpu-unenforced" className="detail">
                  {t("itemenv.unenforced")}
                </p>
              )}
              <p className="detail env-field__origin">
                {/* Never a bare number: an unset value shows what it resolves
                    to AND that it is a default, or it reads as chosen. */}
                <span data-testid="cpu-value">
                  {cpuEffective === null ? "—" : cores(cpuEffective)}
                </span>
                {" · "}
                <span data-testid="cpu-origin">
                  {cpuStated === null ? t("itemenv.size.default") : t("itemenv.size.stated")}
                </span>
                {cpuStated === null || locked ? null : (
                  <>
                    {" · "}
                    <button
                      type="button"
                      className="env-reset"
                      data-testid="reset-cpu"
                      onClick={() => onDraft({ ...draft, cpu: "" })}
                    >
                      {t("itemenv.size.reset")}
                    </button>
                  </>
                )}
              </p>
              {cpuClamped ? (
                <p data-testid="cpu-clamped" className="detail env-field__note">
                  {t(env.cpuBoundBy === "quota" ? "itemenv.size.clamped.quota" : "itemenv.size.clamped.app", {
                    stated: String(cpuStated),
                    effective: String(cpuEffective),
                  })}
                </p>
              ) : null}
            </div>

            <div className="env-field">
              <label htmlFor="itemenv-memory">{t("resources.memory")}</label>
              {memEnforced ? (
                <input
                  className="input"
                  id="itemenv-memory"
                  data-testid="memory-input"
                  value={draft.memory}
                  placeholder={memEffective ? formatBytes(memEffective) : "512M"}
                  disabled={locked}
                  onChange={(e) => onDraft({ ...draft, memory: e.target.value })}
                />
              ) : (
                <p data-testid="memory-unenforced" className="detail">
                  {t("itemenv.unenforced.memory")}
                </p>
              )}
              <p className="detail env-field__origin">
                <span data-testid="memory-value">
                  {memEffective === null ? "—" : formatBytes(memEffective)}
                </span>
                {" · "}
                <span data-testid="memory-origin">
                  {memStated === null ? t("itemenv.size.default") : t("itemenv.size.stated")}
                </span>
                {memStated === null || locked ? null : (
                  <>
                    {" · "}
                    <button
                      type="button"
                      className="env-reset"
                      data-testid="reset-memory"
                      onClick={() => onDraft({ ...draft, memory: "" })}
                    >
                      {t("itemenv.size.reset")}
                    </button>
                  </>
                )}
              </p>
              {memClamped && memStated !== null && memEffective !== null ? (
                <p data-testid="memory-clamped" className="detail env-field__note">
                  {t(
                    env.memoryBoundBy === "quota"
                      ? "itemenv.memory.clamped.quota"
                      : "itemenv.memory.clamped.app",
                    { stated: formatBytes(memStated), effective: formatBytes(memEffective) },
                  )}
                </p>
              ) : null}
            </div>
          </div>
          {canEdit ? null : <p className="detail env-hint">{t("itemenv.readonly")}</p>}

          <h4 className="env-heading">{t("itemenv.usage.total")}</h4>
          <div data-testid="budget-gauge" className="stat-row" role="group" aria-label={t("itemenv.usage.total")}>
            <Gauge label={t("resources.gauge.cpu")} used={budget.cpuInUse} limit={budget.cpu} format={String} />
            <Gauge
              label={t("resources.memory")}
              used={budget.memoryInUse}
              limit={budget.memoryBytes}
              format={formatBytes}
            />
          </div>
        </>
      )}
    </div>
  );
}
