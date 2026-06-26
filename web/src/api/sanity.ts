/**
 * Model-sanity battery API client. Wire shapes mirror the `/sanity/*` routes in
 * `api/health_routes.py`: GET the matrix metadata (models × levels × questions),
 * GET one model's filled cells, POST a run (one cell or the auto battery).
 * Mock/real swap on the same `VITE_USE_MOCK` switch as the rest of the api.
 */

import { apiFetch } from "./http";

export type ChatMessage = { role: string; content: string };

export type SanityLevel = { level: string; label: string };

export type SanityQuestion = {
  /** Stable key (hash of the messages) — the cell's question_key. */
  key: string;
  category: string;
  messages: ChatMessage[];
  expected: string;
  auto_run: boolean;
  auto_levels: string[];
};

export type SanityMeta = {
  models: string[];
  levels: SanityLevel[];
  questions: SanityQuestion[];
};

export type SanityCell = {
  question_key: string;
  level: string;
  output: string;
  reasoned: boolean;
  grade: string; // "pass" | "fail" | "" (eyeball)
  ai_grade: string; // #231: AI judge verdict "pass" | "fail" | ""
  ai_note: string; // #231: AI judge one-line rationale
  aux: string;
  error: string;
  latency_ms: number;
};

export type SanityRunBody = {
  model: string;
  scope: "cell" | "battery";
  question_key?: string;
  level?: string;
};

/** #231: one model's overall fitness verdict (a card above the table). */
export type SanityVerdict = { model: string; score: number; summary: string };

/** #231: a user-authored question (題目管理 panel). `id` is the resource id. */
export type CustomQuestion = {
  id: string;
  category: string;
  prompt: string;
  expected: string;
  levels: string[];
  enabled: boolean;
};

export type CustomQuestionBody = Omit<CustomQuestion, "id">;

export type SanityApi = {
  getMeta(): Promise<SanityMeta>;
  getResults(model: string): Promise<SanityCell[]>;
  run(body: SanityRunBody): Promise<{ queued: boolean }>;
  getVerdicts(): Promise<SanityVerdict[]>;
  runMissing(models: string[], category?: string | null): Promise<{ count: number }>;
  rescore(models: string[]): Promise<{ count: number }>;
  listCustom(): Promise<CustomQuestion[]>;
  createCustom(body: CustomQuestionBody): Promise<CustomQuestion>;
  updateCustom(id: string, body: CustomQuestionBody): Promise<CustomQuestion>;
  deleteCustom(id: string): Promise<void>;
};

