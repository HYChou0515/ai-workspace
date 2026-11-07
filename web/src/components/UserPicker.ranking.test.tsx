// @vitest-environment happy-dom
/**
 * The people picker's order on screen (docs/plan-people-picker-ranking.md D5,
 * D6): frequent people under a "Frequent" label with a remove button, the order
 * fixed while the picker is open, related people first, typed text ranked by
 * match quality.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readFrecency, recordPick } from "../lib/peopleFrecency";
import { UserPicker } from "./UserPicker";

const DIRECTORY = [
  { id: "me", name: "Me Myself", section: "RD-PI-TF", email: "me@x.co", photo_url: null },
  { id: "amy", name: "Amy Lin", section: "Sales", email: "amy@x.co", photo_url: null },
  { id: "ben", name: "Ben Wang", section: "RD-PI-CVD", email: "ben@x.co", photo_url: null },
  { id: "cid", name: "Cid Ho", section: "RD-QA", email: "cid@x.co", photo_url: null },
  { id: "wang01", name: "Dee Chen", section: "Sales", email: "dee@x.co", photo_url: null },
  { id: "eve", name: "Wang Eve", section: "Sales", email: "eve@x.co", photo_url: null },
];

vi.mock("../hooks/useUsers", () => ({
  useUsers: () => DIRECTORY,
}));
// Who is viewing, and whether `/me` has answered yet — the placeholder id
// before it does must neither read nor record anyone's frequent people.
let viewerReady = true;
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUserState: () => ({ id: viewerReady ? "me" : "default-user", ready: viewerReady }),
}));
vi.mock("./UserChip", () => ({ UserAvatar: ({ userId }: { userId: string }) => <span data-avatar={userId} /> }));
vi.mock("./Icon", () => ({ Icon: () => <span data-icon /> }));

/** The names in the list, top to bottom. */
const order = () => [...document.querySelectorAll("[data-person-name]")].map((n) => n.textContent);

beforeEach(() => {
  localStorage.clear();
  viewerReady = true;
});
afterEach(cleanup);

describe("UserPicker — before anything is typed", () => {
  it("lists people in the viewer's nearest sections first, then by name", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    expect(order()).toEqual(["Ben Wang", "Cid Ho", "Amy Lin", "Dee Chen", "Wang Eve"]);
  });

  it("puts the people picked often first, under a Frequent label", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    expect(order()[0]).toBe("Wang Eve");
    const frequent = screen.getByRole("group", { name: "常用" });
    expect(within(frequent).getByText("Wang Eve")).toBeInTheDocument();
    expect(within(frequent).queryByText("Ben Wang")).not.toBeInTheDocument();
  });

  it("labels at most 8 people as frequent", () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      id: `p${i}`,
      name: `Person ${String(i).padStart(2, "0")}`,
      section: "",
      email: "",
      photo_url: null,
    }));
    DIRECTORY.push(...many);
    try {
      for (const p of many) recordPick("me", p.id);
      render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
      const frequent = screen.getByRole("group", { name: "常用" });
      expect(within(frequent).getAllByRole("listitem")).toHaveLength(8);
    } finally {
      DIRECTORY.splice(DIRECTORY.length - many.length, many.length);
    }
  });

  it("puts the people related to this place first of all", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} related={["amy"]} />);
    expect(order().slice(0, 2)).toEqual(["Amy Lin", "Wang Eve"]);
  });

  it("removes a person from Frequent with its button, in this browser", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    fireEvent.click(screen.getByRole("button", { name: "從常用移除 Wang Eve" }));
    expect(screen.queryByRole("group", { name: "常用" })).not.toBeInTheDocument();
    expect(readFrecency("me").has("eve")).toBe(false);
  });
});

describe("UserPicker — picking", () => {
  it("remembers a pick for next time, without reordering the list it was made in", () => {
    // As every caller does: the pick comes back as `selected` and the picker
    // re-renders — which is when a re-read of the frequent list would reorder.
    function Host() {
      const [selected, setSelected] = useState<string[]>([]);
      return (
        <UserPicker
          selected={selected}
          onToggle={(id) => setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))}
          exclude={["me"]}
        />
      );
    }
    render(<Host />);
    const before = order();
    fireEvent.click(screen.getByText("Wang Eve"));
    expect(readFrecency("me").has("eve")).toBe(true);
    expect(order()).toEqual(before);
  });

  it("does not count un-ticking someone as picking them", () => {
    render(<UserPicker selected={["eve"]} onToggle={() => {}} exclude={["me"]} />);
    fireEvent.click(screen.getByText("Wang Eve"));
    expect(readFrecency("me").has("eve")).toBe(false);
  });
});

