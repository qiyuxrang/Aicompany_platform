export interface Role {
  code: string;
  name: string;
}

export interface CurrentUser {
  id: number | string;
  username: string;
  display_name: string;
  roles: Role[];
  must_change_password: boolean;
  is_platform_admin: boolean;
  department_code?: 'product' | 'hr' | 'finance' | 'engineering' | '';
}

export type ModuleStatus = "pending" | "navigation" | "verified" | "disabled";

export interface PortalModule {
  code: string;
  name: string;
  description: string;
  status: ModuleStatus;
  enabled: boolean;
}

export interface BusinessSummary {
  projects: { id: string | number; name: string }[];
  summary: Partial<Record<"project_count" | "contract_amount" | "received_amount" | "receivable_amount", string | number>>;
  source: string;
  updated_at: string;
}

export interface WorkSummaryItem {
  id: string;
  title: string;
  status: string;
  href: string;
  updated_at: string;
  kind: string;
}

export interface WorkSummarySection {
  available: boolean;
  count: number | null;
  items: WorkSummaryItem[];
  reason?: string;
}

export interface WorkSummary {
  modules: Record<"product" | "hr", { available: boolean; reason?: string }>;
  sections: Record<"my_tasks" | "pending_reviews" | "recent_results", WorkSummarySection>;
}

export const summaryLabels = {
  project_count: "项目数量",
  contract_amount: "合同金额",
  received_amount: "已收金额",
  receivable_amount: "应收金额",
} satisfies Record<keyof BusinessSummary["summary"], string>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBusinessSummary(value: unknown): value is BusinessSummary {
  if (!isRecord(value) || typeof value.source !== "string" || !value.source.trim()
    || typeof value.updated_at !== "string" || !Number.isFinite(Date.parse(value.updated_at))
    || !Array.isArray(value.projects) || !isRecord(value.summary)) return false;
  return value.projects.every((project: unknown) => isRecord(project)
    && (typeof project.id === "string" || (typeof project.id === "number" && Number.isFinite(project.id)))
    && typeof project.name === "string")
    && Object.entries(value.summary).every(([key, metric]) => Object.hasOwn(summaryLabels, key)
      && (typeof metric === "string" || (typeof metric === "number" && Number.isFinite(metric))));
}

function isWorkSummaryItem(value: unknown): value is WorkSummaryItem {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.title === "string"
    && typeof value.status === "string"
    && typeof value.href === "string"
    && /^\/(?!\/)[^\s\\]*$/.test(value.href)
    && typeof value.updated_at === "string"
    && Number.isFinite(Date.parse(value.updated_at))
    && typeof value.kind === "string";
}

function isWorkSummarySection(value: unknown): value is WorkSummarySection {
  if (!isRecord(value) || typeof value.available !== "boolean" || !Array.isArray(value.items)
    || !value.items.every(isWorkSummaryItem)) return false;
  if (value.reason !== undefined && typeof value.reason !== "string") return false;
  return value.available
    ? typeof value.count === "number" && Number.isInteger(value.count) && value.count >= value.items.length
    : value.count === null && value.items.length === 0;
}

function isWorkSummary(value: unknown): value is WorkSummary {
  if (!isRecord(value) || !isRecord(value.modules) || !isRecord(value.sections)) return false;
  const modules = value.modules;
  const sections = value.sections;
  const moduleValid = ["product", "hr"].every((code) => {
    const module = modules[code];
    return isRecord(module) && typeof module.available === "boolean"
      && (module.reason === undefined || typeof module.reason === "string");
  });
  return moduleValid && ["my_tasks", "pending_reviews", "recent_results"]
    .every((code) => isWorkSummarySection(sections[code]));
}

export function isModuleCode(code: string): boolean {
  return /^[a-zA-Z0-9_-]+$/.test(code);
}

function modulePath(code: string): string {
  if (!isModuleCode(code)) throw new ApiError(400, "模块参数无效，请从工作台重新选择入口。");
  return `/api/modules/${code}/`;
}

