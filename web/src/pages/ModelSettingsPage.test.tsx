// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { Applied, ModelSettingsPage as PageData } from "../api/modelSettings";
import { parsePatch, patchOf } from "../lib/settingsPatch";
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
    autoHeight,
  }: {
    value: string;
    onChange?: (v: string) => void;
    readOnly?: boolean;
    ariaLabel?: string;
    markers?: { line: number; message: string }[];
    autoHeight?: boolean;
  }) => (
    <textarea
      aria-label={ariaLabel}
      data-markers={JSON.stringify(markers ?? [])}
      data-auto-height={autoHeight ? "true" : "false"}
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
    applied_keys: [],
    key_revision: "rev-1",
    boot_config: (page.defaults ?? DEFAULTS).config ?? {},
    key_slots: [
      { path: "config.llm.api_key", owner: "", kind: "api_key", name: "" },
      { path: "config.agents.presets.kb-default.llm.api_key", owner: "kb-default", kind: "api_key", name: "" },
      { path: "config.agents.presets.kb-default.env.HF_TOKEN", owner: "kb-default", kind: "env", name: "HF_TOKEN" },
    ],
    ...page,
  };
  const client = {
    get: vi.fn(async () => data),
    saveDraft: vi.fn(async (patch: Record<string, unknown>) => {
      data = { ...data, draft: patch };
      return [];
    }),
    check: vi.fn(async (_patch: Record<string, unknown>) => [] as string[]),
    apply: vi.fn(async (_seen?: Record<string, unknown>, _pending?: string[]) => {
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
      error: "",
    })),
  };
  return client;
}

const editor = () => screen.getByRole("textbox", { name: "設定（YAML）" }) as HTMLTextAreaElement;
/** The draft the editor holds, read as the page reads it: YAML (M9) holding
 * the whole settings, stored as what differs from the defaults (M23). */
const draftNow = (defaults: Record<string, unknown> = DEFAULTS) => {
  const parsed = parsePatch(editor().value);
  if (!parsed.ok) throw new Error(parsed.error);
  return patchOf(defaults, parsed.patch);
};
const type = (text: string) => fireEvent.change(editor(), { target: { value: text } });
const SLOT = "config.agents.presets.kb-default.llm.api_key";
/** Open a place's row and type the key into it — M22: a key is picked, never typed as a path. */
const setOnRow = (path: string, value: string) => {
  fireEvent.click(screen.getByTestId(`model-settings-key-edit-${path}`));
  fireEvent.change(screen.getByTestId("model-settings-key-value"), { target: { value } });
};

