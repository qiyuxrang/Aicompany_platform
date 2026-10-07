import { ApiError, apiRequest } from './api';

export interface AgentPage<T> { items: T[]; total?: number; page?: number }
export interface AgentProject { id: string; name: string }
export interface AgentConversation { id: string; title?: string; project_id?: string | null; work_id?: string | null; created_at?: string; updated_at?: string }
export interface AgentAttachmentReference { type: 'agent_attachment'; id: string; sha256: string }
export interface AgentAttachment extends AgentAttachmentReference { name: string }
export type AgentExecutionState = 'pending' | 'submitted' | 'runtime_unconfigured' | 'dispatch_unknown' | 'unavailable' | 'blocked' | (string & {});
export interface AgentMessage { id: string; role: 'user' | 'assistant'; content: string; attachment_references: AgentAttachmentReference[]; created_at: string; work_id?: string | null; execution_state?: AgentExecutionState; execution_reason?: string | null }
export type AgentState = 'queued' | 'running' | 'waiting_input' | 'waiting_confirmation' | 'stopping' | 'completed' | 'failed' | 'cancelled' | 'terminated';
export interface AgentReference { domain_type: string; object_id: string; revision: number | string; public_summary: string; reference_id?: string; digest?: string; current?: boolean; stale?: boolean }
export interface AgentRequirement { version: number; content: string; received_at: string | null; applied_at: string | null }
export interface AgentWork {
  id: string; conversation_id?: string; goal: string; state: AgentState; public_summary?: string; stop_reason?: string;
  current_requirement_version: number; requirements?: AgentRequirement[]; result_references?: AgentReference[]; business_references?: AgentReference[];
  children?: { id: string; title: string; state: AgentState; summary?: string }[];
  owner_name?: string; department_code?: string;
}
export interface AgentManagementWork { id: string; owner_id: number | string; department_code: string; summary: string; state: AgentState; business_references: AgentReference[] }
export interface AgentEmployee { id: number; username: string; display_name: string; department_code: string; is_active: boolean; roles: string[] }
export interface AgentUsage { calls: number; unknown_usage_calls: number; prompt_tokens: number | null; completion_tokens: number | null }
export interface AgentEvent { seq: number; event_key: string; type: string; payload: { summary?: string }; created_at: string }
export interface AgentEventPage { items: AgentEvent[]; has_more: boolean }
export interface AgentSkill { id: string; name: string; description?: string; version: string; digest: string }
export interface AgentInstallation { skill_id: string; version: string; enabled: boolean }

const root = '/api/agent/';
const idPath = (id: string) => encodeURIComponent(id);
const query = (values: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)); });
  return params.size ? `?${params}` : '';
};
const write = <T>(path: string, body: unknown, method = 'POST') => apiRequest<T>(`${root}${path}`, { method, body: JSON.stringify(body) });
async function listPage<T>(path: string, page = 1, signal?: AbortSignal, filters: Record<string, string | undefined> = {}): Promise<AgentPage<T>> {
  const result = await apiRequest<{ items: T[]; total: number }>(`${root}${path}${query({ ...filters, page, page_size: 20 })}`, { signal });
  if (!Array.isArray(result.items) || !Number.isSafeInteger(result.total) || result.total < 0) throw new ApiError(502, '列表格式无效，请重新读取。');
  return { ...result, page };
}
export const nextAgentPage = (page: AgentPage<unknown>) => (page.page || 1) * 20 < (page.total || 0) ? (page.page || 1) + 1 : undefined;
export const agentRequestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
export const listAgentConversations = (page?: number, signal?: AbortSignal) => listPage<AgentConversation>('conversations/', page, signal);
export const createAgentConversation = (client_request_id: string, project_id?: string) => write<AgentConversation>('conversations/', { client_request_id, ...(project_id ? { project_id } : {}) });
export const listAgentProjects = (page?: number, signal?: AbortSignal) => listPage<AgentProject>('projects/', page, signal);
export const createAgentProject = (name: string) => write<AgentProject>('projects/', { name });
export const listAgentMessages = (id: string, page?: number, signal?: AbortSignal) => listPage<AgentMessage>(`conversations/${idPath(id)}/messages/`, page, signal);
export const sendAgentMessage = (id: string, text: string, attachments: AgentAttachmentReference[], client_request_id: string, work_id?: string, defer_dispatch = false) => write<AgentMessage>(`conversations/${idPath(id)}/messages/`, { text, attachment_references: attachments, client_request_id, ...(work_id ? { work_id } : {}), ...(defer_dispatch ? { defer_dispatch: true } : {}) });
export const listAgentWork = (page?: number, signal?: AbortSignal) => listPage<AgentWork>('work/', page, signal);
export const getAgentWork = (id: string, signal?: AbortSignal) => apiRequest<AgentWork>(`${root}work/${idPath(id)}/`, { signal });
export const changeAgentRequirement = (id: string, expected_version: number, message_id: string, text: string) => write<{ version: number; status: string; applied?: boolean; execution_state?: AgentExecutionState }>(`work/${idPath(id)}/requirements/`, { expected_version, message_id, content: text });
export const commandAgentWork = async (id: string, action: 'cancel' | 'retry', expected_version: number, request_id: string) => {
  const response = await write<AgentWork | { state: AgentState }>(`work/${idPath(id)}/${action}/`, { expected_version, request_id });
  return action === 'retry' && 'id' in response ? response : getAgentWork(id);
};
export const getAgentEvents = async (id: string, after_seq: number, signal?: AbortSignal): Promise<AgentEventPage> => {
  const response = await apiRequest<{ items: (Omit<AgentEvent, 'event_key'> & { id: string })[]; total: number }>(`${root}work/${idPath(id)}/events/${query({ after_seq, page_size: 50 })}`, { signal });
  return { items: response.items.map(event => ({ ...event, event_key: event.id })), has_more: response.total > response.items.length };
};
export const listAgentSkills = (signal?: AbortSignal) => apiRequest<AgentPage<AgentSkill>>(`${root}skills/`, { signal });
export const listAgentInstallations = (signal?: AbortSignal) => apiRequest<AgentPage<AgentInstallation>>(`${root}installations/`, { signal });
export const saveAgentInstallation = (skill_id: string, enabled: boolean) => write<AgentInstallation>('installations/', { skill_id, enabled });
export const removeAgentInstallation = (skill_id: string) => apiRequest<void>(`${root}installations/${idPath(skill_id)}/`, { method: 'DELETE' });
export const listAgentManagementWork = (department?: string, page?: number, signal?: AbortSignal) => listPage<AgentManagementWork>('management/work/', page, signal, { department_code: department });
export const listAgentEmployees = (page?: number, signal?: AbortSignal) => listPage<AgentEmployee>('employees/', page, signal);
export const createAgentEmployee = (username: string, display_name: string, department_code: string) => write<AgentEmployee>('employees/', { username, display_name, department_code });
export const updateAgentEmployee = (id: number, changes: { display_name?: string; department_code?: string; is_active?: boolean }) => write<AgentEmployee>('employees/' + id + '/', changes, 'PATCH');
export const getAgentUsage = (filters: { department_code?: string; owner_id?: string; start?: string; end?: string } = {}, signal?: AbortSignal) => apiRequest<AgentUsage>(root + 'management/usage/' + query(filters), { signal });
export const uploadAgentAttachment = async (file: File) => {
  const body = new FormData();
  body.set('file', file);
  const result = await apiRequest<AgentAttachment>(`${root}attachments/`, { method: 'POST', body });
  if (result.type !== 'agent_attachment' || !result.id || typeof result.name !== 'string' || !/^[a-f0-9]{64}$/i.test(result.sha256)) throw new ApiError(502, '附件返回的授权引用不完整，请重新核对。');
  return result;
};

