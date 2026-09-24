/**
 * The chart plugin's entry: importing it registers `view: chart`.
 *
 * It owns its empty state (a chart with no rows still draws its axes) and has
 * no quick-create: it reads a source, it does not make records.
 */
import { registerViewKind } from "@aiws/view-sdk";

import { ChartView } from "./ChartView";

registerViewKind({
  kind: "chart",
  Component: ChartView,
  ownsEmptyState: true,
  suppressQuickCreate: true,
  // Every chart can be put on a named marking from its header (#847 PR 3).
  linkable: true,
});
