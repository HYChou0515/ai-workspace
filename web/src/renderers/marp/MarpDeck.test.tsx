// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarpDeck } from "./MarpDeck";

afterEach(cleanup);

// A fake marp render output (two slides, one workspace-relative image, one
// external image, a css marker) so the component test never needs the real
// engine — the wiring under test is rewrite → sanitize → shadow-root inject.
const fakeRender = () => ({
  html:
    `<div class="marpit">` +
    `<section id="1"><h1>One</h1><img src="./pic.png"></section>` +
    `<section id="2"><h1>Two</h1><img src="https://cdn.example/x.png"></section>` +
    `</div>`,
  css: `section{width:1280px}.marp-test-marker{color:red}`,
});
const resolveAsset = (src: string) => `/api/files?p=${src}`;

function shadowOf(container: HTMLElement): ShadowRoot {
  const host = container.querySelector('[data-testid="marp-host"]') as HTMLElement;
  return host.shadowRoot as ShadowRoot;
}

describe("MarpDeck", () => {
  it("renders each slide from the marp output into an isolated shadow root", () => {
    const { container } = render(
      <MarpDeck text="---\nmarp: true\n---\n# One\n\n---\n\n# Two" resolveAsset={resolveAsset} render={fakeRender} />,
    );
    expect(shadowOf(container).querySelectorAll("section")).toHaveLength(2);
  });

  it("wraps each slide in a fit box so it can scale to the pane width", () => {
    const { container } = render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    const shadow = shadowOf(container);
    expect(shadow.querySelectorAll(".marp-slide-box")).toHaveLength(2);
    expect(shadow.querySelectorAll(".marp-slide-box > section")).toHaveLength(2);
  });

  it("injects the marp theme css into the shadow root (isolated from the app)", () => {
    const { container } = render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    expect(shadowOf(container).innerHTML).toContain("marp-test-marker");
  });

  it("rewrites a workspace-relative image to the file API, leaving external images alone", () => {
    const { container } = render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    const html = shadowOf(container).innerHTML;
    expect(html).toContain("/api/files?p=./pic.png");
    expect(html).toContain("https://cdn.example/x.png");
  });

  it("shows an error note instead of crashing when the deck fails to render", () => {
    const boom = () => {
      throw new Error("bad marp");
    };
    const { container } = render(<MarpDeck text="" resolveAsset={resolveAsset} render={boom} />);
    expect(container.textContent).toMatch(/render this Marp deck/i);
  });
});
