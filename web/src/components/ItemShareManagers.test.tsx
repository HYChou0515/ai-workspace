// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ItemShareManagers } from "./ItemShareManagers";

/**
 * The management grant, presented apart from the role ladder.
 *
 * It is not one more degree of access: it lets someone regrant the item to
 * anybody, and — since per-item environment sizing — decide how much of the
 * OWNER's quota it spends. Offering it as a sixth rung of a nested dropdown
 * would hide both consequences behind "a bit more than Collaborator".
 */

afterEach(cleanup);

describe("ItemShareManagers", () => {
  it("says what the grant actually does before anyone gives it away", () => {
    render(<ItemShareManagers managers={[]} onChange={() => {}} />);

    const warning = screen.getByTestId("managers-consequence").textContent ?? "";
    expect(warning).toMatch(/存取權|access/);
    expect(warning).toMatch(/額度|quota/); // the half that is new, and easy to miss
  });

  it("lists who currently holds it, and can take it back", () => {
    const onChange = vi.fn();
    render(<ItemShareManagers managers={["bob", "carol"]} onChange={onChange} />);

    expect(screen.getByText("bob")).toBeTruthy();
    fireEvent.click(screen.getAllByTestId("manager-remove")[0]);

    expect(onChange).toHaveBeenCalledWith(["carol"]);
  });

  it("adds a person without disturbing the others", () => {
    const onChange = vi.fn();
    render(<ItemShareManagers managers={["bob"]} onChange={onChange} />);

    fireEvent.change(screen.getByTestId("manager-add"), { target: { value: "dave" } });
    fireEvent.submit(screen.getByTestId("manager-add-form"));

    expect(onChange).toHaveBeenCalledWith(["bob", "dave"]);
  });

  it("refuses to add the same person twice", () => {
    // A duplicate subject is not something the backend would reject — it would
    // just sit in the list twice, with two revoke buttons that each look broken.
    const onChange = vi.fn();
    render(<ItemShareManagers managers={["bob"]} onChange={onChange} />);

    fireEvent.change(screen.getByTestId("manager-add"), { target: { value: "bob" } });
    fireEvent.submit(screen.getByTestId("manager-add-form"));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores an empty submission rather than adding a blank grant", () => {
    const onChange = vi.fn();
    render(<ItemShareManagers managers={[]} onChange={() => onChange()} />);

    fireEvent.submit(screen.getByTestId("manager-add-form"));

    expect(onChange).not.toHaveBeenCalled();
  });
});
