// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PresenceBar } from "./PresenceBar";

vi.mock("../hooks/useItemPresence", () => ({ useItemPresence: vi.fn() }));
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: vi.fn() }));
// Avatars resolve names through useUser → stub it so no query provider is needed.
vi.mock("../hooks/useUsers", () => ({
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useItemPresence } from "../hooks/useItemPresence";

afterEach(cleanup);

function setup(roster: string[], me = "alice") {
  vi.mocked(useItemPresence).mockReturnValue(roster);
  vi.mocked(useCurrentUser).mockReturnValue(me);
  render(<PresenceBar slug="pm" itemId="A" />);
}

describe("PresenceBar (#455 P4)", () => {
  it("shows the other viewers (excluding me) as an avatar stack", () => {
    setup(["alice", "bob", "carol"], "alice");
    expect(screen.getByLabelText(/2 other people viewing/i)).toBeInTheDocument();
  });

  it("renders nothing when I'm the only viewer", () => {
    setup(["alice"], "alice");
    expect(screen.queryByLabelText(/viewing/i)).not.toBeInTheDocument();
  });

  it("renders nothing when the roster is empty", () => {
    setup([], "alice");
    expect(screen.queryByLabelText(/viewing/i)).not.toBeInTheDocument();
  });

  it("uses singular copy for a single other viewer", () => {
    setup(["alice", "bob"], "alice");
    expect(screen.getByLabelText(/1 other person viewing/i)).toBeInTheDocument();
  });
});