interface ErrorBody {
  detail?: string;
  code?: string;
}

const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let csrfToken: string | null = null;
let csrfRequest: Promise<string> | null = null;

export const unauthorizedEvent = "portal:unauthorized";
export const passwordChangeRequiredEvent = "portal:password-change-required";
export const opsPermissionRevokedEvent = "portal:ops-permission-revoked";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function errorFrom(response: Response, body: unknown): ApiError {
  const payload = body && typeof body === "object" ? (body as ErrorBody) : {};
  return new ApiError(
    response.status,
    typeof payload.detail === "string" ? payload.detail : "请求未完成，请稍后重试。",
    typeof payload.code === "string" ? payload.code : undefined,
  );
}

async function fetchCsrf(): Promise<string> {
  const response = await fetch("/api/csrf/", { credentials: "same-origin" });
  const body = await parseBody(response);
  if (!response.ok) throw errorFrom(response, body);
  if (!body || typeof body !== "object" || typeof (body as { csrfToken?: unknown }).csrfToken !== "string") {
    throw new ApiError(502, "服务器未返回有效的安全令牌。");
  }
  csrfToken = (body as { csrfToken: string }).csrfToken;
  return csrfToken;
}

async function ensureCsrf(force = false): Promise<string> {
  if (force) csrfToken = null;
  if (!force && csrfToken) return csrfToken;
  if (!csrfRequest) {
    csrfRequest = fetchCsrf().finally(() => {
      csrfRequest = null;
    });
  }
  return csrfRequest;
}

interface RequestOptions {
  signalAuth?: boolean;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (unsafeMethods.has(method)) {
    try {
      headers.set("X-CSRFToken", await ensureCsrf());
    } catch (error) {
      if (options.signalAuth !== false && error instanceof ApiError && error.status === 401) {
        window.dispatchEvent(new Event(unauthorizedEvent));
      }
      throw error;
    }
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  const body = await parseBody(response);

  if (!response.ok) {
    const error = errorFrom(response, body);
    if (options.signalAuth !== false) {
      if (error.status === 401) window.dispatchEvent(new Event(unauthorizedEvent));
      if (error.status === 403 && error.code === "password_change_required") {
        window.dispatchEvent(new Event(passwordChangeRequiredEvent));
      }
      if (path.startsWith("/api/ops/") && error.status === 403 && error.code === "ops_forbidden") {
        window.dispatchEvent(new Event(opsPermissionRevokedEvent));
      }
    }
    throw error;
  }

  return body as T;
}

export async function apiEventStream<T>(path: string, body: unknown, signal: AbortSignal, onDelta: (delta: string) => void): Promise<T> {
  let token: string;
  try { token = await ensureCsrf(); }
  catch (error) {
    if (error instanceof ApiError && error.status === 401) window.dispatchEvent(new Event(unauthorizedEvent));
    throw error;
  }
  const response = await fetch(path, {
    method: "POST", body: JSON.stringify(body), signal, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": token },
  });
  if (!response.ok) {
    const error = errorFrom(response, await parseBody(response));
    if (error.status === 401) window.dispatchEvent(new Event(unauthorizedEvent));
    if (error.status === 403 && error.code === "password_change_required") window.dispatchEvent(new Event(passwordChangeRequiredEvent));
    throw error;
  }
  const reader = response.body?.getReader();
  if (!reader || !response.headers.get("Content-Type")?.startsWith("text/event-stream")) {
    throw new ApiError(502, "回答流格式无效，请重试。", "invalid_response");
  }
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      if (pending.length > 65536) throw new ApiError(502, "回答流过长，请重试。", "invalid_response");
      let boundary = pending.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        if (!frame.startsWith("data: ")) throw new ApiError(502, "回答流格式无效，请重试。", "invalid_response");
        let event: unknown;
        try { event = JSON.parse(frame.slice(6)); } catch { throw new ApiError(502, "回答流格式无效，请重试。", "invalid_response"); }
        if (!event || typeof event !== "object") throw new ApiError(502, "回答流格式无效，请重试。", "invalid_response");
        const value = event as Record<string, unknown>;
        if (value.error && typeof value.error === "object") {
          const error = value.error as Record<string, unknown>;
          const code = typeof error.code === "string" ? error.code : "invalid_response";
          const status = code === "scope_revoked" || code === "forbidden" ? 403 : code === "conflict" ? 409 : 502;
          throw new ApiError(status, typeof error.detail === "string" ? error.detail : "回答未完成，请重试。", code);
        }
        if (typeof value.delta === "string" && value.delta.length <= 4000) onDelta(value.delta);
        else if (value.done && typeof value.done === "object") return value.done as T;
        else throw new ApiError(502, "回答流格式无效，请重试。", "invalid_response");
        boundary = pending.indexOf("\n\n");
      }
    }
    throw new ApiError(502, "回答连接中断，请重试原问题。", "incomplete_stream");
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function clearApiSession(): void {
  csrfToken = null;
  csrfRequest = null;
}

