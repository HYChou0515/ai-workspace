// Empty-state copy for the agent chat (#161). The RCA `AgentPanel` is shared by
// every App, so its empty invite must stay neutral — no RCA system nouns
// (evidence / notebooks / brief / analyses / report). When the App ships example
// prompts (suggestion chips), point at them; otherwise just invite a question.

export function chatEmptyHint(hasExamples: boolean): string {
  return hasExamples
    ? "Ask the agent anything to get started — or try an example below."
    : "Ask the agent anything to get started.";
}
