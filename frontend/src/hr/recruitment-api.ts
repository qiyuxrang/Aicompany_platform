import { apiRequest } from '../api';
export const root = '/api/hr/recruitment/';
export interface Requirement {
  id: string; position_name: string; headcount: number | null; responsibilities: string;
  required_requirements: string; preferred_requirements: string; education_requirement: string;
  experience_requirement: string; skill_requirements: string[]; work_location: string; notes: string;
  input_version: number; current_jd_id: string | null; official_jd_id: string | null;
  missing_items: { field: string; reason: string }[]; updated_at: string;
}
export interface Jd { id: string; request_id: string; version: number; input_version: number; body: string;
  state: string; channel: string; stale: boolean; source_jd_id: string | null }
export interface Resume { id: string; filename: string; processing_status: string; error_code: string; size: number }
export interface Batch { id: string; position_name: string; jd_version_id: string; jd_version: number;
  version: number; status: string; stale: boolean; total: number; completed: number; failed: number;
  progress: number; updated_at: string; artifacts?: Resume[] }
export interface Result extends Resume { score: number | null; unknown_count: number | null; hard_gap_count: number;
  stale: boolean; rule_version: string | null; matrix: { id: string; text: string; verdict: string; evidence: { quote: string; locator: string }[] }[] }
export const get = <T,>(path: string, signal?: AbortSignal) => apiRequest<T>(root + path, { signal });
export const mutate = <T,>(path: string, body: object, method = 'POST', headers?: Record<string, string>) =>
  apiRequest<T>(root + path, { method, body: JSON.stringify(body), headers });
export const upload = (batch: Batch, files: File[]) => {
  const body = new FormData(); body.set('expected_version', String(batch.version));
  files.forEach(file => body.append('files', file));
  return apiRequest<{ batch: Batch }>(root + `batches/${batch.id}/resumes/`, { method: 'POST', body });
};
export const statusText = (status: string) => ({ pending: '待上传/待开始', queued: '排队中', running: '处理中', completed: '已完成',
  partial_failed: '部分失败', failed: '处理失败', draft: '草稿', confirmed: '已确认' }[status] || status);
export const message = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试';
