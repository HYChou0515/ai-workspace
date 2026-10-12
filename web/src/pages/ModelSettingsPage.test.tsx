// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { Applied, ModelSettingsPage as PageData } from "../api/modelSettings";
import { renderWithQuery } from "../test/queryWrapper";
import { ModelSettingsPage } from "./ModelSettingsPage";

const state = vi.hoisted(() => ({ superuser: true, ready: true }));
vi.mock("../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => state.superuser,
  useIsSuperuserState: () => ({ isSuperuser: state.superuser, groups: [], ready: state.ready }),
}));
vi.mock("../hooks/breadcrumbs", () => ({ useBreadcrumbs: () => undefined }));
vi.mock("../components/MonacoEditor", () => ({
  MonacoEditor: ({
    value,
    onChange,
    readOnly,
    ariaLabel,
    markers,
  }: {
    value: string;
    onChange?: (v: string) => void;
    readOnly?: boolean;
    ariaLabel?: string;
    markers?: { line: number; message: string }[];
  }) => (
    <textarea
      aria-label={ariaLabel}
      data-markers={JSON.stringify(markers ?? [])}
      value={value}
      readOnly={readOnly}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

const KB_2 = { config: { agents: { presets: { "kb-default": { model: "openai/kb-2" } } } } };
const DEFAULTS = {
  config: {
    agents: {
      presets: {
        "kb-default": { model: "ollama_chat/qwen3:14b", llm: { api_key: "*** unset ***" } },
        "openai-mini": { model: "openai/gpt-4o-mini" },
      },
    },
  },
  apps: {},
  profiles: {},
};

function applied(version: string, patch: Record<string, unknown>, at = 1): Applied {
  return { version, patch, by: "root", applied_at: at, previous: null, keys_changed: [] };
}

function fakeClient(page: Partial<PageData> = {}) {
  let data: PageData = {
    defaults: DEFAULTS,
    draft: {},
    published: {},
    version: null,
    keys: {},
    pending_keys: [],
    ...page,
  };
  const client = {
    get: vi.fn(async () => data),
    saveDraft: vi.fn(async (patch: Record<string, unknown>) => {
      data = { ...data, draft: patch };
      return [];
    }),
    check: vi.fn(async (_patch: Record<string, unknown>) => [] as string[]),
    apply: vi.fn(async (_seen?: Record<string, unknown>) => {
      data = { ...data, published: data.draft, version: "abcdef1234567890" };
      return applied("abcdef1234567890", data.draft);
    }),
    history: vi.fn(async () => [] as Applied[]),
    rollback: vi.fn(async (version: string) => applied(version, {})),
    setKey: vi.fn(async (_path: string, _value: string) => undefined),
    clearKey: vi.fn(async (_path: string) => undefined),
    testConnection: vi.fn(async (preset: string) => ({
      ok: true,
      model: `${preset}-model`,
      ms: 12,
      reply: "OK",
      error: "",
    })),
  };
  return client;
}

const editor = () => screen.getByRole("textbox", { name: "草稿（JSON）" }) as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(editor(), { target: { value: text } });

beforeEach(() => {
  state.superuser = true;
  state.ready = true;
});

describe("ModelSettingsPage", () => {
  it("says it is not for you, and asks nothing, when you are not a superuser", async () => {
    state.superuser = false;
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);

    expect(await screen.findByTestId("model-settings-not-allowed")).toBeInTheDocument();
    expect(client.get).not.toHaveBeenCalled();
  });

  it("shows the draft in the editor and what is in force", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2, published: KB_2, version: "0123456789abcdef" })} />);

    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));
    expect(screen.getByTestId("model-settings-in-force")).toHaveTextContent("01234567");
  });

  it("names a JSON mistake once typing pauses, and saves nothing", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type("{ not json");

    expect(await screen.findByTestId("model-settings-parse")).toHaveTextContent("不是合法的 JSON");
    expect(screen.getByTestId("model-settings-save")).toBeDisabled();
  });

  it("shows what the checks Apply runs would refuse, as you type", async () => {
    const client = fakeClient();
    client.check.mockImplementation(async (patch) =>
      "server" in ((patch.config as Record<string, unknown>) ?? {}) ? ["config.server: change it in config.yaml"] : [],
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type('{"config": {"server": {"port": 1}}}');

    expect(await screen.findByTestId("model-settings-problems", {}, { timeout: 3000 })).toHaveTextContent(
      "config.server",
    );
  });

  it("saves a draft, then applies it after showing every value it changes", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type(JSON.stringify(KB_2));
    expect(screen.getByTestId("model-settings-apply")).toBeDisabled(); // unsaved: save first
    fireEvent.click(screen.getByTestId("model-settings-save"));
    await waitFor(() => expect(client.saveDraft).toHaveBeenCalledWith(KB_2));
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    const diff = await screen.findByTestId("model-settings-diff");
    expect(diff).toHaveTextContent("config.agents.presets.kb-default.model: ollama_chat/qwen3:14b → openai/kb-2");
    expect(client.apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "套用" }));

    // the draft the dialog listed goes with it, so the server can refuse another
    await waitFor(() => expect(client.apply).toHaveBeenCalledWith(KB_2));
    expect(await screen.findByTestId("model-settings-notice")).toHaveTextContent("abcdef12");
  });

  it("applies nothing when the confirmation is cancelled", async () => {
    const client = fakeClient({ draft: KB_2 });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByTestId("model-settings-diff")).toBeNull());
    expect(client.apply).not.toHaveBeenCalled();
  });

  it("offers Apply only when it would do something, and says why not", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2, published: KB_2, version: "v1v1v1v1" })} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));

    expect(screen.getByTestId("model-settings-apply")).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId("model-settings-status")).toHaveTextContent("沒有要套用的改動"));
    // said once, right above the button — not repeated beside it
    expect(screen.queryByTestId("model-settings-apply-why")).toBeNull();
  });

  it("does not offer Apply while the checks name a problem", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.check.mockResolvedValue(["config.agents.presets.kb-default.model: nope"]);
    renderWithQuery(<ModelSettingsPage client={client} />);

    expect(await screen.findByTestId("model-settings-problems")).toBeInTheDocument();
    expect(screen.getByTestId("model-settings-apply")).toBeDisabled();
  });

  it("draws each problem on the line it names", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.check.mockResolvedValue(["config.agents.presets.kb-default.model: nope"]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByTestId("model-settings-problems");

    // line 6 of the pretty-printed draft is `"model": "openai/kb-2"`
    expect(JSON.parse(editor().getAttribute("data-markers") ?? "[]")).toEqual([
      { line: 6, message: "config.agents.presets.kb-default.model: nope" },
    ]);
  });

  it("applies a key-only change and names the key in the confirmation", async () => {
    const path = "config.agents.presets.kb-default.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    await waitFor(() => expect(screen.getByTestId("model-settings-status")).toHaveTextContent("1 個金鑰"));
    fireEvent.click(screen.getByTestId("model-settings-apply"));

    expect(await screen.findByTestId("model-settings-key-diff")).toHaveTextContent(`${path} (…4321)`);
    fireEvent.click(screen.getByRole("button", { name: "套用" }));
    await waitFor(() => expect(client.apply).toHaveBeenCalled());
  });

  it("says when the check itself failed, instead of reporting no problems", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.check.mockRejectedValue(new HttpError(500, "check failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);

    expect(await screen.findByTestId("model-settings-check-failed")).toBeInTheDocument();
    expect(screen.queryByTestId("model-settings-status")).toBeNull();
    expect(screen.getByTestId("model-settings-apply")).toBeDisabled();
  });

  it("shows why an Apply failed even when the server named no problem", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.apply.mockRejectedValue(new HttpError(500, "apply failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    expect(await screen.findByTestId("model-settings-problems")).toHaveTextContent("apply failed: 500");
  });

  it("shows why a save failed", async () => {
    const client = fakeClient();
    client.saveDraft.mockRejectedValue(new HttpError(500, "saving the draft failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type(JSON.stringify(KB_2));
    fireEvent.click(screen.getByTestId("model-settings-save"));

    expect(await screen.findByTestId("model-settings-save-failed")).toHaveTextContent("saving the draft failed");
  });

  it("does not offer to test a preset the draft hides", async () => {
    renderWithQuery(
      <ModelSettingsPage client={fakeClient({ draft: { config: { agents: { presets: { "openai-mini": null } } } } })} />,
    );
    const select = (await screen.findByLabelText("要測試的 preset")) as HTMLSelectElement;

    expect([...select.options].map((o) => o.value)).toEqual(["kb-default"]);
  });

  it("is not stuck on unsaved edits when the saved draft says the same in other spacing", async () => {
    // review r2 (ux): dirtiness compared the TEXT, so a save of the same content
    // left "unsaved edits" on with Apply off for good — CLAUDE.md: sameShape.
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));

    type(JSON.stringify(KB_2)); // one line instead of pretty-printed

    expect(screen.getByTestId("model-settings-save")).toBeDisabled();
    expect(screen.queryByTestId("model-settings-apply-why")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());
  });

  it("says to fix the JSON, not to save, when the text does not parse", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type("{ not json");

    await waitFor(() => expect(screen.getByTestId("model-settings-apply-why")).toHaveTextContent("先修正 JSON"));
  });

  it("waits for a pause in typing before calling the JSON wrong", async () => {
    // review r2 (ux): the banner came at every keystroke (NN/g: not too early).
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type('{"config": ');

    expect(screen.queryByTestId("model-settings-parse")).toBeNull();
    expect(await screen.findByTestId("model-settings-parse")).toBeInTheDocument();
  });

  it("keeps unsaved edits across a switch of tabs", async () => {
    // review r2 (ux): the editor's text lived in the Edit tab, so looking at
    // the keys and coming back threw it away without a word.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type(JSON.stringify(KB_2));

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));
    fireEvent.click(screen.getByRole("tab", { name: "編輯（未儲存）" }));

    expect(JSON.parse(editor().value)).toEqual(KB_2);
  });

  it("shows which keys wait for Apply, and applies them from the keys tab", async () => {
    const set = "config.agents.presets.kb-default.llm.api_key";
    const cleared = "config.llm.api_key";
    const client = fakeClient({ keys: { [set]: "…4321" }, pending_keys: [cleared, set] });
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId(`model-settings-key-pending-${set}`)).toHaveTextContent("等待套用");
    expect(screen.getByTestId(`model-settings-key-pending-${cleared}`)).toHaveTextContent("已清除");
    fireEvent.click(await screen.findByTestId("model-settings-keys-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    await waitFor(() => expect(client.apply).toHaveBeenCalled());
  });

  it("names a value that replaces a deploy default as changed, from that default", async () => {
    const client = fakeClient({ draft: KB_2 });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));

    expect(await screen.findByTestId("model-settings-diff")).toHaveTextContent(
      "config.agents.presets.kb-default.model: ollama_chat/qwen3:14b → openai/kb-2",
    );
  });

  it("labels the key path with visible text, not only a placeholder", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByText("設定路徑")).toBeVisible();
    expect(screen.getByLabelText("設定路徑")).toBe(screen.getByTestId("model-settings-key-path"));
  });

  it("says a short key is set, in the reader's language", async () => {
    // review r3: the server's short-key marker was shown raw ("set").
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { "config.llm.api_key": "" } })} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys")).toHaveTextContent("已設定");
  });

  it("from the keys tab, says the Edit tab holds unsaved edits and leads there", async () => {
    // review r3 (ux): Apply on the keys tab was blocked by edits only the Edit
    // tab can save, with nothing saying so — and the Edit tab looked clean.
    const path = "config.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type(JSON.stringify(KB_2));

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByRole("tab", { name: "編輯（未儲存）" })).toBeInTheDocument();
    expect(screen.getByTestId("model-settings-keys-apply-why")).toHaveTextContent("編輯分頁有沒儲存的改動");
    fireEvent.click(screen.getByTestId("model-settings-go-edit"));
    expect(screen.getByRole("textbox", { name: "草稿（JSON）" })).toBeInTheDocument();
  });

  it("from the keys tab, does not point at a list of problems it does not show", async () => {
    const path = "config.llm.api_key";
    const client = fakeClient({ draft: KB_2, keys: { [path]: "…4321" }, pending_keys: [path] });
    client.check.mockResolvedValue(["config.agents.presets.kb-default.model: nope"]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByTestId("model-settings-problems");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    const why = screen.getByTestId("model-settings-keys-apply-why");
    expect(why).toHaveTextContent("編輯分頁的草稿有問題");
    expect(why).not.toHaveTextContent("上面");
  });

  it("does not call the JSON wrong beside Apply before typing pauses", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type('{"config": ');

    expect(screen.getByTestId("model-settings-apply-why")).not.toHaveTextContent("JSON");
    await waitFor(() => expect(screen.getByTestId("model-settings-apply-why")).toHaveTextContent("先修正 JSON"));
  });

  it("does not roll back over unsaved edits", async () => {
    // review r3: the rolled-back draft replaced the editor's unsaved text.
    const client = fakeClient({ published: KB_2, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([applied("v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));

    expect(await screen.findByTestId("model-settings-rollback-1")).toBeDisabled();
    expect(screen.getByTestId("model-settings-rollback-why")).toHaveTextContent("沒儲存的改動");
  });

  it("says what a rollback did, and offers none that would change nothing", async () => {
    const client = fakeClient({ published: KB_2, version: "v3v3v3v3v3" });
    client.history.mockResolvedValue([
      applied("v3v3v3v3v3", KB_2, 3),
      applied("v2v2v2v2v2", KB_2, 2), // the same values: rolling back changes nothing
      applied("v1v1v1v1v1", {}, 1),
    ]);
    client.rollback.mockResolvedValue(applied("v4v4v4v4v4", {}, 4));
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));
    await screen.findByTestId("model-settings-history");

    expect(screen.queryByTestId("model-settings-rollback-1")).toBeNull();
    fireEvent.click(screen.getByTestId("model-settings-rollback-2"));
    fireEvent.click(await screen.findByRole("button", { name: "回復" }));

    expect(await screen.findByTestId("model-settings-rolled-back")).toHaveTextContent("v1v1v1v1");
  });

  it("keeps unsaved edits when the saved draft changes under them", async () => {
    // confirmation round: any refetch with another superuser's draft replaced
    // the text being typed, without a word.
    const client = fakeClient();
    const { client: queryClient } = renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    const theirs = { config: { llm: { base_url: "http://theirs" } } };
    client.get.mockImplementation(async () => ({
      defaults: {},
      draft: theirs,
      published: {},
      version: null,
      keys: {},
      pending_keys: [],
    }));
    await act(async () => queryClient.invalidateQueries());

    expect(JSON.parse(editor().value)).toEqual({ config: { llm: { base_url: "http://mine" } } });
  });

  it("says why a rollback is off without suggesting that saving would help", async () => {
    // confirmation round: "save or undo" — but a rollback replaces a saved
    // draft too, so saving did not protect the edits.
    const client = fakeClient({ published: KB_2, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([applied("v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));

    const why = await screen.findByTestId("model-settings-rollback-why");
    expect(why).toHaveTextContent("放棄改動");
    expect(why).toHaveTextContent("存起來也會被換掉");
  });

  it("from the keys tab, says the check failed rather than that the draft has problems", async () => {
    const path = "config.llm.api_key";
    const client = fakeClient({ draft: KB_2, keys: { [path]: "…4321" }, pending_keys: [path] });
    client.check.mockRejectedValue(new HttpError(500, "check failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByTestId("model-settings-check-failed");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys-apply-why")).toHaveTextContent("沒辦法檢查");
  });

  it("from the keys tab, says the Edit tab's text is not JSON instead of asking to save it", async () => {
    const path = "config.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] })} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });
    type("{ not json");
    await screen.findByTestId("model-settings-parse");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys-apply-why")).toHaveTextContent("不是合法的 JSON");
  });

  it("confirms an Apply made from the keys tab on the keys tab", async () => {
    // confirmation round: the notice lived in the pending-only row, which an
    // Apply empties — so the tab acted on went blank.
    const path = "config.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] });
    client.apply.mockImplementation(async () => {
      // what the server does: the key is in force, nothing waits any more
      client.get.mockImplementation(async () => ({
        defaults: {},
        draft: {},
        published: {},
        version: "abcdef1234567890",
        keys: { [path]: "…4321" },
        pending_keys: [],
      }));
      return applied("abcdef1234567890", {});
    });
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(await screen.findByTestId("model-settings-keys-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));
    // once the page has re-read: nothing waits, so the pending row is gone
    await waitFor(() => expect(screen.queryByTestId("model-settings-keys-apply")).toBeNull());

    expect(screen.getByTestId("model-settings-notice")).toHaveTextContent("abcdef12");
  });

  it("says why a key could not be cleared", async () => {
    const path = "config.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" } });
    client.clearKey.mockRejectedValue(new HttpError(500, "clearing the key failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(screen.getByTestId(`model-settings-key-clear-${path}`));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "清除" }));

    expect(await screen.findByTestId("model-settings-key-refused")).toHaveTextContent("clearing the key failed");
  });

  it("discards unsaved edits with one button, also after a tab switch", async () => {
    // final review (ux): the messages said "undo", but the editor's undo history
    // is gone once a tab switch unmounts it — NN/g heuristic #3, user control.
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));
    fireEvent.click(screen.getByRole("tab", { name: "編輯（未儲存）" }));

    fireEvent.click(screen.getByTestId("model-settings-discard"));

    expect(JSON.parse(editor().value)).toEqual(KB_2);
    expect(screen.getByRole("tab", { name: "編輯" })).toBeInTheDocument();
  });

  it("shows what each version changed from the one before it", async () => {
    // final review (ux): M5 promises each version's diff from the previous one;
    // the rows showed only hashes.
    const v1 = { ...applied("v1v1v1v1v1", KB_2, 1), previous: null };
    const later = { config: { agents: { presets: { "kb-default": { model: "openai/kb-3" } } } } };
    const v2 = { ...applied("v2v2v2v2v2", later, 2), previous: "v1v1v1v1v1" };
    const client = fakeClient({ published: later, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([v2, v1]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    const first = await screen.findByTestId("model-settings-version-changes-0");
    expect(first).toHaveTextContent("openai/kb-2 → openai/kb-3");
    expect(screen.getByTestId("model-settings-version-changes-1")).toHaveTextContent(
      "ollama_chat/qwen3:14b → openai/kb-2",
    );
  });

  it("keeps the path format in helper text, not only in the placeholder", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    const field = screen.getByLabelText("設定路徑");
    expect(field).not.toHaveAttribute("placeholder");
    expect(field).toHaveAccessibleDescription(/config\.agents\.presets\.openai-mini\.llm\.api_key/);
  });

  it("lists a refused Apply's reasons", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.apply.mockRejectedValue(
      new HttpError(422, "apply failed", undefined, undefined, { problems: ["config.kb: read at boot"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    expect(await screen.findByTestId("model-settings-problems")).toHaveTextContent("config.kb: read at boot");
  });

  it("lists each changed value beside its default, and resets one", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(KB_2));

    const changes = screen.getByTestId("model-settings-changes");
    expect(changes).toHaveTextContent("openai/kb-2");
    expect(changes).toHaveTextContent("預設：ollama_chat/qwen3:14b");

    fireEvent.click(screen.getByTestId("model-settings-reset-config.agents.presets.kb-default.model"));

    expect(JSON.parse(editor().value)).toEqual({});
  });

  it("resets a value of a preset whose name holds a dot", async () => {
    // review r1: a joined path split back on "." read `qwen2.5` as `qwen2` -> `5`.
    const dotted = { config: { agents: { presets: { "qwen2.5": { model: "m" } } } } };
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: dotted })} />);
    await waitFor(() => expect(JSON.parse(editor().value)).toEqual(dotted));

    fireEvent.click(screen.getByTestId("model-settings-reset-config.agents.presets.qwen2.5.model"));

    expect(JSON.parse(editor().value)).toEqual({});
  });

  it("tests a preset's connection", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    fireEvent.change(screen.getByLabelText("要測試的 preset"), { target: { value: "openai-mini" } });
    fireEvent.click(screen.getByTestId("model-settings-test"));

    expect(await screen.findByTestId("model-settings-test-result")).toHaveTextContent("openai-mini-model");
    expect(client.testConnection).toHaveBeenCalledWith("openai-mini");
  });

  it("rolls back to an earlier version after confirming", async () => {
    const client = fakeClient({ published: KB_2, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([applied("v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    const history = await screen.findByTestId("model-settings-history");
    expect(within(history).getByText("目前")).toBeInTheDocument();
    expect(screen.queryByTestId("model-settings-rollback-0")).toBeNull(); // the one in force
    fireEvent.click(screen.getByTestId("model-settings-rollback-1"));
    expect(await screen.findByTestId("model-settings-diff")).toHaveTextContent("config.agents.presets.kb-default.model");
    fireEvent.click(screen.getByRole("button", { name: "回復" }));

    await waitFor(() => expect(client.rollback).toHaveBeenCalledWith("v1v1v1v1v1"));
  });

  it("sets a key in a masked field and shows only its last four characters", async () => {
    const client = fakeClient({ keys: { "config.llm.api_key": "…9876" } });
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys")).toHaveTextContent("…9876");
    const value = screen.getByTestId("model-settings-key-value") as HTMLInputElement;
    expect(value.type).toBe("text");
    expect(value).toHaveAttribute("data-masked");
    fireEvent.change(screen.getByTestId("model-settings-key-path"), {
      target: { value: "config.agents.presets.kb-default.llm.api_key" },
    });
    fireEvent.change(value, { target: { value: "sk-new" } });
    await act(async () => fireEvent.click(screen.getByTestId("model-settings-key-set")));

    expect(client.setKey).toHaveBeenCalledWith("config.agents.presets.kb-default.llm.api_key", "sk-new");
  });

  it("names why a key was refused", async () => {
    const client = fakeClient();
    client.setKey.mockRejectedValue(
      new HttpError(422, "refused", undefined, undefined, { problems: ["config.x.model: not a secret"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));
    fireEvent.change(screen.getByTestId("model-settings-key-path"), { target: { value: "config.x.model" } });
    fireEvent.change(screen.getByTestId("model-settings-key-value"), { target: { value: "v" } });
    fireEvent.click(screen.getByTestId("model-settings-key-set"));

    expect(await screen.findByTestId("model-settings-key-refused")).toHaveTextContent("not a secret");
  });
});
