import { CenterLink } from "../centers/shared";
import { DocumentSymbol, EmptyState, formatDate, LoadState, outputNames, ProductIcon, projectUrl, stageNames, useProductOverview } from "./workbench-shared";
import type { TaskSummary } from "./product-api";
import "./product-home.css";

const pendingActionNames: Record<string, string> = {
  retrieve: "查看资料检索进度",
  knowledge: "查看知识检索进度",
  blueprint: "查看蓝图生成进度",
  write: "查看正文编制进度",
  render: "查看文档生成进度",
  candidate: "查看候选成果生成进度",
  three_drafts: "查看成果生成进度",
  generate_outputs: "查看成果生成进度",
  presentation: "查看汇报 PPT 生成进度",
};

function nextAction(task: TaskSummary) {
  if (task.state === "FAILED") return "查看失败原因";
  if (task.state === "CANCELLED") return "查看项目记录";
  if (task.state === "COMPLETED") return "查看项目成果";
  if (task.state === "WAITING_REVIEW") return task.stage === "BLUEPRINT" ? "查看待确认项目蓝图" : "查看待审核项目成果";
  if (["DRAFT", "WAITING_INPUT"].includes(task.state)) return "查看资料缺口";
  if (["QUEUED", "RUNNING"].includes(task.state) && task.pending_action && pendingActionNames[task.pending_action]) return pendingActionNames[task.pending_action];
  return "继续项目";
}

function ProjectStatus({ state }: Pick<TaskSummary, "state">) {
  if (state === "COMPLETED") return <span className="pd-badge good">已完成</span>;
  if (state === "CANCELLED") return <span className="pd-badge neutral">已取消</span>;
  if (state === "WAITING_REVIEW") return <span className="pd-badge warning">待确认</span>;
  if (["FAILED", "WAITING_INPUT"].includes(state)) return <span className="pd-badge warning">{state === "FAILED" ? "处理失败" : "待处理"}</span>;
  return <span className="pd-badge info">进行中</span>;
}

