/**
 * Dispatch a file path to its renderer. The full type→renderer table lives in
 * ./registry — add a preview type there, nothing here changes.
 */
import { rendererComponent } from "./registry";

export function FileView({
  investigationId,
  path,
}: {
  investigationId: string;
  path: string;
}) {
  const Component = rendererComponent(path);
  return <Component investigationId={investigationId} path={path} />;
}
