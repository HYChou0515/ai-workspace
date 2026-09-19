// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Onboarding } from "../api/types";
import { OnboardingModal } from "./OnboardingModal";

afterEach(cleanup);

const CONTENT: Onboarding = {
  version: "1",
  title: "Welcome to RCA",
  intro: "Investigate failures end to end.",
  points: [
    { title: "Add evidence", body: "Upload logs and data." },
    { title: "Ask the agent", body: "Rank suspect factors." },
  ],
};

function setup(over: Partial<Parameters<typeof OnboardingModal>[0]> = {}) {
  const onGotIt = vi.fn();
  const onDontShowAgain = vi.fn();
  render(
    <OnboardingModal
      content={CONTENT}
      scope={{ kind: "app", slug: "rca" }}
      onGotIt={onGotIt}
      onDontShowAgain={onDontShowAgain}
      {...over}
    />,
  );
  return { onGotIt, onDontShowAgain };
}

describe("OnboardingModal", () => {
  it("renders the title, intro, and every point", () => {
    setup();
    expect(screen.getByText("Welcome to RCA")).toBeInTheDocument();
    expect(screen.getByText("Investigate failures end to end.")).toBeInTheDocument();
    expect(screen.getByText("Add evidence")).toBeInTheDocument();
    expect(screen.getByText("Upload logs and data.")).toBeInTheDocument();
    expect(screen.getByText("Ask the agent")).toBeInTheDocument();
    expect(screen.getByText("Rank suspect factors.")).toBeInTheDocument();
  });

  it("is an accessible modal dialog labelled by its title", () => {
    setup();
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });

  it("'Got it' invokes onGotIt (close-for-now)", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.click(screen.getByRole("button", { name: /got it/i }));
    expect(onGotIt).toHaveBeenCalledTimes(1);
    expect(onDontShowAgain).not.toHaveBeenCalled();
  });

  it("'Don't show again' invokes onDontShowAgain (permanent)", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.click(screen.getByRole("button", { name: /don't show again/i }));
    expect(onDontShowAgain).toHaveBeenCalledTimes(1);
    expect(onGotIt).not.toHaveBeenCalled();
  });

  it("Escape closes for now (onGotIt), never a permanent dismiss", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onGotIt).toHaveBeenCalledTimes(1);
    expect(onDontShowAgain).not.toHaveBeenCalled();
  });

  it("offers a link to the full help page when onSeeFull is provided (#230)", () => {
    const onSeeFull = vi.fn();
    setup({ onSeeFull });
    fireEvent.click(screen.getByText(/See the full guide/));
    expect(onSeeFull).toHaveBeenCalledTimes(1);
  });

  it("omits the full-guide link when onSeeFull is not provided (#230)", () => {
    setup();
    expect(screen.queryByText(/See the full guide/)).not.toBeInTheDocument();
  });
});

/**
 * #fe-responsive — measured in a real browser at 390x844: the three action
 * buttons sit in one `space-between` row, each pinned to `height: 32` with no
 * wrap control. On a narrow modal the labels wrapped to a second line inside
 * that fixed height and were clipped mid-glyph — "Got it" lost its lower half.
 * Same defect family as the workspace's bottom-panel tab row.
 */
describe("OnboardingModal actions survive a narrow modal (#fe-responsive)", () => {
  it("keeps each action label on one line", () => {
    setup();
    for (const label of ["Don't show again", "Got it"]) {
      const btn = screen.getByRole("button", { name: label });
      expect(btn.style.whiteSpace).toBe("nowrap");
      expect(btn.style.flexShrink).toBe("0");
    }
  });

  it("lets the action row wrap so the buttons keep their full height", () => {
    setup();
    const row = screen.getByTestId("onboarding-actions");
    expect(row.style.flexWrap).toBe("wrap");
  });
});

