// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { HelpApi, ReleasesInfo } from "../api/help";
import { BreadcrumbProvider } from "../hooks/breadcrumbs";
import { QueryWrap } from "../test/queryWrapper";
import { ReleasesPage } from "./ReleasesPage";

const TWO_RELEASES: ReleasesInfo = {
  releases: [
    {
      version: "2026.07.06",
      date: "2026-07-06",
      unreleased: false,
      sections: [
        { group: "Added", items: ["A shiny new thing"] },
        { group: "Fixed", items: ["An annoying bug"] },
        { group: "Documentation", items: ["Tidied the docs"] },
        { group: "Changed", items: ["Reworked internals"] },
      ],
    },
    {
      version: "2026.07.05",
      date: "2026-07-05",
      unreleased: false,
      sections: [{ group: "Performance", items: ["Faster startup"] }],
    },
  ],
};

const client = (releases: ReleasesInfo): HelpApi => ({
  getHelpInfo: async () => ({ collection_id: "c", documents: [] }),
  getReleases: async () => releases,
});

const renderPage = (releases: ReleasesInfo) =>
  rtlRender(
    <QueryWrap>
      <MemoryRouter>
        <BreadcrumbProvider>
          <ReleasesPage client={client(releases)} />
        </BreadcrumbProvider>
      </MemoryRouter>
    </QueryWrap>,
  );

afterEach(cleanup);

describe("ReleasesPage (#441)", () => {
  it("renders a card per release, newest first, with version + date", async () => {
    renderPage(TWO_RELEASES);
    await screen.findByText("2026.07.06");
    const versions = screen.getAllByTestId("release-version").map((n) => n.textContent);
    expect(versions).toEqual(["2026.07.06", "2026.07.05"]);
    expect(screen.getByText("2026-07-06")).toBeInTheDocument();
  });

  it("badges only the newest release as latest", async () => {
    renderPage(TWO_RELEASES);
    await screen.findByText("2026.07.06");
    const badges = screen.getAllByTestId("latest-badge");
    expect(badges).toHaveLength(1);
    // the badge sits inside the newest release's card, next to 2026.07.06
    expect(badges[0].closest("[data-testid='release-card']")).toHaveTextContent("2026.07.06");
  });

  it("hides Changed/Documentation in the default (highlights) view, shows them in detailed", async () => {
    renderPage(TWO_RELEASES);
    await screen.findByText("A shiny new thing");
    // highlights: user-facing groups only
    expect(screen.getByText("An annoying bug")).toBeInTheDocument();
    expect(screen.queryByText("Tidied the docs")).not.toBeInTheDocument();
    expect(screen.queryByText("Reworked internals")).not.toBeInTheDocument();
    // switch to detailed
    fireEvent.click(screen.getByTestId("view-toggle-detailed"));
    expect(screen.getByText("Tidied the docs")).toBeInTheDocument();
    expect(screen.getByText("Reworked internals")).toBeInTheDocument();
  });

  it("shows an empty state when there are no releases", async () => {
    renderPage({ releases: [] });
    await screen.findByTestId("releases-empty");
    expect(screen.queryByTestId("release-card")).not.toBeInTheDocument();
  });
});
