import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, apiRequest } from "../api";
import { CenterLink, SectionHeader, type WorkspaceProps } from "./shared";

const root = "/api/engineering/jobs/";
const knowledgeRoot = "/api/engineering/knowledge/";
const quotaRoot = "/api/engineering/quota/";
const sectionNames: Record<string, string> = { overview: "工程部工作台", estimate: "内部成本草稿", quota: "套用定额" };
const statusLabels: Record<string, string> = {
  ready: "已就绪", locked: "待解锁", not_configured: "未配置", unavailable: "暂不可用", disabled: "未启用",
  pending: "待处理", queued: "排队中", running: "处理中", completed: "已完成", failed: "失败", blocked: "已阻止", cancelled: "已取消",
};
const statusLabel = (value?: string) => value ? statusLabels[value] || "状态待核实" : "服务未返回";

interface Capability { status: string; detail?: string }
interface Capabilities { cost?: Capability; ragflow?: Capability }
interface SourceHealth { conclusion?: string; warnings: string[] | null; zeroItems?: string; sourceDistribution?: string; raw?: string }
interface SummaryFile { status?: string; validationPassed?: boolean; preflightIssues: string[] | null; validationIssues: string[] | null; sourceHealth?: SourceHealth; pendingConfirmations: string[] | null }
interface EngineeringJob {
  id: string;
  status: string;
  region?: string;
  inspection?: { ok?: boolean; files: { name?: string; passed?: boolean; issues: string[] | null }[] };
  summary?: SummaryFile[];
  error?: { code?: string; detail?: string; retryable?: boolean };
  downloadAvailable: boolean;
  needsReview: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function text(value: unknown): string | undefined { return typeof value === "string" && value.trim() ? value.trim() : undefined; }
function list(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.flatMap((item) => typeof item === "string" && item.trim() ? [item.trim()] : isRecord(item) ? [text(item.detail) ?? text(item.message) ?? text(item.code)].filter(Boolean) as string[] : []);
}
function display(value: unknown): string | undefined {
  if (typeof value === "string") return text(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (!isRecord(value) && !Array.isArray(value)) return undefined;
  try { return JSON.stringify(value); } catch { return undefined; }
}
function statistic(value: unknown): string | undefined {
  if (isRecord(value)) return Object.entries(value).map(([key, item]) => `${key}：${display(item) ?? "未返回"}`).join("；");
  return display(value);
}
function sourceHealth(value: unknown): SourceHealth | undefined {
  if (!isRecord(value)) return display(value) ? { warnings: null, raw: display(value) } : undefined;
  const audit = isRecord(value.audit) ? value.audit : value;
  const conclusion = text(audit["结论"]);
  const warnings = list(audit["警告"]);
  const zeroItems = statistic(audit["零价项"]);
  const sourceDistribution = statistic(audit["来源分布"]);
  return conclusion || warnings !== null || zeroItems || sourceDistribution ? { conclusion, warnings, zeroItems, sourceDistribution, raw: display(value) } : { warnings: null, raw: display(value) };
}
function readCapability(value: unknown): Capability | undefined {
  return isRecord(value) && text(value.status) ? { status: text(value.status)!, detail: text(value.detail) } : undefined;
}
function readCapabilities(value: unknown): Capabilities | undefined {
  if (!isRecord(value)) return undefined;
  return { cost: readCapability(value.cost), ragflow: readCapability(value.ragflow) };
}
function readJob(value: unknown): EngineeringJob | null {
  if (!isRecord(value) || !text(value.id) || !text(value.status)) return null;
  const inspection = isRecord(value.inspection) ? {
    ok: typeof value.inspection.ok === "boolean" ? value.inspection.ok : undefined,
    files: Array.isArray(value.inspection.files) ? value.inspection.files.filter(isRecord).map((file) => ({ name: text(file.name), passed: typeof file.passed === "boolean" ? file.passed : undefined, issues: list(file.issues) })) : [],
  } : undefined;
  const summary = isRecord(value.result) && isRecord(value.result.summary) && Array.isArray(value.result.summary.files)
    ? value.result.summary.files.filter(isRecord).map((file) => ({ status: text(file.status), validationPassed: typeof file.validation_passed === "boolean" ? file.validation_passed : undefined, preflightIssues: list(file.preflight_issues), validationIssues: list(file.validation_issues), sourceHealth: sourceHealth(file.source_health), pendingConfirmations: list(file.pending_confirmations) })) : undefined;
  const error = isRecord(value.error) ? { code: text(value.error.code), detail: text(value.error.detail), retryable: typeof value.error.retryable === "boolean" ? value.error.retryable : undefined } : undefined;
  const failed = ["failed", "blocked"].includes(text(value.status)!.toLowerCase());
  const needsReview = failed || summary?.some((file) => file.validationPassed === false || !!file.pendingConfirmations?.length || file.sourceHealth?.conclusion === "可疑" || !!file.sourceHealth?.warnings?.length) === true;
  return { id: text(value.id)!, status: text(value.status)!, region: text(value.region), inspection, summary, error, downloadAvailable: isRecord(value.result) && value.result.download_available === true, needsReview };
}
function readList(value: unknown): { jobs: EngineeringJob[]; capabilities?: Capabilities } | null {
  if (!isRecord(value) || !Array.isArray(value.jobs)) return null;
  const jobs = value.jobs.map(readJob);
  return jobs.every((job): job is EngineeringJob => job !== null) ? { jobs, capabilities: readCapabilities(value.capabilities) } : null;
}
function readDetail(value: unknown): { job: EngineeringJob; capabilities?: Capabilities } | null {
  if (!isRecord(value)) return null;
  const job = readJob(value.job);
  return job ? { job, capabilities: readCapabilities(value.capabilities) } : null;
}
function errorMessage(error: unknown) { return error instanceof Error && error.message ? error.message : "请求未完成，请稍后重试。"; }

interface KnowledgeStatus { status: "locked" | "ready" | "unavailable" | "not_configured"; detail: string; dataset_document_count?: number }
interface KnowledgeSource { document_id: string; title: string; content: string; chunk_id: string }
function readKnowledgeStatus(value: unknown): KnowledgeStatus {
  if (!isRecord(value) || !["locked", "ready", "unavailable", "not_configured"].includes(String(value.status)) || typeof value.detail !== "string"
    || (value.dataset_document_count !== undefined && (!Number.isInteger(value.dataset_document_count) || (value.dataset_document_count as number) < 0))) throw new Error("知识库状态返回格式无效。");
  return value as unknown as KnowledgeStatus;
}
function readKnowledgeSources(value: unknown): KnowledgeSource[] {
  if (!isRecord(value) || value.status !== "completed" || !Array.isArray(value.sources) || !value.sources.every((source: unknown) =>
    isRecord(source) && ["document_id", "title", "content", "chunk_id"].every((key) => typeof source[key] === "string"))) throw new Error("检索结果返回格式无效，未展示不可信数据。");
  return value.sources as KnowledgeSource[];
}
async function fetchKnowledgeStatus(signal: AbortSignal) { return readKnowledgeStatus(await apiRequest<unknown>(knowledgeRoot, { signal })); }
async function retrieveKnowledge(question: string) {
  return readKnowledgeSources(await apiRequest<unknown>(`${knowledgeRoot}retrieve/`, { method: "POST", body: JSON.stringify({ question }) }));
}

function KnowledgePanel() {
  const [status, setStatus] = useState<KnowledgeStatus>();
  const [statusError, setStatusError] = useState("");
  const [refreshing, setRefreshing] = useState(true);
  const statusController = useRef<AbortController | null>(null);
  const [question, setQuestion] = useState("");
  const [sources, setSources] = useState<KnowledgeSource[]>();
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const refresh = () => {
    statusController.current?.abort();
    const controller = new AbortController();
    statusController.current = controller;
    setRefreshing(true); setStatus(undefined); setSources(undefined); setStatusError(""); setSearchError("");
    void fetchKnowledgeStatus(controller.signal).then((result) => { if (!controller.signal.aborted) setStatus(result); }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setStatusError(errorMessage(reason));
    }).finally(() => { if (!controller.signal.aborted) setRefreshing(false); });
  };
  useEffect(() => {
    refresh();
    return () => statusController.current?.abort();
  }, []);
  const canSearch = status?.status === "ready" && status.dataset_document_count !== 0;
  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const bounded = question.trim();
    if (!canSearch || searching || !bounded || bounded.length > 500) return;
    setSearching(true); setSearchError(""); setSources(undefined);
    try { setSources(await retrieveKnowledge(bounded)); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) setStatus({ status: "locked", detail: errorMessage(reason) });
      setSearchError(errorMessage(reason));
    } finally { setSearching(false); }
  };
  return <section className="center-panel" aria-label="工程知识检索">
    <h3>工程知识检索（仅供证据参考）</h3>
    <p>独立检索资料片段，不生成价格、定额推荐或审定结果。</p>
    {statusError ? <p role="alert">知识库状态读取失败：{statusError}</p> : status ? <p role="status">知识库状态：{statusLabel(status.status)} · {status.detail}{status.dataset_document_count !== undefined ? ` · 文档数：${status.dataset_document_count}` : ""}</p> : <p role="status">正在读取知识库状态…</p>}
    {status?.status === "ready" && status.dataset_document_count === 0 && <p>知识库暂无文档，暂不可检索。</p>}
    <button type="button" className="button secondary" onClick={refresh} disabled={refreshing || searching}>刷新知识库状态</button>
    <form onSubmit={search}><div className="center-field"><label htmlFor="engineering-knowledge-question">检索问题</label><input id="engineering-knowledge-question" value={question} maxLength={500} onChange={(event) => setQuestion(event.target.value)} disabled={!canSearch || searching} /></div><button className="button secondary" disabled={!canSearch || searching || !question.trim()}>{searching ? "正在检索…" : "检索资料"}</button></form>
    {searchError && <p role="alert">检索失败：{searchError}</p>}
    {sources && <div aria-label="检索证据"><h4>检索证据</h4>{sources.length ? <ul>{sources.map((source, index) => <li key={`${source.document_id}-${source.chunk_id}-${index}`}><strong>{source.title}</strong><p>{source.content}</p><small>文档 ID：{source.document_id} · 片段 ID：{source.chunk_id}</small></li>)}</ul> : <p>未检索到相关资料。</p>}</div>}
  </section>;
}

