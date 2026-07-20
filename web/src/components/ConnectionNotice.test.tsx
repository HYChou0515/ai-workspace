// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ConnectionNotice } from "./ConnectionNotice";

afterEach(cleanup);

/**
 * The whole point is that a frozen answer stops being frightening.
 *
 * When the live stream drops, events published in the gap are gone (there is no
 * replay), so the answer on screen simply stops growing. The content is safe —
 * the turn is persisted and re-read — so the only thing missing was TELLING the
 * user. Silence leaves them to conclude it broke; a label turns it into a wait.
 */
describe("ConnectionNotice", () => {
  it("says nothing while the stream is healthy", () => {
    const { container } = render(
      <ConnectionNotice connection={{ state: "live", error: null, attempts: 0 }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("says nothing during the very first connect", () => {
    // A notice on first paint would flag every page load as a problem.
    const { container } = render(
      <ConnectionNotice connection={{ state: "connecting", error: null, attempts: 0 }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("announces a reconnect in progress", () => {
    render(
      <ConnectionNotice
        connection={{ state: "reconnecting", error: "stream failed: 504", attempts: 1 }}
      />,
    );
    expect(screen.getByTestId("connection-notice")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("重新連線");
  });

  // A single failed retry is noise; a climbing count is an outage, and saying
  // the same thing forever is how the old UI made a spinner meaningless.
  it("escalates once the retries keep failing", () => {
    render(
      <ConnectionNotice
        connection={{ state: "reconnecting", error: "stream failed: 502", attempts: 5 }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("連線持續中斷");
  });

  // The answer is not lost, and saying so is the difference between "wait" and
  // "start again".
  it("reassures that nothing is lost", () => {
    render(
      <ConnectionNotice
        connection={{ state: "reconnecting", error: "stream failed: 504", attempts: 1 }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("不會遺失");
  });
});
