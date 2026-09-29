import {
  DependencyList,
  FormEvent,
  MouseEvent,
  ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { ApiError, isApiError } from "../api";
import { AuditRow, OpsPage, OpsRangeDays, TrendPoint } from "./api";

export function navigateOps(href: string, replace = false): void {
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (current === href) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function OpsLink({ href, className, children, onClick, ariaCurrent }: {
  href: string;
  className?: string;
  ariaCurrent?: "page";
  children: ReactNode;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  return (
    <a
      href={href}
      className={className}
      aria-current={ariaCurrent}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigateOps(href);
      }}
    >
      {children}
    </a>
  );
}

export function useOpsSearch(): URLSearchParams {
  const [search, setSearch] = useState(window.location.search);
  useEffect(() => {
    const update = () => setSearch(window.location.search);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return new URLSearchParams(search);
}

export function setOpsQuery(
  changes: Record<string, string | number | null | undefined>,
  { replace = false, resetPage = false }: { replace?: boolean; resetPage?: boolean } = {},
): void {
  const params = new URLSearchParams(window.location.search);
  if (resetPage) params.delete("page");
  Object.entries(changes).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, String(value));
  });
  const query = params.toString();
  navigateOps(`${window.location.pathname}${query ? `?${query}` : ""}`, replace);
}

export function readDays(value: string | null, fallback: OpsRangeDays = 7): OpsRangeDays {
  return value === "1" || value === "7" || value === "30" ? Number(value) as OpsRangeDays : fallback;
}

export function readPage(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

export type LoadState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error"; error: ApiError | Error };

export function useOpsData<T>(load: () => Promise<T>, dependencies: DependencyList): [LoadState<T>, () => void] {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<LoadState<T>>({ kind: "loading" });
  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    void load().then(
      (data) => active && setState({ kind: "ready", data }),
      (error: unknown) => active && setState({
        kind: "error",
        error: error instanceof Error ? error : new Error("请求未完成，请稍后重试。"),
      }),
    );
    return () => { active = false; };
  }, [...dependencies, revision]);
  return [state, () => setRevision((value) => value + 1)];
}

export function PageHeader({ eyebrow, title, description, updatedAt, onRefresh, children }: {
  eyebrow: string;
  title: string;
  description: string;
  updatedAt?: string;
  onRefresh?: () => void;
  children?: ReactNode;
}) {
  return (
    <header className="ops-page-header">
      <div>
        <p className="ops-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="ops-page-actions">
        {updatedAt && <span className="ops-updated">更新于 {formatDateTime(updatedAt)}</span>}
        {children}
        {onRefresh && <button className="button secondary compact" type="button" onClick={onRefresh}>手动刷新</button>}
      </div>
    </header>
  );
}

export function RangeTabs({ value }: { value: OpsRangeDays }) {
  return (
    <div className="ops-segmented" aria-label="时间范围">
      {([1, 7, 30] as const).map((days) => (
        <button
          key={days}
          type="button"
          aria-pressed={value === days}
          onClick={() => setOpsQuery({ days }, { resetPage: true })}
        >
          {days}天
        </button>
      ))}
    </div>
  );
}

export function StatePanel<T>({ state, onRetry, empty, children }: {
  state: LoadState<T>;
  onRetry: () => void;
  empty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (state.kind === "loading") {
    return (
      <div className="ops-skeleton-grid" aria-busy="true" aria-label="正在加载运维数据">
        {[0, 1, 2].map((key) => <span className="ops-skeleton" key={key} />)}
      </div>
    );
  }
  if (state.kind === "error") {
    const forbidden = isApiError(state.error) && state.error.status === 403;
    return (
      <section className="ops-state" role="alert">
        <strong>{forbidden ? "无运维访问权限" : "数据加载失败"}</strong>
        <p>{forbidden ? "当前账号不是平台管理员，或尚未完成首次密码修改。后端仍会独立校验每次请求。" : state.error.message}</p>
        {!forbidden && <button className="button secondary compact" type="button" onClick={onRetry}>重试</button>}
      </section>
    );
  }
  if (empty?.(state.data)) {
    return (
      <section className="ops-state empty">
        <strong>暂无匹配数据</strong>
        <p>当前筛选条件下没有可展示记录。</p>
      </section>
    );
  }
  return <>{children(state.data)}</>;
}

export function StatusText({ value }: { value: string }) {
  return <span className={`ops-status ${statusTone(value)}`}>{statusLabel(value)}</span>;
}

export function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    active: "启用",
    inactive: "停用",
    enabled: "启用",
    disabled: "已停用",
    pending: "待接入",
    navigation: "导航接入",
    verified: "已验证集成",
    reachable: "可达",
    unavailable: "不可用",
    not_configured: "未配置",
    error: "检查异常",
    healthy: "正常",
    degraded: "需关注",
    open: "待处理",
    investigating: "处理中",
    closed: "人工关闭",
    recovered: "已恢复",
    unresolved: "未恢复",
    success: "成功",
    not_collected: "未采集",
    available: "可用",
    missing: "未接入",
    historical_record: "历史演练记录",
    failure: "失败",
    collected: "已采集",
    critical: "严重", warning: "警告", high: "高", medium: "中", low: "低",
    busy: "处理中，请稍后重试", stale: "状态已变化", throttled: "请求过于频繁",
    denied: "已拒绝", service_denied: "服务认证失败", permission_denied: "无访问权限",
    mapping_denied: "未配置身份映射", revoked: "权限已撤销", upstream_denied: "业务系统拒绝访问",
    upstream_error: "业务系统异常",
  };
  return labels[value] ?? (value || "未提供");
}

