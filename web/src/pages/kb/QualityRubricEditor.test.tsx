// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi } from "../../api/kb";
import { LocaleProvider } from "../../lib/i18n";
import { renderWithQuery as renderQ } from "../../test/queryWrapper";

import { QualityRubricEditor } from "./QualityRubricEditor";

afterEach(cleanup);

function renderEditor(rubric: string, client: Partial<KbApi>) {
  return renderQ(
    <LocaleProvider>
      <QualityRubricEditor
        collectionId="c1"
        rubric={rubric}
        client={client as unknown as KbApi}
      />
    </LocaleProvider>,
  );
}

describe("QualityRubricEditor", () => {
  it("prefills the current rubric and saves an edit via updateCollection", async () => {
    const updateCollection = vi.fn(async () => {});
    renderEditor("old rubric", { updateCollection });
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.value).toBe("old rubric");

    fireEvent.change(ta, { target: { value: "judge clarity and noise" } });
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(updateCollection).toHaveBeenCalledWith("c1", {
        quality_rubric: "judge clarity and noise",
      }),
    );
  });

  it("disables save until the rubric is edited (not dirty)", () => {
    renderEditor("same", { updateCollection: vi.fn() });
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
