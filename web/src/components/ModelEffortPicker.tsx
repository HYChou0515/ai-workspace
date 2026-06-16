/**
 * ModelEffortPicker — the composer's combined model + effort control
 * (design handoff 3.0). One chip in the input row (`✨ name | effort ▾`)
 * opening an upward popover:
 *
 *   - **Model** — every picker entry with its one-line blurb
 *     (`AgentConfig.description`); the first entry is the deploy's
 *     default. Selection SEMANTICS belong to the caller: the RCA
 *     surface persists the pick onto the investigation, the KB surface
 *     sends it per message — this component only reports the click.
 *   - **Reasoning effort** — the shared sticky value
 *     (`lib/reasoningEffort`) both surfaces read at send time. "Auto"
 *     = don't send the param (model's own default).
 *   - **Knowledge search depth** (KB surface, `retrieval` prop) — the
 *     quick/standard/thorough dial from `lib/kbEnhancementMode`.
 */

import { useState } from "react";

import type { ReasoningEffort } from "../api/types";
import {
  PRESETS,
  useKbEnhancementMode,
  useKbWikiToggle,
  type CustomEnhancements,
  type EnhancementMode,
  type EnhancementSelection,
} from "../lib/kbEnhancementMode";
import { useReasoningEffort } from "../lib/reasoningEffort";
import { Icon } from "./Icon";

export type PickerEntry = {
  name: string;
  model: string;
  description?: string;
};

const EFFORTS: { id: ReasoningEffort | null; label: string; note: string }[] = [
  { id: null, label: "Auto", note: "The model's own default" },
  { id: "low", label: "Low", note: "Quick answer, lighter thinking" },
  { id: "medium", label: "Med", note: "Balanced depth" },
  { id: "high", label: "High", note: "Exhaustive — slower, more thorough" },
];

const DEPTHS: { id: Exclude<EnhancementMode, "custom">; label: string; note: string }[] = [
  { id: "quick", label: "Quick", note: "Fastest — searches your words as-is" },
  { id: "standard", label: "Standard", note: "Light query expansion (recommended)" },
  { id: "thorough", label: "Thorough", note: "Widest search — slowest, highest recall" },
];

function chipEffortLabel(effort: ReasoningEffort | null): string {
  if (effort === null) return "auto";
  return effort === "medium" ? "med" : effort;
}

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
function Segments<T extends string | null>({
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
                fontSize: 12,
                fontWeight: on ? 600 : 400,
                cursor: "pointer",
              }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-paper-d)", marginTop: 6, lineHeight: 1.4 }}>
        {active.note}.
      </div>
    </>
  );
}

/** The old depth picker's Advanced disclosure, preserved: exact
 * expand / hyde / rerank values. Editing one auto-flips the mode to
 * "custom" (or snaps back when it matches a preset) — lib logic. */
function DepthSliders({
  sel,
  onSlider,
}: {
  sel: EnhancementSelection;
  onSlider: (knob: keyof CustomEnhancements, value: number | boolean) => void;
}) {
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
        fontSize: 11,
        color: "var(--text-paper-d)",
      }}
    >
      {(
        [
          { knob: "expand", title: "Alternative query phrasings to generate (0 = off)" },
          { knob: "hyde", title: "Hypothetical-document probes to embed (0 = off)" },
        ] as const
      ).map(({ knob, title }) => (
        <span key={knob} style={row}>
          <span title={title}>{knob}</span>
          <input
            type="range"
            min={0}
            max={10}
            step={1}
            value={Math.min(10, Math.max(0, display[knob]))}
            aria-label={`${knob} value`}
            title={title}
            onChange={(e) => onSlider(knob, Number(e.target.value))}
          />
          <span style={{ minWidth: 18, textAlign: "right" }}>{display[knob]}</span>
        </span>
      ))}
      <span title="LLM-rerank the merged candidate set">rerank</span>
      <span />
      <input
        type="checkbox"
        checked={display.rerank}
        aria-label="rerank on"
        title="LLM-rerank the merged candidate set"
        onChange={(e) => onSlider("rerank", e.target.checked)}
      />
    </div>
  );
}