interface QuotaCandidate { code: string; major: string; name: string; unit: string; score: string; source: string; unitCompatible: boolean }
function readQuotaCandidates(value: unknown): QuotaCandidate[] {
  if (!isRecord(value) || value.status !== "candidates" || value.classification !== "internal_unapproved" || !Array.isArray(value.candidates)) {
    throw new Error("定额候选返回格式无效，未展示不可信数据。");
  }
  return value.candidates.map((candidate) => {
    if (!isRecord(candidate) || !text(candidate.code) || !text(candidate.major) || !text(candidate.name) || !text(candidate.unit)
      || !display(candidate.score) || !text(candidate.source) || typeof candidate.unit_compatible !== "boolean") {
      throw new Error("定额候选返回格式无效，未展示不可信数据。");
    }
    return { code: text(candidate.code)!, major: text(candidate.major)!, name: text(candidate.name)!, unit: text(candidate.unit)!, score: display(candidate.score)!, source: text(candidate.source)!, unitCompatible: candidate.unit_compatible };
  });
}

function QuotaCandidateSearch() {
  const [name, setName] = useState("");
  const [unit, setUnit] = useState("");
  const [candidates, setCandidates] = useState<QuotaCandidate[]>();
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const boundedName = name.trim();
    const boundedUnit = unit.trim();
    if (!boundedName || boundedName.length > 100 || !boundedUnit || boundedUnit.length > 20 || searching) return;
    setSearching(true); setError(""); setCandidates(undefined);
    try {
      const query = new URLSearchParams({ name: boundedName, unit: boundedUnit });
      setCandidates(readQuotaCandidates(await apiRequest<unknown>(`${quotaRoot}?${query}`)));
    } catch (reason) { setError(errorMessage(reason)); } finally { setSearching(false); }
  };
  return <section className="center-panel" aria-label="内部未审批定额候选查询">
    <h3>手动查询内部未审批定额候选</h3><p>仅供人工筛选：不自动套用，不生成价格合计，也不构成正式 D-05 申报。</p>
    <form onSubmit={search}><div className="center-field"><label htmlFor="engineering-quota-name">名称</label><input id="engineering-quota-name" value={name} maxLength={100} required disabled={searching} onChange={(event) => setName(event.target.value)} /></div><div className="center-field"><label htmlFor="engineering-quota-unit">单位</label><input id="engineering-quota-unit" value={unit} maxLength={20} required disabled={searching} onChange={(event) => setUnit(event.target.value)} /></div><button className="button secondary" disabled={searching || !name.trim() || !unit.trim()}>{searching ? "正在查询…" : "查询内部候选"}</button></form>
    {error && <p role="alert">候选查询失败：{error}</p>}
    {candidates && <div aria-label="内部未审批定额候选结果"><h4>候选结果</h4>{candidates.length ? <ul>{candidates.map((candidate) => <li key={`${candidate.code}-${candidate.unit}`}><strong>{candidate.code} · {candidate.name}</strong><p>专业：{candidate.major} · 单位：{candidate.unit} · 匹配分：{candidate.score}</p><p>原始来源：{candidate.source}</p><p>单位兼容：{candidate.unitCompatible ? "兼容" : "不兼容"}</p></li>)}</ul> : <p>未找到符合条件的内部未审批定额候选。</p>}</div>}
  </section>;
}

