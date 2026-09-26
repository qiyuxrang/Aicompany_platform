import { CenterLink } from "../centers/shared";
import { CompanyMark, DocumentSymbol, EmptyState, formatDate, LoadState, outputNames, ProductIcon, projectUrl, stageNames, StatusBadge, useProductOverview, type ProductIconName } from "./workbench-shared";

const steps: { title: string; detail: string; icon: ProductIconName }[] = [
  { title: "上传资料", detail: "整理项目依据", icon: "upload" }, { title: "事实与蓝图", detail: "厘清目标与边界", icon: "file" },
  { title: "人工确认", detail: "核对具体版本", icon: "shield" }, { title: "技术方案", detail: "编制实施方案", icon: "settings" },
  { title: "可研报告", detail: "论证可行性", icon: "file" }, { title: "汇报 PPT", detail: "从已审内容派生", icon: "layers" }, { title: "审核归档", detail: "保留来源与版本", icon: "folder" },
];
export default function ProductDashboard({ preview = false }: { preview?: boolean }) {
  const { data, error, reload } = useProductOverview("", !preview);
  const tiles: { key: "active" | "review" | "generation" | "completed_month"; title: string; icon: ProductIconName; tone: string; filter: string; hint: string }[] = [
    { key: "active", title: "进行中项目", icon: "folder", tone: "blue", filter: "active", hint: "未完成、未取消的项目" },
    { key: "review", title: "待审核项目", icon: "clock", tone: "orange", filter: "review", hint: "等待蓝图或成果人工审核" },
    { key: "generation", title: "编制中项目", icon: "file", tone: "green", filter: "generation", hint: "正文、检查或文档生成阶段" },
    { key: "completed_month", title: "本月完成", icon: "check", tone: "purple", filter: "completed_month", hint: "本月转为已完成的项目" },
  ];
  return <div className="pd-workspace">
    <section className="pd-hero">
      <div className="pd-hero-copy"><div className="pd-eyebrow"><span className="pd-live-dot" />项目协作 · 专业成果</div><h2>产品事业部工作台</h2><p className="pd-hero-subtitle">从项目资料，到有据可依的专业成果。</p><p>汇集项目事实与专业知识，协同完成技术方案、可研报告和汇报材料。</p>
        <div className="pd-actions"><CenterLink href="/centers/product/new" className="button primary"><ProductIcon name="plus" />新建项目</CenterLink><CenterLink href="/centers/product/sources" className="button secondary"><ProductIcon name="upload" />管理资料</CenterLink>{data?.recent_projects[0] && <CenterLink href={projectUrl(data.recent_projects[0].id)} className="pd-quiet-link"><ProductIcon name="clock" />继续最近项目</CenterLink>}</div>
      </div>
      <div className="pd-hero-art" aria-hidden="true"><div className="pd-orbit"/><div className="pd-paper paper-back"><span/><span/><span/></div><div className="pd-paper paper-front"><span className="pd-paper-label">PROJECT</span><b/><span/><span/><div className="pd-mini-chart"><i/><i/><i/><i/></div></div><span className="pd-art-chip"><ProductIcon name="shield"/>来源可追溯</span><span className="pd-art-mark"><CompanyMark/></span></div>
    </section>
    <div className="pd-stat-grid">{tiles.map(tile => <CenterLink key={tile.key} href={`/centers/product/projects?filter=${tile.filter}`} className="pd-stat" ><span className={`pd-stat-icon ${tile.tone}`}><ProductIcon name={tile.icon}/></span><div><span>{tile.title}</span><strong aria-label={`${tile.title}数量`}>{data ? data.metrics[tile.key] : "—"}<small>{data ? "个" : ""}</small></strong><small className="pd-stat-hint">{tile.hint}</small></div><span className="pd-chevron">›</span></CenterLink>)}</div>
    <section className="pd-panel pd-flow"><div className="pd-panel-heading"><h3>核心工作流程</h3><CenterLink href="/centers/product/templates">查看编制说明 <span aria-hidden="true">›</span></CenterLink></div><ol>{steps.map((step, index) => <li key={step.title}><span className={`pd-step-icon step-${index}`}><ProductIcon name={step.icon}/></span><strong>{step.title}</strong><small>{step.detail}</small>{index !== steps.length - 1 && <span className="pd-step-arrow" aria-hidden="true">→</span>}</li>)}</ol></section>
    {preview ? <div className="pd-feedback" role="note">管理预览不读取项目资料或业务指标。请使用已授权产品账号进入工作台。</div> : !data ? <LoadState error={error} reload={reload}/> : <>
      <div className="pd-dashboard-grid">
        <section className="pd-panel"><div className="pd-panel-heading"><h3>最近项目</h3><CenterLink href="/centers/product/projects">查看全部 ›</CenterLink></div>{data.recent_projects.length ? <div className="pd-recent-list">{data.recent_projects.map(task => <CenterLink href={projectUrl(task.id)} className="pd-recent-project" key={task.id}><span className="pd-project-icon"><ProductIcon name="folder"/></span><div><strong>{task.title}</strong><small>{formatDate(task.updated_at)} · {stageNames[task.stage] || "阶段待确认"}</small></div><StatusBadge task={task}/></CenterLink>)}</div> : <EmptyState title="从第一个项目开始" detail="创建项目，上传资料，逐步形成蓝图与成果。" create/>}</section>
        <section className="pd-panel"><div className="pd-panel-heading"><h3>待处理事项</h3><CenterLink href="/centers/product/projects?filter=review">查看待审核 ›</CenterLink></div>{data.todos.length ? <div className="pd-recent-list">{data.todos.map(task => <CenterLink href={projectUrl(task.id)} className="pd-todo" key={task.id}><span className={`pd-todo-dot ${task.state === "WAITING_REVIEW" ? "orange" : "red"}`}/><div><strong>{task.title}</strong><small>{task.state === "WAITING_REVIEW" ? `${stageNames[task.stage] || "项目"}待确认` : task.state === "FAILED" ? "执行未完成，请查看原因并重试" : "需要补充资料、授权或修改依据"}</small></div><span aria-hidden="true">›</span></CenterLink>)}</div> : <EmptyState title="暂无待处理事项" detail="需要补充或审核的项目会显示在这里。"/>}</section>
        <section className="pd-panel"><div className="pd-panel-heading"><h3>最近成果</h3><CenterLink href="/centers/product/outputs">查看成果 ›</CenterLink></div>{data.recent_outputs.length ? <div className="pd-recent-list">{data.recent_outputs.map(output => <CenterLink key={output.id} href={projectUrl(output.task_id, "outputs")} className="pd-recent-output"><DocumentSymbol family={output.family}/><div><strong>{output.title}</strong><small>{outputNames[output.family]} · v{output.version}</small></div><span className={`pd-badge ${output.review_status === "approved" ? "good" : "neutral"}`}>{output.review_status === "approved" ? "已批准" : output.current ? "待审核稿" : "历史版本"}</span></CenterLink>)}</div> : <EmptyState title="尚无成果文件" detail="生成的文档将按项目保存，草稿与正式成果分开标记。"/>}</section>
      </div>
      <div className="pd-dashboard-foot"><span>仅统计当前账号获授权的项目 · 更新于 {formatDate(data.as_of)}</span><button className="text-button" onClick={reload}>刷新工作台</button></div>
      {!data.capabilities.model_generation && <p className="pd-service-note"><ProductIcon name="shield"/>项目创建、资料整理和人工蓝图可用；AI 生成尚需模型与预算授权。</p>}
    </>}
  </div>;
}
