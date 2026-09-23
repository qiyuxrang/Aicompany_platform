import { apiRequest } from "../api";

export interface JobRevision {
  id: string;
  version: number;
  input_version: number;
  kind: "generated" | "manual" | "confirmed";
  body: string;
  parent_id: string | null;
  created_by_id: number;
  confirmed_by_id: number | null;
  confirmed_at: string | null;
  created_at: string;
}

export interface JobTask {
  id: string;
  owner_id: number;
  title: string;
  department: string;
  objective: string;
  responsibilities: string;
  requirements: string;
  state: "draft" | "generated" | "confirmed";
  version: number;
  input_version: number;
  missing_fields: string[];
  current_revision: JobRevision | null;
  official_revision: JobRevision | null;
  current_revision_stale: boolean;
  revisions: JobRevision[];
  updated_at: string;
}

export interface JobInput {
  title: string;
  department: string;
  objective: string;
  responsibilities: string;
  requirements: string;
}

export type ProbationAction = "start_collecting" | "submit_to_manager" | "manager_approve" | "hr_archive";
export interface ProbationCase {
  id: string;
  owner_id: number;
  assigned_manager_id: number;
  employee_name: string;
  position: string;
  materials: string[];
  notes: string;
  manager_opinion: string;
  hr_conclusion: string;
  state: "draft" | "collecting" | "manager_pending" | "hr_pending" | "archived";
  version: number;
  actions: ProbationAction[];
  assistant_enabled: false;
  assistant_mode: "manual";
  assistant_reason: string;
  updated_at: string;
  transitions: { from_state: string; to_state: string; action: string; actor_id: number; comment: string; created_at: string }[];
  revisions: { before: Record<string, unknown>; after: Record<string, unknown>; changed_fields: string[]; actor_id: number; case_version: number; created_at: string }[];
}

const jobsRoot = "/api/hr/jobs/";
const probationsRoot = "/api/hr/probations/";
const json = (method: "POST" | "PATCH", body: object): RequestInit => ({ method, body: JSON.stringify(body) });

export const listJobs = (signal?: AbortSignal) => apiRequest<JobTask[]>(jobsRoot, { signal });
export const createJob = (body: JobInput, signal?: AbortSignal) => apiRequest<JobTask>(jobsRoot, { ...json("POST", body), signal });
export const updateJob = (task: JobTask, body: Partial<JobInput>, signal?: AbortSignal) => apiRequest<JobTask>(`${jobsRoot}${encodeURIComponent(task.id)}/`, {
  ...json("PATCH", { expected_version: task.version, ...body }), signal,
});
export const generateJob = (task: JobTask, signal?: AbortSignal) => apiRequest<JobTask>(`${jobsRoot}${encodeURIComponent(task.id)}/generate/`, {
  ...json("POST", { expected_version: task.version }), signal,
});
export const reviseJob = (task: JobTask, body: string, signal?: AbortSignal) => apiRequest<JobTask>(`${jobsRoot}${encodeURIComponent(task.id)}/revisions/`, {
  ...json("POST", { expected_version: task.version, body }), signal,
});
export const confirmJob = (task: JobTask, revisionId: string, signal?: AbortSignal) => apiRequest<JobTask>(`${jobsRoot}${encodeURIComponent(task.id)}/confirm/`, {
  ...json("POST", { expected_version: task.version, revision_id: revisionId }), signal,
});

export const listProbations = (signal?: AbortSignal) => apiRequest<ProbationCase[]>(probationsRoot, { signal });
export const createProbation = (body: { employee_name: string; position: string; assigned_manager_id: number; materials: string[]; notes: string }, signal?: AbortSignal) => apiRequest<ProbationCase>(probationsRoot, {
  ...json("POST", body), signal,
});
export const transitionProbation = (item: ProbationCase, action: ProbationAction, comment: string, signal?: AbortSignal) => apiRequest<ProbationCase>(`${probationsRoot}${encodeURIComponent(item.id)}/transition/`, {
  ...json("POST", { expected_version: item.version, action, comment }), signal,
});
