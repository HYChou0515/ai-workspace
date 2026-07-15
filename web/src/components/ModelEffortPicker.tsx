/**
 * ModelEffortPicker — the composer's combined model + reasoning-depth control.
 * One chip in the input row (`✨ name | depth ▾`) opening an upward popover:
 *
 *   - **Model** — every picker entry with its one-line blurb
 *     (`AgentConfig.description`); the first entry is the deploy's default.
 *     The raw model id is NOT shown (#160) — operators name the entry. Selection
 *     SEMANTICS belong to the caller: the RCA surface persists the pick onto the
 *     investigation, the KB surface sends it per message — this component only
 *     reports the click.
 *   - **Reasoning depth** — the shared sticky value (`lib/reasoningEffort`) both
 *     surfaces read at send time. Three levels (low/medium/high), lightest by
 *     default; #160 removed the old "Auto" option.
 *   - **Knowledge search depth** (KB surface, `retrieval` prop) — the
 *     quick/standard/thorough dial from `lib/kbEnhancementMode`.
 *
 * All user-facing copy is routed through `lib/i18n` (#160).
 */

import { useState } from "react";

import type { ReasoningEffort } from "../api/types";
import { type MsgKey, useT } from "../lib/i18n";
import {
  PRESETS,
  useKbEnhancementMode,
  useKbWikiToggle,
  type CustomEnhancements,
  type EnhancementMode,
  type EnhancementSelection,
} from "../lib/kbEnhancementMode";
import { useReasoningEffort } from "../lib/reasoningEffort";
import { KB_SEARCH_MAX_UI_MAX, useKbSearchMax } from "../lib/kbSearchMax";
import { KB_WIKI_MAX_UI_MAX, useKbWikiMax } from "../lib/kbWikiMax";
import { Icon } from "./Icon";
import { pxToRem } from "../lib/pxToRem";

export type PickerEntry = {
  name: string;
  model: string;
  description?: string;
};

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="caps"
      style={{ fontSize: "var(--text-mono-caps)", color: "var(--text-paper-d)", marginBottom: 6 }}
    >
      {children}
    </div>
  );
}

