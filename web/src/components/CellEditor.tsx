/**
 * Code cell editor. v1 uses a plain textarea — clean abstraction so we
 * can swap to Monaco (@monaco-editor/react) or CodeMirror later without
 * touching callers. Per plan-frontend §8.
 */

import { useEffect, useRef } from "react";

export function CellEditor({
  value,
  onChange,
  language = "python",
  readOnly,
}: {
  value: string;
  onChange: (next: string) => void;
  language?: string;
  readOnly?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow with content; reset to natural size each render to honour
  // shrink-when-deleted.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0";
    el.style.height = `${Math.max(el.scrollHeight, 48)}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      readOnly={readOnly}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      data-language={language}
      style={{
        width: "100%",
        padding: 12,
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        background: "var(--paper)",
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        lineHeight: 1.55,
        color: "var(--text-paper)",
        resize: "none",
        outline: "none",
      }}
    />
  );
}
