import { apiRequest } from "../api";

export interface SelectOption { value: string; label: string }

export interface TenderSource {
  code: string;
  name: string;
  health_state: string;
  health_label?: string;
  health_detail?: string;
  last_success_at: string | null;
  last_failure_at?: string | null;
  consecutive_failures?: number;
}

export interface OpportunityBudget {
  amount_yuan: string | null;
  cap_yuan: string | null;
  raw: string;
}

export interface OpportunitySummary {
  id: number;
  project_name: string;
  project_code: string;
  region: string;
  purchaser: string;
  budget: OpportunityBudget;
  publish_at: string | null;
  publish_date: string | null;
  publish_precision: "date" | "minute" | "second" | "unknown";
  bid_deadline: string | null;
  status: string;
  status_label?: string;
  source: TenderSource | null;
  original_url: string | null;
  current_version: number;
  first_seen_at: string | null;
}

export interface OpportunityDetail extends OpportunitySummary {
  agency?: string;
  notice_type?: string;
  procurement_method?: string;
  bid_open_at?: string | null;
  signup_time_text?: string;
  contact?: { person?: string; phone?: string };
  unknown_fields?: string[];
  notices?: Array<{ id: number; title: string; notice_type: string; source: TenderSource | null; original_url: string; publish_at: string | null; publish_date: string | null; publish_precision: string }>;
  versions?: Array<{ version: number; change_summary: string[]; is_current: boolean; created_at: string | null }>;
}

export interface OpportunityListPayload {
  items: OpportunitySummary[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface OpportunityFilters {
  q?: string;
  region?: string;
  source?: string;
  status?: string;
  purchaser?: string;
  need?: string;
  notice_type?: string;
  publish_period?: string;
  deadline_period?: string;
  budget_band?: string;
  page?: number;
  page_size?: number;
}

export interface OpportunityOptions {
  regions: SelectOption[];
  sources: SelectOption[];
  statuses: SelectOption[];
  needs: SelectOption[];
  noticeTypes: SelectOption[];
  publishPeriods: SelectOption[];
  deadlinePeriods: SelectOption[];
  budgetBands: SelectOption[];
}

export interface RefreshSourceResult {
  state: string;
  display_state?: string;
  complete?: boolean;
  new_notices?: number;
  new_versions?: number;
  error_code?: string;
  error_detail?: string;
}

export interface RefreshBatch {
  id: string;
  stored_state: string;
  display_state: string;
  results: Record<string, RefreshSourceResult>;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface RefreshOverview {
  available: boolean;
  unavailable_reason: string;
  batch: RefreshBatch | null;
  last_success_at: string | null;
}

const root = "/api/product/";
const opportunities = `${root}opportunities/`;

function queryString(filters: OpportunityFilters): string {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && String(value).trim()) query.set(key, String(value));
  });
  return query.toString();
}

function optionList(value: unknown): SelectOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    if (typeof item === "string") return [{ value: item, label: item }];
    if (!item || typeof item !== "object") return [];
    const entry = item as { value?: unknown; code?: unknown; label?: unknown; name?: unknown };
    const optionValue = typeof entry.value === "string" ? entry.value : typeof entry.code === "string" ? entry.code : "";
    if (!optionValue) return [];
    return [{ value: optionValue, label: typeof entry.label === "string" ? entry.label : typeof entry.name === "string" ? entry.name : optionValue }];
  });
}

function normalizeBatch(value: unknown): RefreshBatch | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const id = typeof item.batch_id === "string" ? item.batch_id : typeof item.id === "string" ? item.id : "";
  if (!id) return null;
  const storedState = typeof item.stored_state === "string" ? item.stored_state : typeof item.state === "string" ? item.state : "";
  return {
    id,
    stored_state: storedState,
    display_state: typeof item.display_state === "string" ? item.display_state : storedState,
    results: item.results && typeof item.results === "object" ? item.results as Record<string, RefreshSourceResult> : {},
    created_at: typeof item.created_at === "string" ? item.created_at : undefined,
    started_at: typeof item.started_at === "string" ? item.started_at : null,
    finished_at: typeof item.finished_at === "string" ? item.finished_at : null,
  };
}

export function listOpportunities(filters: OpportunityFilters, signal?: AbortSignal) {
  const query = queryString(filters);
  return apiRequest<OpportunityListPayload>(`${opportunities}${query ? `?${query}` : ""}`, { signal });
}

export async function getOpportunity(id: number, signal?: AbortSignal): Promise<OpportunityDetail> {
  const value = await apiRequest<OpportunityDetail | { opportunity: OpportunityDetail }>(`${opportunities}${encodeURIComponent(id)}/`, { signal });
  return "opportunity" in value ? value.opportunity : value;
}

export async function getOpportunityOptions(signal?: AbortSignal): Promise<OpportunityOptions> {
  const value = await apiRequest<Record<string, unknown>>(`${root}options/`, { signal });
  return {
    regions: optionList(value.regions ?? value.region),
    sources: optionList(value.sources ?? value.source),
    statuses: optionList(value.statuses ?? value.status),
    needs: optionList(value.needs ?? value.need),
    noticeTypes: optionList(value.notice_types ?? value.notice_type),
    publishPeriods: optionList(value.publish_periods ?? value.publish_period),
    deadlinePeriods: optionList(value.deadline_periods ?? value.deadline_period),
    budgetBands: optionList(value.budget_bands ?? value.budget_band),
  };
}

export async function getTenderSources(signal?: AbortSignal): Promise<TenderSource[]> {
  const value = await apiRequest<TenderSource[] | { items: TenderSource[] }>(`${root}sources/`, { signal });
  return Array.isArray(value) ? value : Array.isArray(value.items) ? value.items : [];
}

export async function getRefreshOverview(signal?: AbortSignal): Promise<RefreshOverview> {
  const value = await apiRequest<Record<string, unknown>>(`${root}refresh/`, { signal });
  const available = typeof value.available === "boolean" ? value.available
    : typeof value.refresh_available === "boolean" ? value.refresh_available
    : value.enabled === true && value.consumer_available === true;
  return {
    available,
    unavailable_reason: typeof value.unavailable_reason === "string" ? value.unavailable_reason
      : value.enabled === false ? "后台未启用手动刷新。"
      : value.consumer_online === false ? "刷新消费者当前离线。"
      : value.sources_ready === false ? "采集来源尚未就绪。"
      : typeof value.detail === "string" ? value.detail : "",
    batch: normalizeBatch(value.active_batch ?? value.batch),
    last_success_at: typeof value.last_success_at === "string" ? value.last_success_at : null,
  };
}

export async function requestRefresh(signal?: AbortSignal): Promise<RefreshBatch> {
  const value = await apiRequest<unknown>(`${root}refresh/`, { method: "POST", body: "{}", signal });
  const batch = normalizeBatch(value);
  if (!batch) throw new Error("刷新接口未返回有效批次。");
  return batch;
}

export async function getRefreshBatch(id: string, signal?: AbortSignal): Promise<RefreshBatch> {
  const value = await apiRequest<unknown>(`${root}refresh/${encodeURIComponent(id)}/`, { signal });
  const batch = normalizeBatch(value);
  if (!batch) throw new Error("刷新状态接口未返回有效批次。");
  return batch;
}
