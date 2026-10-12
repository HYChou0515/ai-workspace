import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import type { FileService } from "../api/fileService";
import { itemChatApi } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import type { Message } from "../api/types";
import {
  isRunTerminal,
  phaseView,
  stepBoard,
  stepOf,
  type PhaseNode,
  type StepStateDTO,
  type WorkflowRunDTO,
} from "../api/workflows";
import { useItemChat } from "../hooks/useItemChat";
import { useLaunchableWorkflows } from "../hooks/useLaunchableWorkflows";
import {
  useCancelRun,
  useConfirmSteer,
  useDecide,
  useRetryRun,
  useRun,
  useSteerRun,
} from "../hooks/useWorkflow";
import { executionName, statusText } from "../lib/executionTab";
import { pxToRem } from "../lib/pxToRem";
import { logFromMessages } from "../pages/investigation/agentLog";
import { EntryView } from "./AgentEntryView";
import { CardDiffReview } from "./CardDiffReview";
import { Popover, PopoverItem } from "./Popover";
import { SteerConfirmCard } from "./SteerConfirmCard";
import { chipStyle } from "./StatusChip";
import { WorkflowDecisionCard } from "./WorkflowDecisionCard";

/** A row whose step is being worked on, waits for a person or broke: open on
 * arrival and left open by Collapse all (plan D7) — it is what the page is for. */
function sticky(node: PhaseNode, run: WorkflowRunDTO | undefined): boolean {
  if (node.status === "running" || node.status === "awaiting_human" || node.status === "failed")
    return true;
  return run?.status === "error" && node.current;
}

const errorText = (err: unknown): string => {
  if (err && typeof err === "object" && "detail" in err && typeof err.detail === "string")
    return err.detail;
  return err instanceof Error ? err.message : String(err);
};

function chipTone(status: string) {
  switch (status) {
    case "passed":
    case "done":
      return "ok" as const;
    case "failed":
    case "error":
      return "err" as const;
    case "running":
    case "retrying":
      return "info" as const;
    case "awaiting_human":
      return "warn" as const;
    default:
      return "muted" as const;
  }
}

const STEP_STATUS: Record<string, string> = {
  pending: "Not started",
  running: "Running",
  retrying: "Retrying",
  passed: "Done",
  skipped: "Already done",
  failed: "Failed",
  awaiting_human: "Waiting for you",
};

/**
 * One workflow execution, as an editor tab (plan-workflow-out-of-chat D6 / D7 /
 * D9). A header names it ("Classify wafers · 10/12 14:03"), says its status and
 * how far it got, and holds Stop and the `⋯` menu (Change this execution…, D2).
 * Below, one row per step (the diagram's phases, the unit "Step N of M" counts),
 * as an accordion (NN/g "Accordions on Desktop"; WAI-ARIA APG Accordion): closed
 * by default, with Expand all / Collapse all; the step being worked on, one
 * waiting at a gate and one that failed stay open. An open row shows that step's
 * prompt, reasoning, tool calls and reply — its own messages out of the
 * execution's conversation, selected by run and phase — plus its files; a gate
 * is decided in its row; a failed one offers Continue from here and Re-run this
 * step.
 */
