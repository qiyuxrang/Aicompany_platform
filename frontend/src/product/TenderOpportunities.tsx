import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api";
import {
  getOpportunity,
  getOpportunityOptions,
  getRefreshBatch,
  getRefreshOverview,
  getTenderSources,
  listOpportunities,
  requestRefresh,
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
const filterKeys = ["q", "region", "source", "status", "purchaser", "need", "notice_type", "publish_period", "deadline_period", "budget_band"] as const;
const terminalStates = new Set(["SUCCESS", "PARTIAL", "FAILED", "INTERRUPTED"]);
const statusLabels: Record<string, string> = { ACTIVE: "进行中", UPDATED: "有更新", AWARDED: "已中标", CLOSED: "已结束" };
const refreshLabels: Record<string, string> = { QUEUED: "等待执行", RUNNING: "正在采集", SUCCESS: "刷新完成", PARTIAL: "部分更新", FAILED: "刷新失败", INTERRUPTED: "执行中断，等待人工确认" };

type ListState = { kind: "loading" } | { kind: "ready"; data: OpportunityListPayload } | { kind: "error"; message: string };
type DetailState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: OpportunityDetail } | { kind: "error"; message: string };

function initialFilters(): OpportunityFilters {
  const params = new URLSearchParams(window.location.search);
  const filters: OpportunityFilters = {};
  filterKeys.forEach(key => { const value = params.get(key); if (value) filters[key] = value; });
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

function updateUrl(filters: OpportunityFilters, page: number, notice?: number | null): void {
  const params = new URLSearchParams(window.location.search);
  filterKeys.forEach(key => {
    const value = filters[key]?.trim();
    if (value) params.set(key, value); else params.delete(key);
  });
  if (page > 1) params.set("page", String(page)); else params.delete("page");
  if (notice) params.set("notice", String(notice)); else if (notice === null) params.delete("notice");
  const query = params.toString();
  window.history.pushState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
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

function publishedAt(item: OpportunitySummary): string {
  return item.publish_precision === "date" ? formatDateTime(item.publish_date, true) : formatDateTime(item.publish_at);
}

function statusLabel(item: OpportunitySummary): string {
  return item.status_label || statusLabels[item.status] || item.status || "状态未提供";
}

function refreshLabel(value?: string): string {
  return value ? refreshLabels[value] || value : "状态未提供";
}

function batchDisplayState(batch: RefreshBatch): string {
  return batch.display_state || batch.stored_state;
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
      {item.unknown_fields && item.unknown_fields.length > 0 && <p className="tender-unknown">公告未明确：{item.unknown_fields.join("、")}。页面保持为空，不作推测。</p>}
    </section>
    {item.notices && item.notices.length > 0 && <section className="pd-panel"><div className="pd-panel-heading"><h3>来源与原始公告</h3></div><ul className="tender-record-list">{item.notices.map(notice => <li key={notice.id}><div><strong>{notice.title || item.project_name}</strong><small>{sourceName(notice.source)} · {notice.notice_type || "公告"}</small></div><a href={notice.original_url} target="_blank" rel="noopener noreferrer">查看原始公告 ↗</a></li>)}</ul></section>}
    {item.versions && item.versions.length > 0 && <section className="pd-panel"><div className="pd-panel-heading"><h3>公告版本</h3></div><ul className="tender-record-list">{item.versions.map(version => <li key={version.version}><div><strong>v{version.version}{version.is_current ? " · 当前版本" : ""}</strong><small>{formatDateTime(version.created_at)}</small></div><span>{version.change_summary.length > 0 ? version.change_summary.join("、") : "未提供变化摘要"}</span></li>)}</ul></section>}
  </div>;
}

function Sources({ sources }: { sources: TenderSource[] }) {
  return <section className="pd-panel tender-sources" aria-labelledby="tender-sources-title">
    <div className="pd-panel-heading"><h3 id="tender-sources-title">来源状态</h3><span>{sources.length} 个来源</span></div>
    {sources.length === 0 ? <p className="tender-muted">暂无来源状态。</p> : <div className="tender-source-grid">{sources.map(source => <article key={source.code}>
      <div><strong>{source.name || source.code}</strong><span className={`tender-health health-${source.health_state}`}>{source.health_label || source.health_state}</span></div>
      <p>{source.health_detail || "来源未提供补充说明。"}</p>
      <small>最近成功：{formatDateTime(source.last_success_at)}</small>
      {source.last_failure_at && <small>最近失败：{formatDateTime(source.last_failure_at)}</small>}
    </article>)}</div>}
  </section>;
}

export default function TenderOpportunities() {
  const [draft, setDraft] = useState<OpportunityFilters>(initialFilters);
  const [filters, setFilters] = useState<OpportunityFilters>(initialFilters);
  const [page, setPage] = useState(initialPage);
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [options, setOptions] = useState<OpportunityOptions>({ regions: [], sources: [], statuses: [], needs: [], noticeTypes: [], publishPeriods: [], deadlinePeriods: [], budgetBands: [] });
  const [sources, setSources] = useState<TenderSource[]>([]);
  const [overview, setOverview] = useState<RefreshOverview | null>(null);
  const [batch, setBatch] = useState<RefreshBatch | null>(null);
  const [detailId, setDetailId] = useState<number | null>(noticeId);
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [metadataMessage, setMetadataMessage] = useState("");
  const [refreshMessage, setRefreshMessage] = useState("");
  const [staleMessage, setStaleMessage] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [reload, setReload] = useState(0);
  const [metadataReload, setMetadataReload] = useState(0);

  const revoke = useCallback(() => {
    setAccessRevoked(true);
    setList({ kind: "error", message: "商机访问权限已变化，当前内容已隐藏。" });
    setDetail({ kind: "idle" });
    setSources([]);
    setBatch(null);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setList(current => current.kind === "ready" ? current : { kind: "loading" });
    void listOpportunities({ ...filters, page, page_size: PAGE_SIZE }, controller.signal).then(data => {
      setList({ kind: "ready", data });
      setStaleMessage("");
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (isAccessError(error)) return revoke();
      setList(current => current.kind === "ready" ? current : { kind: "error", message: message(error, "商机列表加载失败。") });
      setStaleMessage("商机列表暂时无法更新，当前内容可能过时。");
    });
    return () => controller.abort();
  }, [filters, page, reload, revoke]);

  useEffect(() => {
    const controller = new AbortController();
    setMetadataMessage("");
    void Promise.allSettled([
      getOpportunityOptions(controller.signal),
      getTenderSources(controller.signal),
      getRefreshOverview(controller.signal),
    ]).then(results => {
      if (controller.signal.aborted) return;
      const rejected = results.find(result => result.status === "rejected" && isAccessError(result.reason));
      if (rejected) return revoke();
      const [optionResult, sourceResult, refreshResult] = results;
      if (optionResult.status === "fulfilled") setOptions(optionResult.value);
      if (sourceResult.status === "fulfilled") setSources(sourceResult.value);
      if (refreshResult.status === "fulfilled") {
        setOverview(refreshResult.value);
        if (refreshResult.value.batch) setBatch(refreshResult.value.batch);
      }
      if (results.some(result => result.status === "rejected")) setMetadataMessage("部分筛选、来源或刷新状态暂不可用。");
    });
    return () => controller.abort();
  }, [metadataReload, revoke]);

  const loadDetail = useCallback((id: number) => {
    setDetail({ kind: "loading" });
    void getOpportunity(id).then(data => setDetail({ kind: "ready", data })).catch(error => {
      if (isAccessError(error)) return revoke();
      setDetail({ kind: "error", message: message(error, "商机详情加载失败。") });
    });
  }, [revoke]);

  useEffect(() => {
    if (!detailId) { setDetail({ kind: "idle" }); return; }
    loadDetail(detailId);
  }, [detailId, loadDetail]);

  useEffect(() => {
    if (!batch || terminalStates.has(batchDisplayState(batch)) || !["QUEUED", "RUNNING"].includes(batch.stored_state)) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await getRefreshBatch(batch.id);
        if (stopped) return;
        setBatch(next);
        if (terminalStates.has(batchDisplayState(next))) {
          setRefreshMessage(batchDisplayState(next));
          setReload(value => value + 1);
          setMetadataReload(value => value + 1);
        } else timer = window.setTimeout(poll, REFRESH_POLL_MS);
      } catch (error) {
        if (stopped) return;
        if (isAccessError(error)) return revoke();
        setStaleMessage("刷新状态查询失败，已停止轮询；现有商机可能过时。");
      }
    };
    timer = window.setTimeout(poll, REFRESH_POLL_MS);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [batch?.id, batch?.stored_state, revoke]);

  const selectOptions = useMemo(() => options.sources.length > 0 ? options.sources : sources.map(source => ({ value: source.code, label: source.name || source.code })), [options.sources, sources]);
  const bind = (key: keyof OpportunityFilters) => (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setDraft(current => ({ ...current, [key]: event.target.value }));
  const renderSelect = (label: string, key: keyof OpportunityFilters, entries: SelectOption[]) => <label>{label}<select value={String(draft[key] || "")} onChange={bind(key)}><option value="">全部</option>{entries.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setFilters({ ...draft });
    updateUrl(draft, 1, null);
  };

  const reset = () => {
    setDraft({}); setFilters({}); setPage(1); updateUrl({}, 1, null);
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
      setBatch(next);
      setRefreshMessage(batchDisplayState(next));
    } catch (error) {
      if (isAccessError(error)) return revoke();
      if (error instanceof ApiError && error.status === 409) {
        try {
          const current = await getRefreshOverview();
          setOverview(current); setBatch(current.batch);
          setRefreshMessage(current.batch ? batchDisplayState(current.batch) : error.message);
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

  if (detailId) return <div className="pd-workspace tender-workspace"><Detail state={detail} onBack={closeDetail} onRetry={() => loadDetail(detailId)} /></div>;

  const active = batch && ["QUEUED", "RUNNING"].includes(batch.stored_state);
  const interrupted = batch && batchDisplayState(batch) === "INTERRUPTED";
  return <div className="pd-workspace tender-workspace">
    <section className="tender-hero">
      <div><p className="pd-eyebrow">LOCAL VERIFIED DATA</p><h2>商机获取</h2><p>查看已入库的结构化公告，按需触发受控刷新；打开官方原文核对后再进入项目判断。</p></div>
      <button className="button primary" type="button" onClick={() => void startRefresh()} disabled={refreshing || Boolean(active) || overview?.available === false}>{refreshing ? "提交中…" : interrupted ? "执行中断" : active ? "刷新进行中" : "刷新公告"}</button>
    </section>

    {overview?.available === false && <div className="notice info" role="note">刷新当前不可用：{overview.unavailable_reason || "后端未开放手动刷新。"}</div>}
    {metadataMessage && <div className="notice info" role="status">{metadataMessage}</div>}
    {staleMessage && <div className="notice error" role="alert">{staleMessage}</div>}
    {(batch || refreshMessage) && <section className="pd-panel tender-refresh" aria-live="polite"><div><strong>刷新状态</strong><span className={`tender-badge batch-${(batch ? batchDisplayState(batch) : "unknown").toLowerCase()}`}>{refreshLabel(refreshMessage || (batch ? batchDisplayState(batch) : undefined))}</span></div>{batch && terminalStates.has(batchDisplayState(batch)) && Object.entries(batch.results).length > 0 && <ul>{Object.entries(batch.results).map(([code, result]) => <li key={code}><strong>{code}</strong><span>{refreshLabel(result.display_state || result.state)}{typeof result.new_notices === "number" ? ` · 新增 ${result.new_notices} 条` : ""}{typeof result.new_versions === "number" && result.new_versions > 0 ? ` · 更新 ${result.new_versions} 版` : ""}{result.error_code ? ` · ${result.error_code}` : ""}</span></li>)}</ul>}</section>}

    <form className="pd-panel tender-filters" aria-label="商机筛选" onSubmit={submit}>
      <label>关键词<input value={draft.q || ""} onChange={bind("q")} placeholder="项目名称或编号" /></label>
      {options.regions.length > 0 ? renderSelect("地区", "region", options.regions) : <label>地区<input value={draft.region || ""} onChange={bind("region")} /></label>}
      {renderSelect("来源", "source", selectOptions)}
      {renderSelect("状态", "status", options.statuses)}
      <label>采购单位<input value={draft.purchaser || ""} onChange={bind("purchaser")} /></label>
      {renderSelect("目标需求", "need", options.needs)}
      {renderSelect("公告类型", "notice_type", options.noticeTypes)}
      {renderSelect("发布时间", "publish_period", options.publishPeriods)}
      {renderSelect("截止时间", "deadline_period", options.deadlinePeriods)}
      {renderSelect("预算", "budget_band", options.budgetBands)}
      <div className="pd-actions"><button className="button primary" type="submit">筛选</button><button className="button secondary" type="button" onClick={reset}>重置</button></div>
    </form>

    <section className="pd-panel tender-list" aria-labelledby="tender-list-title">
      <div className="pd-panel-heading"><h3 id="tender-list-title">已入库商机</h3>{list.kind === "ready" && <span>共 {list.data.total} 条</span>}</div>
      {list.kind === "loading" && <div className="tender-state" role="status">正在加载商机…</div>}
      {list.kind === "error" && <div className="tender-state" role="alert"><strong>商机列表加载失败</strong><p>{list.message}</p><button className="button secondary" onClick={() => setReload(value => value + 1)}>重试</button></div>}
      {list.kind === "ready" && list.data.items.length === 0 && <div className="tender-state"><strong>暂无符合条件的商机</strong><p>页面只展示真实已入库数据，不使用占位公告。</p></div>}
      {list.kind === "ready" && list.data.items.length > 0 && <><div className="tender-table-wrap"><table><caption className="sr-only">商机列表</caption><thead><tr><th>项目名称</th><th>地区</th><th>采购单位</th><th>预算</th><th>发布时间</th><th>截止时间</th><th>状态</th><th>来源</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.id}><td><button className="tender-title" type="button" onClick={() => openDetail(item.id)}>{item.project_name || "项目名称未提供"}</button>{item.project_code && <small>编号：{item.project_code}</small>}</td><td>{item.region || "—"}</td><td>{item.purchaser || "—"}</td><td>{formatBudget(item)}</td><td>{publishedAt(item)}</td><td>{formatDateTime(item.bid_deadline)}</td><td><span className={`tender-badge status-${item.status.toLowerCase()}`}>{statusLabel(item)}</span></td><td>{sourceName(item.source)}</td></tr>)}</tbody></table></div><div className="tender-pager"><span>第 {list.data.page} 页</span><div className="pd-actions"><button className="button secondary" type="button" disabled={page <= 1} onClick={() => changePage(page - 1)}>上一页</button><button className="button secondary" type="button" disabled={!list.data.has_more} onClick={() => changePage(page + 1)}>下一页</button></div></div></>}
    </section>
    <Sources sources={sources}/>
  </div>;
}
