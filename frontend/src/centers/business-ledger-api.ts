import { apiRequest } from '../api';

export type LedgerDepartment = 'engineering' | 'finance' | 'presales';
export type LedgerState = 'draft' | 'submitted' | 'published';

export interface LedgerPermissions {
  can_edit: boolean;
  can_submit: boolean;
  can_publish: boolean;
}

export interface LedgerDepartmentAccess extends LedgerPermissions {
  department: LedgerDepartment;
  title: string;
}

export interface BusinessLedger {
  department: LedgerDepartment;
  title: string;
  state: LedgerState;
  revision: number;
  as_of: string | null;
  source_name: string;
  records: Record<string, string>[];
  fields: Record<string, string>;
  statuses: string[];
  permissions: LedgerPermissions;
  updated_at: string | null;
  last_return_reason: string;
  record_meta?: Record<string, FinanceRecordMeta>;
}

export interface FinanceRecordMeta {
  project_id: string; record_author_id: number | string; draft_actor_id: number | string;
  draft_revision: number; draft_checksum: string; deleted: boolean;
}

export interface LedgerVersion {
  id: string;
  revision: number;
  state: LedgerState;
  as_of: string | null;
  source_name: string;
  record_count?: number;
  created_at?: string;
  updated_at?: string;
  published_at?: string | null;
  actor_name?: string;
  action?: string;
  actor?: { id: number | string; name: string } | null;
}

export interface LedgerVersionDetail {
  id: string;
  department: LedgerDepartment;
  revision: number;
  state: LedgerState;
  action: string;
  as_of: string | null;
  source_name: string;
  records: Record<string, string>[];
  checksum: string;
  actor: { id: number | string; name: string } | null;
  created_at: string;
  record_meta?: Record<string, FinanceRecordMeta>;
}

const root = '/api/business/ledgers/';

export function listLedgerPermissions(signal?: AbortSignal) {
  return apiRequest<{ departments: LedgerDepartmentAccess[] }>(`${root}permissions/`, { signal });
}

export function getLedger(department: LedgerDepartment, signal?: AbortSignal) {
  return apiRequest<BusinessLedger>(`${root}${department}/`, { signal });
}

export function updateLedgerMetadata(ledger: BusinessLedger, values: { as_of: string; source_name: string }) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/`, {
    method: 'PATCH', body: JSON.stringify({ expected_revision: ledger.revision, ...values }),
  });
}

export function addLedgerRecord(ledger: BusinessLedger, record: Record<string, string>) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/records/`, {
    method: 'POST', body: JSON.stringify({ expected_revision: ledger.revision, record }),
  });
}

export function updateLedgerRecord(ledger: BusinessLedger, projectId: string, record: Record<string, string>) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/records/${encodeURIComponent(projectId)}/`, {
    method: 'PUT', body: JSON.stringify({ expected_revision: ledger.revision, record }),
  });
}

export function deleteLedgerRecord(ledger: BusinessLedger, projectId: string) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/records/${encodeURIComponent(projectId)}/`, {
    method: 'DELETE', body: JSON.stringify({ expected_revision: ledger.revision }),
  });
}

export function importLedger(ledger: BusinessLedger, file: File, asOf: string) {
  const body = new FormData();
  body.set('file', file);
  body.set('as_of', asOf);
  body.set('expected_revision', String(ledger.revision));
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/import/`, { method: 'POST', body });
}

export function transitionLedger(ledger: BusinessLedger, action: 'submit' | 'publish', reason?: never) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/${action}/`, {
    method: 'POST', body: JSON.stringify({ expected_revision: ledger.revision, ...(reason ? { reason } : {}) }),
  });
}

export function publishFinanceRecord(ledger: BusinessLedger, recordId: string, entry: FinanceRecordMeta, publishedRevision: number) {
  return apiRequest<BusinessLedger>(`${root}finance/publish/`, {
    method: 'POST', body: JSON.stringify({ expected_revision: ledger.revision, expected_published_revision: publishedRevision,
      records: [{ record_id: recordId, source_revision: entry.draft_revision, source_checksum: entry.draft_checksum }] }),
  });
}

export function returnLedger(ledger: BusinessLedger, reason: string) {
  return apiRequest<BusinessLedger>(`${root}${ledger.department}/return/`, {
    method: 'POST', body: JSON.stringify({ expected_revision: ledger.revision, reason }),
  });
}

export function listLedgerVersions(department: LedgerDepartment, signal?: AbortSignal) {
  return apiRequest<{ department: LedgerDepartment; versions: LedgerVersion[] }>(`${root}${department}/versions/`, { signal })
    .then(result => result.versions);
}

export function getLedgerVersion(department: LedgerDepartment, id: string, signal?: AbortSignal) {
  return apiRequest<LedgerVersionDetail>(`${root}${department}/versions/${encodeURIComponent(id)}/`, { signal });
}