export function ExecutionView({
  slug,
  itemId,
  runId,
  profile,
  fileService,
  onOpenFile,
}: {
  slug: string;
  itemId: string;
  runId: string;
  profile: string;
  fileService: FileService;
  onOpenFile: (path: string) => void;
}) {
  const runQ = useRun(slug, itemId, runId);
  const run = runQ.data;
  const workflows = useLaunchableWorkflows(slug, itemId, profile);
  const def = workflows.find((w) => w.id === run?.workflow_id);
  const nodes = phaseView(def?.phases ?? [], run);
  const groups = stepBoard(nodes, run?.steps ?? []);
  const { at, of } = stepOf(nodes);
  const live = !!run && !isRunTerminal(run.status);
  const chatId = run?.chat_id ?? "";

  const thread = useQuery({
    queryKey: qk.itemChat(slug, itemId, chatId),
    queryFn: () => itemChatApi.getChat(slug, itemId, chatId),
    enabled: !!chatId,
    refetchInterval: live ? 2000 : false,
  });
  const messages = thread.data?.messages ?? [];
  const mine = messages.filter((m) => m.run_id === runId);
  // Written before messages were marked, or by a turn outside any step: shown
  // apart, never guessed into a row. Unmarked ones count only in a conversation
  // that is this execution's own (a schedule's is shared with other executions).
  const unplaced = [
    ...(thread.data?.runId === runId ? messages.filter((m) => !m.run_id) : []),
    ...mine.filter((m) => !m.phase),
  ];

  const cancel = useCancelRun(slug, itemId);
  const decide = useDecide(slug, itemId, runId);
  const retry = useRetryRun(slug, itemId, runId);
  const steer = useSteerRun(slug, itemId, runId);
  const confirmSteer = useConfirmSteer(slug, itemId, runId);
  const [failure, setFailure] = useState<string | null>(null);
  const report = { onError: (err: unknown) => setFailure(errorText(err)), onSuccess: () => setFailure(null) };

  const [open, setOpen] = useState<Record<string, boolean>>({});
  const isOpen = (n: PhaseNode) => open[n.id] ?? sticky(n, run);
  const [changing, setChanging] = useState(false);
  const [instruction, setInstruction] = useState("");
  const changeId = useId();

  const title = def?.title || run?.workflow_id || "Workflow";
  const firstRunning = nodes.findIndex((n) => n.status === "running");
  const gate = run?.status === "awaiting_human" ? run.pending_decision : null;
  const steerPlan = run?.pending_steer ?? null;
  const decideWith = (choice: string, input?: string) =>
    decide.mutate(input === undefined ? { choice } : { choice, input }, report);

  if (runQ.isError) {
    return (
      <div role="alert" style={{ padding: 20 }}>
        This execution could not be read: {errorText(runQ.error)}
      </div>
    );
  }

  return (
    <div
      className="scrollable"
      data-testid="execution-view"
      style={{ height: "100%", overflowY: "auto", padding: "16px 20px", fontSize: pxToRem(13) }}
    >
      <header style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: pxToRem(17) }}>{executionName(title, run?.started)}</h2>
          {run && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
              <span data-testid="execution-status" style={chipStyle(chipTone(run.status))}>
                {statusText(run.status)}
              </span>
              {of > 0 && <span style={{ color: "var(--text-paper-d)" }}>{`Step ${at} of ${of}`}</span>}
            </div>
          )}
        </div>
        {live && (
          <button
            type="button"
            className="btn"
            data-variant="danger"
            data-size="sm"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(runId, report)}
          >
            Stop
          </button>
        )}
        {run?.status === "cancelled" && (
          <button
            type="button"
            className="btn"
            data-size="sm"
            disabled={retry.isPending}
            onClick={() => retry.mutate("", report)}
          >
            Continue
          </button>
        )}
        <Popover
          align="end"
          trigger={({ onClick, open: menuOpen }) => (
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="secondary"
              aria-label="More actions"
              aria-expanded={menuOpen}
              onClick={onClick}
            >
              ⋯
            </button>
          )}
        >
          {(close) => (
            <PopoverItem
              onClick={() => {
                close();
                setChanging(true);
              }}
            >
              Change this execution…
            </PopoverItem>
          )}
        </Popover>
      </header>

      {failure && (
        <p role="alert" style={{ color: "var(--err)", margin: "10px 0 0" }}>
          {failure}
        </p>
      )}
      {run?.status === "error" && typeof run.result?.error === "string" && (
        <p style={{ color: "var(--err)", margin: "10px 0 0" }}>{run.result.error}</p>
      )}

      {changing && (
        <section
          aria-label="Change this execution"
          style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}
        >
          <label htmlFor={changeId}>What should change?</label>
          <textarea
            id={changeId}
            rows={3}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            style={{ font: "inherit", padding: 6 }}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-size="sm"
              disabled={!instruction.trim() || steer.isPending}
              onClick={() =>
                steer.mutate(instruction.trim(), {
                  ...report,
                  onSuccess: () => {
                    setFailure(null);
                    setChanging(false);
                    setInstruction("");
                  },
                })
              }
            >
              Propose the change
            </button>
            <button type="button" className="btn" data-size="sm" onClick={() => setChanging(false)}>
              Cancel
            </button>
          </div>
        </section>
      )}

      {steerPlan && (
        <div style={{ marginTop: 12 }}>
          <SteerConfirmCard
            plan={steerPlan}
            busy={confirmSteer.isPending}
            onConfirm={(approve) => confirmSteer.mutate(approve, report)}
          />
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, margin: "14px 0 6px" }}>
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="secondary"
          onClick={() => setOpen(Object.fromEntries(nodes.map((n) => [n.id, true])))}
        >
          Expand all
        </button>
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="secondary"
          onClick={() => setOpen({})}
        >
          Collapse all
        </button>
      </div>

      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {groups.map(({ node, steps }, i) => (
          <StepRow
            key={node.id}
            node={node}
            steps={steps}
            run={run}
            expanded={isOpen(node)}
            onToggle={() => setOpen((o) => ({ ...o, [node.id]: !isOpen(node) }))}
            messages={mine.filter((m) => m.phase === node.id)}
            liveChat={i === firstRunning && chatId && thread.data ? { chatId, persisted: messages } : null}
            slug={slug}
            itemId={itemId}
            fileService={fileService}
            onOpenFile={onOpenFile}
          >
            {gate && gate.phase === node.id && (
              <WorkflowDecisionCard
                decision={gate}
                busy={decide.isPending}
                onDecide={decideWith}
                aux={
                  <CardDiffReview
                    slug={slug}
                    itemId={itemId}
                    allow={gate.allow}
                    busy={decide.isPending}
                    onDecide={decideWith}
                    service={fileService}
                  />
                }
              />
            )}
            {node.status === "failed" || (run?.status === "error" && node.current) ? (
              <FailureActions
                steps={steps}
                busy={retry.isPending}
                canRetry={run?.status === "error" || run?.status === "cancelled"}
                onRetry={(rerun) => retry.mutate(rerun, report)}
              />
            ) : null}
          </StepRow>
        ))}
      </ol>

      {unplaced.length > 0 && (
        <section aria-label="Not in a step" style={{ marginTop: 14 }}>
          <div className="caps" style={{ color: "var(--text-paper-d)", marginBottom: 4 }}>
            Not in a step
          </div>
          <Entries messages={unplaced} />
        </section>
      )}
    </div>
  );
}