export default function ProductDashboard({ preview = false }: { preview?: boolean }) {
  const { data, error, reload } = useProductOverview("", !preview);
  const recent = data?.recent_projects[0];
  const counts = data ? [
    { label: "全部项目", value: data.metrics.all ?? data.pagination.total, filter: "all" },
    { label: "进行中", value: data.metrics.active, filter: "active" },
    { label: "待确认", value: data.metrics.review, filter: "review" },
    { label: "已完成", value: data.metrics.completed ?? "—", filter: "completed" },
    { label: "本月完成", value: data.metrics.completed_month, filter: "completed_month" },
  ] : [];

  return <div className="pd-workspace ph-workspace">
    <header className="ph-header">
      <h1>产品事业部工作台</h1>
      <div className="pd-actions"><CenterLink href="/centers/product/new" className="button primary"><ProductIcon name="upload"/>上传资料生成成果</CenterLink><CenterLink href="/centers/product/outputs" className="button secondary"><ProductIcon name="folder"/>查看历史成果</CenterLink></div>
    </header>

    {preview ? <div className="pd-feedback" role="note">请使用已授权产品账号查看项目。</div> : !data ? <LoadState error={error} reload={reload}/> : <>
      {recent ? <section className="ph-resume" aria-labelledby="resume-project-title">
        <div className="ph-resume-main">
          <div className="ph-section-label"><ProductIcon name="clock"/>最近更新</div>
          <div className="ph-resume-title"><div><h2 id="resume-project-title">{recent.title}</h2><p>{stageNames[recent.stage] || "阶段未记录"} · 更新于 {formatDate(recent.updated_at)}</p></div><ProjectStatus state={recent.state}/></div>
          <div className="ph-next-action"><span>下一步</span><strong>{nextAction(recent)}</strong></div>
        </div>
        <CenterLink href={projectUrl(recent.id)} className="button primary">继续处理<ProductIcon name="arrow"/></CenterLink>
      </section> : <section className="pd-panel"><h2 className="sr-only">最近项目</h2><EmptyState title="从第一个项目开始" detail="创建项目后，下一步任务会显示在这里。" create/></section>}

      <nav className="ph-counts" aria-label="项目状态筛选">
        {counts.map(item => <CenterLink key={item.filter} href={`/centers/product/projects?filter=${item.filter}`}><span>{item.label}</span><strong aria-label={`${item.label}项目数量`}>{item.value}</strong></CenterLink>)}
      </nav>

      <div className="ph-columns">
      <section className="ph-section" aria-labelledby="recent-projects-title">
        <div className="ph-section-heading"><div><span className="ph-section-label">项目</span><h2 id="recent-projects-title">最近项目</h2></div><CenterLink href="/centers/product/projects">查看历史项目</CenterLink></div>
        {data.recent_projects.length ? <div className="ph-project-list">{data.recent_projects.map(task => <CenterLink href={projectUrl(task.id)} className="ph-project-row" key={task.id}>
          <span className="ph-project-mark"><ProductIcon name="folder"/></span>
          <span className="ph-project-copy"><strong>{task.title}</strong><small>{formatDate(task.updated_at)} · {stageNames[task.stage] || "阶段未记录"}</small></span>
          <span className="ph-project-state"><ProjectStatus state={task.state}/></span>
          <span className="ph-project-next"><small>下一步</small><strong>{nextAction(task)}</strong></span>
          <ProductIcon name="arrow"/>
        </CenterLink>)}</div> : <EmptyState title="暂无最近项目" detail="上传项目资料后，可从这里继续处理。" create/>}
      </section>

      <section className="ph-section" aria-labelledby="todos-title">
        <div className="ph-section-heading"><div><span className="ph-section-label">待办</span><h2 id="todos-title">待处理事项</h2></div><CenterLink href="/centers/product/projects?filter=review">查看待确认</CenterLink></div>
        {data.todos.length ? <div className="ph-todo-list">{data.todos.map(task => <CenterLink href={projectUrl(task.id)} key={task.id}>
          <span className={`ph-status-dot ${task.state === "WAITING_REVIEW" ? "review" : "attention"}`} aria-hidden="true" />
          <span><strong>{task.title}</strong><small>{task.state === "WAITING_REVIEW" ? `${stageNames[task.stage] || "项目"}待确认` : task.state === "FAILED" ? "执行未完成，请查看原因并重试" : "需要补充资料、授权或修改依据"}</small></span>
          <span>{nextAction(task)}</span><ProductIcon name="arrow" />
        </CenterLink>)}</div> : <EmptyState title="暂无待处理事项" detail="需要补充或审核的项目会显示在这里。"/>}
      </section>
      </div>

      <section className="ph-section" aria-labelledby="recent-outputs-title">
        <div className="ph-section-heading"><div><span className="ph-section-label">成果</span><h2 id="recent-outputs-title">最近生成成果</h2></div><CenterLink href="/centers/product/outputs">查看历史成果</CenterLink></div>
        {data.recent_outputs.length ? <div className="ph-output-list">{data.recent_outputs.map(output => <CenterLink key={output.id} href={`/centers/product/outputs?q=${encodeURIComponent(output.title)}`}>
          <DocumentSymbol family={output.family}/><span><strong>{output.title}</strong><small>{outputNames[output.family]} · v{output.version} · {formatDate(output.created_at)}</small></span><span className={`pd-badge ${output.review_status === "approved" ? "good" : "neutral"}`}>{output.review_status === "approved" ? "已批准" : output.current ? "当前草稿" : "历史版本"}</span><ProductIcon name="arrow"/>
        </CenterLink>)}</div> : <div className="ph-inline-empty"><ProductIcon name="file"/><div><strong>尚无成果文件</strong><p>生成后的技术方案、可研报告和汇报材料会显示在这里。</p></div></div>}
      </section>

      <footer className="ph-footer"><span>仅显示当前账号获授权的项目 · 更新于 {formatDate(data.as_of)}</span><button className="text-button" onClick={reload}>刷新工作台</button></footer>
      {!data.capabilities.model_generation && <p className="pd-service-note"><ProductIcon name="shield"/>项目创建、资料整理和人工蓝图可用；AI 生成需要模型调用与资料外发授权。</p>}
    </>}
  </div>;
}