/** Handoff-style segmented control (dark active segment). */
function Segments<T extends string>({
  options,
  value,
  onPick,
}: {
  options: { id: T; label: string; note: string }[];
  value: T;
  onPick: (id: T) => void;
}) {
  const active = options.find((o) => o.id === value) ?? options[0]!;
  return (
    <>
      <div
        style={{
          display: "flex",
          border: "1px solid var(--paper-3)",
          borderRadius: 6,
          padding: 3,
          background: "var(--white)",
          gap: 3,
        }}
      >
        {options.map((o) => {
          const on = o.id === value;
          return (
            <button
              key={String(o.id)}
              type="button"
              onClick={() => onPick(o.id)}
              style={{
                flex: 1,
                textAlign: "center",
                padding: "5px 0",
                borderRadius: 4,
                border: "none",
                background: on ? "var(--ink)" : "transparent",
                color: on ? "var(--text-dark)" : "var(--text-paper)",
                fontSize: pxToRem(12),
                fontWeight: on ? 600 : 400,
                cursor: "pointer",
              }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", marginTop: 6, lineHeight: 1.4 }}>
        {active.note}
      </div>
    </>
  );
}

/** The advanced search knobs (formerly raw expand / hyde / rerank), now in
 * plain language (#160). Editing one auto-flips the mode to "custom" (or snaps
 * back when it matches a preset) — lib logic. */
function DepthSliders({
  sel,
  onSlider,
}: {
  sel: EnhancementSelection;
  onSlider: (knob: keyof CustomEnhancements, value: number | boolean) => void;
}) {
  const t = useT();
  const display: CustomEnhancements =
    sel.mode === "custom" && sel.custom
      ? sel.custom
      : PRESETS[sel.mode === "custom" ? "standard" : sel.mode];
  const row: React.CSSProperties = { display: "contents" };
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto",
        gap: 6,
        alignItems: "center",
        marginTop: 8,
        padding: "6px 8px",
        border: "1px solid var(--paper-3)",
        borderRadius: 6,
        background: "var(--white)",
        fontSize: pxToRem(11),
        color: "var(--text-paper-d)",
      }}
    >
      {(
        [
          { knob: "expand", label: t("depth.expand"), title: t("depth.expand.title") },
          { knob: "hyde", label: t("depth.hyde"), title: t("depth.hyde.title") },
        ] as const
      ).map(({ knob, label, title }) => (
        <span key={knob} style={row}>
          <span title={title}>{label}</span>
          <input
            type="range"
            min={0}
            max={10}
            step={1}
            value={Math.min(10, Math.max(0, display[knob]))}
            aria-label={label}
            title={title}
            onChange={(e) => onSlider(knob, Number(e.target.value))}
          />
          <span style={{ minWidth: 18, textAlign: "right" }}>{display[knob]}</span>
        </span>
      ))}
      <span title={t("depth.rerank.title")}>{t("depth.rerank")}</span>
      <span />
      <input
        type="checkbox"
        checked={display.rerank}
        aria-label={t("depth.rerank")}
        title={t("depth.rerank.title")}
        onChange={(e) => onSlider("rerank", e.target.checked)}
      />
    </div>
  );
}

/** #334: per-message cap on how many times this reply searches the KB. A plain
 * stepper (independent of the quick/standard/thorough depth dial — those govern
 * how hard EACH search digs; this governs HOW MANY searches). 0 = don't search. */
function SearchMaxStepper({
  value,
  onChange,
  labelKey = "searchmax.label",
  titleKey = "searchmax.title",
  zeroKey = "searchmax.zero",
  decKey = "searchmax.dec",
  incKey = "searchmax.inc",
  max = KB_SEARCH_MAX_UI_MAX,
}: {
  value: number;
  onChange: (n: number) => void;
  labelKey?: MsgKey;
  titleKey?: MsgKey;
  zeroKey?: MsgKey;
  decKey?: MsgKey;
  incKey?: MsgKey;
  max?: number;
}) {
  const t = useT();
  const btn: React.CSSProperties = {
    border: "none",
    background: "transparent",
    color: "var(--text-paper)",
    cursor: "pointer",
    fontSize: pxToRem(14),
    lineHeight: 1,
    padding: "3px 9px",
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
      <span style={{ fontSize: pxToRem(12), color: "var(--text-paper)" }} title={t(titleKey)}>
        {t(labelKey)}
      </span>
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>{t(zeroKey)}</span>
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          border: "1px solid var(--paper-3)",
          borderRadius: 6,
          background: "var(--white)",
        }}
      >
        <button
          type="button"
          aria-label={t(decKey)}
          disabled={value <= 0}
          onClick={() => onChange(value - 1)}
          style={{ ...btn, opacity: value <= 0 ? 0.4 : 1, cursor: value <= 0 ? "default" : "pointer" }}
        >
          −
        </button>
        <span
          aria-label={t(labelKey)}
          style={{ minWidth: 22, textAlign: "center", fontSize: pxToRem(12), color: "var(--text-paper)" }}
        >
          {value}
        </span>
        <button
          type="button"
          aria-label={t(incKey)}
          disabled={value >= max}
          onClick={() => onChange(value + 1)}
          style={{
            ...btn,
            opacity: value >= max ? 0.4 : 1,
            cursor: value >= max ? "default" : "pointer",
          }}
        >
          ＋
        </button>
      </div>
    </div>
  );
}