export function mergeAgentEvents(current: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const merged = [...current];
  let cursor = current.at(-1)?.seq ?? 0;
  for (const event of [...incoming].sort((left, right) => left.seq - right.seq)) {
    if (!Number.isSafeInteger(event.seq) || event.seq < 1 || !event.event_key) throw new ApiError(502, '事件格式无效，请重新同步。');
    const previous = merged.find(item => item.seq === event.seq || item.event_key === event.event_key);
    if (previous) {
      if (previous.seq !== event.seq || previous.event_key !== event.event_key) throw new ApiError(502, '事件版本冲突，请重新同步。');
      continue;
    }
    if (event.seq <= cursor) throw new ApiError(502, '事件顺序异常，请重新连接补读。');
    merged.push(event); cursor = event.seq;
  }
  return merged;
}

export function agentReferencePath(reference: AgentReference, action: 'view' | 'download', management = false): string | null {
  if (management) {
    if (!reference.reference_id || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(reference.reference_id)) return null;
    const types = ['document_task', 'document_source', 'document_artifact', 'resume_batch', 'resume_artifact', 'business_revision'];
    if (!types.includes(reference.domain_type) || action === 'download' && !['document_source', 'document_artifact', 'resume_artifact'].includes(reference.domain_type)) return null;
    return '/api/agent/management/references/' + idPath(reference.reference_id) + (action === 'download' ? '/download/' : '/');
  }
  const id = idPath(reference.object_id);
  if (reference.domain_type === 'document_source' && /^[a-f0-9]{64}$/i.test(String(reference.revision)) && /^[A-Za-z0-9_-]+$/.test(reference.object_id)) return action === 'view' ? '/api/product/sources/' + id + '/preview/?page=1' : null;
  if (!/^[A-Za-z0-9_-]+$/.test(reference.object_id) || !Number.isSafeInteger(Number(reference.revision)) || Number(reference.revision) < 1) return null;
  if (reference.domain_type === 'document_artifact') return '/api/product/artifacts/' + id + (action === 'view' ? '/preview/?page=1' : '/download/');
  if (reference.domain_type === 'document_task' && action === 'view') return '/api/product/tasks/' + id + '/';
  if (reference.domain_type === 'resume_batch' && action === 'view') return '/api/hr/recruitment/batches/' + id + '/summary/';
  if (reference.domain_type === 'resume_artifact') return '/api/hr/recruitment/resumes/' + id + (action === 'download' ? '/download/' : '/');
  if (reference.domain_type === 'product_artifact') return `/api/product/artifacts/${id}/${action === 'view' ? 'preview/?page=1' : 'download/'}`;
  if (reference.domain_type === 'product_source') return `/api/product/sources/${id}/${action === 'view' ? 'preview/?page=1' : 'download/'}`;
  if (['business_revision', 'business_ledger_revision'].includes(reference.domain_type) && action === 'view') return `/api/business/ledgers/finance/versions/${id}/`;
  if (reference.domain_type === 'hr_resume') return `/api/hr/recruitment/resumes/${id}/download/`;
  return null;
}
