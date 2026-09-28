import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import {
  getOpportunity,
  getOpportunityOptions,
  getRefreshBatch,
  getRefreshOverview,
  getTenderSources,
  listOpportunities,
  requestRefresh,
  updateOpportunityState,
  type OpportunityUserState,
  type OpportunityDetail,
  type OpportunityFilters,
  type OpportunityListPayload,
  type OpportunityOptions,
  type OpportunitySummary,
  type RefreshBatch,
  type RefreshOverview,
  type SelectOption,
  type TenderSource,
} from "./tender-api";
import "./tender-opportunities.css";

const PAGE_SIZE = 20;
export const REFRESH_POLL_MS = 3000;
export const OVERVIEW_POLL_MS = 30000;
const filterKeys = ["industry", "notice_category", "region", "user_state", "participation"] as const;
const userStates = [{ value: "unread", label: "未看" }, { value: "read", label: "已看" }, { value: "favorite", label: "重点关注" }, { value: "irrelevant", label: "不相关" }];
const participationOptions = [{ value: "open", label: "可参与" }, { value: "unknown", label: "截止待核实" }, { value: "expired", label: "已截止/出结果" }];
const legacyFilterKeys = ["q", "source", "status", "purchaser", "need", "notice_type", "publish_period", "deadline_period", "budget_band", "classification_status", "ordering", "publish_from", "publish_to", "deadline_from", "deadline_to", "budget_min", "budget_max"];
const industryOptions: SelectOption[] = [
  { value: "coal", label: "煤炭" }, { value: "water", label: "水利水电" },
  { value: "power", label: "电力与新能源" }, { value: "medical", label: "医疗卫生" },
  { value: "transport", label: "交通运输" }, { value: "petrochemical", label: "石油与化工" },
  { value: "municipal", label: "市政与公共设施" }, { value: "education", label: "教育与科研" },
  { value: "manufacturing", label: "工业制造" },
];
const noticeCategories: SelectOption[] = [
  { value: "procurement", label: "招标采购" }, { value: "change", label: "变更公告" },
  { value: "result", label: "结果公告" }, { value: "all", label: "全部公告" },
];
const defaultFilters: OpportunityFilters = { notice_category: "procurement" };
const terminalStates = new Set(["SUCCESS", "PARTIAL", "FAILED", "INTERRUPTED"]);
const statusLabels: Record<string, string> = { ACTIVE: "进行中", UPDATED: "有更新", AWARDED: "已中标", CLOSED: "已结束" };
const refreshLabels: Record<string, string> = { QUEUED: "等待执行", RUNNING: "正在采集", SUCCESS: "刷新完成", PARTIAL: "部分更新", FAILED: "刷新失败", BLOCKED: "暂不可用", WAITING_RETRY: "等待重试", SKIPPED: "已有任务正在执行", INTERRUPTED: "执行中断，等待人工确认" };
const refreshErrors: Record<string, string> = {
  blocked: "来源暂时无法访问", preflight_failed: "来源访问检查未通过", ingestion_disabled: "公告采集尚未启用",
  detail_failed: "部分公告详情读取失败", list_failed: "公告列表读取失败", execution_failed: "公告采集执行失败",
  recovery_required: "上次执行中断，等待人工确认", timeout: "来源响应超时", consumer_offline: "采集服务当前离线",
  coverage_partial: "本轮仅完成部分范围更新，其余公告尚待获取", source_blocked: "来源限制访问，本轮更新已停止",
};
const healthLabels: Record<string, string> = { ok: "来源可用", healthy: "来源可用", degraded: "部分更新", partial: "部分更新", error: "更新失败", failed: "更新失败", blocked: "暂不可用", unknown: "尚未验证", pending: "尚未采集", disabled: "尚未启用" };

type ListState = { kind: "loading" } | { kind: "ready"; data: OpportunityListPayload } | { kind: "error"; message: string };
type DetailState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: OpportunityDetail } | { kind: "error"; message: string };

function initialFilters(): OpportunityFilters {
  const params = new URLSearchParams(window.location.search);
  const filters: OpportunityFilters = { ...defaultFilters };
  const selected = new Set((params.get("industry") || "").split(","));
  const industries = industryOptions.filter(item => selected.has(item.value)).map(item => item.value);
  if (industries.length > 0 && industries.length < industryOptions.length) filters.industry = industries.join(",");
  const category = params.get("notice_category");
  if (noticeCategories.some(item => item.value === category)) filters.notice_category = category!;
  const region = params.get("region")?.trim();
  if (region) filters.region = region;
  if (userStates.some(item => item.value === params.get("user_state"))) filters.user_state = params.get("user_state")!;
  if (participationOptions.some(item => item.value === params.get("participation"))) filters.participation = params.get("participation")!;
  return filters;
}

function initialPage(): number {
  const value = Number(new URLSearchParams(window.location.search).get("page"));
  return Number.isInteger(value) && value > 0 ? value : 1;
}