function Entries({ messages }: { messages: Message[] }) {
  const entries = useMemo(() => logFromMessages(messages).entries, [messages]);
  return (
    <>
      {entries.map((e, i) => (
        <EntryView key={i} entry={e} />
      ))}
    </>
  );
}

/** What the running step's turn has streamed beyond the saved thread: the
 * execution's conversation, live, minus what is already persisted. */
function LiveTail({
  slug,
  itemId,
  chatId,
  persisted,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  persisted: Message[];
}) {
  const agent = useItemChat({ slug, itemId, chatId });
  const saved = useMemo(() => logFromMessages(persisted).entries.length, [persisted]);
  return (
    <>
      {agent.log.entries.slice(saved).map((e, i) => (
        <EntryView key={`live-${i}`} entry={e} />
      ))}
    </>
  );
}

function StepRow({
  node,
  steps,
  run,
  expanded,
  onToggle,
  messages,
  liveChat,
  slug,
  itemId,
  fileService,
  onOpenFile,
  children,
}: {
  node: PhaseNode;
  steps: StepStateDTO[];
  run: WorkflowRunDTO | undefined;
  expanded: boolean;
  onToggle: () => void;
  messages: Message[];
  liveChat: { chatId: string; persisted: Message[] } | null;
  slug: string;
  itemId: string;
  fileService: FileService;
  onOpenFile: (path: string) => void;
  children?: React.ReactNode;
}) {
  const bodyId = useId();
  const counter = node.total > 1 ? ` · ${node.done}/${node.total}` : "";
  return (
    <li
      data-testid="execution-step"
      data-phase={node.id}
      style={{ border: "1px solid var(--paper-3)", borderRadius: "var(--radius-card)", background: "var(--white)" }}
    >
      <h3 style={{ margin: 0, fontSize: "inherit" }}>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={onToggle}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            width: "100%",
            padding: "8px 12px",
            border: 0,
            background: "transparent",
            textAlign: "left",
            font: "inherit",
            fontWeight: 600,
          }}
        >
          <span aria-hidden style={{ width: 12 }}>
            {expanded ? "▾" : "▸"}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>{node.title}</span>
          <span style={chipStyle(chipTone(node.status))}>
            {(STEP_STATUS[node.status] ?? node.status) + counter}
          </span>
        </button>
      </h3>
      {expanded && (
        <div id={bodyId} style={{ padding: "4px 12px 10px", display: "flex", flexDirection: "column", gap: 8 }}>
          {children}
          {steps.map((s) => (
            <StepLine
              key={`${s.name}/${s.key}`}
              step={s}
              files={s.key ? undefined : run?.step_files?.[s.name]}
              fileService={fileService}
              onOpenFile={onOpenFile}
              many={steps.length > 1}
            />
          ))}
          <Entries messages={messages} />
          {liveChat && <LiveTail slug={slug} itemId={itemId} {...liveChat} />}
          {steps.length === 0 && messages.length === 0 && !liveChat && !children && (
            <span style={{ color: "var(--text-paper-d)" }}>Nothing recorded for this step yet.</span>
          )}
        </div>
      )}
    </li>
  );
}