describe("UserPicker — after typing", () => {
  it("ranks by match quality: a name that starts with the text first", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "wang" } });
    // name starts (Wang Eve) > name contains (Ben Wang) > id starts (wang01).
    expect(order()).toEqual(["Wang Eve", "Ben Wang", "Dee Chen"]);
  });

  it("puts a related person first among equally good matches", () => {
    // "n" is inside Amy Lin, Ben Wang, Dee Chen and Wang Eve alike; with no
    // related people the viewer's nearest section (Ben, RD-PI-CVD) leads.
    const { unmount } = render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "n" } });
    expect(order()[0]).toBe("Ben Wang");
    unmount();
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} related={["amy"]} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "n" } });
    expect(order()[0]).toBe("Amy Lin");
  });

  it("shows no Frequent label while searching", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "wang" } });
    expect(screen.queryByRole("group", { name: "常用" })).not.toBeInTheDocument();
  });
});

describe("UserPicker — review round 1", () => {
  it("puts focus back in the search box after removing someone from Frequent", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    const x = screen.getByRole("button", { name: "從常用移除 Wang Eve" });
    x.focus();
    fireEvent.click(x);
    expect(document.activeElement).toBe(screen.getByRole("searchbox"));
  });

  it("neither reads nor records frequent people before it knows who is viewing", () => {
    recordPick("default-user", "eve");
    viewerReady = false;
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    expect(screen.queryByRole("group", { name: "常用" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Amy Lin"));
    expect(readFrecency("default-user").has("amy")).toBe(false);
  });

  it("can be told not to remember a one-off pick", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} rememberPicks={false} />);
    fireEvent.click(screen.getByText("Wang Eve"));
    expect(readFrecency("me").has("eve")).toBe(false);
  });

  it("labels 8 frequent people even when related people are among the most picked", () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      id: `p${i}`,
      name: `Person ${String(i).padStart(2, "0")}`,
      section: "",
      email: "",
      photo_url: null,
    }));
    DIRECTORY.push(...many);
    try {
      // Two related people picked most of all, then ten others.
      for (let k = 0; k < 5; k++) {
        recordPick("me", "amy");
        recordPick("me", "ben");
      }
      for (const p of many) recordPick("me", p.id);
      render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} related={["amy", "ben"]} />);
      const frequent = screen.getByRole("group", { name: "常用" });
      expect(within(frequent).getAllByRole("listitem")).toHaveLength(8);
    } finally {
      DIRECTORY.splice(DIRECTORY.length - many.length, many.length);
    }
  });
});

describe("UserPicker — section labels before typing (相關 / 常用 / 所有)", () => {
  const names = (group: HTMLElement) =>
    [...group.querySelectorAll("[data-person-name]")].map((n) => n.textContent);

  it("labels the related people, the frequent people and everyone else", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} related={["amy"]} />);
    expect(names(screen.getByRole("group", { name: "相關" }))).toEqual(["Amy Lin"]);
    expect(names(screen.getByRole("group", { name: "常用" }))).toEqual(["Wang Eve"]);
    expect(names(screen.getByRole("group", { name: "所有" }))).toEqual(["Ben Wang", "Cid Ho", "Dee Chen"]);
  });

  it("labels everyone else as soon as only frequent people sit above them", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    expect(screen.queryByRole("group", { name: "相關" })).not.toBeInTheDocument();
    expect(names(screen.getByRole("group", { name: "所有" }))).not.toContain("Wang Eve");
  });

  it("needs no labels when nothing is suggested", () => {
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />);
    expect(screen.queryAllByRole("group")).toHaveLength(0);
  });

  it("drops every label once something is typed", () => {
    recordPick("me", "eve");
    render(<UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} related={["amy"]} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "n" } });
    expect(screen.queryAllByRole("group")).toHaveLength(0);
  });
});