function noticeId(): number | null {
  const value = Number(new URLSearchParams(window.location.search).get("notice"));
  return Number.isInteger(value) && value > 0 ? value : null;
}

function updateUrl(filters: OpportunityFilters, page: number, notice?: number | null, replace = false): void {
  // Another page can receive popstate before this component is unmounted.
  // Its URL must never inherit the tender board's default filters.
  if (!/^\/centers\/product\/opportunities\/?$/.test(window.location.pathname)) return;
  const params = new URLSearchParams(window.location.search);
  legacyFilterKeys.forEach(key => params.delete(key));
  filterKeys.forEach(key => {
    const value = filters[key]?.trim();
    if (value) params.set(key, value); else params.delete(key);
  });
  if (page > 1) params.set("page", String(page)); else params.delete("page");
  if (notice) params.set("notice", String(notice)); else if (notice === null) params.delete("notice");
  const query = params.toString();
  window.history[replace ? "replaceState" : "pushState"]({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? readable(error.message, fallback) : fallback;
}

function readable(value: string | undefined, fallback: string): string {
  return value && /[\u3400-\u9fff]/.test(value) ? value : fallback;
}

function failureReason(code?: string, detail?: string): string {
  return readable(detail, (code && refreshErrors[code]) || "来源更新失败，请稍后重试或查看来源状态。");
}

function isAccessError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

function formatDateTime(value?: string | null, dateOnly = false): string {
  if (!value) return "—";
  if (dateOnly && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", dateOnly ? { year: "numeric", month: "2-digit", day: "2-digit" } : {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function formatBudget(item: OpportunitySummary): string {
  const value = item.budget?.amount_yuan || item.budget?.cap_yuan;
  if (!value) return item.budget?.raw?.trim() || "—";
  const [integer, fraction = ""] = String(value).split(".");
  const decimals = fraction.replace(/0+$/, "");
  return `¥${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${decimals ? `.${decimals}` : ""}`;
}

function sourceName(source: OpportunitySummary["source"]): string {
  return source?.name || source?.code || "—";
}

function publishedAt(item: { publish_precision: string; publish_date: string | null; publish_at: string | null }): string {
  return item.publish_precision === "date" ? formatDateTime(item.publish_date, true) : formatDateTime(item.publish_at);
}

function statusLabel(item: OpportunitySummary): string {
  return item.participation_label || (item.participation_status ? { open: "可参与", expired: "已截止", unknown: "截止待核实", awarded: "已出结果" }[item.participation_status] : "") || item.status_label || statusLabels[item.status] || item.status || "状态未提供";
}

function refreshLabel(value?: string): string {
  return value ? refreshLabels[value] || readable(value, "状态暂不可用") : "状态暂不可用";
}

function batchDisplayState(batch: RefreshBatch): string {
  return refreshLabels[batch.display_state] ? batch.display_state : batch.stored_state;
}

function IndustrySelect({ value, entries, onChange }: { value?: string; entries: SelectOption[]; onChange: (value: string) => void }) {
  const dropdown = useRef<HTMLDetailsElement>(null);
  const selected = new Set((value || "").split(",").filter(Boolean));
  const label = selected.size === 0 ? "全部行业" : entries.filter(item => selected.has(item.value)).map(item => item.label).join("、");
  const toggle = (code: string) => {
    const next = new Set(selected);
    if (next.has(code)) next.delete(code); else next.add(code);
    const codes = entries.filter(item => next.has(item.value)).map(item => item.value);
    onChange(codes.length === entries.length ? "" : codes.join(","));
  };
  return <div className="tender-industry"><span id="tender-industry-label">行业（可多选）</span>
    <details ref={dropdown} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); dropdown.current?.removeAttribute("open"); dropdown.current?.querySelector("summary")?.focus(); }
    }} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) dropdown.current?.removeAttribute("open");
    }}>
      <summary role="button" aria-labelledby="tender-industry-label tender-industry-selection"><span id="tender-industry-selection" title={label}>{label}</span></summary>
      <fieldset className="tender-industry-menu"><legend className="sr-only">选择行业</legend>
        <label><input type="checkbox" checked={selected.size === 0} onChange={() => onChange("")} />全部行业</label>
        {entries.map(item => <label key={item.value}><input type="checkbox" checked={selected.has(item.value)} onChange={() => toggle(item.value)} />{item.label}</label>)}
      </fieldset>
    </details>
  </div>;
}