function statusTone(value: string): string {
  return ["reachable", "healthy", "success", "recovered", "verified", "available", "active", "enabled", "collected"].includes(value)
    ? "success"
    : ["unavailable", "error", "failure", "open", "unresolved", "inactive", "disabled", "critical", "high"].includes(value)
      ? "danger"
      : ["investigating", "degraded", "pending", "warning", "medium"].includes(value) ? "warning" : "neutral";
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "未提供";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatMetric(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "未采集" : `${new Intl.NumberFormat("zh-CN").format(value)}${suffix}`;
}

export function formatErrorRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "未采集";
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value * 100)}%`;
}

export function safeAdminHref(value: string): string | null {
  return /^\/admin\/(?:[a-zA-Z0-9_/-]+)?$/.test(value) ? value : null;
}

export function Pagination({ page }: { page: OpsPage<unknown> }) {
  if (page.pages <= 1) return null;
  return (
    <nav className="ops-pagination" aria-label="分页">
      <button type="button" disabled={page.page <= 1} onClick={() => setOpsQuery({ page: page.page - 1 })}>上一页</button>
      <span>第 {page.page} / {page.pages} 页，共 {page.total} 条</span>
      <button type="button" disabled={page.page >= page.pages} onClick={() => setOpsQuery({ page: page.page + 1 })}>下一页</button>
    </nav>
  );
}

export const auditActions: Record<string, string> = {
  login: "登录", logout: "退出登录", password_change: "修改密码", password_reset: "重置密码",
  user_create: "创建账号", user_change: "修改账号", role_create: "创建角色", role_change: "调整角色",
  module_create: "创建模块", module_change: "修改模块", module_launch: "进入模块", module_check: "检查模块",
  businessmapping_create: "创建身份映射", businessmapping_change: "调整身份映射",
  operational_issue_update: "更新问题处理", business_summary: "查询经营摘要",
  business_read: "读取经营数据", ticket_issue: "签发身份票据", ticket_redeem: "兑换身份票据",
  bootstrap_admin: "初始化管理员",
  ui_fixture_create: "创建界面验收账号", ui_fixture_close: "关闭界面验收账号",
};

export function auditActionLabel(value: string): string {
  return auditActions[value] ?? "其他操作";
}

function auditFieldLabel(value: string): string {
  const labels: Record<string, string> = {
    username: "用户名", display_name: "显示名称", is_active: "账号状态", roles: "角色",
    password: "密码", password_invalidated: "密码已作废", modules: "模块授权", name: "名称",
    code: "标识", enabled: "启停状态", status: "状态", url: "访问地址", description: "说明",
    user: "平台账号", external_user_id: "旧系统账号标识", note: "备注", notes: "备注",
    must_change_password: "首次改密要求", session_version: "会话版本",
  };
  return labels[value] ?? "其他字段";
}

export function AuditTable({ rows }: { rows: AuditRow[] }) {
  if (rows.length === 0) return <p className="ops-inline-empty">暂无审计记录。</p>;
  return (
    <div className="ops-table-wrap" role="region" aria-label="审计表格，可横向滚动" tabIndex={0}>
      <table className="ops-table">
        <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>对象</th><th>结果</th><th>变更字段</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{formatDateTime(row.created_at)}</td>
              <td>{row.actor || "系统"}</td>
              <td>{auditActionLabel(row.action)}{!auditActions[row.action] && <details><summary>原始标识</summary>{row.action}</details>}</td>
              <td>{row.target || "—"}</td>
              <td><StatusText value={row.result} /></td>
              <td>{row.changes.length > 0 ? row.changes.map(auditFieldLabel).join("、") : "—"}
                {row.changes.some((field) => auditFieldLabel(field) === "其他字段") && <details><summary>原始字段</summary>{row.changes.join("、")}</details>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrendChart({ points, days, module = "", detailPath = "/ops/usage" }: {
  points: TrendPoint[];
  days: OpsRangeDays;
  module?: string;
  detailPath?: string;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(760);
  useEffect(() => {
    const container = chartRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(container);
    return () => observer.disconnect();
  }, [points.length === 0]);
  if (points.length === 0) return <p className="ops-inline-empty">当前范围暂无趋势数据。</p>;
  const height = 180;
  const padding = 32;
  const peak = Math.max(1, ...points.flatMap((point) => [point.login_count, point.module_launches]));
  const tickStep = Math.max(1, Math.ceil(peak / 3));
  const max = tickStep * 3;
  const labelStep = Math.max(1, Math.ceil((points.length - 1) / Math.max(1, Math.floor((width - padding * 2) / 65))));
  const x = (index: number) => points.length === 1 ? width / 2 : padding + index * ((width - padding * 2) / (points.length - 1));
  const y = (value: number) => height - padding - value / max * (height - padding * 2);
  const line = (key: "login_count" | "module_launches") => points.map((point, index) => `${x(index)},${y(point[key])}`).join(" ");
  return (
    <div className="ops-chart-wrap" ref={chartRef}>
      <div className="ops-chart-legend"><span className="login">成功登录次数</span><span className="launch">模块启动次数</span></div>
      <svg className="ops-trend" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="成功登录与模块启动趋势，点击数据点查看当日明细">
        {[0, 1, 2, 3].map((tick) => <g key={tick}>
          <line x1={padding} y1={y(tick * tickStep)} x2={width - padding} y2={y(tick * tickStep)} className="axis" />
          <text x={padding - 8} y={y(tick * tickStep) + 4} textAnchor="end">{formatMetric(tick * tickStep)}</text>
        </g>)}
        <polyline points={line("login_count")} className="trend-line login" />
        <polyline points={line("module_launches")} className="trend-line launch" />
        {points.map((point, index) => {
          const params = new URLSearchParams({ days: String(days), date: point.date });
          if (module) params.set("module", module);
          const href = `${detailPath}?${params.toString()}`;
          const pointX = x(index);
          const loginY = y(point.login_count);
          const launchY = y(point.module_launches);
          const hitTop = Math.min(loginY, launchY) - 10;
          const hitHeight = Math.abs(loginY - launchY) + 20;
          return (
            <g key={point.date}>
              {(index % labelStep === 0 && index < points.length - Math.ceil(labelStep / 2) || index === points.length - 1) && <text x={pointX} y={height - 8} textAnchor="middle">{point.date.slice(5)}</text>}
              <g
                role="link"
                tabIndex={0}
                data-href={href}
                onClick={() => navigateOps(href)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    navigateOps(href);
                  }
                }}
                aria-label={`${point.date}：登录 ${point.login_count} 次，模块启动 ${point.module_launches} 次`}
              >
                <rect className="trend-hit-target" x={pointX - 12} y={hitTop} width="24" height={hitHeight} aria-hidden="true" />
                <title>{point.date}：登录 {point.login_count} 次，模块启动 {point.module_launches} 次</title>
                <circle cx={pointX} cy={loginY} r="3" className="trend-point login" />
                <circle cx={pointX} cy={launchY} r="3" className="trend-point launch" />
              </g>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function Drawer({ title, open, opener, onClose, children }: {
  title: string;
  open: boolean;
  opener: HTMLElement | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
      if (event.key === "Tab" && drawerRef.current) {
        const focusable = Array.from(drawerRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ));
        const first = focusable[0];
        const last = focusable.at(-1);
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      opener?.focus();
    };
  }, [open, opener]);
  if (!open) return null;
  return (
    <div className="ops-drawer-layer">
      <button type="button" className="ops-drawer-backdrop" aria-label="关闭详情" onClick={onClose} />
      <aside ref={drawerRef} className="ops-drawer" role="dialog" aria-modal="true" aria-labelledby="ops-drawer-title">
        <header>
          <h2 id="ops-drawer-title">{title}</h2>
          <button ref={closeRef} type="button" className="ops-icon-button" onClick={onClose} aria-label="关闭详情">×</button>
        </header>
        <div className="ops-drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function SearchForm({ initialValue, label, onSubmit }: {
  initialValue: string;
  label: string;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);
  useEffect(() => setValue(initialValue), [initialValue]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(value.trim());
  };
  return (
    <form className="ops-search" role="search" onSubmit={submit}>
      <label className="ops-search-label" htmlFor="ops-search-input">{label}</label>
      <input id="ops-search-input" value={value} onChange={(event) => setValue(event.target.value)} placeholder={label} />
      <button className="button secondary compact" type="submit">搜索</button>
    </form>
  );
}

export function DefinitionList({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <p className="ops-inline-empty">接口未提供可展示信息。</p>;
  return (
    <dl className="ops-definition-grid">
      {entries.map(([key, value]) => (
        <div key={key}><dt>{definitionLabel(key)}</dt><dd>{displayValue(value)}</dd></div>
      ))}
    </dl>
  );
}

function definitionLabel(key: string): string {
  const labels: Record<string, string> = {
    login_users: "登录活跃人数",
    login_count: "成功登录次数",
    module_launches: "模块启动次数",
    module_filter: "模块筛选口径",
    timestamp: "演练时间",
    table_count: "表数量",
    all_tables_equal: "表数据一致",
    source_unchanged: "源数据未变化",
    scope_matches: "演练范围匹配",
  };
  return labels[key] ?? key;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(displayValue).join("、") || "未提供";
  return "已由后端安全隐藏";
}
