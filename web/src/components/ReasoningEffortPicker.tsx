/** Per-message reasoning-effort selector (Default / Low / Medium / High),
 * sticky in localStorage. The send hooks read the current value at send time;
 * Default sends nothing → the model's own default. Has no visible effect on
 * models that don't support reasoning (the param is dropped server-side). */
import type { ReasoningEffort } from "../api/types";
import { useReasoningEffort } from "../lib/reasoningEffort";

export function ReasoningEffortPicker() {
  const [value, setValue] = useReasoningEffort();
  return (
    <label
      title="Reasoning effort (reasoning models only)"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 12,
        color: "var(--text-paper-d)",
      }}
    >
      <span>Effort</span>
      <select
        value={value ?? ""}
        onChange={(e) => setValue((e.target.value || null) as ReasoningEffort | null)}
        style={{
          fontSize: 12,
          padding: "2px 6px",
          borderRadius: 6,
          border: "1px solid var(--rule)",
          background: "var(--paper)",
          color: "var(--text-paper)",
        }}
      >
        <option value="">Default</option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
      </select>
    </label>
  );
}