function ProjectTags({ item }: { item: OpportunitySummary }) {
  return <div className="tender-tags">{item.notice_category && <span className="tender-tag">{noticeCategories.find(category => category.value === item.notice_category)?.label || "公告类型待核实"}</span>}
    {item.relevance_tier && <span className={`tender-tag relevance-${item.relevance_tier}`}>{item.relevance_label || (item.relevance_tier === "core" ? "核心商机" : "相关商机")}</span>}
    {item.user_state && <span className="tender-tag">{item.user_state.is_read ? "已看" : "未看"}</span>}
    {item.latest_batch_new && <span className="tender-tag tender-new">本轮新增</span>}
    {item.notice_count !== undefined && item.notice_count > 1 && <span className="tender-tag">{item.notice_count} 份公告</span>}
    {item.industry_label && <span className="tender-tag industry-tag">{item.industry_label}</span>}
    {item.digital_tags?.map(tag => <span className="tender-tag" key={tag}>{tag}</span>)}
    {item.classification_status === "review" && <span className="tender-tag">待核实分类</span>}
  </div>;
}

function attachmentTextStatus(status: string): string {
  return ({ extracted: "正文已提取", available: "附件可打开，正文未提取", success: "正文已提取", pending: "正文待提取", unverified: "附件正文待核实", failed: "正文提取失败" } as Record<string, string>)[status] || "正文未提取";
}

function safeExternalUrl(value?: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? value : null;
  } catch { return null; }
}

const evidenceLabels: Record<string, string> = {
  project_name: "项目名称", project_code: "项目编号", region: "地区", purchaser: "采购单位", agency: "代理机构",
  budget: "预算", budget_raw: "预算", budget_amount_yuan: "预算金额", budget_cap: "预算上限", budget_cap_yuan: "预算上限",
  bid_deadline: "投标截止", bid_open_at: "开标时间", signup_time: "报名时间", signup_time_text: "报名时间", procurement_scope: "采购范围",
  publish_at: "发布时间", publish_date: "发布日期", notice_type: "公告类型", procurement_method: "采购方式",
  contact_person: "联系人", contact_phone: "联系电话",
};

function ScopeAndEvidence({ item }: { item: OpportunityDetail }) {
  const entries = Object.entries(item.field_evidence || {}).filter(([field]) => evidenceLabels[field]);
  const warnings = [...new Set((item.extraction_warnings || []).map(value => readable(value, "部分字段尚待核实，请以原始公告为准。")))];
  if (!item.procurement_scope && !item.signup_time_text && !entries.length && !warnings.length) return null;
  return <section className="pd-panel tender-evidence">
    <details open><summary>采购范围与报名信息</summary>
      {item.procurement_scope && <div><h3>采购范围</h3><p className="tender-scope-text">{item.procurement_scope}</p></div>}
      {item.signup_time_text && <div><h3>报名时间</h3><p>{item.signup_time_text}</p></div>}
      {warnings.length > 0 && <ul className="tender-unknown" aria-label="待核实说明">{warnings.map(value => <li key={value}>{value}</li>)}</ul>}
    </details>
    {entries.length > 0 && <details><summary>查看字段原文与待核实说明</summary><ul className="tender-record-list">{entries.map(([field, evidence]) => {
      const needsCheck = evidence.conflict || [evidence.status, evidence.verification_status].some(value => value && /unverified|review|conflict|unknown|missing/.test(value));
      return <li key={field}><div><strong>{evidenceLabels[field]}{needsCheck ? " · 待核实" : " · 原文依据"}</strong>
        {evidence.raw ? <p className="tender-scope-text">{evidence.raw}</p> : <small>未提供原文依据</small>}
        {evidence.conflict ? <small>存在冲突，请核对原始公告。</small> : null}</div>
        {safeExternalUrl(evidence.source_url) && <a href={evidence.source_url} target="_blank" rel="noopener noreferrer">核对原始公告 ↗</a>}</li>;
    })}</ul></details>}
  </section>;
}