export function ModelEffortPicker({
  models,
  selectedName,
  onSelectModel,
  retrieval = false,
  wikiAvailable = false,
  wikiBudget = false,
}: {
  models: PickerEntry[];
  /** Active entry name; null = the deploy default (first entry). */
  selectedName: string | null;
  onSelectModel: (name: string) => void;
  /** KB surface: include the knowledge-search depth section. */
  retrieval?: boolean;
  /** Issue #50: a collection in scope builds a wiki, so surface the wiki control
   * (hidden when no collection has a wiki — it would do nothing). */
  wikiAvailable?: boolean;
  /** #506: render the wiki control as a budgeted "max wiki searches" number picker
   * (KB chat: wiki is an in-agent tool) instead of the legacy boolean toggle (the
   * RCA composer, whose wiki flag still routes through the whole-page reader). */
  wikiBudget?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [effort, setEffort] = useReasoningEffort();
  const [depthSel, setDepthMode, setDepthSlider] = useKbEnhancementMode();
  const [searchWiki, setSearchWiki] = useKbWikiToggle();
  const [maxSearches, setMaxSearches] = useKbSearchMax();
  const [maxWiki, setMaxWiki] = useKbWikiMax();

  if (models.length === 0) return null;
  const active = models.find((m) => m.name === selectedName) ?? models[0]!;

  const EFFORTS: { id: ReasoningEffort; label: string; note: string }[] = [
    { id: "low", label: t("effort.low"), note: t("effort.low.note") },
    { id: "medium", label: t("effort.medium"), note: t("effort.medium.note") },
    { id: "high", label: t("effort.high"), note: t("effort.high.note") },
  ];
  const DEPTHS: { id: Exclude<EnhancementMode, "custom">; label: string; note: string }[] = [
    { id: "quick", label: t("depth.quick"), note: t("depth.quick.note") },
    { id: "standard", label: t("depth.standard"), note: t("depth.standard.note") },
    { id: "thorough", label: t("depth.thorough"), note: t("depth.thorough.note") },
  ];

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        aria-label={t("picker.aria")}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          minHeight: 28,
          padding: "0 8px",
          border: `1px solid ${open ? "var(--accent)" : "var(--paper-3)"}`,
          borderRadius: 6,
          background: open ? "var(--accent-soft)" : "var(--white)",
          cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        <Icon name="sparkle" size={13} color="var(--accent)" />
        <span style={{ fontSize: pxToRem(12), color: "var(--text-paper)" }}>{active.name}</span>
        <span style={{ width: 1, height: 14, background: "var(--paper-3)" }} />
        <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>{t(`effort.${effort}`)}</span>
        <Icon name="chev_d" size={11} color="var(--text-paper-d)" />
      </button>

      {open && (
        <>
          <div
            aria-hidden
            onClick={() => setOpen(false)}
            style={{ position: "fixed", inset: 0, zIndex: 80 }}
          />
          <div
            role="dialog"
            aria-label={t("picker.aria")}
            style={{
              position: "absolute",
              bottom: "calc(100% + 8px)",
              right: 0,
              width: 320,
              background: "var(--paper)",
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-card)",
              boxShadow: "0 12px 40px rgba(20,22,28,.16)",
              zIndex: 81,
              overflow: "hidden",
            }}
          >
            <div style={{ padding: "10px 12px 4px" }}>
              <SectionLabel>{t("picker.model")}</SectionLabel>
            </div>
            <div
              style={{
                padding: "0 8px 8px",
                display: "flex",
                flexDirection: "column",
                gap: 2,
                maxHeight: 260,
                overflowY: "auto",
              }}
            >
              {models.map((m, i) => {
                const on = m.name === active.name;
                return (
                  <div
                    key={m.name}
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelectModel(m.name)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") onSelectModel(m.name);
                    }}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 10,
                      padding: "8px 10px",
                      borderRadius: 6,
                      background: on ? "var(--accent-soft)" : "transparent",
                      cursor: "pointer",
                    }}
                  >
                    <span style={{ marginTop: 2, display: "inline-flex" }}>
                      <Icon
                        name={on ? "check" : "sparkle"}
                        size={14}
                        color={on ? "var(--accent-h)" : "var(--text-paper-d2)"}
                      />
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          style={{
                            fontSize: pxToRem(12.5),
                            color: "var(--text-paper)",
                            fontWeight: on ? 600 : 400,
                          }}
                        >
                          {m.name}
                        </span>
                        {i === 0 && (
                          <span
                            style={{
                              fontSize: pxToRem(10),
                              padding: "1px 6px",
                              borderRadius: 999,
                              background: "var(--accent-soft)",
                              color: "var(--accent-h)",
                              fontWeight: 600,
                            }}
                          >
                            {t("picker.default")}
                          </span>
                        )}
                        <span style={{ flex: 1 }} />
                      </div>
                      {m.description && (
                        <div
                          style={{
                            fontSize: pxToRem(11),
                            color: "var(--text-paper-d)",
                            marginTop: 2,
                            lineHeight: 1.4,
                          }}
                        >
                          {m.description}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <div style={{ borderTop: "1px solid var(--paper-3)", padding: "10px 12px" }}>
              <SectionLabel>{t("picker.effort")}</SectionLabel>
              <Segments options={EFFORTS} value={effort} onPick={setEffort} />
            </div>

            {retrieval && (
              <div style={{ borderTop: "1px solid var(--paper-3)", padding: "10px 12px" }}>
                <div style={{ display: "flex", alignItems: "baseline" }}>
                  <SectionLabel>{t("picker.depth")}</SectionLabel>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    onClick={() => setAdvanced((v) => !v)}
                    aria-expanded={advanced}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--text-paper-d)",
                      cursor: "pointer",
                      fontSize: pxToRem(11),
                      padding: 0,
                    }}
                  >
                    <span aria-hidden>{advanced ? "▾ " : "▸ "}</span>
                    {t("picker.advanced")}
                  </button>
                </div>
                <Segments
                  options={DEPTHS}
                  value={
                    (depthSel.mode === "custom" ? "standard" : depthSel.mode) as Exclude<
                      EnhancementMode,
                      "custom"
                    >
                  }
                  onPick={(id) => setDepthMode(id)}
                />
                {depthSel.mode === "custom" && (
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", marginTop: 4 }}>
                    {t("depth.custom.note")}
                  </div>
                )}
                <SearchMaxStepper value={maxSearches} onChange={setMaxSearches} />
                {advanced && <DepthSliders sel={depthSel} onSlider={setDepthSlider} />}
                {wikiAvailable &&
                  (wikiBudget ? (
                    // #506: wiki is a budgeted in-agent tool — a number picker like
                    // kb_search, not a routing toggle.
                    <SearchMaxStepper
                      value={maxWiki}
                      onChange={setMaxWiki}
                      labelKey="wikimax.label"
                      titleKey="wikimax.title"
                      zeroKey="wikimax.zero"
                      decKey="wikimax.dec"
                      incKey="wikimax.inc"
                      max={KB_WIKI_MAX_UI_MAX}
                    />
                  ) : (
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginTop: 8,
                        fontSize: pxToRem(13),
                        cursor: "pointer",
                      }}
                      title={t("picker.wiki.title")}
                    >
                      <input
                        type="checkbox"
                        checked={searchWiki}
                        onChange={(e) => setSearchWiki(e.target.checked)}
                      />
                      {t("picker.wiki")}
                    </label>
                  ))}
              </div>
            )}

            <div
              style={{
                borderTop: "1px solid var(--paper-3)",
                padding: "8px 12px",
                background: "var(--paper-2)",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Icon name="clock" size={11} color="var(--text-paper-d2)" />
              <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                {t(`picker.footer.${effort}`)}
              </span>
              <span style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => setOpen(false)}
                style={{
                  border: "none",
                  background: "none",
                  fontSize: pxToRem(12),
                  color: "var(--accent-h)",
                  cursor: "pointer",
                  fontWeight: 500,
                  padding: 0,
                }}
              >
                {t("picker.done")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
