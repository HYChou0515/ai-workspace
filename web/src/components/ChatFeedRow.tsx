/**
 * One entry of a chat feed, redrawn only when what it shows changes — shared by
 * the workspace chat and the KB chat (the long-chat perf fix).
 *
 * A streamed token replaces the live entry and nothing else (`reduceAgent`
 * copies the array and keeps every finished entry's object), so a finished
 * message has nothing new to draw. Drawing it anyway re-parsed its markdown per
 * token: on a 300-turn chat the main thread was busy for the whole answer and
 * froze for over a second at a time. A row therefore takes only what it shows,
 * and its handlers come from ONE object whose identity does not change
 * (`useStableActions`).
 */
import { memo, useMemo, useRef, type ComponentProps } from "react";

import { EntryView } from "./AgentEntryView";

type EntryProps = ComponentProps<typeof EntryView>;

/** What a row may do. A surface leaves out what it does not offer; a row draws
 * an affordance only for an action that is present. */
export type FeedActions = {
  answer?: NonNullable<EntryProps["onAnswerQuestion"]>;
  openCitation?: NonNullable<EntryProps["onOpenCitation"]>;
  requestAccess?: NonNullable<EntryProps["onRequestAccess"]>;
  fileUrl?: (path: string) => string;
  /** #51 P6: re-run the step at this index as a diagnostic. */
  replay?: (index: number) => void;
  /** #38: undo the user turn at this index and everything after it. */
  undo?: (index: number) => void;
  /** #397: report the answer at this index as a wiki error. */
  reportWiki?: (index: number) => void;
};

type AnyFn = (...args: never[]) => unknown;

/**
 * `impl`'s functions behind proxies whose identity holds across renders: each
 * proxy calls the LATEST `impl` at call time. The object is rebuilt only when
 * the set of present actions changes (an action offered or withdrawn), which is
 * when a row has a different affordance to draw.
 */
export function useStableActions<T extends Record<string, AnyFn | undefined>>(
  impl: T,
): T {
  const latest = useRef(impl);
  latest.current = impl;
  const present = Object.keys(impl)
    .filter((k) => impl[k] !== undefined)
    .sort()
    .join(",");
  return useMemo(() => {
    const out: Record<string, AnyFn> = {};
    for (const key of present ? present.split(",") : []) {
      out[key] = (...args: never[]) => (latest.current[key] as AnyFn)(...args);
    }
    return out as T;
  }, [present]);
}

export const ChatFeedRow = memo(function ChatFeedRow({
  entry,
  index,
  settled,
  me,
  answeredQuestions,
  actions,
}: {
  entry: EntryProps["entry"];
  /** #51 P6: hydrated entries map 1:1 onto the persisted conversation
   * (`logFromMessages`), so the entry index IS the message index. */
  index: number;
  /** Not streaming: the in-flight turn isn't persisted yet, so an index would
   * lie — replay, undo and report are offered only when settled. */
  settled: boolean;
  /** #583: who is reading, so their own messages align right. */
  me?: EntryProps["currentUser"];
  answeredQuestions?: EntryProps["answeredQuestions"];
  actions: FeedActions;
}) {
  const answer = entry.kind === "message" && entry.message.role === "assistant";
  const canReplay = settled && (entry.kind === "tool_call" || answer);
  const canUndo =
    settled && entry.kind === "message" && entry.message.role === "user";
  const { replay, undo, reportWiki } = actions;
  return (
    // chat-feed.css: an off-screen entry is not laid out; an answer stands in
    // at an answer's height until it has been drawn once.
    <div
      className={
        answer ? "chat-feed-row chat-feed-row--answer" : "chat-feed-row"
      }
    >
      <EntryView
        entry={entry}
        onAnswerQuestion={actions.answer}
        answeredQuestions={answeredQuestions}
        currentUser={me}
        onOpenCitation={actions.openCitation}
        onRequestAccess={actions.requestAccess}
        onReplay={canReplay && replay ? () => replay(index) : undefined}
        onUndo={canUndo && undo ? () => undo(index) : undefined}
        onReportWiki={
          settled && answer && reportWiki ? () => reportWiki(index) : undefined
        }
        fileUrl={actions.fileUrl}
      />
    </div>
  );
});
