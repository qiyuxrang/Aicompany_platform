import { ApiError, apiRequest, isModuleCode } from "../api";

export type OpsRangeDays = 1 | 7 | 30;

export interface OpsRange {
  days: OpsRangeDays;
  start: string;
  end: string;
  timezone: string;
}

export interface OpsPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface AuditRow {
  id: number | string;
  created_at: string;
  actor: string;
  action: string;
  target: string;
  result: string;
  changes: string[];
}

export interface ModuleCheck {
  state: "reachable" | "unavailable" | "not_configured" | "disabled" | "error";
  checked_at: string | null;
  duration_ms: number | null;
  message: string;
  next_check_at: string | null;
  stale: boolean;
}

export interface OpsModule {
  id: number | string;
  code: string;
  name: string;
  description: string;
  url: string | null;
  status: string;
  enabled: boolean;
  admin_url: string;
  check: ModuleCheck | null;
}

export interface IssueNote {
  at: string;
  actor: string;
  text: string;
}

export interface OpsIssue {
  id: number | string;
  module_code: string;
  module_name: string;
  severity: string;
  title: string;
  status: "open" | "investigating" | "closed" | "recovered";
  health_state: "unresolved" | "recovered";
  first_seen: string;
  last_seen: string;
  occurrences: number;
  evidence: string;
  checked_at: string | null;
  notes: IssueNote[];
  admin_url: string;
}

export interface PerformanceSummary {
  state: string;
  scope: string;
  window_seconds: number;
  collected_since: string | null;
  requests: number;
  errors: number;
  error_rate: number | null;
  average_ms: number | null;
  max_ms: number | null;
  routes: Record<string, unknown>[];
}

export interface BackupSummary {
  state: string;
  message: string;
  checked_at: string | null;
  record: null | {
    timestamp: string;
    table_count: number;
    all_tables_equal: boolean;
    source_unchanged: boolean;
    scope_matches: boolean;
  };
}

export interface TrendPoint {
  date: string;
  login_users: number;
  login_count: number;
  module_launches: number;
}

export interface OpsOverview {
  updated_at: string;
  range: OpsRange;
  health: { state: string; checked_at: string; database_ms: number | null; message: string };
  accounts: { enabled: number; total: number };
  usage: { login_users: number; login_count: number; module_launches: number };
  performance: PerformanceSummary;
  issue_count: number;
  trend: TrendPoint[];
  modules: OpsModule[];
  recent_issues: OpsIssue[];
  recent_audit: AuditRow[];
  backup: BackupSummary;
  version: string;
  my_modules: { code: string; name: string; status: string; enabled: boolean }[];
}

export interface OpsUser {
  id: number | string;
  username: string;
  display_name: string;
  is_active: boolean;
  must_change_password: boolean;
  roles: { code: string; name: string }[];
  last_login: string | null;
  admin_url: string;
  password_url: string;
}

export interface OpsUserDetail extends OpsUser {
  recent_audit: AuditRow[];
}

export interface OpsUsersPage extends OpsPage<OpsUser> {
  roles: { code: string; name: string }[];
}

export interface OpsEmployeeUsage {
  id: number | string;
  username: string;
  display_name: string;
  login_count: number;
  module_launches: number;
}

export interface OpsModelUsageRoute {
  code: string;
  name: string;
  calls: number;
  successes: number;
  failures: number;
}

export interface OpsModelUsage {
  enabled_routes: number;
  calls: number;
  successes: number;
  failures: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  routes: OpsModelUsageRoute[];
}

export interface OpsUsage {
  updated_at: string;
  range: OpsRange;
  summary: { enabled_accounts: number; login_users: number; login_count: number; module_launches: number };
  trend: TrendPoint[];
  ranking: { code: string; name: string; launches: number }[];
  employees?: OpsEmployeeUsage[];
  model_usage?: OpsModelUsage | null;
  definitions: Record<string, string>;
}

export interface OpsModulesResponse {
  updated_at: string;
  items: OpsModule[];
}

export interface OpsIssuesPage extends OpsPage<OpsIssue> {}

export interface OpsMaintenance {
  updated_at: string;
  environment: {
    mode: string;
    https: boolean;
    database_engine: string;
    timezone: string;
    host_metrics: { state: string; message: string };
  };
  performance: PerformanceSummary;
  backup: BackupSummary;
  version: string;
  deployment_history: { state: string; message: string };
  audit: OpsPage<AuditRow>;
}

function queryPath(path: string, values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function numericId(id: number | string): string {
  const value = String(id);
  if (!/^\d+$/.test(value)) throw new ApiError(400, "对象参数无效，请从列表重新选择。");
  return value;
}

export function getOpsOverview(days: OpsRangeDays): Promise<OpsOverview> {
  return apiRequest(queryPath("/api/ops/overview/", { days }));
}

export function getOpsUsers(filters: { q: string; status: string; role: string; page: number }): Promise<OpsUsersPage> {
  return apiRequest(queryPath("/api/ops/users/", filters));
}

export function getOpsUser(id: number | string): Promise<OpsUserDetail> {
  return apiRequest(`/api/ops/users/${numericId(id)}/`);
}

export function getOpsUsage(days: OpsRangeDays, module: string): Promise<OpsUsage> {
  if (module && !isModuleCode(module)) return Promise.reject(new ApiError(400, "模块筛选无效。"));
  return apiRequest(queryPath("/api/ops/usage/", { days, module }));
}

export function getOpsModules(): Promise<OpsModulesResponse> {
  return apiRequest("/api/ops/modules/");
}

export function checkOpsModule(code: string): Promise<{ module: OpsModule; issue: OpsIssue | null }> {
  if (!isModuleCode(code)) return Promise.reject(new ApiError(400, "模块参数无效。"));
  return apiRequest(`/api/ops/modules/${code}/check/`, { method: "POST", body: "{}" });
}

export function getOpsIssues(filters: {
  severity: string;
  status: string;
  module: string;
  days: OpsRangeDays | 30;
  page: number;
}): Promise<OpsIssuesPage> {
  if (filters.module && !isModuleCode(filters.module)) return Promise.reject(new ApiError(400, "模块筛选无效。"));
  return apiRequest(queryPath("/api/ops/issues/", filters));
}

export function getOpsIssue(id: number | string): Promise<OpsIssue> {
  return apiRequest(`/api/ops/issues/${numericId(id)}/`);
}

export function updateOpsIssue(
  id: number | string,
  values: { status?: "open" | "investigating" | "closed"; note?: string },
): Promise<OpsIssue> {
  return apiRequest(`/api/ops/issues/${numericId(id)}/`, {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function getOpsMaintenance(filters: { days: OpsRangeDays; page: number; action: string }): Promise<OpsMaintenance> {
  return apiRequest(queryPath("/api/ops/maintenance/", filters));
}