// The teaching's prose is markdown (intro, each point's body, the footer), so an
// App can put a screenshot inline or under the points. A point's title stays
// plain — it is the heading beside the number.
describe("OnboardingModal — markdown and images", () => {
  it("renders intro and body as markdown", () => {
    setup({
      content: {
        ...CONTENT,
        intro: "Investigate **failures** end to end.",
        points: [{ title: "Add *evidence*", body: "Upload `logs` and data." }],
      },
    });
    expect(screen.getByText("failures").tagName).toBe("STRONG");
    expect(screen.getByText("logs").tagName).toBe("CODE");
    expect(screen.getByText("Add *evidence*")).toBeInTheDocument(); // the title, verbatim
  });

  it("embeds an App's assets/ image through the assets route", () => {
    const { container } = render(
      <OnboardingModal
        content={{ ...CONTENT, points: [{ title: "Look", body: "![the form](assets/create.png)" }] }}
        scope={{ kind: "app", slug: "rca" }}
        onGotIt={vi.fn()}
        onDontShowAgain={vi.fn()}
      />,
    );
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "/api/apps/rca/assets/create.png");
    expect(img).toHaveAttribute("alt", "the form");
  });

  it("draws the footer under the points and above the buttons", () => {
    const { container } = render(
      <OnboardingModal
        content={{ ...CONTENT, footer: "![overview](assets/overview.png)\n\nMore in the guide." }}
        scope={{ kind: "app", slug: "rca" }}
        onGotIt={vi.fn()}
        onDontShowAgain={vi.fn()}
      />,
    );
    const footer = screen.getByTestId("onboarding-footer");
    const list = container.querySelector("ol");
    const actions = screen.getByTestId("onboarding-actions");
    expect(list!.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(footer.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(footer.querySelector("img")).toHaveAttribute("src", "/api/apps/rca/assets/overview.png");
    expect(screen.getByText("More in the guide.")).toBeInTheDocument();
  });

  it("draws no footer container when there is no footer", () => {
    setup();
    expect(screen.queryByTestId("onboarding-footer")).toBeNull();
  });

  it("marks every prose block as the modal's own, in the compact variant", () => {
    // `.md-body` carries its own colour and size, which would beat the dimmed
    // paper colour and the 14/13px the modal sets on its wrappers. The
    // modal's class is what `base.css` keys the context override on (see
    // onboardingProse.test.ts for the rule itself), so every block must carry
    // it — intro, each body, and the footer.
    const { container } = render(
      <OnboardingModal
        content={{ ...CONTENT, footer: "More in the guide." }}
        scope={{ kind: "app", slug: "rca" }}
        onGotIt={vi.fn()}
        onDontShowAgain={vi.fn()}
      />,
    );
    const articles = [...container.querySelectorAll("article")];
    expect(articles).toHaveLength(1 + CONTENT.points.length + 1);
    for (const a of articles) expect(a).toHaveClass("onboarding-prose", "md-body", "md-compact");
  });

  it("renders pm's backticked sentence with the backticks as code (the one shipped string that changes)", async () => {
    // The one pre-existing shipped string with a markdown construct in it:
    // it must read as it did minus the backticks, with the path as `<code>`.
    // Read from disk so the test follows the manifest.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const manifest = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, "../../../src/workspace_app/apps/pm/app.json"), "utf8"),
    );
    const ob = manifest.onboarding as Onboarding;
    const raw = ob.points.map((p) => p.body).find((b) => b.includes("`"));
    expect(raw).toBeDefined();
    const { container } = render(
      <OnboardingModal content={ob} scope={{ kind: "app", slug: "pm" }} onGotIt={vi.fn()} onDontShowAgain={vi.fn()} />,
    );
    expect(container.textContent).toContain(raw!.replaceAll("`", ""));
    const code = [...container.querySelectorAll("code")].map((c) => c.textContent);
    expect(code).toEqual([raw!.split("`")[1]]);
  });

  it("keeps a platform-level absolute image path as written", () => {
    const { container } = render(
      <OnboardingModal
        content={{ ...CONTENT, footer: "![](/onboarding/welcome.png)" }}
        scope={{ kind: "platform" }}
        onGotIt={vi.fn()}
        onDontShowAgain={vi.fn()}
      />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "/onboarding/welcome.png");
  });

  it("renders every shipped App's teaching word for word", async () => {
    // Parity with the plain-text rendering this replaces: every shipped
    // app.json string that IS plain text (no markdown construct in it) must
    // read exactly as it did. The oracle is the raw string itself — plain text
    // is valid markdown. Strings that use markdown on purpose (rca's screenshot,
    // pm's backticks, the template's example) are covered by the tests above.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const appsDir = path.resolve(__dirname, "../../../src/workspace_app/apps");
    const slugs = fs.readdirSync(appsDir).filter((d) => fs.existsSync(path.join(appsDir, d, "app.json")));
    expect(slugs.length).toBeGreaterThanOrEqual(5);
    for (const slug of slugs) {
      const manifest = JSON.parse(fs.readFileSync(path.join(appsDir, slug, "app.json"), "utf8"));
      const ob = manifest.onboarding as Onboarding | undefined;
      if (!ob) continue;
      const { container, unmount } = render(
        <OnboardingModal content={ob} scope={{ kind: "app", slug }} onGotIt={vi.fn()} onDontShowAgain={vi.fn()} />,
      );
      const text = container.textContent ?? "";
      const plain = (raw: string) => raw !== "" && !/[`*_#\[\]\n]/.test(raw);
      const strings = [ob.intro, ...ob.points.flatMap((p) => [p.title, p.body]), ob.footer ?? ""];
      expect(strings.filter(plain).length).toBeGreaterThan(0); // the oracle is not vacuous
      for (const raw of strings.filter(plain)) expect(text).toContain(raw);
      unmount();
    }
  });
});