export function ModelEffortPicker({
  models,
  selectedName,
  onSelectModel,
  retrieval = false,
  wikiAvailable = false,
}: {
  models: PickerEntry[];
  /** Active entry name; null = the deploy default (first entry). */
  selectedName: string | null;
  onSelectModel: (name: string) => void;
  /** KB surface: include the knowledge-search depth section. */
  retrieval?: boolean;
  /** Issue #50: show the "Search the wiki" toggle (a collection in scope
   * builds one). Hidden when no collection has a wiki — the toggle would do
   * nothing. */
  wikiAvailable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [effort, setEffort] = useReasoningEffort();
  const [depthSel, setDepthMode, setDepthSlider] = useKbEnhancementMode();
  const [searchWiki, setSearchWiki] = useKbWikiToggle();

  if (models.length === 0) return null;
  const active = models.find((m) => m.name === selectedName) ?? models[0]!;

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        aria-label="Model and effort"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          height: 28,
          padding: "0 8px",
          border: `1px solid ${open ? "var(--accent)" : "var(--paper-3)"}`,
          borderRadius: 6,
          background: open ? "var(--accent-soft)" : "var(--white)",
          cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        <Icon name="sparkle" size={13} color="var(--accent)" />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink)" }}>
          {active.name}
        </span>
        <span style={{ width: 1, height: 14, background: "var(--paper-3)" }} />
        <span style={{ fontSize: 12, color: "var(--text-paper-d)" }}>
          {chipEffortLabel(effort)}
        </span>
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
            aria-label="Model and effort options"
            style={{
              position: "absolute",
              bottom: "calc(100% + 8px)",
              right: 0,
              width: 320,
              background: "var(--paper)",
              border: "1px solid var(--paper-3)",
              borderRadius: 10,
              boxShadow: "0 12px 40px rgba(20,22,28,.16)",
              zIndex: 81,
              overflow: "hidden",
            }}
          >
            <div style={{ padding: "10px 12px 4px" }}>
              <SectionLabel>Model</SectionLabel>
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
                            fontFamily: "var(--font-mono)",
                            fontSize: 12.5,
                            color: "var(--ink)",
                            fontWeight: on ? 600 : 400,
                          }}
                        >
                          {m.name}
                        </span>
                        {i === 0 && (
                          <span
                            style={{
                              fontSize: 10,
                              padding: "1px 6px",
                              borderRadius: 999,
                              background: "var(--accent-soft)",
                              color: "var(--accent-h)",
                              fontWeight: 600,
                            }}
                          >
                            default
                          </span>
                        )}
                        <span style={{ flex: 1 }} />
                        <span
                          style={{
                            fontSize: 11,
                            color: "var(--text-paper-d2)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            maxWidth: 120,
                          }}
                        >
                          {m.model}
                        </span>
                      </div>
                      {m.description && (
                        <div
                          style={{
                            fontSize: 11,
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
              <SectionLabel>Reasoning effort</SectionLabel>
              <Segments options={EFFORTS} value={effort} onPick={setEffort} />
            </div>

            {retrieval && (
              <div style={{ borderTop: "1px solid var(--paper-3)", padding: "10px 12px" }}>
                <div style={{ display: "flex", alignItems: "baseline" }}>
                  <SectionLabel>Knowledge search depth</SectionLabel>
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
                      fontSize: 11,
                      padding: 0,
                    }}
                  >
                    {advanced ? "▾ Advanced" : "▸ Advanced"}
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
                  <div
                    style={{ fontSize: 11, color: "var(--text-paper-d)", marginTop: 4 }}
                  >
                    Customised below — picking a level above replaces it.
                  </div>
                )}
                {advanced && <DepthSliders sel={depthSel} onSlider={setDepthSlider} />}
                {wikiAvailable && (
                  <label
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      marginTop: 8,
                      fontSize: 13,
                      cursor: "pointer",
                    }}
                    title="Also consult the AI-maintained wiki for this question"
                  >
                    <input
                      type="checkbox"
                      checked={searchWiki}
                      onChange={(e) => setSearchWiki(e.target.checked)}
                    />
                    Search the wiki
                  </label>
                )}
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
              <span style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
                {effort === "high"
                  ? "Slower, more thorough"
                  : effort === "low"
                    ? "Fastest, lighter"
                    : "Balanced latency"}
              </span>
              <span style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => setOpen(false)}
                style={{
                  border: "none",
                  background: "none",
                  fontSize: 12,
                  color: "var(--accent-h)",
                  cursor: "pointer",
                  fontWeight: 500,
                  padding: 0,
                }}
              >
                Done
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
