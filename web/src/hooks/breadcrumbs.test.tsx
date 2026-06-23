// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

import { BreadcrumbProvider, useBreadcrumbs, useBreadcrumbTrail } from "./breadcrumbs";
import type { Crumb } from "./breadcrumbs";

function Producer({ crumbs }: { crumbs: Crumb[] }) {
  useBreadcrumbs(crumbs);
  return null;
}

function Trail() {
  const crumbs = useBreadcrumbTrail();
  return <div data-testid="trail">{crumbs.map((c) => c.label).join(" / ")}</div>;
}

describe("breadcrumbs", () => {
  it("a page publishes crumbs that the trail reader renders", () => {
    render(
      <BreadcrumbProvider>
        <Producer
          crumbs={[
            { label: "Home", to: "/" },
            { label: "RCA", to: "/a/rca" },
          ]}
        />
        <Trail />
      </BreadcrumbProvider>,
    );
    expect(screen.getByTestId("trail")).toHaveTextContent("Home / RCA");
  });

  it("the latest published trail wins when the page's crumbs change", () => {
    const { rerender } = render(
      <BreadcrumbProvider>
        <Producer crumbs={[{ label: "Home", to: "/" }]} />
        <Trail />
      </BreadcrumbProvider>,
    );
    expect(screen.getByTestId("trail")).toHaveTextContent("Home");

    rerender(
      <BreadcrumbProvider>
        <Producer
          crumbs={[
            { label: "Home", to: "/" },
            { label: "Knowledge base", to: "/kb" },
          ]}
        />
        <Trail />
      </BreadcrumbProvider>,
    );
    expect(screen.getByTestId("trail")).toHaveTextContent("Home / Knowledge base");
  });

  it("useBreadcrumbs no-ops outside a provider (page stays testable in isolation)", () => {
    expect(() => render(<Producer crumbs={[{ label: "Home" }]} />)).not.toThrow();
  });
});