function StepLine({
  step,
  files,
  fileService,
  onOpenFile,
  many,
}: {
  step: StepStateDTO;
  files: { result: string; transcript: string } | undefined;
  fileService: FileService;
  onOpenFile: (path: string) => void;
  many: boolean;
}) {
  const done = step.status === "passed" || step.status === "skipped";
  // Only agent steps write a transcript: ask before offering a link that opens nothing.
  const transcript = useQuery({
    queryKey: ["executionTranscript", files?.transcript],
    queryFn: () => fileService.exists(files!.transcript),
    enabled: !!files,
  });
  const label = (what: string) => (many ? `${step.name} ${what.toLowerCase()}` : what);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", color: "var(--text-paper-d)" }}>
      {many && <span style={{ fontWeight: 600, color: "var(--text-paper)" }}>{step.name}</span>}
      {step.attempts > 1 && <span>{`${step.attempts} attempts`}</span>}
      {step.status === "failed" && step.reason && (
        <span style={{ color: "var(--err)" }}>{step.reason}</span>
      )}
      {files && done && (
        <button type="button" className="btn" data-size="sm" data-variant="secondary" onClick={() => onOpenFile(files.result)}>
          {label("Result")}
        </button>
      )}
      {files && transcript.data && (
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="secondary"
          onClick={() => onOpenFile(files.transcript)}
        >
          {label("Transcript")}
        </button>
      )}
    </div>
  );
}

function FailureActions({
  steps,
  busy,
  canRetry,
  onRetry,
}: {
  steps: StepStateDTO[];
  busy: boolean;
  canRetry: boolean;
  onRetry: (rerun: string) => void;
}) {
  if (!canRetry) return null;
  // Re-run names a step on the board; a failed loop element has left it.
  const named = steps.find((s) => s.status === "failed" && !s.key) ?? steps.find((s) => !s.key);
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button type="button" className="btn" data-variant="primary" data-size="sm" disabled={busy} onClick={() => onRetry("")}>
        Continue from here
      </button>
      {named && (
        <button type="button" className="btn" data-size="sm" disabled={busy} onClick={() => onRetry(named.name)}>
          Re-run this step
        </button>
      )}
    </div>
  );
}
