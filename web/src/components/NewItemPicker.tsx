import { Icon } from "./Icon";

/**
 * The bar's "+ New chat" (#132, topic-hub §4). It used to be a menu of a free
 * chat plus the profile's workflows; a workflow is started from the workspace's
 * Workflows sidebar now, never from a chat (plan-workflow-out-of-chat D3 / D7),
 * so what is left is one action, and one action is a button. Presentational —
 * the parent wires the create.
 */
export function NewItemPicker({
  onFreeChat,
  disabled = false,
}: {
  onFreeChat: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="new-item-picker">
      <button
        type="button"
        className="new-item-picker__trigger"
        disabled={disabled}
        onClick={onFreeChat}
        data-testid="new-item-button"
      >
        <Icon name="plus" size={12} color="var(--text-paper-d)" />
        <span>New chat</span>
      </button>
    </div>
  );
}
