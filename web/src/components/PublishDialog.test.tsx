// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Estimate, PublishChoices } from "../api/publish";
import { renderWithQuery } from "../test/queryWrapper";
import { PublishDialog } from "./PublishDialog";

// The picker reads users and groups from the server; neither is what these
// tests are about.
vi.mock("../hooks/usePickableGroups", () => ({
  usePickableGroups: () => [{ resource_id: "g-ops", name: "Ops" }],
}));
const pickerProps: Array<{ related?: string[] }> = [];
vi.mock("./UserPicker", () => ({
  UserPicker: (props: { onToggle: (id: string) => void; related?: string[] }) => {
    pickerProps.push(props);
    return (
      <button type="button" onClick={() => props.onToggle("amy")}>
        pick amy
      </button>
    );
  },
}));
// The item's people (plan-people-picker-ranking D4).
vi.mock("../hooks/useItemPeople", () => ({
  useItemPeople: (slug: string, itemId: string) => (slug === "rca" && itemId === "i-1" ? ["ann", "bo"] : []),
}));

const SMALL: Estimate = { files: 1200, bytes: 3 * 1024 ** 2, biggest: [] };

function open(props: Partial<Parameters<typeof PublishDialog>[0]> = {}, estimate: Estimate = SMALL) {
  const onConfirm = vi.fn<(c: PublishChoices) => void>();
  const onClose = vi.fn();
  const client = { estimate: vi.fn(async () => estimate) };
  renderWithQuery(
    <PublishDialog
      kind="wui"
      slug="rca"
      itemId="i-1"
      path="/pages/a/page.ai.yaml"
      client={client}
      onConfirm={onConfirm}
      onClose={onClose}
      {...props}
    />,
  );
  return { onConfirm, onClose, client, dialog: screen.getByTestId("publish-dialog") };
}

const publishButton = (d: HTMLElement) => within(d).getByRole("button", { name: "發布" });

afterEach(() => cleanup());

