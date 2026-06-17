/**
 * The "new chat" trigger (topic-hub §3): opening a FREE chat — a human-driven chat
 * scoped to the item. Workflow launches now live in their own rich launcher
 * (RunWorkflowPicker), so this is a plain primary affordance. Presentational — the
 * parent wires the actual create.
 */
export function NewChatPicker({
  onFreeChat,
  disabled = false,
}: {
  onFreeChat: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="new-chat-picker__trigger"
      disabled={disabled}
      onClick={() => onFreeChat()}
      data-testid="new-chat-button"
    >
      + New chat
    </button>
  );
}