beforeEach(() => {
  state.superuser = true;
  state.ready = true;
  // unsaved edits are kept for the session; one test's must not reach the next
  sessionStorage.clear();
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

    await waitFor(() => expect(draftNow()).toEqual(KB_2));
    expect(screen.getByTestId("model-settings-in-force")).toHaveTextContent("01234567");
  });

  it("names a YAML mistake once typing pauses, and saves nothing", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type("{ not json");

    expect(await screen.findByTestId("model-settings-parse")).toHaveTextContent("不是合法的 YAML");
    expect(screen.getByTestId("model-settings-save")).toBeDisabled();
  });

  it("shows what the checks Apply runs would refuse, as you type", async () => {
    const client = fakeClient();
    client.check.mockImplementation(async (patch) =>
      "server" in ((patch.config as Record<string, unknown>) ?? {}) ? ["config.server: change it in config.yaml"] : [],
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type('{"config": {"server": {"port": 1}}}');

    expect(await screen.findByTestId("model-settings-problems", {}, { timeout: 3000 })).toHaveTextContent(
      "config.server",
    );
  });

  it("saves a draft, then applies it after showing every value it changes", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type(JSON.stringify(KB_2));
    expect(screen.getByTestId("model-settings-apply")).toBeDisabled(); // unsaved: save first
    fireEvent.click(screen.getByTestId("model-settings-save"));
    await waitFor(() => expect(client.saveDraft).toHaveBeenCalledWith(KB_2, {}));
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    const diff = await screen.findByTestId("model-settings-diff");
    expect(diff).toHaveTextContent("config.agents.presets.kb-default.model: ollama_chat/qwen3:14b → openai/kb-2");
    expect(client.apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "套用" }));

    // the draft the dialog listed goes with it, so the server can refuse another
    await waitFor(() => expect(client.apply).toHaveBeenCalledWith(KB_2, [], "rev-1"));
    expect(await screen.findByTestId("model-settings-notice")).toHaveTextContent("abcdef12");
  });

  it("applies nothing when the confirmation is cancelled", async () => {
    const client = fakeClient({ draft: KB_2 });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByTestId("model-settings-diff")).toBeNull());
    expect(client.apply).not.toHaveBeenCalled();
  });

  it("offers Apply only when it would do something, and says why not", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2, published: KB_2, version: "v1v1v1v1" })} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

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

    // line 5 of the draft as the page writes it (YAML) is `model: openai/kb-2`
    expect(JSON.parse(editor().getAttribute("data-markers") ?? "[]")).toEqual([
      { line: 5, message: "config.agents.presets.kb-default.model: nope" },
    ]);
  });

  it("applies a key-only change and names the key in the confirmation", async () => {
    const path = "config.agents.presets.kb-default.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

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

  it("shows why an Apply failed even when the server named no problem, and lets it be retried", async () => {
    // final review 4: a failure that names no problem (a 409, a 500) was listed
    // as a problem, and Apply stayed blocked with nothing to fix.
    const client = fakeClient({ draft: KB_2 });
    client.apply.mockRejectedValueOnce(new HttpError(500, "apply failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    expect(await screen.findByTestId("model-settings-apply-failed")).toHaveTextContent("apply failed: 500");
    expect(screen.queryByTestId("model-settings-problems")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());
  });

  it("keeps the page and its unsaved edits when a refresh fails", async () => {
    // final review 4: a failed background refetch swapped the whole page for
    // "load failed", and the text being typed went with it.
    const client = fakeClient();
    const { client: queryClient } = renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    client.get.mockRejectedValue(new HttpError(503, "down"));
    await act(async () => queryClient.invalidateQueries());

    expect(draftNow()).toEqual({ config: { llm: { base_url: "http://mine" } } });
    expect(screen.getByTestId("model-settings-refresh-failed")).toBeInTheDocument();
  });

  it("shows an empty text value as empty quotes, not as nothing", async () => {
    // final review 4: `Default: ` with nothing after it looked like missing data.
    const client = fakeClient({
      defaults: { config: { llm: { base_url: "" } } },
      draft: { config: { llm: { base_url: "http://x" } } },
    });
    renderWithQuery(<ModelSettingsPage client={client} />);

    expect(await screen.findByTestId("model-settings-changes")).toHaveTextContent('""');
  });

  it("lets a long value wrap instead of widening the page", async () => {
    // final review 4: hiding a preset printed the whole preset on one line —
    // the page scrolled sideways and the Apply dialog clipped it.
    const client = fakeClient({ draft: KB_2 });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));

    const item = (await screen.findByTestId("model-settings-diff")).querySelector("li");
    expect(item?.style.overflowWrap).toBe("anywhere");
    const row = screen.getByTestId("model-settings-changes").querySelector("li > span");
    expect((row as HTMLElement | null)?.style.overflowWrap).toBe("anywhere");
  });

  it("shows why a save failed", async () => {
    const client = fakeClient();
    client.saveDraft.mockRejectedValue(new HttpError(500, "saving the draft failed: 500"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

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
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

    type(JSON.stringify(KB_2)); // one line instead of pretty-printed

    expect(screen.getByTestId("model-settings-save")).toBeDisabled();
    expect(screen.queryByTestId("model-settings-apply-why")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());
  });

  it("says to fix the YAML, not to save, when the text does not parse", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type("{ not json");

    await waitFor(() => expect(screen.getByTestId("model-settings-apply-why")).toHaveTextContent("先修正 YAML"));
  });

  it("waits for a pause in typing before calling the YAML wrong", async () => {
    // review r2 (ux): the banner came at every keystroke (NN/g: not too early).
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type('{"config": ');

    expect(screen.queryByTestId("model-settings-parse")).toBeNull();
    expect(await screen.findByTestId("model-settings-parse")).toBeInTheDocument();
  });

  it("keeps unsaved edits across a switch of tabs", async () => {
    // review r2 (ux): the editor's text lived in the Edit tab, so looking at
    // the keys and coming back threw it away without a word.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type(JSON.stringify(KB_2));

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));
    fireEvent.click(screen.getByRole("tab", { name: "編輯（未儲存）" }));

    expect(draftNow()).toEqual(KB_2);
  });

  it("puts the keys tab's Apply above the list of places, where it is seen", async () => {
    // review 18 (UX): with every place listed (M22) the list runs ~16 rows, and
    // the Apply under it sat below the fold after a key was set.
    const client = fakeClient({ keys: { [SLOT]: "…4321" }, pending_keys: [SLOT] });
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    const apply = await screen.findByTestId("model-settings-keys-apply");
    const list = screen.getByTestId("model-settings-keys");
    expect(list.compareDocumentPosition(apply) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
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

  it("offers each place a key can go by name, with no path to type", async () => {
    // M22 (user: "要手打設定路徑" was counter-intuitive): NN/g heuristic #6,
    // recognition rather than recall — the places are listed, the person picks.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    const list = screen.getByTestId("model-settings-keys");
    expect(list).toHaveTextContent("kb-default · API 金鑰");
    expect(list).toHaveTextContent("kb-default · HF_TOKEN");
    expect(list).toHaveTextContent("預設（llm） · API 金鑰");
    expect(screen.queryByTestId("model-settings-key-path")).toBeNull();
    expect(within(list).queryByRole("textbox")).toBeNull();
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
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type(JSON.stringify(KB_2));

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByRole("tab", { name: "編輯（未儲存）" })).toBeInTheDocument();
    expect(screen.getByTestId("model-settings-keys-apply-why")).toHaveTextContent("編輯分頁有沒儲存的改動");
    fireEvent.click(screen.getByTestId("model-settings-go-edit"));
    expect(screen.getByRole("textbox", { name: "設定（YAML）" })).toBeInTheDocument();
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

  it("does not call the YAML wrong beside Apply before typing pauses", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type('{"config": ');

    expect(screen.getByTestId("model-settings-apply-why")).not.toHaveTextContent("YAML");
    await waitFor(() => expect(screen.getByTestId("model-settings-apply-why")).toHaveTextContent("先修正 YAML"));
  });

  it("does not roll back over unsaved edits", async () => {
    // review r3: the rolled-back draft replaced the editor's unsaved text.
    const client = fakeClient({ published: KB_2, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([applied("v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
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
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    const theirs = { config: { llm: { base_url: "http://theirs" } } };
    client.get.mockImplementation(async () => ({
      defaults: {},
      draft: theirs,
      published: {},
      version: null,
      keys: {},
      pending_keys: [],
      applied_keys: [],
      key_revision: "rev-1",
      boot_config: {},
      key_slots: [],
    }));
    await act(async () => queryClient.invalidateQueries());

    expect(draftNow()).toEqual({ config: { llm: { base_url: "http://mine" } } });
  });

  it("says why a rollback is off without suggesting that saving would help", async () => {
    // confirmation round: "save or undo" — but a rollback replaces a saved
    // draft too, so saving did not protect the edits.
    const client = fakeClient({ published: KB_2, version: "v2v2v2v2v2" });
    client.history.mockResolvedValue([applied("v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
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

  it("from the keys tab, says the Edit tab's text is not YAML instead of asking to save it", async () => {
    const path = "config.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] })} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type("{ not json");
    await screen.findByTestId("model-settings-parse");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys-apply-why")).toHaveTextContent("不是合法的 YAML");
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
        applied_keys: [path],
        key_revision: "rev-2",
        boot_config: {},
        key_slots: [],
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

    expect(await screen.findByTestId(`model-settings-key-clear-failed-${path}`)).toHaveTextContent(
      "clearing the key failed",
    );
  });

  it("discards unsaved edits from one button, also after a tab switch", async () => {
    // final review (ux): the messages said "undo", but the editor's undo history
    // is gone once a tab switch unmounts it — NN/g heuristic #3, user control.
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));
    fireEvent.click(screen.getByRole("tab", { name: "編輯（未儲存）" }));

    fireEvent.click(screen.getByTestId("model-settings-discard"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放棄變更" }));

    await waitFor(() => expect(draftNow()).toEqual(KB_2));
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

  it("still lists a key whose place is gone, so it can be cleared", async () => {
    const orphan = "config.agents.presets.gone.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { [orphan]: "…1111" } })} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId("model-settings-keys")).toHaveTextContent(orphan);
    expect(screen.getByTestId(`model-settings-key-clear-${orphan}`)).toBeInTheDocument();
  });

  it("adds a secret under a new env name: the one thing typed, the preset picked", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.change(screen.getByLabelText("Preset"), { target: { value: "kb-default" } });
    fireEvent.change(screen.getByLabelText("變數名稱"), { target: { value: "GH_TOKEN" } });
    fireEvent.change(screen.getByTestId("model-settings-env-value"), { target: { value: "ghp-0000" } });
    fireEvent.submit(screen.getByTestId("model-settings-env-value").closest("form")!);

    await waitFor(() =>
      expect(client.setKey).toHaveBeenCalledWith("config.agents.presets.kb-default.env.GH_TOKEN", "ghp-0000"),
    );
  });

  it("says under the env form why its secret was refused, and keeps what was typed", async () => {
    // review 17: the form emptied itself on submit and the refusal was drawn
    // only under an open row — so it said nothing, or named another key.
    const client = fakeClient();
    client.setKey.mockRejectedValue(
      new HttpError(422, "refused", undefined, undefined, {
        problems: ["the saved draft does not load — fix it on the Edit tab first"],
      }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));
    setOnRow(SLOT, "sk-row"); // another row's field open meanwhile

    fireEvent.change(screen.getByLabelText("變數名稱"), { target: { value: "GH_TOKEN" } });
    fireEvent.change(screen.getByTestId("model-settings-env-value"), { target: { value: "ghp-0000" } });
    fireEvent.submit(screen.getByTestId("model-settings-env-value").closest("form")!);

    expect(await screen.findByTestId("model-settings-env-refused")).toHaveTextContent("fix it on the Edit tab");
    expect(screen.queryByTestId("model-settings-key-refused")).toBeNull();
    expect(screen.getByLabelText("變數名稱")).toHaveValue("GH_TOKEN");
    expect(screen.getByTestId("model-settings-env-value")).toHaveValue("ghp-0000");
  });

  it("says what a variable name may hold, and says it at the field when one cannot be used", async () => {
    // review 18 (UX): a bad name greyed Add out with no word of why.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));
    const name = screen.getByLabelText("變數名稱");
    expect(name).toHaveAccessibleDescription(/英文字母、數字和底線/);
    expect(name).not.toHaveAttribute("aria-invalid", "true");

    fireEvent.change(name, { target: { value: "MY-KEY" } });

    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("model-settings-env-name-invalid")).toHaveTextContent("MY-KEY");
  });

  it("empties the env form once its secret is taken", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.change(screen.getByLabelText("變數名稱"), { target: { value: "GH_TOKEN" } });
    fireEvent.change(screen.getByTestId("model-settings-env-value"), { target: { value: "ghp-0000" } });
    fireEvent.submit(screen.getByTestId("model-settings-env-value").closest("form")!);

    await waitFor(() => expect(screen.getByTestId("model-settings-env-value")).toHaveValue(""));
    expect(screen.getByLabelText("變數名稱")).toHaveValue("");
  });

  it("applies what its confirmation listed, even if the page re-reads meanwhile", async () => {
    // final review: the dialog listed the draft of the moment it opened, but the
    // Apply read the draft of the latest render — a refetch in between sent another.
    const client = fakeClient({ draft: KB_2 });
    const { client: queryClient } = renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());
    fireEvent.click(screen.getByTestId("model-settings-apply"));
    await screen.findByTestId("model-settings-diff");

    const other = { config: { agents: { presets: { "kb-default": { model: "openai/OTHER" } } } } };
    await act(async () => {
      await client.saveDraft(other);
      await queryClient.invalidateQueries();
    });
    // the page has re-read: the editor now shows the other superuser's draft
    await waitFor(() => expect(draftNow()).toEqual(other));
    fireEvent.click(screen.getByRole("button", { name: "套用" }));

    await waitFor(() => expect(client.apply).toHaveBeenCalledWith(KB_2, [], "rev-1"));
  });

  it("does not say the draft changes nothing while its text does not parse", async () => {
    // final review (ux): an unparsable text was read as an empty patch, so the
    // section claimed "everything is the deploy default".
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

    type('{"config": ');

    const section = screen.getByRole("region", { name: "這頁改過的值" });
    expect(section).not.toHaveTextContent("全部是部署預設");
    expect(section).toHaveTextContent("YAML");
  });

  it("names hiding a preset as removing it, not as going back to the default", async () => {
    // final review: kind "removed" read 「回到預設」 — the opposite of a hide.
    const hide = { config: { agents: { presets: { "openai-mini": null } } } };
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: hide })} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());

    fireEvent.click(screen.getByTestId("model-settings-apply"));

    const diff = await screen.findByTestId("model-settings-diff");
    expect(diff).toHaveTextContent("移除");
    expect(diff).not.toHaveTextContent("回到預設");
  });

  it("says a key not in force is withdrawn at once when cleared", async () => {
    // final review 3: the dialog said "takes effect at the next Apply" — but a
    // key that was never in force is simply withdrawn, now.
    const path = "config.llm.api_key";
    renderWithQuery(
      <ModelSettingsPage client={fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path], applied_keys: [] })} />,
    );
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(screen.getByTestId(`model-settings-key-clear-${path}`));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("立刻");
    expect(dialog).not.toHaveTextContent("下次套用");
  });

  it("says a key in force stops at the next Apply when cleared", async () => {
    const path = "config.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { [path]: "…4321" }, applied_keys: [path] })} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(screen.getByTestId(`model-settings-key-clear-${path}`));

    expect(await screen.findByRole("dialog")).toHaveTextContent("下次套用");
  });

  it("titles the Apply confirmation by what it applies, not as a draft", async () => {
    const path = "config.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path] })} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(await screen.findByTestId("model-settings-keys-apply"));

    expect(await screen.findByRole("dialog")).not.toHaveTextContent("草稿？");
  });

  it("opens one row's field at a time, and Cancel closes it", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(screen.getByTestId(`model-settings-key-edit-${SLOT}`));
    expect(screen.getByTestId("model-settings-key-value").closest("li")).toBe(
      screen.getByTestId(`model-settings-key-row-${SLOT}`),
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByTestId("model-settings-key-value")).toBeNull();
  });

  it("lists a refused Apply's reasons", async () => {
    const client = fakeClient({ draft: KB_2 });
    client.apply.mockRejectedValue(
      new HttpError(422, "apply failed", undefined, undefined, { problems: ["config.kb: read at boot"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    expect(await screen.findByTestId("model-settings-problems")).toHaveTextContent("config.kb: read at boot");
  });

  it("lists each changed value beside its default, and resets one", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);
    await waitFor(() => expect(draftNow()).toEqual(KB_2));

    const changes = screen.getByTestId("model-settings-changes");
    expect(changes).toHaveTextContent("openai/kb-2");
    expect(changes).toHaveTextContent("預設：ollama_chat/qwen3:14b");

    fireEvent.click(screen.getByTestId("model-settings-reset-config.agents.presets.kb-default.model"));

    expect(draftNow()).toEqual({});
  });

  it("resets a value of a preset whose name holds a dot", async () => {
    // review r1: a joined path split back on "." read `qwen2.5` as `qwen2` -> `5`.
    const dotted = { config: { agents: { presets: { "qwen2.5": { model: "m" } } } } };
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: dotted })} />);
    await waitFor(() => expect(draftNow()).toEqual(dotted));

    fireEvent.click(screen.getByTestId("model-settings-reset-config.agents.presets.qwen2.5.model"));

    expect(draftNow()).toEqual({});
  });

  it("tests a preset's connection", async () => {
    const client = fakeClient();
    client.testConnection.mockResolvedValue({ ok: true, model: "openai-mini-model", ms: 11855, error: "" });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    fireEvent.change(screen.getByLabelText("要測試的 preset"), { target: { value: "openai-mini" } });
    fireEvent.click(screen.getByTestId("model-settings-test"));

    // M24: the test stops at the first token, so it says when the model began
    // to answer — there is no reply to show. In seconds, like the running line
    // above it (review 18; NN/g heuristic #4, consistency).
    expect(await screen.findByTestId("model-settings-test-result")).toHaveTextContent(
      "連得上 openai-mini-model：11.9 秒後開始回覆",
    );
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
    setOnRow(SLOT, "sk-new");
    const value = screen.getByTestId("model-settings-key-value") as HTMLInputElement;
    expect(value.type).toBe("text");
    expect(value).toHaveAttribute("data-masked");
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
    setOnRow(SLOT, "v");
    fireEvent.click(screen.getByTestId("model-settings-key-set"));

    expect(await screen.findByTestId("model-settings-key-refused")).toHaveTextContent("not a secret");
  });

  it("says a failed clear on the key's own row, not at the path field", async () => {
    // final review 6: a failed Clear marked the EMPTY path field invalid and
    // sat under it, away from the row and without naming the key.
    const path = "config.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, applied_keys: [path] });
    client.clearKey.mockRejectedValue(new HttpError(503, "clearing the key failed: 503"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(screen.getByTestId(`model-settings-key-clear-${path}`));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "清除" }));

    const failed = await screen.findByTestId(`model-settings-key-clear-failed-${path}`);
    expect(failed).toHaveTextContent("clearing the key failed");
    expect(failed.closest("li")).toBe(screen.getByTestId(`model-settings-key-clear-${path}`).closest("li"));
    expect(document.querySelector('[aria-invalid="true"]')).toBeNull();
    expect(screen.queryByTestId("model-settings-key-refused")).toBeNull();
  });

  it("lets a history row wrap, so its button drops below on a phone", async () => {
    // final review 6: at 390 px the button kept its column and the change list
    // was squeezed to ~200 px (Material 3: compact = one column).
    const client = fakeClient({ version: "v2v2v2v2v2v2v2v2", published: KB_2 });
    client.history.mockResolvedValue([applied("v2v2v2v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    const row = (await screen.findByTestId("model-settings-rollback-1")).closest("li");
    expect(row?.style.flexWrap).toBe("wrap");
  });

  it("says a connection test is running, and for how long, without changing the button", async () => {
    // final review 7: a bare "…" in a shrunken button for a call that can take
    // 40 s, and nothing for a screen reader (NN/g "Progress Indicators").
    const client = fakeClient();
    client.testConnection.mockImplementation(() => new Promise(() => {}));
    renderWithQuery(<ModelSettingsPage client={client} />);
    const button = await screen.findByTestId("model-settings-test");
    const label = button.textContent;

    fireEvent.click(button);

    const running = await screen.findByTestId("model-settings-test-running");
    expect(running).toHaveAttribute("role", "status");
    expect(running.textContent).toMatch(/\d/);
    expect(button.textContent).toBe(label);
  });

  it("keeps buttons in the page's font inside the monospace lists", async () => {
    // final review 7: Reset and Clear inherited the lists' monospace font.
    const path = "config.llm.api_key";
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2, keys: { [path]: "…4321" } })} />);

    expect((await screen.findByTestId("model-settings-changes")).style.fontFamily).toBe("");
    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));
    expect(screen.getByTestId("model-settings-keys").style.fontFamily).toBe("");
  });

  it("saves on top of what it was editing, and only overwrites a newer save once told", async () => {
    // final review 8: Save sent no precondition, so another superuser's newer
    // draft was silently replaced.
    const client = fakeClient({ draft: KB_2 });
    const { client: queryClient } = renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    const theirs = { config: { llm: { base_url: "http://theirs" } } };
    const base = client.get.getMockImplementation();
    client.get.mockImplementation(async () => ({ ...(await base!()), draft: theirs }));
    await act(async () => queryClient.invalidateQueries());
    client.saveDraft.mockRejectedValueOnce(new HttpError(409, "the saved draft changed since you started editing"));

    fireEvent.click(screen.getByTestId("model-settings-save"));

    expect(await screen.findByTestId("model-settings-save-failed")).toHaveTextContent("changed since");
    expect(client.saveDraft).toHaveBeenLastCalledWith({ config: { llm: { base_url: "http://mine" } } }, KB_2);
    fireEvent.click(screen.getByTestId("model-settings-save"));
    await waitFor(() =>
      expect(client.saveDraft).toHaveBeenLastCalledWith({ config: { llm: { base_url: "http://mine" } } }, theirs),
    );
  });

  it("writes sentences in the page's font, not monospace", async () => {
    // final review 8: the test result and the key refusal were whole sentences
    // set in the monospace meant for paths and values.
    const client = fakeClient();
    client.setKey.mockRejectedValue(
      new HttpError(422, "refused", undefined, undefined, { problems: ["config.x: no such setting"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByTestId("model-settings-test"));
    expect((await screen.findByTestId("model-settings-test-result")).style.fontFamily).toBe("");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));
    setOnRow(SLOT, "v");
    fireEvent.click(screen.getByTestId("model-settings-key-set"));
    expect((await screen.findByTestId("model-settings-key-refused")).style.fontFamily).toBe("");
  });

  it("asks before Discard edits throws the edits away", async () => {
    // final review 9: Discard threw away hand-written text at once, no undo
    // (NN/g "Confirmation Dialogs"; M3's own example is "Discard draft?").
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    fireEvent.click(screen.getByTestId("model-settings-discard"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "繼續編輯" }));
    expect(draftNow()).toEqual({ config: { llm: { base_url: "http://mine" } } });

    fireEvent.click(screen.getByTestId("model-settings-discard"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放棄變更" }));
    await waitFor(() => expect(draftNow()).toEqual({}));
  });

  it("gives unsaved edits back when the page is opened again", async () => {
    // final review 9: leaving the page (a link, a reload) lost the edits with
    // no question and no undo; the router here cannot block a navigation.
    const first = renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    first.unmount();

    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);

    await waitFor(() => expect(draftNow()).toEqual({ config: { llm: { base_url: "http://mine" } } }));
    expect(screen.getByTestId("model-settings-restored")).toBeInTheDocument();
  });

  it("asks before the tab closes while there are unsaved edits", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("leads a failed connection test with a plain sentence", async () => {
    // final review 9: the raw provider exception was the whole message.
    const client = fakeClient();
    client.testConnection.mockResolvedValue({
      ok: false,
      model: "openai/x",
      ms: 0,
      error: "litellm.InternalServerError: OpenAIException - Missing credentials",
    });
    renderWithQuery(<ModelSettingsPage client={client} />);

    fireEvent.click(await screen.findByTestId("model-settings-test"));

    const result = await screen.findByTestId("model-settings-test-result");
    expect(result.textContent?.startsWith("測試 kb-default 沒有成功")).toBe(true);
    // final review 10: not "the provider said" — most refusals are the page's own
    expect(result).not.toHaveTextContent("提供方");
    const detail = within(result).getByTestId("model-settings-test-detail");
    expect(detail).toHaveTextContent("Missing credentials");
    // final review 11: often the page's own sentence, so not set as code
    expect(detail.style.fontFamily).toBe("");
  });

  it("says a rollback's result where the person is, and announces it", async () => {
    // final review 10: on a phone the message appeared above the screen, unread.
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    const client = fakeClient({ version: "v2v2v2v2v2v2v2v2", published: KB_2 });
    client.history.mockResolvedValue([applied("v2v2v2v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    fireEvent.click(await screen.findByTestId("model-settings-rollback-1"));
    fireEvent.click(await screen.findByRole("button", { name: "回復" }));

    const said = await screen.findByTestId("model-settings-rolled-back");
    expect(said).toHaveAttribute("role", "status");
    await waitFor(() => expect(scrolled.mock.contexts).toContain(said));
  });

  it("announces a saved draft", async () => {
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');

    fireEvent.click(screen.getByTestId("model-settings-save"));

    expect(await screen.findByTestId("model-settings-notice")).toHaveAttribute("role", "status");
  });

  it("draws Set key as a secondary action beside Apply", async () => {
    // final review 10: two filled buttons on one tab (M3: filled = the main one).
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    expect(screen.getByTestId(`model-settings-key-edit-${SLOT}`)).toHaveAttribute("data-variant", "secondary");
    setOnRow(SLOT, "v");
    expect(screen.getByTestId("model-settings-key-set")).toHaveAttribute("data-variant", "secondary");
  });

  it("builds the next Save on its own last save, even when typing went on meanwhile", async () => {
    // final review 11: typing between a save and its refetch left `based_on` at
    // the draft before — the next Save got a false "someone else saved".
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    const t1 = { config: { llm: { base_url: "http://one" } } };
    const t2 = { config: { llm: { base_url: "http://two" } } };
    let release: () => void = () => {};
    const base = client.get.getMockImplementation()!;
    type(JSON.stringify(t1));
    client.get.mockImplementationOnce(
      () => new Promise((resolve) => (release = () => void base().then(resolve))),
    );

    fireEvent.click(screen.getByTestId("model-settings-save"));
    await waitFor(() => expect(client.saveDraft).toHaveBeenCalledTimes(1));
    type(JSON.stringify(t2));
    await act(async () => release());
    fireEvent.click(screen.getByTestId("model-settings-save"));

    await waitFor(() => expect(client.saveDraft).toHaveBeenLastCalledWith(t2, t1));
  });

  it("does not leave an Edit-tab result beside a refused key", async () => {
    // final review 11: "Applied: version …" sat right above the key refusal.
    const client = fakeClient({ draft: KB_2 });
    client.setKey.mockRejectedValue(
      new HttpError(422, "refused", undefined, undefined, { problems: ["config.x: no"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(screen.getByTestId("model-settings-apply")).toBeEnabled());
    fireEvent.click(screen.getByTestId("model-settings-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));
    await screen.findByTestId("model-settings-notice");

    fireEvent.click(screen.getByRole("tab", { name: "API 金鑰" }));
    setOnRow(SLOT, "v");
    fireEvent.click(screen.getByTestId("model-settings-key-set"));

    await screen.findByTestId("model-settings-key-refused");
    expect(screen.queryByTestId("model-settings-notice")).toBeNull();
  });

  it("sets a key on Enter, as a form does", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));
    setOnRow("config.llm.api_key", "sk-0000-0000-0000-0001");

    fireEvent.submit(screen.getByTestId("model-settings-key-value").closest("form")!);

    await waitFor(() => expect(client.setKey).toHaveBeenCalledWith("config.llm.api_key", "sk-0000-0000-0000-0001"));
  });

  it("keeps the preset picker where it is while a test runs", async () => {
    // final review 11: the running line shared the picker's row and squeezed it.
    const client = fakeClient();
    client.testConnection.mockImplementation(() => new Promise(() => {}));
    renderWithQuery(<ModelSettingsPage client={client} />);
    const button = await screen.findByTestId("model-settings-test");

    fireEvent.click(button);

    const running = await screen.findByTestId("model-settings-test-running");
    expect(running.parentElement).not.toBe(button.parentElement);
  });

  it("says a failed rollback in words, not as an Error object", async () => {
    const client = fakeClient({ version: "v2v2v2v2v2v2v2v2", published: KB_2 });
    client.history.mockResolvedValue([applied("v2v2v2v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    client.rollback.mockRejectedValue(new HttpError(404, "rollback failed: 404"));
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    fireEvent.click(await screen.findByTestId("model-settings-rollback-1"));
    fireEvent.click(await screen.findByRole("button", { name: "回復" }));

    const said = await screen.findByText("rollback failed: 404");
    expect(said.textContent).not.toContain("Error:");
  });

  it("compares the first version with the deploy defaults, and says so", async () => {
    // final review 12: "same values as the version before" — there is none.
    const client = fakeClient({ version: "v1v1v1v1v1v1v1v1" });
    client.history.mockResolvedValue([applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "版本紀錄" }));

    const changes = await screen.findByTestId("model-settings-version-changes-0");
    expect(changes).toHaveTextContent("預設");
    expect(changes).not.toHaveTextContent("前一版");
  });

  it("brings a connection test's progress into view", async () => {
    // final review 15: at 1280×900 the running line sat below the fold.
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    const client = fakeClient();
    client.testConnection.mockImplementation(() => new Promise(() => {}));
    renderWithQuery(<ModelSettingsPage client={client} />);

    fireEvent.click(await screen.findByTestId("model-settings-test"));

    const running = await screen.findByTestId("model-settings-test-running");
    await waitFor(() => expect(scrolled.mock.contexts).toContain(running));
  });

  it("says rollback waits for unsaved edits only where a rollback is offered, with a way there", async () => {
    // final review 15: the reason showed with no row to roll back, and gave no
    // way to the Edit tab (the keys tab does).
    const same = fakeClient({ version: "v1v1v1v1v1v1v1v1", published: {} });
    same.history.mockResolvedValue([applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    const first = renderWithQuery(<ModelSettingsPage client={same} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));
    await screen.findByTestId("model-settings-version-changes-0");
    expect(screen.queryByTestId("model-settings-rollback-why")).toBeNull();
    first.unmount();
    sessionStorage.clear();

    const client = fakeClient({ version: "v2v2v2v2v2v2v2v2", published: KB_2 });
    client.history.mockResolvedValue([applied("v2v2v2v2v2v2v2v2", KB_2, 2), applied("v1v1v1v1v1v1v1v1", {}, 1)]);
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type('{"config": {"llm": {"base_url": "http://mine"}}}');
    fireEvent.click(screen.getByRole("tab", { name: "版本紀錄" }));
    await screen.findByTestId("model-settings-rollback-why");

    fireEvent.click(screen.getByTestId("model-settings-go-edit"));

    expect(await screen.findByRole("textbox", { name: "設定（YAML）" })).toBeInTheDocument();
  });

  it("saves only the line changed in a pasted copy of the whole settings", async () => {
    // M23 (user: 能改的要能複製貼上): the whole settings pasted — a section the
    // page does not edit, secrets as the boot dump masks them — with one model
    // changed is that one change.
    const client = fakeClient({
      boot_config: { ...DEFAULTS.config, server: { port: 9090 } },
    });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type(
      [
        "config:",
        "  server:",
        "    port: 9090",
        "  agents:",
        "    presets:",
        "      kb-default:",
        "        model: openai/gpt-4.1-mini",
        "        llm:",
        '          api_key: "*** unset ***"',
        "      openai-mini:",
        "        model: openai/gpt-4o-mini",
        "apps: {}",
        "profiles: {}",
      ].join("\n"),
    );

    fireEvent.click(screen.getByTestId("model-settings-save"));

    await waitFor(() =>
      expect(client.saveDraft).toHaveBeenCalledWith(
        { config: { agents: { presets: { "kb-default": { model: "openai/gpt-4.1-mini" } } } } },
        {},
      ),
    );
  });

  it("keeps the editor within the window, so Save and Apply stay in reach", async () => {
    // review 17 (UX): grown to its content — the whole settings, ~1000 lines
    // since M23 — the editor was 18,016 px tall and Save sat below all of it.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    expect(editor().dataset.autoHeight).toBe("false");
    expect(screen.getByTestId("model-settings-editor-box").style.height).toMatch(/vh/);
  });

  it("offers to save a reordered list — order is what a fallback chain means", async () => {
    // review 18: the page compared lists as sets, so reordering a saved chain
    // read as no unsaved edit, and Save was not offered.
    const chain = (order: string[]) => ({ config: { agents: { presets: { "kb-default": { fallbacks: order } } } } });
    const client = fakeClient({ draft: chain(["b", "c", "x"]) });
    renderWithQuery(<ModelSettingsPage client={client} />);
    await waitFor(() => expect(editor().value).toContain("fallbacks"));
    expect(screen.getByTestId("model-settings-save")).toBeDisabled();

    type(editor().value.replace(/- b\n(\s+)- c\n(\s+)- x/, "- x\n$1- c\n$2- b"));

    expect(draftNow()).toEqual(chain(["x", "c", "b"]));
    expect(screen.getByTestId("model-settings-save")).toBeEnabled();
  });

  it("says in YAML's own terms that the settings must be keys, not a list", async () => {
    // review 18: the message spoke JSON (an object {…}) of a YAML text.
    renderWithQuery(<ModelSettingsPage client={fakeClient()} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });

    type("- a\n- b\n");

    expect(await screen.findByTestId("model-settings-parse")).toHaveTextContent("最外層要是「名稱: 值」");
  });

  it("gives a value read only at boot the default the deploy booted with", async () => {
    // demo (2026-10-13): `config.server.port` said 預設沒有這個值 — the column
    // read the live-editable defaults, not the boot config the save diffs against.
    renderWithQuery(
      <ModelSettingsPage client={fakeClient({ boot_config: { ...DEFAULTS.config, server: { port: 9090 } } })} />,
    );
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type("config:\n  server:\n    port: 1\n");

    const row = await screen.findByTestId("model-settings-changes");
    expect(row).toHaveTextContent("config.server.port: 1");
    expect(row).toHaveTextContent("預設：9090");
    expect(row).not.toHaveTextContent("預設沒有這個值");
  });

  it("shows the whole settings again once a pasted fragment is saved", async () => {
    // demo (2026-10-13): after saving a fragment the editor kept the fragment
    // until a reload — M23 says the editor holds the whole settings.
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "設定（YAML）" });
    type("config:\n  agents:\n    presets:\n      kb-default:\n        model: openai/gpt-4.1\n");

    fireEvent.click(screen.getByTestId("model-settings-save"));

    await waitFor(() => expect(editor().value).toContain("openai-mini:"));
    expect(editor().value).toContain("model: openai/gpt-4.1");
    expect(draftNow()).toEqual({ config: { agents: { presets: { "kb-default": { model: "openai/gpt-4.1" } } } } });
  });

  it("shows the saved draft as YAML, as config.yaml is written", async () => {
    // M9 (user): the editor is text, YAML/JSON — built as JSON only, which
    // narrowed it; config.yaml is YAML and so is the page now.
    renderWithQuery(<ModelSettingsPage client={fakeClient({ draft: KB_2 })} />);

    await waitFor(() => expect(editor().value).toContain("kb-default:"));
    expect(editor().value).not.toMatch(/\{\s*"/); // not JSON text
    expect(draftNow()).toEqual(KB_2);
  });

  it("draws a refused key as an error at its field", async () => {
    // final review 5: plain black text, away from the field, no aria-invalid.
    const client = fakeClient();
    client.setKey.mockRejectedValue(
      new HttpError(422, "refused", undefined, undefined, { problems: ["config.x.model: not a secret"] }),
    );
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));
    setOnRow(SLOT, "v");
    fireEvent.click(screen.getByTestId("model-settings-key-set"));

    const refused = await screen.findByTestId("model-settings-key-refused");
    expect(refused).toHaveClass("ev-banner--err");
    const value = screen.getByTestId("model-settings-key-value");
    expect(value).toHaveAttribute("aria-invalid", "true");
    expect(value.getAttribute("aria-describedby")).toContain(refused.id);
    expect(refused.closest("li")).toBe(screen.getByTestId(`model-settings-key-row-${SLOT}`));
  });

  it("sends the key revision its confirmation listed", async () => {
    // final review 5: the server compares it, so a key changed since is a 409.
    const path = "config.llm.api_key";
    const client = fakeClient({ keys: { [path]: "…4321" }, pending_keys: [path], key_revision: "rev-7" });
    renderWithQuery(<ModelSettingsPage client={client} />);
    fireEvent.click(await screen.findByRole("tab", { name: "API 金鑰" }));

    fireEvent.click(await screen.findByTestId("model-settings-keys-apply"));
    fireEvent.click(await screen.findByRole("button", { name: "套用" }));

    await waitFor(() => expect(client.apply).toHaveBeenCalledWith({}, [path], "rev-7"));
  });
});
