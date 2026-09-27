// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P24 — a table in a narrow pane scrolls inside the pane.
 *
 * Measured in Chromium at 390 wide (`/view?layout=`: a scatter beside a
 * csv-table over an entity table, on one marking): the entity table's
 * fixed layout shared a 137 px pane among its columns, so LOT showed as "L" and
 * its values could not be reached at all; and a table grew to its full height
 * (775 px in a 356 px pane), so its own sideways scrollbar sat below the pane's
 * bottom. A table now keeps each column at least TABLE_COLUMN_MIN_REM wide
 * (and scrolls sideways past that) and takes the pane's free height, scrolling
 * vertically inside itself. happy-dom lays nothing out, so this holds the
 * rules; the pixels are measured in a browser.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { DialogProvider } from "../../components/Dialog";
import { effective, ENTITY_VIEWS_CSS } from "../../test/cssRules";
import { EntityViewBody, parseViewSpec } from "./EntityViews";
import { TABLE_COLUMN_MIN_REM } from "./TableView";

const lotType: EntityType = {
  name: "lot",
  records_path: "lots",
  fields: [
    { name: "lot", role: "text" },
    { name: "status", role: "text" },
    { name: "owner", role: "text" },
  ],
  form: [],
};
const LOTS: EntityInstance[] = [1, 2].map((n) => ({
  number: n,
  type_name: "lot",
  fields: { lot: `L${n}`, status: "open", owner: "ann" },
  body: "",
  diagnostics: [],
}));

function table(text: string): HTMLTableElement {
  const { container } = render(
    <DialogProvider>
      <EntityViewBody spec={parseViewSpec(text)!} type={lotType} entities={LOTS} onCreate={vi.fn()} onPatch={vi.fn()} />
    </DialogProvider>,
  );
  return container.querySelector("table.ev-table") as HTMLTableElement;
}

afterEach(cleanup);

describe("an entity table in a narrow pane", () => {
  it("keeps every column readable and scrolls sideways past that", () => {
    // checkbox 36 px + # 44 px + a drag grip 24 px (a table with no sort)
    expect(table("view: table\nentity: lot\n").style.minWidth).toBe(`calc(104px + ${3 * TABLE_COLUMN_MIN_REM}rem)`);
    cleanup();
    // sorted: no grip; two columns shown
    expect(table("view: table\nentity: lot\nsort:\n  - { field: lot }\ncolumns: [lot, owner]\n").style.minWidth).toBe(
      `calc(80px + ${2 * TABLE_COLUMN_MIN_REM}rem)`,
    );
  });

  it("takes the pane's free height and scrolls inside it, so both its scrollbars are in the pane", () => {
    // the view grows into the panel's free height from nothing (a table's
    // own height would make the panel as tall as every row) …
    expect(effective(ENTITY_VIEWS_CSS, ".ev-tableview", "flex")).toBe("1 1 0");
    expect(effective(ENTITY_VIEWS_CSS, ".ev-tableview", "display")).toBe("flex");
    expect(effective(ENTITY_VIEWS_CSS, ".ev-tableview", "flex-direction")).toBe("column");
    // … but not below a few rows in a very short pane (the pane scrolls then)
    expect(effective(ENTITY_VIEWS_CSS, ".ev-tableview", "min-height")).toBe("6rem");
    // the bordered wrap is as tall as its rows, and shrinks to the view's
    // height (scrolling) when they are taller
    expect(effective(ENTITY_VIEWS_CSS, ".ev-table-wrap", "flex")).toBe("0 1 auto");
    expect(effective(ENTITY_VIEWS_CSS, ".ev-table-wrap", "min-height")).toBe("0");
    expect(effective(ENTITY_VIEWS_CSS, ".ev-table-wrap", "overflow")).toBe("auto");
  });
});
