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
  }: {
    value: string;
    onChange?: (v: string) => void;
    readOnly?: boolean;
    ariaLabel?: string;
  }) => (
    <textarea aria-label={ariaLabel} value={value} readOnly={readOnly} onChange={(e) => onChange?.(e.target.value)} />
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
  let data: PageData = { defaults: DEFAULTS, draft: {}, published: {}, version: null, keys: {}, ...page };
  const client = {
    get: vi.fn(async () => data),
    saveDraft: vi.fn(async (patch: Record<string, unknown>) => {
      data = { ...data, draft: patch };
      return [];
    }),
    check: vi.fn(async (_patch: Record<string, unknown>) => [] as string[]),
    apply: vi.fn(async () => {
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

  it("names a JSON mistake at once, and saves nothing", async () => {
    const client = fakeClient();
    renderWithQuery(<ModelSettingsPage client={client} />);
    await screen.findByRole("textbox", { name: "草稿（JSON）" });

    type("{ not json");

    expect(screen.getByTestId("model-settings-parse")).toHaveTextContent("不是合法的 JSON");
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
    expect(diff).toHaveTextContent("config.agents.presets.kb-default.model: openai/kb-2");
    expect(client.apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "套用" }));

    await waitFor(() => expect(client.apply).toHaveBeenCalled());
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
