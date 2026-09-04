/**
 * Who may manage an item: rewire its access, and — since per-item environment
 * sizing — decide how much of the OWNER's quota it may spend.
 *
 * Deliberately NOT a sixth rung on the role ladder. The ladder is nested, so a
 * new top rung would hand every "Collaborator + a bit more" the power to regrant
 * the item to anyone, and it would sit in a dropdown as though it were one more
 * degree of the same thing. It is a different KIND of grant, so it is a separate
 * control with its own sentence.
 *
 * The backend has supported this grant since #608 (`grantsAnySubject` over
 * `change_permission`, groups included). Nothing in the UI ever offered it, so
 * in practice only the owner and superusers had it — a live authorisation path
 * with no way to use it, which is the dead-knob shape this codebase keeps
 * recording.
 */

import { describe, expect, it } from "vitest";

import { itemManagersFromPermission, withItemManagers } from "./itemManagers";

const OWNER = "alice";

describe("itemManagersFromPermission", () => {
  it("lists the people explicitly granted it", () => {
    const got = itemManagersFromPermission(
      { visibility: "restricted", change_permission: ["user:bob", "user:carol"] },
      OWNER,
    );

    expect(got).toEqual(["bob", "carol"]);
  });

  it("drops the owner, who holds it by being the owner", () => {
    // Listing them would offer a revoke that does nothing — the backend bypasses
    // the grant list for the owner entirely.
    const got = itemManagersFromPermission(
      { visibility: "restricted", change_permission: ["user:alice", "user:bob"] },
      OWNER,
    );

    expect(got).toEqual(["bob"]);
  });

  it("ignores group subjects, which round-trip untouched", () => {
    // v1 grants this to people only. A group subject is preserved on save rather
    // than silently dropped, so an operator who set one by hand keeps it.
    const got = itemManagersFromPermission(
      { visibility: "restricted", change_permission: ["group:sre", "user:bob"] },
      OWNER,
    );

    expect(got).toEqual(["bob"]);
  });

  it("is empty when nobody was granted it", () => {
    expect(itemManagersFromPermission({ visibility: "restricted" }, OWNER)).toEqual([]);
  });
});

describe("withItemManagers", () => {
  it("replaces the user grants and leaves every other verb alone", () => {
    const next = withItemManagers(
      {
        visibility: "restricted",
        read_meta: ["user:dave"],
        change_permission: ["user:bob"],
      },
      ["carol"],
    );

    expect(next.change_permission).toEqual(["user:carol"]);
    expect(next.read_meta).toEqual(["user:dave"]);
  });

  it("keeps group subjects that the dialog does not manage", () => {
    const next = withItemManagers(
      { visibility: "restricted", change_permission: ["group:sre", "user:bob"] },
      [],
    );

    expect(next.change_permission).toEqual(["group:sre"]);
  });

  it("does not mutate the permission it was given", () => {
    // The dialog holds the original to diff against; mutating it in place would
    // make "did anything change" answer no.
    const original = { visibility: "restricted" as const, change_permission: ["user:bob"] };
    withItemManagers(original, ["carol"]);

    expect(original.change_permission).toEqual(["user:bob"]);
  });
});
