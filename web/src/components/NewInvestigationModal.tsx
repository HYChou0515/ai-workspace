import { useEffect, useState } from "react";

import { api } from "../api";
import type { InvestigationInput, Severity } from "../api/types";

/**
 * "+ New investigation" modal. Simplified from the design per plan §9:
 * title (required) / description / topics chip-input / severity / product.
 * Owner + status are server-defaulted (owner = current user, status =
 * triaging) and not exposed in this form.
 */
export function NewInvestigationModal({
  open,
  onSubmit,
  onClose,
  initialTemplate,
}: {
  open: boolean;
  onSubmit: (input: InvestigationInput) => void;
  onClose: () => void;
  /** Preselect this template profile when opened from the Templates gallery. */
  initialTemplate?: string;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<Severity>("P2");
  const [product, setProduct] = useState("");
  const [topics, setTopics] = useState<string[]>([]);
  const [topicDraft, setTopicDraft] = useState("");
  const [templates, setTemplates] = useState<string[]>(["default"]);
  const [template, setTemplate] = useState("default");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api
      .listTemplates()
      .then((t) => alive && t.length > 0 && setTemplates(t))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [open]);

  // Preselect the template the gallery launched us with.
  useEffect(() => {
    if (open && initialTemplate) setTemplate(initialTemplate);
  }, [open, initialTemplate]);

  if (!open) return null;

  const commitTopic = () => {
    const t = topicDraft.trim();
    setTopicDraft("");
    if (!t) return;
    setTopics((prev) => (prev.includes(t) ? prev : [...prev, t]));
  };

  const canSubmit = title.trim().length > 0;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    onSubmit({
      title: title.trim(),
      description: description.trim(),
      severity,
      product: product.trim(),
      topics,
      templateProfile: template,
    });
  };

  return (
    <div
      role="dialog"
      aria-modal
      aria-labelledby="new-investigation-title"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,22,28,0.55)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        style={{
          width: 620,
          maxHeight: "90vh",
          background: "var(--paper)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-modal)",
          display: "flex",
          flexDirection: "column",
          fontFamily: "var(--font-body)",
        }}
      >
        <header
          style={{
            padding: "20px 24px 12px",
            borderBottom: "1px solid var(--paper-3)",
          }}
        >
          <div className="caps">New investigation</div>
          <h2
            id="new-investigation-title"
            style={{
              margin: "4px 0 0",
              fontFamily: "var(--font-display)",
              fontSize: "var(--text-display-sm)",
              lineHeight: "var(--leading-display-sm)",
              fontWeight: 800,
              letterSpacing: "-0.02em",
            }}
          >
            Start an RCA
          </h2>
        </header>

        <div
          className="scrollable"
          style={{ padding: 24, overflowY: "auto", display: "flex", flexDirection: "column", gap: 16 }}
        >
          <Field label="Title" required>
            <input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Reflow zone-3 drift on MX-7 board"
              style={inputStyle()}
            />
          </Field>

          <Field label="Description">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Initial brief — first signal, lots/units affected, theories so far…"
              rows={4}
              style={{ ...inputStyle(), resize: "vertical", minHeight: 90 }}
            />
          </Field>

          <Field label="Topics">
            <div
              style={{
                ...inputStyle(),
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                padding: 6,
                minHeight: 36,
                alignItems: "center",
              }}
            >
              {topics.map((t) => (
                <span
                  key={t}
                  style={{
                    background: "var(--paper-2)",
                    borderRadius: "var(--radius-chip)",
                    padding: "2px 8px",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    display: "inline-flex",
                    gap: 4,
                    alignItems: "center",
                  }}
                >
                  {t}
                  <button
                    type="button"
                    onClick={() => setTopics((prev) => prev.filter((x) => x !== t))}
                    aria-label={`remove topic ${t}`}
                    style={{ color: "var(--text-paper-d)", padding: "0 2px" }}
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                value={topicDraft}
                onChange={(e) => setTopicDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    commitTopic();
                  } else if (e.key === "Backspace" && topicDraft === "" && topics.length > 0) {
                    setTopics((prev) => prev.slice(0, -1));
                  }
                }}
                onBlur={commitTopic}
                placeholder={topics.length === 0 ? "type a tag, press Enter…" : ""}
                aria-label="topics"
                style={{
                  flex: 1,
                  minWidth: 120,
                  border: 0,
                  outline: "none",
                  background: "transparent",
                  fontSize: "var(--text-body-sm)",
                }}
              />
            </div>
          </Field>

          <Field label="Severity">
            <SeveritySegmented value={severity} onChange={setSeverity} />
          </Field>

          <Field label="Product">
            <input
              value={product}
              onChange={(e) => setProduct(e.target.value)}
              placeholder="e.g. MX-7 board"
              style={inputStyle()}
            />
          </Field>

          <Field label="Template">
            <select
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              style={inputStyle()}
            >
              {templates.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <footer
          style={{
            padding: "12px 24px",
            borderTop: "1px solid var(--paper-3)",
            background: "var(--paper-2)",
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
          }}
        >
          <button type="button" onClick={onClose} style={btnGhost()}>
            Cancel
          </button>
          <button type="submit" disabled={!canSubmit} style={btnPrimary(!canSubmit)}>
            Create &amp; ask agent
          </button>
        </footer>
      </form>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const id = label.toLowerCase().replace(/\s+/g, "-");
  // Inject the id into the first child via cloneElement-like wrapper:
  // simpler — render the label inside a <label> wrapping the child.
  return (
    <label htmlFor={id} style={{ display: "block" }}>
      <div
        style={{
          fontSize: "var(--text-body-sm)",
          fontWeight: 600,
          marginBottom: 4,
          color: "var(--text-paper)",
        }}
      >
        {label}
        {required && <span style={{ color: "var(--accent)" }}> *</span>}
      </div>
      {/* Children render their own input element; we pass `id` via aria-labelledby pattern. */}
      <FieldChildIdProxy id={id}>{children}</FieldChildIdProxy>
    </label>
  );
}

/**
 * Clones the first child element and sets `id` on it so the wrapping
 * <label> can use `htmlFor` correctly. Avoids hand-threading ids through
 * each Field invocation.
 */
function FieldChildIdProxy({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  if (
    typeof children === "object" &&
    children !== null &&
    "props" in children &&
    "type" in children
  ) {
    // It's a React element. Clone it with id (or keep existing).
    // Using a runtime clone to avoid a hard React import dependency in tests.
    const child = children as { props: Record<string, unknown> } & {
      type: unknown;
    };
    if (!child.props.id) {
      return <ChildWithId id={id}>{children}</ChildWithId>;
    }
  }
  return <>{children}</>;
}

import { cloneElement, isValidElement } from "react";

function ChildWithId({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  if (!isValidElement(children)) return <>{children}</>;
  // The form field element is the one we want to label.
  const child = children as React.ReactElement<{ id?: string }>;
  return cloneElement(child, { id });
}

function SeveritySegmented({
  value,
  onChange,
}: {
  value: Severity;
  onChange: (s: Severity) => void;
}) {
  const all: Severity[] = ["P0", "P1", "P2", "P3", "P4"];
  return (
    <div style={{ display: "flex", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)", overflow: "hidden", width: "fit-content" }}>
      {all.map((s) => {
        const active = s === value;
        return (
          <button
            key={s}
            type="button"
            onClick={() => onChange(s)}
            style={{
              padding: "6px 14px",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 600,
              borderRight: "1px solid var(--paper-3)",
              background: active ? "var(--accent)" : "var(--white)",
              color: active ? "var(--white)" : "var(--text-paper)",
            }}
          >
            {s}
          </button>
        );
      })}
    </div>
  );
}

function inputStyle(): React.CSSProperties {
  return {
    display: "block",
    width: "100%",
    padding: "8px 10px",
    border: "1px solid var(--paper-3)",
    borderRadius: "var(--radius-btn)",
    background: "var(--white)",
    fontSize: "var(--text-body-sm)",
    color: "var(--text-paper)",
    outline: "none",
  };
}

function btnGhost(): React.CSSProperties {
  return {
    height: 32,
    padding: "0 14px",
    border: "1px solid var(--paper-3)",
    borderRadius: "var(--radius-btn)",
    background: "transparent",
    fontSize: "var(--text-body-sm)",
  };
}

function btnPrimary(disabled: boolean): React.CSSProperties {
  return {
    height: 32,
    padding: "0 14px",
    border: 0,
    borderRadius: "var(--radius-btn)",
    background: disabled ? "var(--paper-3)" : "var(--accent)",
    color: disabled ? "var(--text-paper-d)" : "var(--white)",
    fontSize: "var(--text-body-sm)",
    fontWeight: 500,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}
