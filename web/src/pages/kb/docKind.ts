/**
 * Map a document's filename/extension to an Icon for the table + drawer. We
 * ingest md/txt today; the map leaves room for the richer kinds the design
 * anticipates (sheets, images), so both surfaces stay visually consistent.
 */

import type { IconName } from "../../components/Icon";

export function kindIcon(path: string): IconName {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (ext === "csv" || ext === "tsv" || ext === "xlsx") return "filter";
  if (ext === "png" || ext === "jpg" || ext === "jpeg" || ext === "gif") return "eye";
  return "file";
}
