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

export type SanityApi = {
  getMeta(): Promise<SanityMeta>;
  getResults(model: string): Promise<SanityCell[]>;
  run(body: SanityRunBody): Promise<{ queued: boolean }>;
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
      aux: "",
      error: "",
      latency_ms: 420,
    },
  ],
};

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
};

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const sanityApi: SanityApi = useMock ? mockSanityApi : realSanityApi;
