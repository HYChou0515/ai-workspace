import type { AgentEvent } from "../events";

export type Workspace = {
  resource_id: string;
  name: string;
  description: string;
  attached_agent_config_id: string | null;
};

export type WorkspaceInput = {
  name: string;
  description?: string;
};

export type MessageRole = "user" | "assistant" | "tool";

export type Message = {
  role: MessageRole;
  content: string;
  tool_call_id?: string | null;
  tool_name?: string | null;
};

export type Conversation = {
  resource_id: string;
  workspace_id: string;
  messages: Message[];
};

export type FileInfo = {
  path: string;
  size: number;
};

export type FileContent =
  | { kind: "text"; path: string; size: number; text: string }
  | { kind: "binary"; path: string; size: number };

export type StreamArgs = {
  workspaceId: string;
  content: string;
  signal?: AbortSignal;
};

export interface ApiClient {
  listWorkspaces(): Promise<Workspace[]>;
  createWorkspace(input: WorkspaceInput): Promise<Workspace>;

  /** Returns the (single) conversation for the workspace, or null if none yet. */
  getConversationByWorkspace(workspaceId: string): Promise<Conversation | null>;

  listFiles(workspaceId: string): Promise<FileInfo[]>;
  readFile(workspaceId: string, path: string): Promise<FileContent>;

  streamAgentEvents(args: StreamArgs): AsyncGenerator<AgentEvent>;
}
