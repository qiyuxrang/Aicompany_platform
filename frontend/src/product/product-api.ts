import { apiRequest } from "../api";

export interface TaskInput {
  project: string;
  requirements: string;
  items: { row_id: string | number; name: string; quantity: string | number; unit: string }[];
  background: string;
  conditions: string[];
  knowledge_sources?: { id: string; location: string }[];
}
export interface BlueprintPayload {
  purpose: string;
  audience: string;
  chapters: { id: string; title: string; scope: string; source_ids: string[] }[];
  conditions: { text: string; type: "program" | "model" | "human" }[];
  missing: string[];
  conflicts: string[];
  template_version: string;
}
export interface ChapterPayload { chapter_id: string; title: string; paragraphs: string[]; source_ids: string[] }
export type ReviewCategory = "fact" | "inference" | "conflict" | "missing";
export interface InputIssue { issue_hash: string; [key: string]: unknown }
export interface RenderEvidence {
  kind: "candidate";
  status: "rendered" | "verified";
  page_count: number;
  pages: { page: number; sha256: string }[];
}
export interface Artifact { id: string; version: number; sha256: string; render_evidence?: RenderEvidence }
export interface ContentChecks {
  facts: boolean;
  quantities: boolean;
  terms: boolean;
  sources: boolean;
  completeness: boolean;
  conditions: boolean;
}
export interface ArtifactVerification {
  expected_version: number;
  sha256: string;
  pages: { page: number; sha256: string; passed: boolean; comment: string }[];
  content_checks: ContentChecks;
  comment: string;
}
export interface TaskSummary { id: string; title: string; state: string; stage: string; version: number }
export interface DocumentTask extends TaskSummary {
  input_version: number;
  blueprint_version: number;
  input: TaskInput | null;
  blueprint: (Artifact & { payload: BlueprintPayload }) | null;
  chapters: { id: string; version?: number; payload: ChapterPayload }[];
  artifacts: Artifact[];
  sources: { id: string; original_name: string }[];
  approvals: unknown[];
  issues: unknown[];
  error_code: string;
  actions: string[] | Record<string, boolean>;
  blockers: Record<string, { code: string; detail: string }>;
  reviewer_id: string | number | null;
  owner_id: string | number;
  input_issues: InputIssue[];
  impact: Record<string, unknown>;
}
export interface CreateTask { title: string; input: TaskInput; reviewer_id?: number }
export interface ConversationTaskResult {
  task: DocumentTask;
  intent: {
    mode: "deterministic_fallback";
    model_called: false;
    requested_outputs: { type: "technical_solution" | "feasibility" | "presentation"; status: string; code: string }[];
    detected_paths: string[];
  };
  blockers: Record<string, string>;
}
const root = "/api/product/tasks/";
const pathFor = (id: string) => `${root}${encodeURIComponent(id)}/`;

export async function listTasks(signal: AbortSignal): Promise<{ tasks: TaskSummary[] }> {
  const result = await apiRequest<TaskSummary[] | { tasks: TaskSummary[] }>(root, { signal });
  return Array.isArray(result) ? { tasks: result } : result;
}
export const getTask = (id: string, signal: AbortSignal) => apiRequest<DocumentTask>(pathFor(id), { signal });
export const createTask = (body: CreateTask, key: string, signal: AbortSignal) => apiRequest<DocumentTask>(root, {
  method: "POST", body: JSON.stringify(body), headers: { "Idempotency-Key": key }, signal,
});
export const createConversationTask = (body: { message: string }, key: string, signal: AbortSignal) => apiRequest<ConversationTaskResult>("/api/product/conversations/", {
  method: "POST", body: JSON.stringify(body), headers: { "Idempotency-Key": key }, signal,
});
export const continueConversation = (id: string, body: { expected_version: number; message: string }, signal: AbortSignal) => apiRequest<ConversationTaskResult>(`${pathFor(id)}conversation/`, {
  method: "POST", body: JSON.stringify(body), signal,
});
export const updateTask = (id: string, endpoint: string, body: object, signal: AbortSignal) => apiRequest<unknown>(`${pathFor(id)}${endpoint}`, {
  method: endpoint === "" || endpoint === "blueprint/" ? "PATCH" : "POST", body: JSON.stringify(body), signal,
});
export const artifactDownload = (id: string) => `/api/product/artifacts/${encodeURIComponent(id)}/download/`;
export const artifactPreview = (id: string, page: number) => `/api/product/artifacts/${encodeURIComponent(id)}/preview/?page=${encodeURIComponent(String(page))}`;
export const verifyArtifact = (id: string, body: ArtifactVerification, signal: AbortSignal) => apiRequest<unknown>(`/api/product/artifacts/${encodeURIComponent(id)}/verification/`, {
  method: "POST", body: JSON.stringify(body), signal,
});
export async function uploadSource(id: string, file: File, version: number, signal: AbortSignal): Promise<DocumentTask> {
  const body = new FormData();
  body.append("file", file);
  body.append("expected_version", String(version));
  const result = await apiRequest<{ source_id: string; task: DocumentTask }>(`${pathFor(id)}sources/`, { method: "POST", body, signal });
  return result.task;
}
