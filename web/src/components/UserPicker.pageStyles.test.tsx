// @vitest-environment happy-dom
/**
 * The picker's labelled sections inside a `.page` (the admin exception picker on
 * My resources): that page styles every `ul > li` as a bordered, padded flex row,
 * and a section is an <li> — so it would grow a second divider above 所有 and its
 * group would shrink to its content (review of P7).
 */
import "@testing-library/jest-dom/vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { recordPick } from "../lib/peopleFrecency";
import { UserPicker } from "./UserPicker";

vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [
    { id: "me", name: "Me", section: "A", email: "", photo_url: null },
    { id: "amy", name: "Amy", section: "A", email: "", photo_url: null },
    { id: "ben", name: "Ben", section: "B", email: "", photo_url: null },
  ],
}));
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUserState: () => ({ id: "me", ready: true }) }));
vi.mock("./UserChip", () => ({ UserAvatar: () => <span /> }));
vi.mock("./Icon", () => ({ Icon: () => <span /> }));

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.head.querySelectorAll("style[data-test]").forEach((s) => s.remove());
});

describe("UserPicker inside a .page", () => {
  it("keeps each section a plain block: no padding, no border of its own", () => {
    const style = document.createElement("style");
    style.dataset.test = "";
    style.textContent = readFileSync(join(process.cwd(), "src/styles/my-resources.css"), "utf8");
    document.head.append(style);
    recordPick("me", "amy");
    render(
      <div className="page">
        <UserPicker selected={[]} onToggle={() => {}} exclude={["me"]} />
      </div>,
    );
    for (const name of ["常用", "所有"]) {
      const li = screen.getByRole("group", { name }).closest("li") as HTMLElement;
      const cs = getComputedStyle(li);
      expect(cs.display).toBe("block");
      expect(cs.borderTopStyle === "none" || cs.borderTopWidth === "0px").toBe(true);
      expect(cs.paddingTop).toBe("0px");
    }
  });
});