export const realSanityApi: SanityApi = {
  async getMeta() {
    const r = await apiFetch("/sanity/questions");
    if (!r.ok) throw new Error(`sanity meta failed: ${r.status}`);
    return (await r.json()) as SanityMeta;
  },
  async getResults(model: string) {
    const r = await apiFetch(`/sanity/results?model=${encodeURIComponent(model)}`);
    if (!r.ok) throw new Error(`sanity results failed: ${r.status}`);
    return (await r.json()) as SanityCell[];
  },
  async run(body: SanityRunBody) {
    const r = await apiFetch("/sanity/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`sanity run failed: ${r.status}`);
    return (await r.json()) as { queued: boolean };
  },
  async getVerdicts() {
    const r = await apiFetch("/sanity/verdicts");
    if (!r.ok) throw new Error(`sanity verdicts failed: ${r.status}`);
    return (await r.json()) as SanityVerdict[];
  },
  async runMissing(models: string[], category?: string | null) {
    const r = await apiFetch("/sanity/run-missing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ models, category: category ?? null }),
    });
    if (!r.ok) throw new Error(`sanity run-missing failed: ${r.status}`);
    return (await r.json()) as { count: number };
  },
  async rescore(models: string[]) {
    const r = await apiFetch("/sanity/rescore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ models }),
    });
    if (!r.ok) throw new Error(`sanity rescore failed: ${r.status}`);
    return (await r.json()) as { count: number };
  },
  async listCustom() {
    const r = await apiFetch("/sanity/custom-questions");
    if (!r.ok) throw new Error(`custom questions failed: ${r.status}`);
    return (await r.json()) as CustomQuestion[];
  },
  async createCustom(body: CustomQuestionBody) {
    const r = await apiFetch("/sanity/custom-questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`create custom question failed: ${r.status}`);
    return (await r.json()) as CustomQuestion;
  },
  async updateCustom(id: string, body: CustomQuestionBody) {
    const r = await apiFetch(`/sanity/custom-questions/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`update custom question failed: ${r.status}`);
    return (await r.json()) as CustomQuestion;
  },
  async deleteCustom(id: string) {
    const r = await apiFetch(`/sanity/custom-questions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    if (!r.ok) throw new Error(`delete custom question failed: ${r.status}`);
  },
};

/** Dev-mode canned matrix so the grid is demoable offline. */
const mockMeta: SanityMeta = {
  models: ["ollama_chat/qwen3:14b", "ollama_chat/qwen3:8b"],
  levels: [
    { level: "none", label: "Off" },
    { level: "low", label: "Low" },
    { level: "medium", label: "Medium" },
    { level: "high", label: "High" },
  ],
  questions: [
    {
      key: "q-capital",
      category: "基礎知識",
      messages: [{ role: "user", content: "台灣的首都是哪裡?" }],
      expected: "回答台北",
      auto_run: true,
      auto_levels: ["none", "medium"],
    },
    {
      key: "q-essay",
      category: "生成穩定性",
      messages: [{ role: "user", content: "寫一篇 300 字關於海洋的短文" }],
      expected: "接近 300 字、通順",
      auto_run: true,
      auto_levels: ["none", "low", "medium", "high"],
    },
  ],
};

const mockCells: Record<string, SanityCell[]> = {
  "ollama_chat/qwen3:14b": [
    {
      question_key: "q-capital",
      level: "none",
      output: "台北市。",
      reasoned: false,
      grade: "pass",
      ai_grade: "pass",
      ai_note: "正確點出台北",
      aux: "",
      error: "",
      latency_ms: 420,
    },
    {
      // A long answer so the in-cell truncation + "show full" modal are demoable offline.
      question_key: "q-essay",
      level: "medium",
      output:
        "海洋是地球上最廣闊的水體,覆蓋了約七成的表面,孕育著無數的生命。" +
        "清晨時分,陽光灑在波光粼粼的海面上,海鷗在浪花間盤旋;" +
        "潮水一次次拍打著沙灘,留下細碎的貝殼與泡沫。" +
        "深處的洋流默默調節著全球的氣候,連結著遙遠的大陸。" +
        "對人類而言,海洋既是糧食與資源的寶庫,也是需要共同守護的脆弱家園。",
      reasoned: true,
      grade: "",
      ai_grade: "pass",
      ai_note: "長度與通順度符合",
      aux: "143 字",
      error: "",
      latency_ms: 3120,
    },
  ],
};

const mockVerdicts: SanityVerdict[] = [
  {
    model: "ollama_chat/qwen3:14b",
    score: 82,
    summary: "- 適合 KB 問答、JSON 格式輸出\n- 中文處理穩定\n- ⚠️ reasoning-off 偶有跳針",
  },
];

const mockCustom: CustomQuestion[] = [];

export const mockSanityApi: SanityApi = {
  async getMeta() {
    return mockMeta;
  },
  async getResults(model: string) {
    return mockCells[model] ?? [];
  },
  async run() {
    return { queued: true };
  },
  async getVerdicts() {
    return mockVerdicts;
  },
  async runMissing() {
    return { count: 0 };
  },
  async rescore() {
    return { count: 0 };
  },
  async listCustom() {
    return mockCustom;
  },
  async createCustom(body) {
    return { id: "mock-1", ...body };
  },
  async updateCustom(id, body) {
    return { id, ...body };
  },
  async deleteCustom() {
    return;
  },
};

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const sanityApi: SanityApi = useMock ? mockSanityApi : realSanityApi;