export async function getMe(): Promise<CurrentUser> {
  return apiRequest<CurrentUser>("/api/me/", {}, { signalAuth: false });
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  const user = await apiRequest<CurrentUser>(
    "/api/login/",
    {
      method: "POST",
      body: JSON.stringify({ username, password }),
    },
    { signalAuth: false },
  );
  await ensureCsrf(true);
  return user;
}

export async function logout(): Promise<void> {
  await apiRequest<void>("/api/logout/", { method: "POST", body: "{}" });
}

export async function changePassword(oldPassword: string | undefined, newPassword: string, confirmPassword?: string): Promise<{ detail: string }> {
  return apiRequest<{ detail: string }>(
    "/api/password/",
    {
      method: "POST",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword, confirm_password: confirmPassword }),
    },
  );
}

export async function getModules(): Promise<PortalModule[]> {
  const result = await apiRequest<unknown>("/api/modules/");
  if (!Array.isArray(result) || !result.every(isPortalModule)) {
    throw new ApiError(502, "模块列表返回格式无效。");
  }
  return result;
}

function isPortalModule(value: unknown): value is PortalModule {
  return isRecord(value) && typeof value.code === "string" && isModuleCode(value.code)
    && typeof value.name === "string" && typeof value.description === "string"
    && typeof value.enabled === "boolean" && typeof value.status === "string"
    && ["pending", "navigation", "verified", "disabled"].includes(value.status);
}

export async function getModule(code: string): Promise<PortalModule> {
  const result = await apiRequest<unknown>(modulePath(code));
  if (!isPortalModule(result) || result.code !== code) throw new ApiError(502, "模块详情返回格式无效。");
  return result;
}

export async function launchModule(code: string): Promise<string> {
  const result = await apiRequest<{ url?: unknown }>(`${modulePath(code)}launch/`, {
    method: "POST",
    body: "{}",
  });
  if (typeof result?.url !== "string" || !result.url.trim()) {
    throw new ApiError(502, "服务器未返回有效的系统入口。");
  }
  let target: URL;
  try {
    target = new URL(result.url, window.location.origin);
  } catch {
    throw new ApiError(502, "服务器未返回有效的系统入口。");
  }
  if ((target.protocol !== "http:" && target.protocol !== "https:") || target.username || target.password) {
    throw new ApiError(502, "服务器返回了不受支持的系统入口。");
  }
  return target.href;
}

export async function getBusinessSummary(): Promise<BusinessSummary> {
  const result = await apiRequest<unknown>("/api/business/summary/");
  if (!isBusinessSummary(result)) throw new ApiError(502, "经营摘要返回格式无效，未展示不可信数据。");
  return result;
}

export async function getWorkSummary(): Promise<WorkSummary> {
  const result = await apiRequest<unknown>("/api/work/summary/");
  if (!isWorkSummary(result)) throw new ApiError(502, "工作摘要返回格式无效，未展示不可信数据。");
  return result;
}
