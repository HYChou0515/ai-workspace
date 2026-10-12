// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { blockMaskedCopy } from "./maskedSecret";

afterEach(cleanup);

function field(masked: boolean) {
  render(
    <input
      data-testid="secret"
      className="input"
      data-masked={masked}
      defaultValue="s3cret"
      autoComplete="off"
      spellCheck={false}
      autoCapitalize="off"
      autoCorrect="off"
      onCopy={blockMaskedCopy}
      onCut={blockMaskedCopy}
    />,
  );
  return screen.getByTestId("secret");
}

describe("blockMaskedCopy", () => {
  // A password field refuses copy and cut; a masked text field does not unless
  // told — dots on screen, the plain value on the clipboard.
  it("refuses copy and cut while the value is drawn as dots", () => {
    const el = field(true);
    // fireEvent returns false when a handler called preventDefault.
    expect(fireEvent.copy(el)).toBe(false);
    expect(fireEvent.cut(el)).toBe(false);
  });

  it("lets the value go once it is shown", () => {
    const el = field(false);
    expect(fireEvent.copy(el)).toBe(true);
    expect(fireEvent.cut(el)).toBe(true);
  });
});