function Detail({ state, onBack, onRetry }: { state: DetailState; onBack: () => void; onRetry: () => void }) {
  if (state.kind === "idle") return null;
  if (state.kind === "loading") return <div className="tender-state" role="status">正在加载商机详情…</div>;
  if (state.kind === "error") return <div className="tender-state" role="alert"><strong>商机详情加载失败</strong><p>{state.message}</p><div className="pd-actions"><button className="button secondary" onClick={onBack}>返回列表</button><button className="button primary" onClick={onRetry}>重试</button></div></div>;
  const item = state.data;
  return <div className="tender-detail">
    <button className="tender-back" type="button" onClick={onBack}>← 返回商机列表</button>
    <section className="pd-panel">
      <div className="tender-detail-head"><div><span className={`tender-badge status-${item.status.toLowerCase()}`}>{statusLabel(item)}</span><h2>{item.project_name || "项目名称未提供"}</h2><p>{item.project_code ? `项目编号：${item.project_code}` : "项目编号未提供"}</p></div>{item.original_url && <a className="button primary" href={item.original_url} target="_blank" rel="noopener noreferrer">查看原始公告 ↗</a>}</div>
      <ProjectTags item={item} />
      <dl className="tender-detail-grid">
        <div><dt>采购单位</dt><dd>{item.purchaser || "—"}</dd></div>
        <div><dt>来源</dt><dd>{sourceName(item.source)}</dd></div>
        <div><dt>地区</dt><dd>{item.region || "—"}</dd></div>
        <div><dt>预算</dt><dd>{formatBudget(item)}</dd></div>
        <div><dt>发布时间</dt><dd>{publishedAt(item)}</dd></div>
        <div><dt>投标截止</dt><dd>{formatDateTime(item.bid_deadline)}</dd></div>
        <div><dt>当前版本</dt><dd>v{item.current_version}</dd></div>
        <div><dt>首次发现</dt><dd>{formatDateTime(item.first_seen_at)}</dd></div>
        {item.notice_type && <div><dt>公告类型</dt><dd>{item.notice_type}</dd></div>}
        {item.agency && <div><dt>代理机构</dt><dd>{item.agency}</dd></div>}
        {item.procurement_method && <div><dt>采购方式</dt><dd>{item.procurement_method}</dd></div>}
        {item.bid_open_at && <div><dt>开标时间</dt><dd>{formatDateTime(item.bid_open_at)}</dd></div>}
      </dl>
      {item.unknown_fields && item.unknown_fields.length > 0 && <p className="tender-unknown">公告未明确：{[...new Set(item.unknown_fields.map(field => evidenceLabels[field] || readable(field, "其他信息")))].join("、")}。页面保持为空，不作推测。</p>}
    </section>
    <ScopeAndEvidence item={item} />
    {item.notices && item.notices.length > 0 && <section className="pd-panel"><div className="pd-panel-heading"><h3>项目全部公告</h3></div><ul className="tender-record-list">{item.notices.map(notice => <li key={notice.id}><div><strong>{notice.title || item.project_name}</strong><small>{sourceName(notice.source)} · {notice.notice_type || "公告"} · {publishedAt(notice)}</small></div><a href={notice.original_url} target="_blank" rel="noopener noreferrer">查看原始公告 ↗</a></li>)}</ul></section>}
    {item.attachments && item.attachments.length > 0 && <section className="pd-panel"><div className="pd-panel-heading"><h3>官方公开附件</h3></div><ul className="tender-record-list">{item.attachments.map((attachment, index) => <li key={`${attachment.url}:${index}`}><div><strong>{attachment.name || "公告附件"}</strong><small>{attachmentTextStatus(attachment.text_status)}</small></div>{safeExternalUrl(attachment.url) ? <a href={attachment.url} target="_blank" rel="noopener noreferrer">查看官方附件 ↗</a> : <span>附件链接不可用</span>}</li>)}</ul></section>}
    {item.versions && item.versions.length > 0 && <section className="pd-panel"><div className="pd-panel-heading"><h3>公告版本</h3></div><ul className="tender-record-list">{item.versions.map(version => <li key={version.version}><div><strong>v{version.version}{version.is_current ? " · 当前版本" : ""}</strong><small>{formatDateTime(version.created_at)}</small></div><span>{version.change_summary.length > 0 ? version.change_summary.join("、") : "未提供变化摘要"}</span></li>)}</ul></section>}
  </div>;
}

function StateActions({ item, pending, onChange, includeRead = false }: { item: OpportunitySummary; pending: boolean; onChange: (patch: Partial<OpportunityUserState>) => void; includeRead?: boolean }) {
  return <div className="tender-state-actions" aria-label={`${item.project_name}的标记`}>
    {includeRead && !item.user_state?.is_read && <button type="button" disabled={pending} onClick={() => onChange({ is_read: true })}>标为已看</button>}
    <button type="button" disabled={pending} aria-pressed={!!item.user_state?.is_favorite} onClick={() => onChange({ is_favorite: !item.user_state?.is_favorite })}>{item.user_state?.is_favorite ? "取消关注" : "重点关注"}</button>
    <button type="button" disabled={pending} onClick={() => onChange({ is_irrelevant: !item.user_state?.is_irrelevant })}>{item.user_state?.is_irrelevant ? "恢复相关" : "不相关"}</button>
  </div>;
}

