import { describe, expect, it } from "vitest";

import { effective as effectiveIn, ruleBody } from "../test/cssRules";
import { readSrcFile } from "../test/readSrcFile";

/**
 * The welcome card's prose goes through `MarkdownBody`, and `.md-body` sets
 * its own colour and size (`base.css`): full text colour, the body scale —
 * right for a chat bubble, wrong here, where the modal draws its intro at
 * 14px and its bodies at 13px in the dimmed paper colour on wrapper divs.
 * A class rule on the element beats an inherited inline style on its parent,
 * so without an override those wrapper styles are dead and every App's card
 * reads in full black (measured in Chromium: wrapper #5C5F66, article
 * #1A1B1F). The override says: in the onboarding context, the article takes
 * whatever its wrapper set — a compound selector on the article itself
 * (`.onboarding-prose.md-body`), one class more specific than `.md-body`.
 *
 * This reads the stylesheet rather than the DOM because the test environment
 * does not cascade. The browser measurement after the fix is recorded in
 * docs/plan-onboarding-images.md (as-built).
 */
const BASE_CSS = readSrcFile("styles/base.css");
const effective = (selector: string, property: string) => effectiveIn(BASE_CSS, selector, property);

describe("the onboarding modal's prose takes its wrapper's colour and size", () => {
  it("inherits colour, size and leading instead of the .md-body defaults", () => {
    expect(effective(".onboarding-prose.md-body", "color")).toBe("inherit");
    expect(effective(".onboarding-prose.md-body", "font-size")).toBe("inherit");
    expect(effective(".onboarding-prose.md-body", "line-height")).toBe("inherit");
  });

  it("comes after the compact variant, which sets the same properties at the same specificity", () => {
    // `.md-body.md-compact` and `.onboarding-prose.md-body` are both two
    // classes; on a tie the later rule wins, so the order in the file IS the
    // rule. A guard on the declarations alone would pass with the override
    // moved above the variant — and silently lose.
    const variant = BASE_CSS.indexOf(".md-body.md-compact {");
    const override = BASE_CSS.indexOf(".onboarding-prose.md-body {");
    expect(variant).toBeGreaterThan(-1);
    expect(override).toBeGreaterThan(variant);
  });

  it("drops the trailing paragraph margin, so a block ends where its wrapper does", () => {
    // `.md-body.md-compact p { margin: 0 0 6px }` would leave 6px of air under
    // every block where the plain-text modal had none.
    expect(ruleBody(BASE_CSS, ".onboarding-prose.md-body > :last-child")).toMatch(/margin-bottom:\s*0;/);
  });
});
