/**
 * ReplayDialog (#51 P6) — re-runs one past AI step (a chat answer, a
 * tool decision, or a document's processing) against the CURRENT model
 * and shows the raw outcome beside what originally happened.
 *
 * Q4 (plan-sanity-checks): a replay is a pure probe. Nothing in the
 * conversation or document changes, and a tool the model wants to call
 * is shown as INTENT — never executed. The point is human comparison:
 * "is the model still capable of what this step needed?"
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  type ReplayApi,
  type ReplayOut,
  type ReplayToolCallOut,
  replayApi,
} from "../api/health";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";
import { ModalShell } from "./ModalShell";
import { pxToRem } from "../lib/pxToRem";

export type ReplayRequest =
  | { kind: "turn"; source: "rca" | "kb"; threadId: string; messageIndex: number }
  | { kind: "doc"; documentId: string };

function requestKey(req: ReplayRequest): readonly unknown[] {
  return req.kind === "turn"
    ? (["replay", "turn", req.source, req.threadId, req.messageIndex] as const)
    : (["replay", "doc", req.documentId] as const);
}

function CallIntent({ call, prefix }: { call: ReplayToolCallOut; prefix?: string }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(12),
        padding: "6px 10px",
        background: "var(--paper-2)",
        borderRadius: 6,
        overflowWrap: "anywhere",
      }}
    >
      {prefix && <span style={{ color: "var(--text-paper-d)" }}>{prefix} </span>}
      <strong>{call.name}</strong>({JSON.stringify(call.arguments)})
    </div>
  );
}

/** #69 observability: what the replay sent the model — put side by side
 * with the live turn's logged trace (`WORKSPACE_LLM_TRACE`) to spot a
 * config-induced difference (e.g. a stray `parallel_tool_calls`). */
function RequestPanel({ request }: { request: NonNullable<ReplayOut["request"]> }) {
  const rows: [string, string][] = [
    ["endpoint", request.endpoint],
    ["tools", request.tools.length ? request.tools.join(", ") : "—"],
    ["tool_choice", request.tool_choice],
    ["parallel_tool_calls", request.parallel_tool_calls],
  ];
  return (
    <div
      style={{
        marginTop: 10,
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(11),
        color: "var(--text-paper-d)",
        background: "var(--paper-2)",
        borderRadius: 6,
        padding: "6px 10px",
      }}
    >
      <div style={{ marginBottom: 4 }}>Sent to the model</div>
      {rows.map(([k, v]) => (
        <div key={k}>
          {k}={v}
        </div>
      ))}
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 14 }}>
      <div
        className="caps"
        style={{ fontSize: "var(--text-mono-caps)", color: "var(--text-paper-d)", marginBottom: 6 }}
      >
        {label}
      </div>
      {children}
    </section>
  );
}

export function ReplayDialog({
  request,
  onClose,
  client = replayApi,
}: {
  request: ReplayRequest;
  onClose: () => void;
  client?: ReplayApi;
}) {
  const t = useT();
  const [showThinking, setShowThinking] = useState(false);

  const { data, error, isPending } = useQuery<ReplayOut, Error>({
    queryKey: requestKey(request),
    queryFn: () =>
      request.kind === "turn"
        ? client.replayTurn({
            source: request.source,
            thread_id: request.threadId,
            message_index: request.messageIndex,
          })
        : client.replayDoc(request.documentId),
    // A replay is a deliberate, potentially slow probe — never refire it
    // in the background.
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  const original = data?.original ?? null;

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel="Replay"
      width="min(680px, 100%)"
      panelStyle={{ padding: "18px 20px 22px" }}
    >
        <header style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: 0, fontSize: pxToRem(16) }}>Replay with the current AI</h2>
            <p
              style={{
                margin: "4px 0 0",
                fontSize: "var(--text-body-sm)",
                color: "var(--text-paper-d)",
              }}
            >
              Re-runs this step as a test. Nothing here changes your conversation or documents.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            style={{
              border: "none",
              background: "none",
              cursor: "pointer",
              color: "var(--text-paper-d)",
              padding: 4,
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </header>

        {isPending && (
          <div
            role="status"
            style={{
              marginTop: 18,
              padding: "14px 12px",
              borderRadius: 7,
              background: "var(--paper-2)",
              color: "var(--text-paper-d)",
              fontSize: "var(--text-body-sm)",
            }}
          >
            Asking the AI again… this can take a minute.
          </div>
        )}

        {error && (
          <div
            role="alert"
            style={{
              marginTop: 18,
              padding: "10px 12px",
              borderRadius: 7,
              background: "rgba(198,138,46,.12)",
              color: "var(--warn)",
              fontSize: "var(--text-body-sm)",
            }}
          >
            {error.message}
          </div>
        )}

        {data && (
          <>
            {original && (
              <Section label="What happened then">
                {original.tool_name ? (
                  <>
                    <CallIntent
                      call={{ name: original.tool_name, arguments: original.tool_args ?? {} }}
                    />
                    {original.content && (
                      <pre
                        style={{
                          margin: "6px 0 0",
                          fontFamily: "var(--font-mono)",
                          fontSize: pxToRem(12),
                          padding: "6px 10px",
                          background: "var(--paper-2)",
                          borderRadius: 6,
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          maxHeight: 160,
                          overflow: "auto",
                        }}
                      >
                        {original.content}
                      </pre>
                    )}
                  </>
                ) : (
                  <div className="md-body md-compact">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{original.content}</ReactMarkdown>
                  </div>
                )}
              </Section>
            )}

            <Section label="What the AI says now">
              {data.reasoning && (
                <button
                  type="button"
                  onClick={() => setShowThinking((v) => !v)}
                  style={{
                    border: "none",
                    background: "none",
                    padding: 0,
                    marginBottom: 6,
                    color: "var(--text-paper-d)",
                    fontSize: pxToRem(12),
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <Icon name={showThinking ? "chev_d" : "chev_r"} size={11} />
                  {showThinking ? t("replay.hideThinking") : t("replay.showThinking")}
                </button>
              )}
              {showThinking && data.reasoning && (
                <pre
                  style={{
                    margin: "0 0 8px",
                    fontFamily: "var(--font-mono)",
                    fontSize: pxToRem(12),
                    padding: "6px 10px",
                    background: "var(--paper-2)",
                    borderRadius: 6,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                    color: "var(--text-paper-d)",
                    maxHeight: 200,
                    overflow: "auto",
                  }}
                >
                  {data.reasoning}
                </pre>
              )}
              {data.tool_calls.map((c, i) => (
                <div key={i} style={{ marginBottom: 6 }}>
                  <CallIntent call={c} prefix="would call" />
                </div>
              ))}
              {data.text && (
                <div className="md-body md-compact">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.text}</ReactMarkdown>
                </div>
              )}
              {!data.text && data.tool_calls.length === 0 && (
                <div style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
                  The AI returned nothing this time.
                </div>
              )}
              {data.note && (
                <div
                  style={{
                    marginTop: 8,
                    fontSize: "var(--text-body-sm)",
                    color: "var(--text-paper-d)",
                    fontStyle: "italic",
                  }}
                >
                  {data.note}
                </div>
              )}
              <div style={{ marginTop: 10, fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                {data.model && <span>{data.model} · </span>}
                took {(data.latency_ms / 1000).toFixed(1)}s
              </div>
              {data.request && <RequestPanel request={data.request} />}
            </Section>
          </>
        )}
    </ModalShell>
  );
}