function Sources({ sources }: { sources: TenderSource[] }) {
  return <section className="pd-panel tender-sources" aria-labelledby="tender-sources-title">
    <div className="pd-panel-heading"><h3 id="tender-sources-title">来源状态</h3><span>{sources.length} 个来源</span></div>
    {sources.length === 0 ? <p className="tender-muted">暂无来源状态。</p> : <div className="tender-source-grid">{sources.map(source => <article key={source.code}>
      <div><strong>{source.name || source.code}</strong><span className={`tender-health health-${source.health_state}`}>{source.latest_run?.state === "PARTIAL" ? "部分更新" : healthLabels[source.health_state] || readable(source.health_label, "状态待确认")}</span></div>
      <p>{source.latest_run?.detail || source.latest_run?.error_code ? failureReason(source.latest_run.error_code, source.latest_run.detail || source.latest_run.error_detail) : readable(source.health_detail, "来源未提供补充说明。")}</p>
      <small>最近尝试：{source.latest_run?.started_at ? formatDateTime(source.latest_run.started_at) : "暂无记录"}</small>
      <small>最近成功：{source.last_success_at ? formatDateTime(source.last_success_at) : "尚未成功更新"}</small>
      {source.last_failure_at && <small>最近失败：{formatDateTime(source.last_failure_at)}</small>}
      {source.latest_run && <small>最近一轮：{refreshLabel(source.latest_run.state)}{typeof source.latest_run.statistics?.new_notices === "number" ? ` · 新增 ${source.latest_run.statistics.new_notices} 条` : ""}{typeof source.latest_run.statistics?.new_versions === "number" ? ` · 更新 ${source.latest_run.statistics.new_versions} 版` : ""}</small>}
    </article>)}</div>}
  </section>;
}

