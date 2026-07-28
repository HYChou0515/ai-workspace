// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

describe("MarpDeck — present mode", () => {
  afterEach(() => {
    // biome-ignore lint: test cleanup of the fullscreen stub
    delete (HTMLElement.prototype as { requestFullscreen?: unknown }).requestFullscreen;
  });

  function stubFullscreen() {
    const req = vi.fn().mockResolvedValue(undefined);
    (HTMLElement.prototype as { requestFullscreen?: unknown }).requestFullscreen = req;
    return req;
  }

  it("enters fullscreen when Present is clicked", () => {
    const req = stubFullscreen();
    render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    fireEvent.click(screen.getByRole("button", { name: /present/i }));
    expect(req).toHaveBeenCalled();
  });

  it("steps slides with the arrow keys, clamped at both ends", () => {
    stubFullscreen();
    render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    fireEvent.click(screen.getByRole("button", { name: /present/i }));
    const counter = () => screen.getByTestId("marp-present-counter").textContent;
    expect(counter()).toBe("1 / 2");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(counter()).toBe("2 / 2");
    fireEvent.keyDown(window, { key: "ArrowRight" }); // clamp at the last slide
    expect(counter()).toBe("2 / 2");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(counter()).toBe("1 / 2");
    fireEvent.keyDown(window, { key: "ArrowLeft" }); // clamp at the first slide
    expect(counter()).toBe("1 / 2");
  });

  it("leaves present mode when fullscreen ends (Esc)", () => {
    stubFullscreen();
    render(<MarpDeck text="" resolveAsset={resolveAsset} render={fakeRender} />);
    fireEvent.click(screen.getByRole("button", { name: /present/i }));
    expect(screen.queryByTestId("marp-present-counter")).toBeInTheDocument();
    fireEvent(document, new Event("fullscreenchange"));
    expect(screen.queryByTestId("marp-present-counter")).toBeNull();
  });
});
