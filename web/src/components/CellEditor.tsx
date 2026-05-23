/**
 * Code cell editor — Monaco, auto-growing to fit content. Falls back to
 * a skeleton while the lazy editor chunk loads. The old textarea is gone;
 * callers keep the same value/onChange API.
 */

import { MonacoEditor, monacoLanguage } from "./MonacoEditor";

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
  return (
    <MonacoEditor
      value={value}
      onChange={onChange}
      language={monacoLanguage(language)}
      readOnly={readOnly}
      autoHeight
      minHeight={48}
    />
  );
}
