// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UserPicker } from "./UserPicker";

afterEach(cleanup);

// Stub useUsers to return a fixed directory — UserPicker filters client-side.
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [
    { id: "alice.k", name: "Alice K", section: "Reflow", email: "alice@x.co", photo_url: null },
    { id: "bob123", name: "Bob Wong", section: "Etch", email: "bob@x.co", photo_url: null },
    { id: "carol", name: "Carol Ng", section: "AOI", email: "carol@x.co", photo_url: null },
  ],
}));

// Avatar fetches its own image; stub to a no-op so tests don't try the network.
vi.mock("./UserChip", () => ({
  UserAvatar: ({ userId }: { userId: string }) => <span data-avatar={userId} />,
}));

vi.mock("./Icon", () => ({
  Icon: () => <span data-icon />,
}));

describe("UserPicker — search by id and name", () => {
  it("shows everyone when the search box is empty", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    expect(screen.getByText("Alice K")).toBeInTheDocument();
    expect(screen.getByText("Bob Wong")).toBeInTheDocument();
    expect(screen.getByText("Carol Ng")).toBeInTheDocument();
  });

  it("filters by display name (case-insensitive)", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "WONG" } });
    expect(screen.getByText("Bob Wong")).toBeInTheDocument();
    expect(screen.queryByText("Alice K")).not.toBeInTheDocument();
  });

  it("also filters by user id (case-insensitive)", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "BOB123" } });
    expect(screen.getByText("Bob Wong")).toBeInTheDocument();
    expect(screen.queryByText("Alice K")).not.toBeInTheDocument();
  });

  it("also filters by email (a typed @ before the domain is a real workflow)", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "carol@" } });
    expect(screen.getByText("Carol Ng")).toBeInTheDocument();
    expect(screen.queryByText("Bob Wong")).not.toBeInTheDocument();
  });

  it("also filters by section so a team lead can scope by org", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "etch" } });
    expect(screen.getByText("Bob Wong")).toBeInTheDocument();
    expect(screen.queryByText("Alice K")).not.toBeInTheDocument();
  });

  it("renders the id alongside the name so an id-match is visible", () => {
    // Locks the UI requirement that when someone types an id, they can SEE
    // the matched id in the row — otherwise the filter feels broken even
    // when it's working.
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "bob123" } });
    expect(screen.getByText("Bob Wong")).toBeInTheDocument();
    // The id is rendered somewhere in the matching row.
    expect(screen.getByText(/bob123/)).toBeInTheDocument();
  });

  it("shows a no-matches message when nothing filters in", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "zzz" } });
    expect(screen.getByText(/no matches/i)).toBeInTheDocument();
  });
});