describe("PublishDialog", () => {
  it("lists the item's own people first in its picker (plan-people-picker-ranking D4)", () => {
    pickerProps.length = 0;
    const { dialog } = open();
    // The picker appears once the author chooses specific people or groups.
    fireEvent.click(within(dialog).getByRole("radio", { name: /每次都是新的一份/ }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "指定的人或群組" }));
    expect(pickerProps.length).toBeGreaterThan(0);
    expect(pickerProps.at(-1)?.related).toEqual(["ann", "bo"]);
  });


  it("does not pick a way of sharing for the author, and says why Publish waits", () => {
    const { dialog, onConfirm } = open();

    const sharing = within(dialog).getByRole("group", { name: "別人怎麼用這個頁面" });
    for (const radio of within(sharing).getAllByRole("radio")) expect(radio).not.toBeChecked();
    expect(publishButton(dialog)).toBeDisabled();
    expect(within(dialog).getByRole("status")).toHaveTextContent("先選別人怎麼用這個頁面。");
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("shows each way of sharing with its cost beside it", () => {
    const { dialog } = open();

    const fresh = within(dialog).getByRole("radio", { name: /每次都是新的一份/ });
    expect(fresh.closest("label")!.textContent!.length).toBeGreaterThan("每次都是新的一份".length);
    expect(within(dialog).getAllByRole("radio", { name: /一份|workspace/ })).toHaveLength(4);
  });

  it("using the workspace directly asks nothing else and publishes for whoever may read it", () => {
    // Seeded with a reach the skill wrote: a direct page has no reach of its
    // own (the source's permission is), so none is sent.
    const { dialog, onConfirm, client } = open({ initial: { reach: "everyone", subjects: ["user:amy"] } });

    fireEvent.click(within(dialog).getByRole("radio", { name: /直接用我的 workspace/ }));

    expect(within(dialog).queryByRole("radio", { name: "所有登入的人" })).toBeNull();
    expect(within(dialog).queryByText("副本的沙盒大小")).toBeNull();
    expect(client.estimate).not.toHaveBeenCalled();
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ sharing: "direct", reach: "me", subjects: [], cpu_cores: null, memory_bytes: null }),
    );
  });

  it("a copying way asks who may use it, the sandbox size, and shows what a copy holds", async () => {
    const { dialog, onConfirm } = open();

    fireEvent.click(within(dialog).getByRole("radio", { name: /每個人一份/ }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "所有登入的人" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "CPU 核心數" }), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "記憶體（GB）" }), { target: { value: "4" } });

    expect(await within(dialog).findByTestId("publish-estimate")).toHaveTextContent("1200");
    expect(within(dialog).queryByRole("note")).toBeNull();
    // The role is asked only of the copy everyone shares.
    expect(within(dialog).queryByRole("radio", { name: "只能看" })).toBeNull();
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({
        sharing: "per_person",
        reach: "everyone",
        cpu_cores: 2,
        memory_bytes: 4 * 1024 ** 3,
      }),
    );
  });

  it("people or groups must name at least one before Publish is available", () => {
    const { dialog, onConfirm } = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /每次都是新的一份/ }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "指定的人或群組" }));

    expect(publishButton(dialog)).toBeDisabled();
    expect(within(dialog).getByRole("status")).toHaveTextContent("先選至少一個人或群組。");

    fireEvent.click(within(dialog).getByRole("button", { name: "pick amy" }));
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Ops" }));
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ reach: "subjects", subjects: ["user:amy", "group:g-ops"] }),
    );
  });

  it("asks what people may do only in the copy everyone shares", () => {
    const { dialog, onConfirm } = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /大家共用一份/ }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "所有登入的人" }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "只能看" }));

    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ sharing: "shared", role: "reader" }));
  });

  it("refuses a sandbox size that is not a number above 0", () => {
    const { dialog } = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /每個人一份/ }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "CPU 核心數" }), { target: { value: "0" } });

    expect(publishButton(dialog)).toBeDisabled();
    expect(within(dialog).getByRole("status")).toHaveTextContent("沙盒大小要是大於 0 的數字。");
  });

  it("names the biggest folders when a copy would hold many files", async () => {
    const { dialog } = open(
      {},
      {
        files: 52_000,
        bytes: 2 * 1024 ** 3,
        biggest: [
          { folder: "/node_modules", files: 50_000, bytes: 1024 ** 3 },
          { folder: "/data", files: 1500, bytes: 1024 ** 2 },
        ],
      },
    );
    fireEvent.click(within(dialog).getByRole("radio", { name: /每次都是新的一份/ }));

    const note = await within(dialog).findByRole("note");
    expect(note).toHaveTextContent("/node_modules");
    expect(note).toHaveTextContent("/data");
    // ~1.2 ms a file: 52 000 files is about a minute.
    expect(within(dialog).getByTestId("publish-estimate")).toHaveTextContent("62");
  });

  it("starts from what the view file's publish: says, and counts that as chosen", () => {
    const { dialog, onConfirm } = open({ initial: { sharing: "fresh", reach: "everyone" } });

    expect(within(dialog).getByRole("radio", { name: /每次都是新的一份/ })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "所有登入的人" })).toBeChecked();
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ sharing: "fresh", reach: "everyone" }));
  });

  it("does not pre-select an agent's way of sharing for a WUI", () => {
    const { dialog } = open({ initial: { sharing: "per_conversation" } });

    const sharing = within(dialog).getByRole("group", { name: "別人怎麼用這個頁面" });
    for (const radio of within(sharing).getAllByRole("radio")) expect(radio).not.toBeChecked();
    expect(publishButton(dialog)).toBeDisabled();
  });

  it("closes at once when nothing was changed from what it opened with", () => {
    const { dialog, onClose } = open({ initial: { sharing: "fresh", reach: "everyone" } });

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("dialog-action-keep")).toBeNull();
  });

  it("asks once before Cancel or Escape throw away a choice", async () => {
    const { dialog, onClose } = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /每個人一份/ }));

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(await screen.findByTestId("dialog-action-keep")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("dialog-action-keep"));
    await waitFor(() => expect(screen.queryByTestId("dialog-action-keep")).toBeNull());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(await screen.findByTestId("dialog-action-keep")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("an agent asks no way of sharing — one copy per conversation — and offers a release note on a re-publish", () => {
    const { dialog, onConfirm } = open({ kind: "agent", path: "/agents/sales/agent.ai.yaml", republish: true });

    expect(within(dialog).getByRole("heading")).toHaveTextContent("發布 agent");
    expect(within(dialog).queryByRole("radio", { name: /每個人一份/ })).toBeNull();
    fireEvent.click(within(dialog).getByRole("radio", { name: "所有登入的人" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "這次改了什麼（選填）" }), {
      target: { value: "  Answers in Chinese now. " },
    });
    fireEvent.click(publishButton(dialog));
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ sharing: "per_conversation", reach: "everyone", note: "Answers in Chinese now." }),
    );
  });

  it("offers no release note on an agent's first publish", () => {
    const { dialog } = open({ kind: "agent", path: "/agents/sales/agent.ai.yaml" });

    expect(within(dialog).queryByRole("textbox", { name: "這次改了什麼（選填）" })).toBeNull();
  });
});