function Issues({ label, values }: { label: string; values: string[] | null }) {
  return <div><strong>{label}</strong>{values === null ? <p>服务未返回。</p> : values.length ? <ul>{values.map((value, index) => <li key={`${value}-${index}`}>{value}</li>)}</ul> : <p>服务返回为空。</p>}</div>;
}

export default function EngineeringPendingPage({ section }: WorkspaceProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [region, setRegion] = useState("");
  const [jobs, setJobs] = useState<EngineeringJob[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities>();
  const [loading, setLoading] = useState(section !== "quota");
  const [uploading, setUploading] = useState(false);
  const [loadingId, setLoadingId] = useState<string>();
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<EngineeringJob>();

  useEffect(() => {
    if (section === "quota") return;
    const controller = new AbortController();
    setLoading(true); setError("");
    void apiRequest<unknown>(root, { signal: controller.signal }).then((response) => {
      const data = readList(response);
      if (!data) throw new Error("任务列表返回格式无效，未展示不可信数据。");
      setJobs(data.jobs); setCapabilities(data.capabilities);
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [section]);

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const next = Array.from(event.target.files ?? []); event.target.value = "";
    if (next.length < 1 || next.length > 2 || next.some((file) => !file.name.toLowerCase().endsWith(".xlsx"))) {
      setFiles([]); setError("仅支持 1 至 2 个 .xlsx 清单文件。"); return;
    }
    setError(""); setFiles(next);
  };
  const openJob = async (id: string) => {
    setLoadingId(id); setError("");
    try {
      const data = readDetail(await apiRequest<unknown>(`${root}${encodeURIComponent(id)}/`));
      if (!data || data.job.id !== id) throw new Error("任务详情返回格式无效，未展示不可信数据。");
      setSelected(data.job); setCapabilities(data.capabilities); setJobs((current) => [data.job, ...current.filter((item) => item.id !== data.job.id)]);
    } catch (reason) { setError(errorMessage(reason)); } finally { setLoadingId(undefined); }
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (files.length < 1 || files.length > 2 || !region.trim()) { setError("请选择 1 至 2 个 .xlsx 清单文件并填写地区。"); return; }
    const body = new FormData(); files.forEach((file) => body.append("files", file)); body.set("region", region.trim());
    setUploading(true); setError("");
    try {
      const data = readDetail(await apiRequest<unknown>(root, { method: "POST", body }));
      if (!data) throw new Error("任务创建返回格式无效，未展示不可信数据。");
      setFiles([]); setCapabilities(data.capabilities); await openJob(data.job.id);
    } catch (reason) { setError(errorMessage(reason)); } finally { setUploading(false); }
  };

  if (section === "quota") return <>
    <SectionHeader title={sectionNames.quota} description="套用定额 D-05 尚未实现；本页不导入、不推荐，也不宣称任何定额已审定。" />
    <QuotaCandidateSearch />
    <section className="center-panel" aria-label="套用定额状态"><span className="status warning">未实现</span><h3>定额能力保持隔离（D-05）</h3><p>知识检索仅供证据参考，不解锁定额推荐或审定结果。</p><CenterLink className="button secondary" href="/centers/cost">返回工程部工作概览</CenterLink></section>
    <KnowledgePanel />
  </>;

  return <>
    <SectionHeader title={sectionNames[section] ?? sectionNames.overview} description="仅生成内部成本草稿；任务状态和核验信息以工程服务返回为准。" />
    <section className="center-panel" aria-label="内部成本草稿">
      <span className="status warning">内部成本草稿</span><h3>上传清单并查询任务</h3><p>仅接受 1 至 2 个 XLSX 清单文件。结果需要人工复核，不构成报价、定额审定或正式成果。</p>
      <form onSubmit={submit}><div className="center-field"><label htmlFor="engineering-files">清单文件</label><input id="engineering-files" type="file" multiple accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={chooseFiles} disabled={uploading} /><small>{files.length ? `已选择 ${files.map((file) => file.name).join("、")}` : "选择 1 至 2 个 .xlsx 文件"}</small></div><div className="center-field"><label htmlFor="engineering-region">地区</label><input id="engineering-region" value={region} maxLength={80} required disabled={uploading} onChange={(event) => setRegion(event.target.value)} /></div><button className="button primary" disabled={uploading || files.length === 0 || !region.trim()}>{uploading ? "正在创建草稿…" : "创建内部成本草稿"}</button></form>
      <p role="note">成本服务：{capabilities?.cost ? `${statusLabel(capabilities.cost.status)}${capabilities.cost.detail ? ` · ${capabilities.cost.detail}` : ""}` : "服务未返回"}<br />上传清单并预检通过、工程文档解析完成后，知识库才可能解锁；定额推荐和正式报价仍未开放。</p>
      {error && <p role="alert">{error}</p>}
    </section>
    <KnowledgePanel />
    <section className="center-panel" aria-label="成本草稿任务"><h3>任务状态</h3>{loading ? <p role="status">正在读取任务列表…</p> : jobs.length ? <ul>{jobs.map((job) => <li key={job.id}><button type="button" className="button secondary" disabled={loadingId === job.id} onClick={() => void openJob(job.id)}>{statusLabel(job.status)} · {job.id}</button>{job.needsReview && <span className="status warning">待复核</span>}</li>)}</ul> : <p>暂无服务端任务。</p>}
      {selected && <article aria-live="polite"><h4>任务 {selected.id}</h4><p>当前状态：{statusLabel(selected.status)}{selected.region ? ` · 地区：${selected.region}` : ""}</p>{selected.error && <p role="alert">任务错误：{selected.error.code ? `${selected.error.code} · ` : ""}{selected.error.detail ?? "服务未返回错误详情"}{selected.error.retryable === true ? " · 可重试" : ""}</p>}{selected.needsReview && <p><span className="status warning">待复核</span> 失败、校验未通过或存在待确认项时不得直接用于对外结果。</p>}
        {selected.inspection && <div><strong>预检</strong><p>ok：{String(selected.inspection.ok ?? "服务未返回")}</p>{selected.inspection.files.map((file, index) => <div key={`${file.name ?? "file"}-${index}`}><p>{file.name ?? `文件 ${index + 1}`} · passed：{String(file.passed ?? "服务未返回")}</p><Issues label="预检问题" values={file.issues} /></div>)}</div>}
        {selected.summary?.map((file, index) => <div key={`summary-${index}`}><h5>结果摘要 {index + 1}</h5><p>状态：{statusLabel(file.status)} · 校验：{file.validationPassed === undefined ? "服务未返回" : file.validationPassed ? "通过" : "未通过"}</p>{file.sourceHealth ? <div><strong>取价健康度（source_health）</strong>{file.sourceHealth.conclusion && <p>审计结论：{file.sourceHealth.conclusion}</p>}<Issues label="审计警告" values={file.sourceHealth.warnings} />{file.sourceHealth.zeroItems && <p>零价项：{file.sourceHealth.zeroItems}</p>}{file.sourceHealth.sourceDistribution && <p>来源分布：{file.sourceHealth.sourceDistribution}</p>}{!file.sourceHealth.conclusion && file.sourceHealth.warnings === null && !file.sourceHealth.zeroItems && !file.sourceHealth.sourceDistribution && <p>原始 source_health：{file.sourceHealth.raw ?? "服务未返回"}</p>}</div> : <p>取价健康度（source_health）：服务未返回</p>}<Issues label="预检问题（preflight_issues）" values={file.preflightIssues} /><Issues label="校验问题（validation_issues）" values={file.validationIssues} /><Issues label="待确认项（pending_confirmations）" values={file.pendingConfirmations} /></div>)}
        {selected.downloadAvailable && <a className="button secondary" href={`${root}${encodeURIComponent(selected.id)}/download/`} download>下载内部成本草稿</a>}</article>}
    </section>
  </>;
}