export default function TenderOpportunities() {
  const [filters, setFilters] = useState<OpportunityFilters>(initialFilters);
  const [page, setPage] = useState(initialPage);
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [listUpdating, setListUpdating] = useState(false);
  const [options, setOptions] = useState<OpportunityOptions>({ regions: [], industries: industryOptions, noticeCategories });
  const [sources, setSources] = useState<TenderSource[]>([]);
  const [overview, setOverview] = useState<RefreshOverview | null>(null);
  const [batch, setBatch] = useState<RefreshBatch | null>(null);
  const [detailId, setDetailId] = useState<number | null>(noticeId);
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [detailReload, setDetailReload] = useState(0);
  const [metadataMessage, setMetadataMessage] = useState("");
  const [stateMessage, setStateMessage] = useState("");
  const [pendingStates, setPendingStates] = useState<Set<number>>(new Set());
  const stateRequests = useRef(new Map<number, Promise<OpportunityUserState>>());
  const readRequests = useRef(new Map<number, Promise<OpportunityUserState>>());
  const [refreshMessage, setRefreshMessage] = useState("");
  const [staleMessage, setStaleMessage] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [reload, setReload] = useState(0);
  const [monitorReload, setMonitorReload] = useState(0);
  const currentBatch = useRef<RefreshBatch | null>(null);
  const observedTerminals = useRef(new Set<string>());

  useEffect(() => {
    updateUrl(initialFilters(), initialPage(), noticeId(), true);
    const sync = () => {
      if (!/^\/centers\/product\/opportunities\/?$/.test(window.location.pathname)) return;
      const nextFilters = initialFilters(), nextPage = initialPage(), nextNotice = noticeId();
      setFilters(nextFilters); setPage(nextPage); setDetailId(nextNotice);
      updateUrl(nextFilters, nextPage, nextNotice, true);
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  const revoke = useCallback(() => {
    setAccessRevoked(true);
    setList({ kind: "error", message: "商机访问权限已变化，当前内容已隐藏。" });
    setDetail({ kind: "idle" });
    setSources([]);
    setBatch(null);
    currentBatch.current = null;
  }, []);

  const applyUserState = useCallback((id: number, userState: OpportunityUserState) => {
    setList(current => current.kind === "ready" ? { ...current, data: { ...current.data,
      items: current.data.items.map(item => item.id === id ? { ...item, user_state: userState } : item) } } : current);
    setDetail(current => current.kind === "ready" && current.data.id === id
      ? { ...current, data: { ...current.data, user_state: userState } } : current);
  }, []);

  const sendUserState = useCallback((id: number, patch: Partial<OpportunityUserState>) => {
    // Serialize partial writes for one project so an older full response cannot
    // overwrite a newer mark. A read continues if its detail view is closed.
    const previous = stateRequests.current.get(id);
    const request = (previous ? previous.catch(() => undefined) : Promise.resolve())
      .then(() => updateOpportunityState(id, patch));
    stateRequests.current.set(id, request); setPendingStates(new Set(stateRequests.current.keys()));
    const cleanup = () => {
      if (stateRequests.current.get(id) === request) {
        stateRequests.current.delete(id); setPendingStates(new Set(stateRequests.current.keys()));
      }
    };
    void request.then(cleanup, cleanup);
    return request;
  }, []);

  const saveUserState = useCallback(async (id: number, patch: Partial<OpportunityUserState>) => {
    if (stateRequests.current.has(id)) return;
    setStateMessage("");
    try {
      const next = await sendUserState(id, patch);
      applyUserState(id, next);
      setReload(value => value + 1);
    } catch (error) {
      if (isAccessError(error)) return revoke();
      setStateMessage(message(error, "标记保存失败，请重试。"));
    }
  }, [applyUserState, revoke, sendUserState]);

  const receiveBatch = useCallback((next: RefreshBatch) => {
    currentBatch.current = next;
    setBatch(next);
    setRefreshMessage("");
    if (!terminalStates.has(batchDisplayState(next))) return false;
    const key = `${next.id}:${batchDisplayState(next)}`;
    if (observedTerminals.current.has(key)) return false;
    observedTerminals.current.add(key);
    setReload(value => value + 1);
    return true;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setListUpdating(true);
    setList(current => current.kind === "ready" ? current : { kind: "loading" });
    void listOpportunities({ ...filters, page, page_size: PAGE_SIZE }, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      const correctedPage = data.page !== page ? data.page : page > 1 && data.items.length === 0 ? 1 : page;
      if (correctedPage !== page) {
        setPage(correctedPage);
        updateUrl(filters, correctedPage, undefined, true);
        // Support a server that still returns an empty stale page while it is
        // being updated; fetch page one without flashing a false empty state.
        if (data.page !== correctedPage) return;
      }
      setList({ kind: "ready", data });
      setStaleMessage("");
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (isAccessError(error)) return revoke();
      setList(current => current.kind === "ready" ? current : { kind: "error", message: message(error, "商机列表加载失败。") });
      setStaleMessage("商机列表暂时无法更新，当前内容可能过时。");
    }).finally(() => {
      if (!controller.signal.aborted) setListUpdating(false);
    });
    return () => controller.abort();
  }, [filters, page, reload, revoke]);

  useEffect(() => {
    if (accessRevoked) return;
    const controller = new AbortController();
    void getOpportunityOptions(controller.signal).then(value => {
      if (!controller.signal.aborted) setOptions(value);
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (isAccessError(error)) return revoke();
      setMetadataMessage("部分筛选选项暂不可用。");
    });
    return () => controller.abort();
  }, [accessRevoked, revoke]);

  useEffect(() => {
    if (accessRevoked) return;
    let stopped = false;
    let timer = 0;
    let controller: AbortController | null = null;
    let nextOverviewAt = 0;
    let pausedBatchId = "";
    const isHidden = () => document.visibilityState === "hidden";
    const isActive = () => currentBatch.current && ["QUEUED", "RUNNING"].includes(batchDisplayState(currentBatch.current));
    const poll = async () => {
      if (stopped || isHidden()) return;
      const request = new AbortController();
      controller = request;
      let denied = false;
      try {
        if (Date.now() >= nextOverviewAt) {
          const results = await Promise.allSettled([getRefreshOverview(request.signal), getTenderSources(request.signal)]);
          if (stopped || request.signal.aborted) return;
          if (results.some(result => result.status === "rejected" && isAccessError(result.reason))) {
            denied = true; revoke(); return;
          }
          const [refreshResult, sourceResult] = results;
          if (refreshResult.status === "fulfilled") {
            setOverview(refreshResult.value);
            if (refreshResult.value.batch) receiveBatch(refreshResult.value.batch);
          }
          if (sourceResult.status === "fulfilled") setSources(sourceResult.value);
          setMetadataMessage(results.some(result => result.status === "rejected") ? "来源或自动更新状态暂不可用，现有内容可能过时。" : "");
          nextOverviewAt = Date.now() + OVERVIEW_POLL_MS;
        } else if (isActive() && currentBatch.current?.id !== pausedBatchId) {
          const next = await getRefreshBatch(currentBatch.current!.id, request.signal);
          if (stopped || request.signal.aborted) return;
          if (receiveBatch(next)) nextOverviewAt = 0;
        }
      } catch (error) {
        if (stopped || request.signal.aborted) return;
        if (isAccessError(error)) { denied = true; revoke(); return; }
        pausedBatchId = currentBatch.current?.id || "";
        setStaleMessage("刷新状态查询失败，现有商机可能过时；稍后将重新检查来源状态。");
      } finally {
        if (!stopped && !request.signal.aborted && !denied && !isHidden()) {
          const delay = isActive() && currentBatch.current?.id !== pausedBatchId ? REFRESH_POLL_MS : OVERVIEW_POLL_MS;
          timer = window.setTimeout(() => void poll(), Math.max(0, Math.min(delay, nextOverviewAt - Date.now())));
        }
      }
    };
    const visibilityChanged = () => {
      window.clearTimeout(timer);
      controller?.abort();
      if (!isHidden()) { nextOverviewAt = 0; pausedBatchId = ""; void poll(); }
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    void poll();
    return () => { stopped = true; window.clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visibilityChanged); };
  }, [accessRevoked, monitorReload, receiveBatch, revoke]);

  useEffect(() => {
    if (!detailId) { setDetail({ kind: "idle" }); return; }
    const controller = new AbortController();
    setDetail({ kind: "loading" });
    void getOpportunity(detailId, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setDetail({ kind: "ready", data });
      setStateMessage("");
      if (!data.user_state?.is_read) {
        let request = readRequests.current.get(data.id);
        if (!request) {
          request = sendUserState(data.id, { is_read: true });
          readRequests.current.set(data.id, request);
          const cleanup = () => { if (readRequests.current.get(data.id) === request) readRequests.current.delete(data.id); };
          void request.then(cleanup, cleanup);
        }
        void request.then(next => {
          if (controller.signal.aborted) return;
          applyUserState(data.id, next);
          setReload(value => value + 1);
        }).catch(error => {
          if (controller.signal.aborted) return;
          if (isAccessError(error)) return revoke();
          setStateMessage(message(error, "已看标记保存失败，可点击“标为已看”重试。"));
        });
      }
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (isAccessError(error)) return revoke();
      setDetail({ kind: "error", message: message(error, "商机详情加载失败。") });
    });
    return () => controller.abort();
  }, [detailId, detailReload, revoke, applyUserState, sendUserState]);

  const changeFilter = (key: typeof filterKeys[number], value: string) => {
    const next = { ...filters, [key]: value };
    setPage(1);
    setFilters(next);
    setDetailId(null);
    updateUrl(next, 1, null);
  };

  const reset = () => {
    setFilters({ ...defaultFilters }); setPage(1); setDetailId(null); updateUrl(defaultFilters, 1, null);
  };

  const openDetail = (id: number) => {
    updateUrl(filters, page, id);
    setDetailId(id);
  };

  const closeDetail = () => {
    updateUrl(filters, page, null);
    setDetailId(null);
  };

  const changePage = (next: number) => {
    setPage(next);
    updateUrl(filters, next, null);
  };

  const startRefresh = async () => {
    setRefreshing(true); setRefreshMessage(""); setStaleMessage("");
    try {
      const next = await requestRefresh();
      receiveBatch(next);
      setMonitorReload(value => value + 1);
    } catch (error) {
      if (isAccessError(error)) return revoke();
      if (error instanceof ApiError && error.status === 409) {
        try {
          const current = await getRefreshOverview();
          setOverview(current);
          if (current.batch) receiveBatch(current.batch);
          else setRefreshMessage(message(error, "已有更新任务正在执行。"));
          setMonitorReload(value => value + 1);
        } catch (followup) {
          if (isAccessError(followup)) return revoke();
          setRefreshMessage(message(followup, "无法恢复正在执行的刷新批次。"));
        }
      } else setRefreshMessage(message(error, "刷新请求失败。"));
    } finally {
      setRefreshing(false);
    }
  };

  if (accessRevoked) return <div className="tender-state" role="alert"><strong>商机访问权限已变化</strong><p>当前内容已隐藏，请返回工作台重新确认授权。</p></div>;

  if (detailId) return <div className="pd-workspace tender-workspace">{stateMessage && <p className="notice error" role="alert">{stateMessage}</p>}{detail.kind === "ready" && <StateActions item={detail.data} pending={pendingStates.has(detailId)} onChange={patch => void saveUserState(detailId, patch)} includeRead />}<Detail state={detail} onBack={closeDetail} onRetry={() => setDetailReload(value => value + 1)} /></div>;

  const active = batch && ["QUEUED", "RUNNING"].includes(batchDisplayState(batch));
  const interrupted = batch && batchDisplayState(batch) === "INTERRUPTED";
  const industries = industryOptions.map(item => options.industries.find(option => option.value === item.value) || item);
  const categories = noticeCategories.map(item => options.noticeCategories.find(option => option.value === item.value) || item);
  const regions = options.regions.some(item => item.value === filters.region) || !filters.region
    ? options.regions : [{ value: filters.region, label: filters.region }, ...options.regions];
  const stats = list.kind === "ready" ? list.data.stats : undefined;
  return <div className="pd-workspace tender-workspace">
    <section className="tender-hero">
      <div><h2>全国商机看板</h2><p>信息化 · 数字化 · 智能化公开商机</p></div>
      <button className="button primary" type="button" onClick={() => void startRefresh()} disabled={refreshing || Boolean(active) || overview?.available === false}>{refreshing ? "提交中…" : interrupted ? "执行中断" : active ? "刷新进行中" : "刷新公告"}</button>
    </section>

    {overview?.available === false && <div className="notice info" role="note">刷新当前不可用：{readable(overview.unavailable_reason, "暂未开放手动刷新。")}</div>}
    {metadataMessage && <div className="notice info" role="status">{metadataMessage}</div>}
    {staleMessage && <div className="notice error" role="alert">{staleMessage}</div>}


    <section className="pd-panel tender-filters" aria-label="商机筛选">
      <IndustrySelect value={filters.industry} entries={industries} onChange={value => changeFilter("industry", value)} />
      <label>公告类型<select value={filters.notice_category} onChange={event => changeFilter("notice_category", event.target.value)}>{categories.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <label>地区<select value={filters.region || ""} onChange={event => changeFilter("region", event.target.value)}><option value="">全国</option>{regions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <button className="button secondary" type="button" onClick={reset}>恢复默认</button>
    </section>

    {listUpdating && list.kind === "ready" && <p className="tender-muted" role="status">正在更新筛选结果，统计与列表暂为上次结果。</p>}
    <div className="tender-personal-filters"><label>我的标记<select value={filters.user_state || ""} onChange={event => changeFilter("user_state", event.target.value)}><option value="">全部（隐藏不相关）</option>{userStates.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>参与状态<select value={filters.participation || ""} onChange={event => changeFilter("participation", event.target.value)}><option value="">全部状态</option>{participationOptions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><span>榆林优先 · 优先可参与与核心商机</span></div>
    {stateMessage && <p className="notice error" role="alert">{stateMessage}</p>}


    <section className="pd-panel tender-list" aria-labelledby="tender-list-title" aria-busy={listUpdating}>
      <div className="pd-panel-heading"><h3 id="tender-list-title">项目商机列表</h3>{list.kind === "ready" && <span>共 {list.data.total} 条 · 最近更新：{list.data.last_updated_at ? formatDateTime(list.data.last_updated_at) : "尚未成功更新"}</span>}</div>
    <section className="tender-stats" aria-label="当前筛选统计" aria-busy={listUpdating}><div><span>符合条件</span><strong>{stats?.total ?? "—"}</strong></div><div><span>今日新增</span><strong>{stats?.today_new ?? "—"}</strong></div><div><span>本轮新增</span><strong>{stats?.latest_batch_new ?? "—"}</strong></div><div><span>即将截止</span><strong>{stats?.closing_soon ?? "—"}</strong></div></section>
      {list.kind === "loading" && <div className="tender-state" role="status">正在加载商机…</div>}
      {list.kind === "error" && <div className="tender-state" role="alert"><strong>商机列表加载失败</strong><p>{list.message}</p><button className="button secondary" onClick={() => setReload(value => value + 1)}>重试</button></div>}
      {list.kind === "ready" && list.data.items.length === 0 && <div className="tender-state"><strong>暂无符合条件的商机</strong><p>已接入来源暂未提供符合当前条件的项目。可调整筛选，并查看下方来源状态。</p></div>}
      {list.kind === "ready" && list.data.items.length > 0 && <><div className="tender-table-wrap"><table><caption className="sr-only">商机列表</caption><thead><tr><th>项目名称</th><th>地区</th><th>采购单位</th><th>预算</th><th>发布时间</th><th>截止时间</th><th>参与状态</th><th>来源</th><th>我的标记</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.id}><td><button className="tender-title" type="button" onClick={() => openDetail(item.id)}>{item.project_name || "项目名称未提供"}</button>{item.project_code && <small>编号：{item.project_code}</small>}<ProjectTags item={item} /></td><td>{item.region || "—"}</td><td><span className="tender-purchaser" title={item.purchaser || undefined}>{item.purchaser || "—"}</span></td><td>{formatBudget(item)}</td><td>{publishedAt(item)}</td><td>{formatDateTime(item.bid_deadline)}</td><td><span className={`tender-badge status-${item.status.toLowerCase()}`}>{statusLabel(item)}</span></td><td>{sourceName(item.source)}</td><td><StateActions item={item} pending={pendingStates.has(item.id)} onChange={patch => void saveUserState(item.id, patch)}/></td></tr>)}</tbody></table></div><div className="tender-pager"><span>第 {list.data.page} 页</span><div className="pd-actions"><button className="button secondary" type="button" disabled={page <= 1} onClick={() => changePage(page - 1)}>上一页</button><button className="button secondary" type="button" disabled={!list.data.has_more} onClick={() => changePage(page + 1)}>下一页</button></div></div></>}
    </section>
    {((batch && batch.trigger !== "scheduled") || refreshMessage) && <section className="pd-panel tender-refresh" aria-live="polite"><div><strong>刷新状态</strong><span className={`tender-badge batch-${(batch ? batchDisplayState(batch) : "unknown").toLowerCase()}`}>{refreshLabel(refreshMessage || (batch ? batchDisplayState(batch) : undefined))}</span></div>{batch && terminalStates.has(batchDisplayState(batch)) && Object.entries(batch.results).length > 0 && <ul>{Object.entries(batch.results).map(([code, result]) => <li key={code}><strong>{sources.find(source => source.code === code)?.name || "公告来源"}</strong><span>{refreshLabel(result.display_state || result.state)}{typeof result.new_notices === "number" ? ` · 新增 ${result.new_notices} 条` : ""}{typeof result.new_versions === "number" && result.new_versions > 0 ? ` · 更新 ${result.new_versions} 版` : ""}{result.detail || result.error_code || result.error_detail ? ` · ${failureReason(result.error_code, result.detail || result.error_detail)}` : ""}</span></li>)}</ul>}</section>}
    <p className="tender-coverage">公告来自已接入的公开来源，覆盖范围以实际获取结果为准。</p>
    <Sources sources={sources}/>
  </div>;
}
