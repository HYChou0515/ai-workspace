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
 * (`hooks/useStableActions`).
 */
import { memo, type ComponentProps } from "react";

import { EntryView } from "./AgentEntryView";

type EntryProps = ComponentProps<typeof EntryView>;

/** What a row may do. A surface leaves out what it does not offer; a row draws
 * an affordance only for an action that is present. */
export type FeedActions = {
  answer?: NonNullable<EntryProps["onAnswerQuestion"]>;
  openCitation?: NonNullable<EntryProps["onOpenCitation"]>;
  requestAccess?: NonNullable<EntryProps["onRequestAccess"]>;
  /** #51 P6: re-run the step at this index as a diagnostic. */
  replay?: (index: number) => void;
  /** #38: undo the user turn at this index and everything after it. */
  undo?: (index: number) => void;
  /** #397: report the answer at this index as a wiki error. */
  reportWiki?: (index: number) => void;
};

export const ChatFeedRow = memo(function ChatFeedRow({
  entry,
  index,
  settled,
  me,
  answeredQuestions,
  actions,
  fileUrl,
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
  /** #285: resolve a workspace path for an embedded image or file link. A prop
   * of its own, NOT one of the identity-stable `actions`: it is called while
   * the markdown is drawn, so a new item must reach the memo'd body as a new
   * function (review: a stable proxy kept the first item's URLs). */
  fileUrl?: (path: string) => string;
}) {
  const answer = entry.kind === "message" && entry.message.role === "assistant";
  const canReplay = settled && (entry.kind === "tool_call" || answer);
  const canUndo = settled && entry.kind === "message" && entry.message.role === "user";
  const { replay, undo, reportWiki } = actions;
  return (
    <EntryView
      entry={entry}
      onAnswerQuestion={actions.answer}
      answeredQuestions={answeredQuestions}
      currentUser={me}
      onOpenCitation={actions.openCitation}
      onRequestAccess={actions.requestAccess}
      onReplay={canReplay && replay ? () => replay(index) : undefined}
      onUndo={canUndo && undo ? () => undo(index) : undefined}
      onReportWiki={settled && answer && reportWiki ? () => reportWiki(index) : undefined}
      fileUrl={fileUrl}
    />
  );
});
