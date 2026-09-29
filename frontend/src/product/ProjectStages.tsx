import { useEffect, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { CenterLink, Field } from "../centers/shared";
import { artifactDownload, artifactHistoryDownload, artifactPreview, draftDownload, draftHistoryDownload, sourceDownload, type BlueprintPayload, type DocumentTask, type DraftOutput, type OutputFamily, type ProductWorkflowProgress, type TaskHistory } from "./product-api";
import { DocumentSymbol, EmptyState, formatBytes, formatDate, goProduct, outputNames, ProductIcon, projectUrl, stageNames, stateNames, StatusBadge, useUnsavedWarning } from "./workbench-shared";

import { blueprintDisplayText } from "./blueprint-display";
import ProjectInputPanel from "./ProjectInputPanel";
import './project-analysis.css';
import SourceMaterials from "./SourceMaterials";
import { SOURCE_ACCEPT, extractionNames, type SourceAction } from "./product-api";

type Action = (endpoint: string, body: object) => Promise<unknown>;
interface Props { task: DocumentTask; outputs: DraftOutput[]; history: TaskHistory | null; outputsError: string; historyError: string; busy: boolean; conflict: boolean; disabled: (action: string) => boolean; onAction: Action; onReload: () => void; onUpload: (file: File, purpose?: 'equipment' | 'background') => Promise<unknown>; onSourceAction?: SourceAction }
const tabs = [{ id: "blueprint", name: "蓝图审批" }, { id: "technical-solution", name: "技术方案" }, { id: "feasibility", name: "可研报告" }, { id: "presentation", name: "汇报演示文稿" }, { id: "sources", name: "项目资料" }, { id: "history", name: "版本记录" }] as const;
type WorkspaceTab = typeof tabs[number]["id"];
const readTab = (): WorkspaceTab => {
  const requested = new URLSearchParams(window.location.search).get("tab") || "blueprint";
  const tab = requested === "overview" ? "blueprint" : requested === "outputs" ? "technical-solution" : requested;
  return tabs.some(item => item.id === tab) ? tab as WorkspaceTab : "blueprint";
};
const projectListUrl = () => {
  const value = new URLSearchParams(window.location.search).get("returnTo");
  if (!value?.startsWith("/centers/product/")) return "/centers/product/projects";
  const url = new URL(value, window.location.origin);
  return url.searchParams.has("task") ? "/centers/product/projects" : `${url.pathname}${url.search}`;
};
const lines = (value: string) => value.split("\n").map(text => text.trim()).filter(Boolean);
const outputTargetLabel = (target: number) => `至少 ${target.toLocaleString()} 字`;

type VisibleStatus = ProductWorkflowProgress['status'];
type VisiblePhase = { id: string; title: string; status: VisibleStatus; detail: string; statusLabel?: string };
function workflowPhases(task: DocumentTask, outputs: DraftOutput[]): VisiblePhase[] {
  const persisted = task.checkpoint?.analysis_progress || task.analysis_progress || {};
  const phase = (key: keyof typeof persisted) => persisted[key];
  const active = ['RUNNING', 'QUEUED'].includes(task.state);
  const knowledge = task.knowledge || task.blueprint_knowledge || { mode: 'source_only_preview', required: false, status: 'source_only_preview', ragflow_used: false, source_count: 0, detail: '知识检索状态尚未由服务端返回。' };
  const currentFamilies = new Set(outputs.filter(item => item.current).map(item => item.family));
  const documents = phase('documents');
  const equipment = phase('equipment');
  const blueprint = phase('blueprint');
  const knowledgePhase = phase('knowledge');
  const webSearch = phase('web_search');
  const reviewPhase = phase('review');
  const normalize = (value: ProductWorkflowProgress | undefined, fallback: VisibleStatus) => value?.status || fallback;
  const countDetail = (value: ProductWorkflowProgress | undefined, fallback: string) => value?.detail || (value?.source_count !== undefined ? `${value.source_count} 份背景材料` : value?.item_count !== undefined ? `${value.item_count} 行设备事实` : fallback);
  const knowledgeReady = knowledge.status === 'ready';
  const knowledgeStatus: VisibleStatus = knowledgeReady
    ? 'completed'
    : knowledgePhase && ['running', 'failed', 'blocked', 'waiting', 'skipped'].includes(knowledgePhase.status)
      ? knowledgePhase.status
      : knowledge.status === 'waiting_for_ragflow' ? 'waiting' : 'pending';
  const knowledgeDetail = knowledgeReady
    ? knowledgePhase?.detail?.replaceAll('RAGFlow', '知识库') || `知识库检索完成，命中 ${knowledge.source_count} 项知识来源`
    : knowledge.status === 'waiting_for_ragflow'
      ? '知识库暂不可用，正式蓝图生成保持锁定'
      : '仅使用项目资料预览，未执行真实知识库检索，不产生知识库命中记录';
  const contentRecords = [documents, equipment].filter((record): record is ProductWorkflowProgress => Boolean(record));
  const contentStatus: VisibleStatus = (['failed', 'blocked', 'running', 'waiting'] as const).find(status => contentRecords.some(record => record.status === status))
    || (contentRecords.length === 2 && contentRecords.every(record => record.status === 'completed') ? 'completed' : 'pending');
  const contentDetail = contentRecords.map(record => countDetail(record, '')).filter(Boolean).join('；')
    || '等待服务端整理项目资料、设备事实与内容结构';
  const revision = task.blueprint_review;
  const currentRound = Math.min(revision.revision_count + 1, revision.revision_limit || 3);
  const reviewStatus = reviewPhase?.status || (task.blueprint_approved ? 'completed' : task.blueprint && task.state === 'WAITING_REVIEW' ? 'waiting' : 'pending');
  const reviewDetail = reviewPhase?.detail || (task.blueprint_approved ? `已通过蓝图 v${task.blueprint?.version || task.blueprint_version}` : task.blueprint && task.state === 'WAITING_REVIEW' ? `等待第 ${currentRound}/${revision.revision_limit} 轮人工审核` : `蓝图生成后进入人工审核，最多 ${revision.revision_limit} 轮`);
  const reportPhase = (family: 'technical-solution' | 'feasibility', suffix: 'technical' | 'feasibility', title: string): VisiblePhase => {
    const latest = [phase(`render_${suffix}`), phase(`review_${suffix}`), phase(`writing_${suffix}`)]
      .filter((record): record is ProductWorkflowProgress => Boolean(record))
      .sort((left, right) => (right.updated_at || '').localeCompare(left.updated_at || ''))[0];
    const saved = currentFamilies.has(family);
    const progress = task.output_generation?.[family];
    const target = task.output_targets?.[family];
    const minimum = target;
    const belowTarget = progress !== undefined && minimum !== undefined && progress.actual_characters < minimum;
    return { id: family, title, status: belowTarget ? 'blocked' : saved ? 'completed' : latest?.status === 'failed' || latest?.status === 'blocked' ? latest.status : latest?.status === 'running' && active ? 'running' : 'pending', detail: belowTarget ? `${progress.actual_characters.toLocaleString()} 字，未达到当前最低 ${minimum.toLocaleString()} 字；可续写或重试` : latest?.detail || (saved ? '文件已保存，可在文档成果中下载' : '蓝图确认后自动生成') };
  };
  return [
    { id: 'knowledge', title: '知识库检索', status: knowledgeStatus, statusLabel: knowledge.status === 'source_only_preview' && knowledgeStatus === 'pending' ? '未执行' : undefined, detail: knowledgeDetail },
    { id: 'web-search', title: '联网降级补充', status: webSearch?.status || 'skipped', statusLabel: !webSearch ? '未配置/未执行' : webSearch.status === 'pending' ? '待配置' : webSearch.status === 'skipped' ? '未执行' : undefined, detail: webSearch?.detail || '当前服务端未配置联网搜索阶段，本次未执行联网检索' },
    { id: 'content', title: '组织语言与内容', status: contentStatus, detail: contentDetail },
    { id: 'blueprint', title: '生成项目蓝图', status: normalize(blueprint, task.blueprint ? 'completed' : task.pending_action === 'blueprint' && active ? 'running' : 'pending'), detail: countDetail(blueprint, task.blueprint ? `项目蓝图 v${task.blueprint.version} 已生成` : '等待前置分析完成') },
    { id: 'review', title: '人工审核蓝图', status: reviewStatus, detail: reviewDetail },
    reportPhase('technical-solution', 'technical', '技术方案'),
    reportPhase('feasibility', 'feasibility', '可行性研究报告'),
    { id: 'presentation', title: '汇报 PPT', status: phase('presentation')?.status || (currentFamilies.has('presentation') ? 'completed' : 'pending'), detail: phase('presentation')?.detail || '基于两份报告制作，不新增项目事实' },
  ];
}

export default function ProjectStages(props: Props) {
  const { task, outputs, history, outputsError, historyError, busy, conflict, disabled, onAction, onReload, onUpload, onSourceAction } = props;
  const [tab, setTab] = useState(readTab);
  const [cancelling, setCancelling] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [sourcePurpose, setSourcePurpose] = useState<'equipment' | 'background'>('background');
  useEffect(() => { let active = true; const sync = () => queueMicrotask(() => { if (active) setTab(readTab()); }); window.addEventListener("popstate", sync); return () => { active = false; window.removeEventListener("popstate", sync); }; }, []);
  const returnTo = projectListUrl();
  const projectHref = (value: WorkspaceTab) => `${projectUrl(task.id, value)}&returnTo=${encodeURIComponent(returnTo)}`;
  const move = (value: WorkspaceTab) => goProduct(projectHref(value));
  const running = ["RUNNING", "QUEUED"].includes(task.state);
  const phases = workflowPhases(task, outputs);
  const finishedPhases = phases.filter(phase => phase.status === 'completed').length;
  const settledPhases = phases.filter(phase => phase.status === 'completed' || phase.status === 'skipped').length;
  const currentPhase = phases.find(phase => phase.status === 'running') || phases.find(phase => phase.status === 'failed' || phase.status === 'blocked') || phases.find(phase => phase.status === 'waiting') || phases.find(phase => phase.status === 'pending');
  const progressTitle = task.state === 'CANCELLED' ? '任务已取消' : task.state === 'FAILED' || task.state === 'WAITING_INPUT' ? '处理已暂停，请查看下方提示' : task.state === 'COMPLETED' ? '交付成果已保存；可选降级节点按服务端记录显示' : task.state === 'QUEUED' ? '排队中，等待开始处理' : task.state === 'WAITING_REVIEW' && task.stage === 'BLUEPRINT' ? '蓝图已生成，等待您确认' : settledPhases === phases.length ? '全部流程节点已结束' : currentPhase ? `${currentPhase.title} · ${currentPhase.statusLabel || { pending: '等待开始', running: '处理中', completed: '已完成', failed: '处理失败', blocked: '暂时受阻', waiting: '等待确认', skipped: '未执行' }[currentPhase.status]}` : '项目资料已保存';
  const milestones = phases;
  const statusLabels: Record<VisibleStatus, string> = { pending: '等待开始', running: '处理中', completed: '已完成', failed: '处理失败', blocked: '暂时受阻', waiting: '等待确认', skipped: '未执行' };
  const runtimeDetail = (detail: string) => detail.replaceAll('RAGFlow', '知识库').replaceAll('PPT', '汇报文稿').replaceAll('execution_failed', '执行失败').replace(/\bv(\d+)\b/g, '第 $1 版');
  const failureMessages: Record<string, string> = {
    busy: '模型服务繁忙，请稍后从已保存进度重试。',
    rate_limited: '模型服务调用频率受限，请稍后重试。',
    timeout: '模型响应超时，请稍后从已保存进度重试。',
    invalid_request: '模型请求未通过参数校验，请联系负责人检查调用配置。',
    internal_error: '模型调用服务发生异常，请重试或联系负责人查看诊断记录。',
    unsupported_capability: '当前模型不支持所需能力，请联系负责人检查模型配置。',
    model_configuration_changed: '模型配置已变更，请刷新后重试。',
    ragflow_unavailable: '知识库服务暂时无法连接，请恢复服务后从已保存进度重试；无需重新上传资料。',
    web_search_unconfigured: '知识库未命中；联网搜索服务尚未接入，已暂停生成。请补充知识库资料后重新检索。',
    output_length_below_target: '正文篇幅未达到当前最低要求，任务未完成；已保存现有章节，可续写补足篇幅，或从已保存进度重试。',
    model_call_limit: '模型调用预算已耗尽，任务未完成；已保存现有进度，补充预算后可继续，或从已保存进度重试。',
  };
  const stateHeading = task.state === "WAITING_REVIEW" && task.stage === "BLUEPRINT" ? "蓝图等待人工确认" : stateNames[task.state] || "项目状态待确认";

  return <div className="pd-workspace pd-project-detail">
    <header className="pd-page-title pd-project-header"><div><CenterLink href={returnTo} className="pd-back-link">← 返回项目清单</CenterLink><span className="pd-eyebrow">项目交付工作台</span><h1>{task.title}</h1><p>项目版本 {task.version} · 更新于 {formatDate(task.updated_at)}</p></div><div className="pd-actions"><button className="button secondary" disabled={busy} onClick={onReload}>刷新状态</button>{!disabled("cancel") && <button className="text-button pd-danger" onClick={() => setCancelling(true)}>取消任务</button>}</div></header>
    <section className="pd-project-statusline" aria-label="项目当前状态" aria-live="polite">
      <div><span>当前状态</span><StatusBadge task={task}/><strong>{stateHeading}</strong></div>
      <div><span>当前阶段</span><strong>{stageNames[task.stage] || "阶段待确认"}</strong></div>
      <div><span>项目所有者</span><strong>{task.owner_name || "未提供"}</strong></div>
      <div><span>审核人</span><strong>{task.reviewer_name || "未指定"}</strong></div>
      {running && <div className="pd-status-progress"><span>{task.state === "QUEUED" ? "等待后台执行" : "后台处理中"}</span><div className="pd-indeterminate" role="progressbar" aria-label={task.state === "QUEUED" ? "等待后台执行" : "后台正在处理"}><span/></div></div>}
    </section>
    {conflict && <div className="pd-feedback" role="alert">版本或前置条件发生变化。未保存的编辑已保留，请刷新后核对；不会自动覆盖服务端内容。</div>}
    {cancelling && <section role="alertdialog" aria-label="确认取消任务" className="pd-feedback"><h3>确认取消当前任务？</h3><p>取消后不能继续编辑这个任务。已上传资料和历史成果仍保留，不会删除。</p><div className="pd-actions"><button className="button secondary" onClick={() => setCancelling(false)}>继续处理</button><button className="button primary" disabled={busy || disabled("cancel")} onClick={async () => { if (await onAction("cancel/", {})) setCancelling(false); }}>确认取消</button></div></section>}
    <section className="pd-compact-progress" data-running={running} aria-label="项目处理进度">
      <div className="pd-compact-progress-heading"><strong>处理进度</strong><span>已结束 {settledPhases} / {phases.length} · 完成 {finishedPhases}</span></div>
      <progress max={phases.length} value={settledPhases} aria-label="已结束处理节点"/>
      <ol className="pd-progress-milestones" aria-label="完整处理链路">{milestones.map((phase, index) => <li key={phase.id} data-status={phase.status} aria-current={phase.status === 'running' || phase.status === 'waiting' ? 'step' : undefined}><span className="pd-milestone-dot" aria-hidden="true">{phase.status === 'completed' ? <ProductIcon name="check"/> : phase.status === 'skipped' ? '—' : phase.status === 'failed' || phase.status === 'blocked' ? '!' : index + 1}</span><strong>{phase.title}</strong><small>{phase.statusLabel || statusLabels[phase.status]}</small></li>)}</ol>
      <p role="status" aria-live="polite">{running && <span className="pd-spinner"/>}{progressTitle}</p>
      <details className="pd-runtime-details"><summary>运行细节</summary><ol>{phases.map(phase => <li key={phase.id}><strong>{phase.title}</strong><span>{phase.statusLabel || statusLabels[phase.status]}</span><p>{runtimeDetail(phase.detail)}</p></li>)}</ol></details>
    </section>
    {task.error_code && <div className="pd-feedback" role="alert"><strong>当前任务需要处理</strong><p>{task.error_code === "model_authorization_required" ? "尚未取得模型调用与资料外发授权。项目已保留，可以先完善资料与人工蓝图。" : task.error_code === "revision_requested" ? "蓝图已退回修改，请查看确认记录并修订。" : failureMessages[task.error_code] || (task.blueprint_approved ? "生成未完成，已保存项目资料和已完成章节。可从现有进度继续，不需要重新审批蓝图。" : "生成未完成，项目资料已保留，请核对提示后重试。")}</p>{!disabled("retry") && <button className="button secondary" disabled={busy} onClick={() => void onAction("retry/", {})}>从已保存进度重试</button>}</div>}
    <Tabs.Root className="pd-project-tabs" value={tab} onValueChange={value => move(value as WorkspaceTab)} activationMode="manual">
      <Tabs.List className="pd-detail-tabs" aria-label="项目交付工作区">{tabs.map(item => <Tabs.Trigger key={item.id} value={item.id}>{item.name}{item.id === "sources" && <small>{task.sources.length}</small>}</Tabs.Trigger>)}</Tabs.List>
      <Tabs.Content value="blueprint" className="pd-tab-panel">
        {!task.blueprint && <section className="pd-workbench-command" aria-labelledby="blueprint-start-title"><div><span className="pd-section-label">可执行操作</span><h3 id="blueprint-start-title">开始蓝图编制</h3></div><div className="pd-actions">{task.blueprint_knowledge?.required && task.blueprint_knowledge.status !== "ready" && <button className="button primary" disabled={disabled("queue_blueprint_knowledge")} onClick={() => void onAction("queue/", { action: "knowledge" })}>先检索知识库</button>}<button className={task.blueprint_knowledge?.required && task.blueprint_knowledge.status !== "ready" ? "button secondary" : "button primary"} disabled={disabled("queue_blueprint")} onClick={() => void onAction("queue/", { action: "blueprint" })}>生成项目蓝图</button></div><BlockerNotes task={task}/></section>}
        <BlueprintPanel task={task} disabled={disabled} busy={busy} onAction={onAction}/>
      </Tabs.Content>
      {(["technical-solution", "feasibility", "presentation"] as const).map(family => <Tabs.Content key={family} value={family} className="pd-tab-panel"><ArtifactWorkspace family={family} task={task} outputs={outputs} outputsError={outputsError} disabled={disabled} onAction={onAction} move={move}/></Tabs.Content>)}
      <Tabs.Content value="sources" className="pd-tab-panel pd-sources-workspace">
        <ProjectInputPanel task={task} disabled={disabled} onAction={onAction}/>
        <section className="pd-workbench-section"><div className="pd-panel-heading"><h3>原始资料</h3><span className="pd-muted">{task.sources.length} 份资料</span></div>{task.sources.length ? <ul className="pd-upload-list">{task.sources.map(source => <li key={source.id}><ProductIcon name="file"/><div><strong>{source.original_name}</strong><small>{source.purpose === 'equipment' ? '设备清单 · ' : source.purpose === 'background' ? '项目背景材料 · ' : ''}{source.parsed ? `${extractionNames[source.parsed.status]} · ` : ""}{formatBytes(source.size)} · {formatDate(source.created_at)}{source.warnings?.length ? ` · ${source.warnings.length} 项待核对` : ""}</small></div><a className="button secondary" href={sourceDownload(source.id)}>下载原文件</a></li>)}</ul> : <EmptyState title="尚未上传附件" detail="可以先填写建设目标，也可以补充设备清单和背景资料。"/>}<div className="pd-source-upload">{task.intake_mode === 'equipment_background' && <><label htmlFor="source-purpose">资料类型</label><select id="source-purpose" value={sourcePurpose} onChange={event => setSourcePurpose(event.target.value as 'equipment' | 'background')}><option value="background">项目背景材料</option><option value="equipment" disabled={task.sources.some(source => source.purpose === 'equipment')}>设备清单（仅一份）</option></select></>}<label htmlFor="project-more-file">补充资料</label><input id="project-more-file" type="file" accept={SOURCE_ACCEPT} disabled={disabled("add_source")} onChange={event => setFile(event.target.files?.[0] || null)}/><button className="button primary" disabled={disabled("add_source") || !file} onClick={async () => { if (file && await onUpload(file, task.intake_mode === 'equipment_background' ? sourcePurpose : undefined)) { setFile(null); setSourcePurpose('background'); } }}>{busy ? "正在上传并解析…" : "上传并保存"}</button></div><p className="pd-muted">补充资料会形成新的输入版本，相关蓝图和成果需重新核对。上传不会覆盖原始文件。</p></section>
        <SourceMaterials task={task} busy={busy} onSourceAction={onSourceAction} onAction={onAction} disabled={disabled}/>
        <section className="pd-workbench-section"><div className="pd-panel-heading"><h3>设备清单事实</h3><span className="pd-badge neutral">{task.input?.items.length || 0} 行</span></div>{task.input?.items.length ? <div className="pd-table-wrap"><table className="pd-table"><thead><tr><th>原行号</th><th>设备名称</th><th>数量</th><th>单位</th><th>来源位置</th></tr></thead><tbody>{task.input.items.map((item, index) => <tr key={index}><td>{item.row_id || "未提供"}</td><td>{item.name || "名称缺失"}</td><td>{item.quantity === "" ? "未提供" : item.quantity}</td><td>{item.unit || "未提供"}</td><td>{task.sources.find(source => source.id === item.source_id)?.original_name || "人工录入"}{item.source_location && <small>{[item.source_location.sheet, item.source_location.part, item.source_location.range, item.source_location.table ? `表 ${item.source_location.table}` : "", item.source_location.row ? `第 ${item.source_location.row} 行` : ""].filter(Boolean).join(" · ")}</small>}</td></tr>)}</tbody></table></div> : <p className="pd-muted">尚未录入设备清单，可上传清单或在项目底稿中补充。</p>}{task.input_issues.length > 0 && <p className="pd-feedback">有 {task.input_issues.length} 项输入问题需要结合来源核对。</p>}<CenterLink className="pd-quiet-link" href={projectUrl(task.id, "sources")}>进入专业工作台核对输入问题 ›</CenterLink></section>
      </Tabs.Content>
      <Tabs.Content value="history" className="pd-tab-panel">
        <section className="pd-workbench-section"><div className="pd-panel-heading"><h3>成果历史版本</h3><span className="pd-muted">保留旧版，不覆盖</span></div>{outputsError ? <p role="alert">{outputsError}</p> : outputs.length ? <div className="pd-table-wrap"><table className="pd-table"><thead><tr><th>成果</th><th>版本</th><th>状态</th><th>完整性摘要</th><th>下载</th></tr></thead><tbody>{[...outputs].reverse().map(item => <tr key={item.id}><td>{outputNames[item.family]}</td><td>v{item.version}</td><td>{item.current ? item.approved ? "当前 · 已批准" : "当前 · 已生成草稿" : "历史 · 已过期"}</td><td><code title={item.sha256}>{item.sha256.slice(0, 12)}…</code></td><td><a href={item.family === "technical-solution" ? item.current ? artifactDownload(item.id) : artifactHistoryDownload(item.id) : item.current ? draftDownload(item.id) : draftHistoryDownload(item.id)}>{item.current ? "下载当前文件" : "下载历史文件"}</a></td></tr>)}</tbody></table></div> : <EmptyState title="暂无成果版本" detail="已保存的输入和蓝图记录仍可在下方追溯。"/>}</section>
        <section className="pd-workbench-section"><h3>项目生成与审核记录</h3>{historyError ? <p role="alert">{historyError}</p> : !history ? <p role="status">正在读取版本链…</p> : <ol className="pd-timeline">{[...history.timeline].reverse().map(event => <li key={`${event.event}-${event.id}`}><span className="pd-timeline-dot"/><div><strong>{({ revision: "版本保存", external_upload: "资料上传", ai_task: "后台处理", artifact: "成果生成", approval: "人工审核" } as Record<string, string>)[event.event] || "项目记录"}{event.family ? ` · ${outputNames[event.family] || event.family}` : ""}{event.version ? ` v${event.version}` : ""}</strong><p>{event.reason || event.kind || "服务端已记录"} · {event.actor?.name || "系统"}</p>{event.sha256 && <small>摘要 {event.sha256.slice(0, 16)}…</small>}</div></li>)}</ol>}</section>
      </Tabs.Content>
    </Tabs.Root>
  </div>;
}

function ArtifactWorkspace({ family, task, outputs, outputsError, disabled, onAction, move }: { family: OutputFamily; task: DocumentTask; outputs: DraftOutput[]; outputsError: string; disabled: (action: string) => boolean; onAction: Action; move: (tab: WorkspaceTab) => void }) {
  const output = outputs.filter(item => item.family === family && item.current).at(-1);
  const artifact = output && task.artifacts.find(item => item.id === output.id);
  const progress = family === "presentation" ? undefined : task.output_generation?.[family];
  const target = family === "presentation" ? undefined : task.output_targets?.[family];
  const targetLabel = target === undefined ? undefined : outputTargetLabel(target);
  const meetsMinimum = progress !== undefined && target !== undefined && progress.actual_characters >= target;
  const format = family === "presentation" ? "PowerPoint · PPTX" : "Word · DOCX";
  if (outputsError) return <div role="alert" className="pd-feedback">{outputsError}</div>;
  return <section className="pd-artifact-workspace" aria-labelledby={`${family}-title`}>
    <header className="pd-artifact-title"><div className="pd-artifact-head"><DocumentSymbol family={family}/><div><span className="pd-section-label">成果工作区</span><h3 id={`${family}-title`}>{outputNames[family]}</h3><small>{format}</small></div></div>{output ? <span className={`pd-badge ${output.approved ? "good" : "warning"}`}>{output.approved ? "已批准" : "草稿 · 未正式发布"}</span> : <span className="pd-badge neutral">尚未生成</span>}</header>
    {output ? <>
      <dl className="pd-artifact-facts"><div><dt>当前版本</dt><dd>v{output.version}</dd></div><div><dt>审批状态</dt><dd>{output.approved ? "已批准" : "待业务确认"}</dd></div><div><dt>内容来源</dt><dd>{output.content_version ? `内容 v${output.content_version} · ${output.content_approved ? "已审" : "来源快照"}` : "结构化内容来源"}</dd></div>{targetLabel && <div><dt>篇幅结果</dt><dd>{progress ? `${progress.actual_characters.toLocaleString()} / ${targetLabel} · ${meetsMinimum ? "已达目标" : "未达当前最低要求，可续写或重试"}` : targetLabel}</dd></div>}</dl>
      <div className="pd-workbench-command"><div><span className="pd-section-label">可执行操作</span><h3>当前成果文件</h3></div><div className="pd-actions"><a className="button primary" href={family === "technical-solution" ? artifactDownload(output.id) : draftDownload(output.id)}><ProductIcon name="download"/>下载文件</a>{artifact?.render_evidence?.pages.length ? <a className="button secondary" href={artifactPreview(artifact.id, 1)} target="_blank" rel="noreferrer">预览第 1 页</a> : <span className="pd-muted">暂无 Office 预览证据</span>}<button className="button secondary" onClick={() => move("history")}>查看历史版本</button></div></div>
    </> : <div className="pd-artifact-empty"><h3>{task.blueprint_approved ? runningOutputMessage(task) : "蓝图尚未确认，成果生成保持锁定"}</h3><p>{task.blueprint_approved ? "生成完成后，当前版本将在此提供下载。" : "请先在蓝图审批中完成确认。"}</p><div className="pd-actions"><button className="button primary" onClick={() => move("blueprint")}>查看蓝图审批</button>{!disabled("retry") && <button className="button secondary" onClick={() => void onAction("retry/", {})}>重试生成</button>}</div></div>}
    <BlockerNotes task={task}/>
  </section>;
}

function runningOutputMessage(task: DocumentTask) { return ["RUNNING", "QUEUED"].includes(task.state) ? "后台正在生成成果" : "当前版本尚无可下载成果"; }

function BlockerNotes({ task }: { task: DocumentTask }) { const notes = Array.from(new Set(Object.entries(task.blockers || {}).filter(([action]) => action !== 'queue_retrieve').map(([, value]) => value.detail))); return notes.length ? <div className="pd-blockers">{notes.map(note => <p key={note}><ProductIcon name="shield"/>{note}</p>)}</div> : null; }

function BlueprintPanel({ task, disabled, busy, onAction }: { task: DocumentTask; disabled: (action: string) => boolean; busy: boolean; onAction: Action }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<BlueprintPayload | null>(null);
  const [baseVersion, setBaseVersion] = useState(task.version);
  const [comment, setComment] = useState("");
  useUnsavedWarning(editing);
  const blueprint = task.blueprint;
  const pendingReview = Boolean(blueprint && task.state === "WAITING_REVIEW" && task.stage === "BLUEPRINT" && !task.blueprint_approved);
  useEffect(() => { setComment(""); }, [blueprint?.id, blueprint?.sha256]);
  const changed = editing && task.version !== baseVersion;
  const decide = async (decision: "approve" | "revise") => {
    if (!blueprint) return;
    if (await onAction("decisions/", { target: "blueprint", target_id: blueprint.id, sha256: blueprint.sha256, decision, comment: comment.trim() })) setComment("");
  };
  function edit() {
    setDraft(blueprint ? structuredClone(blueprint.payload) : { purpose: task.input?.requirements || "", audience: "项目评审人员", chapters: [{ id: "chapter-1", title: "项目概述", scope: "", source_ids: [] }], conditions: (task.input?.conditions || []).map(text => ({ text, type: "human" as const })), missing: [], conflicts: [], template_version: "frozen-original-v1" });
    setBaseVersion(task.version); setEditing(true);
  }
  const sourceChoices = [...task.sources.map(source => ({ id: source.id, name: source.original_name })), ...(task.input?.items || []).filter(item => item.row_id).map(item => ({ id: String(item.row_id), name: `清单行 ${item.row_id}：${item.name}` })),  ...(task.input?.knowledge_sources || []).map(source => ({ id: source.id, name: source.location }))];
  return <section className="pd-panel pd-blueprint"><div className="pd-panel-heading"><div><h3>项目蓝图 {blueprint && <span className="pd-badge info">v{blueprint.version}</span>}</h3><p className="pd-muted">确认每章标题、计划字数、主要内容及资料依据后，再进入正文编制。</p></div><div className="pd-actions">{!editing && <button className="button secondary" disabled={disabled("save_blueprint")} onClick={edit}>{blueprint ? "修改蓝图" : "人工编制蓝图"}</button>}</div></div>
    {editing && draft ? <form onSubmit={async event => { event.preventDefault(); if (!changed && await onAction("blueprint/", { payload: { ...draft, missing: lines(draft.missing.join("\n")), conflicts: lines(draft.conflicts.join("\n")) } })) setEditing(false); }}>
      {changed && <p role="alert" className="pd-feedback">服务端已出现新版本。当前编辑仍保留，请取消本地编辑并重新载入后核对，不能直接覆盖。</p>}
      <fieldset className="pd-form-fields" disabled={busy || changed || disabled("save_blueprint")}>
        <div className="pd-form-columns"><Field id="visual-blueprint-purpose" label="建设目标"><textarea id="visual-blueprint-purpose" required maxLength={5000} value={draft.purpose} onChange={event => setDraft({ ...draft, purpose: event.target.value })}/></Field><Field id="visual-blueprint-audience" label="文档受众"><input id="visual-blueprint-audience" required maxLength={1000} value={draft.audience} onChange={event => setDraft({ ...draft, audience: event.target.value })}/></Field></div>
        <h4>章节与建设范围</h4>{draft.chapters.map((chapter, index) => <div className="pd-blueprint-chapter" key={chapter.id}><span className="pd-number">{index + 1}</span><div><Field id={`visual-chapter-${index}`} label={`第 ${index + 1} 章标题`}><input id={`visual-chapter-${index}`} value={chapter.title} required maxLength={160} onChange={event => setDraft({ ...draft, chapters: draft.chapters.map((value, i) => i === index ? { ...value, title: event.target.value } : value) })}/></Field><Field id={`visual-scope-${index}`} label={`第 ${index + 1} 章范围`}><textarea id={`visual-scope-${index}`} value={chapter.scope} maxLength={5000} onChange={event => setDraft({ ...draft, chapters: draft.chapters.map((value, i) => i === index ? { ...value, scope: event.target.value } : value) })}/></Field><fieldset className="pd-source-choices"><legend>引用资料</legend>{sourceChoices.length ? sourceChoices.map(source => <label key={source.id}><input type="checkbox" checked={chapter.source_ids.includes(source.id)} onChange={event => setDraft({ ...draft, chapters: draft.chapters.map((value, i) => i === index ? { ...value, source_ids: event.target.checked ? [...value.source_ids, source.id] : value.source_ids.filter(id => id !== source.id) } : value) })}/>{source.name}</label>) : <small>尚无附件，可先整理章节范围。</small>}</fieldset></div><button className="text-button pd-danger" type="button" disabled={draft.chapters.length <= 1} aria-label={`移除第 ${index + 1} 章`} onClick={() => setDraft({ ...draft, chapters: draft.chapters.filter((_, i) => i !== index) })}>移除</button></div>)}<button className="button secondary" type="button" disabled={draft.chapters.length >= 200} onClick={() => setDraft({ ...draft, chapters: [...draft.chapters, { id: `chapter-${crypto.randomUUID()}`, title: "", scope: "", source_ids: [] }] })}>＋ 添加章节</button>
        <h4>实施与审核条件</h4>{draft.conditions.map((condition, index) => <div className="pd-condition-row" key={index}><input aria-label={`条件 ${index + 1}`} value={condition.text} required maxLength={1000} onChange={event => setDraft({ ...draft, conditions: draft.conditions.map((value, i) => i === index ? { ...value, text: event.target.value } : value) })}/><select aria-label={`条件 ${index + 1} 核对方式`} value={condition.type} onChange={event => setDraft({ ...draft, conditions: draft.conditions.map((value, i) => i === index ? { ...value, type: event.target.value as "human" | "program" | "model" } : value) })}><option value="human">人工核对</option><option value="program">程序校验</option><option value="model">模型辅助</option></select><button className="text-button" type="button" disabled={task.input?.conditions.includes(condition.text)} onClick={() => setDraft({ ...draft, conditions: draft.conditions.filter((_, i) => i !== index) })}>移除</button></div>)}<button className="text-button" type="button" onClick={() => setDraft({ ...draft, conditions: [...draft.conditions, { text: "", type: "human" }] })}>＋ 添加条件</button>
        <div className="pd-form-columns"><Field id="visual-missing" label="资料缺口（每行一项）"><textarea id="visual-missing" value={draft.missing.join("\n")} onChange={event => setDraft({ ...draft, missing: event.target.value.split("\n") })}/></Field><Field id="visual-conflicts" label="来源冲突（每行一项）"><textarea id="visual-conflicts" value={draft.conflicts.join("\n")} onChange={event => setDraft({ ...draft, conflicts: event.target.value.split("\n") })}/></Field></div>
      </fieldset><div className="pd-actions"><button type="submit" className="button primary" disabled={changed || disabled("save_blueprint")}>保存蓝图新版本</button><button type="button" className="button secondary" disabled={busy} onClick={() => { if (window.confirm("放弃当前未保存的蓝图编辑？")) setEditing(false); }}>取消编辑</button></div>
    </form> : blueprint ? <>
      <BlueprintReview task={task} blueprint={blueprint} canReview={pendingReview && !disabled("confirm_blueprint")} busy={busy} comment={comment} setComment={setComment} onDecide={decide}/>
    </> : <EmptyState title="等待形成项目蓝图" detail="可通过模型生成，也可由项目负责人先人工整理目标、章节与条件。模型授权不足不会阻止人工准备。"/>}
  </section>;
}

function BlueprintReview({ task, blueprint, canReview, busy, comment, setComment, onDecide }: { task: DocumentTask; blueprint: NonNullable<DocumentTask["blueprint"]>; canReview: boolean; busy: boolean; comment: string; setComment: (value: string) => void; onDecide: (decision: "approve" | "revise") => Promise<void> }) {
  const readOnlyMessage = task.blueprint_approved ? "该蓝图已批准，审核内容只读。" : "当前用户无蓝图审批权限，内容仅供查看。";
  return <section className="pd-blueprint-inline" aria-label={`项目蓝图 v${blueprint.version}`}>
      <BlueprintReviewContent task={task} blueprint={blueprint}/>
      <div className="pd-blueprint-review">
        {canReview ? <>
          <Field id="visual-blueprint-comment" label="蓝图确认依据"><textarea id="visual-blueprint-comment" disabled={busy} value={comment} onChange={event => setComment(event.target.value)} rows={3} placeholder="请填写确认依据；如需调整，请描述修改要求。" maxLength={10000}/></Field>
          {task.blueprint_review.revisions_remaining === 0 && <p className="pd-muted">自动修改次数已用完，仍可批准当前蓝图。</p>}
          <div className="pd-form-footer pd-review-actions"><div className="pd-actions"><button className="button secondary" disabled={busy || !comment.trim() || task.blueprint_review.revisions_remaining === 0} onClick={() => void onDecide("revise")}>退回修改（剩余 {task.blueprint_review.revisions_remaining}/{task.blueprint_review.revision_limit} 次）</button><button className="button primary" disabled={busy || !comment.trim()} onClick={() => void onDecide("approve")}>批准蓝图并生成成果<ProductIcon name="arrow"/></button></div></div>
        </> : <p className="pd-blueprint-readonly" role="status">{readOnlyMessage}</p>}
      </div>
  </section>;
}

function BlueprintReviewContent({ task, blueprint }: { task: DocumentTask; blueprint: NonNullable<DocumentTask["blueprint"]> }) {
  const knowledge = task.knowledge || task.blueprint_knowledge || { status: "source_only_preview" as const, ragflow_used: false, source_count: 0, detail: "知识检索状态尚未由服务端返回。" };
  const verified = knowledge.status === "ready" && knowledge.ragflow_used;
  const unavailable = knowledge.status === "waiting_for_ragflow";
  return <>
    <div className="pd-blueprint-summary"><h4>项目概述</h4><p>{blueprintDisplayText(blueprint.payload.purpose)}</p><small>受众：{blueprintDisplayText(blueprint.payload.audience)}</small></div>
    <section className="pd-chapter-plan" aria-label="章节设计"><h4>章节设计 · {blueprint.payload.chapters.length} 个章节</h4><p className="pd-muted">计划字数按当前生成规则均分至各章，不是已生成字数；技术方案与可研分别计算。</p><ol>{blueprint.payload.chapters.map((chapter, index) => <li key={chapter.id}><header><h4>第 {index + 1} 章 · {chapter.title}</h4><div className="pd-chapter-budgets">{(['technical-solution', 'feasibility'] as const).map(family => <span key={family}>{family === 'technical-solution' ? '技术方案' : '可行性研究报告'}：{task.output_targets?.[family] !== undefined ? outputTargetLabel(Math.ceil(task.output_targets[family] / blueprint.payload.chapters.length)) : '字数目标未提供'}</span>)}</div></header><p className="pd-chapter-scope"><strong>主要内容</strong>{blueprintDisplayText(chapter.scope) || "尚未说明，请在修改蓝图中补充本章主要内容。"}</p><small>引用 {chapter.source_ids.length} 项资料</small></li>)}</ol></section>
    <section><h4>实施与审核条件</h4>{blueprint.payload.conditions.length ? blueprint.payload.conditions.map((condition, index) => <p className="pd-condition" key={index}><ProductIcon name="shield"/>{condition.text}<small>{({ human: "人工核对", program: "程序校验", model: "模型辅助" })[condition.type]}</small></p>) : <p className="pd-muted">暂无条件记录</p>}</section>
    <div className="pd-issue-grid"><section><strong>{task.input?.items.length || 0}<small>清单事实行</small></strong><p>来自项目输入与上传清单</p></section><section><strong>{blueprint.payload.conditions.length}<small>待核对条件</small></strong><p>审批不会移除原始条件</p></section><section><strong>{blueprint.payload.missing.filter(Boolean).length + blueprint.payload.conflicts.filter(Boolean).length}<small>缺口与冲突</small></strong><p>{[...blueprint.payload.missing, ...blueprint.payload.conflicts].filter(Boolean).join("；") || "暂未记录，确认前请核对资料完整性"}</p></section></div>
    <div className={`pd-feedback ${verified ? "success" : "pd-knowledge-preview"}`} role="status"><strong>{verified ? "知识库检索已完成" : unavailable ? "知识库暂不可用" : "仅使用项目资料"}</strong><p>{verified ? `已记录 ${knowledge.source_count} 项可追溯知识来源。` : unavailable ? "正式蓝图生成保持锁定；可先整理项目资料与人工蓝图。" : "本版本未执行知识库检索，未产生知识库命中记录。"}</p></div>
    <p className="pd-review-round"><strong>蓝图审核轮次</strong><span>已修改 {task.blueprint_review.revision_count} 次 · 最多 {task.blueprint_review.revision_limit} 次 · 剩余 {task.blueprint_review.revisions_remaining} 次</span></p>
  </>;
}
