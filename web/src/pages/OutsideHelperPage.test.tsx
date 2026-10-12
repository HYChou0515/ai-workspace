/**
 * The 外網小幫手 page (docs/plan-outside-helper.md §3.6, D16): where a person
 * gets the helper and reads the one line on installing it.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HelperRelease } from "../api/outsideLookup";
import { renderWithQuery } from "../test/queryWrapper";

import { OutsideHelperPage } from "./OutsideHelperPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const RELEASE: HelperRelease = {
  version: "0.3.0",
  min_version: "0.2.0",
  file: "outside-helper-0.3.0.zip",
  sha256: "ab".repeat(32),
  size: 41_943_040,
};

function draw({
  release = RELEASE as HelperRelease | null,
  online = false,
}: { release?: HelperRelease | null; online?: boolean } = {}) {
  renderWithQuery(
    <OutsideHelperPage
      client={{
        release: vi.fn(async () => release),
        helperStatus: vi.fn(async () => ({ online })),
      }}
    />,
  );
}

describe("the outside helper page", () => {
  // NN/g / GOV.UK: a link to a file says what the file is and how big.
  it("offers the published zip by name, type and size", async () => {
    draw();

    const link = await screen.findByRole("link", { name: /outside-helper-0\.3\.0\.zip/ });
    expect(link).toHaveAttribute("href", "/api/outside-helper/download/outside-helper-0.3.0.zip");
    expect(link).toHaveAccessibleName(expect.stringMatching(/ZIP.*40 MB/));
    expect(screen.getByText(/0\.3\.0/, { selector: "[data-testid=outside-helper-version]" })).toBeInTheDocument();
  });

  it("says how to install it, and the address the helper asks for", async () => {
    draw();

    expect(await screen.findByText(/install\.pyw/)).toBeInTheDocument();
    expect(screen.getByText(window.location.origin)).toBeInTheDocument();
  });

  it("says when nothing is published", async () => {
    draw({ release: null });

    expect(await screen.findByTestId("outside-helper-unpublished")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /\.zip/ })).not.toBeInTheDocument();
  });

  it("says whether the person's own helper is connected", async () => {
    draw({ online: true });

    expect(await screen.findByText("你的外網小幫手已連線")).toBeInTheDocument();
  });

  it("says when it is not", async () => {
    draw({ online: false });

    expect(await screen.findByText("你的外網小幫手未連線")).toBeInTheDocument();
  });
});
