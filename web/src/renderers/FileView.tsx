/**
 * Dispatch a file path to its renderer. The full type→renderer table lives in
 * ./registry — add a preview type there, nothing here changes. The renderer
 * reads file IO/URL/listing from the `FileService` in context, so the same
 * dispatch serves the investigation workspace and a KB collection alike.
 */
import { rendererComponent } from "./registry";

export function FileView({ path }: { path: string }) {
  const Component = rendererComponent(path);
  return <Component path={path} />;
}
