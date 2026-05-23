import { mockApi } from "./mock";
import { realApi } from "./real";
import type { ApiClient } from "./types";

const useMock = import.meta.env.VITE_USE_MOCK === "1";

if (useMock && import.meta.env.DEV) {
  // Loud-on-dev so we don't forget; never logs in production builds.
  console.warn("[RCA] VITE_USE_MOCK=1 — using in-memory mock API.");
}

export const api: ApiClient = useMock ? mockApi : realApi;

export type {
  ApiClient,
  Conversation,
  ExecuteCellArgs,
  FileContent,
  FileInfo,
  Investigation,
  InvestigationInput,
  Message,
  MessageRole,
  SendMessageArgs,
  Severity,
  Status,
} from "./types";
export {
  formatInvestigationId,
  isCritical,
  isOpen,
  relativeTime,
  summarize,
} from "./types";
