import { useState } from "react";
import { CenterLink } from "../centers/shared";
import { EmptyState, formatDate, goProduct, LoadState, ProductIcon, projectUrl, stageNames, StatusBadge, useProductOverview } from "./workbench-shared";

const filters = [{ code: "all", label: "全部项目" }, { code: "active", label: "进行中" }, { code: "review", label: "待确认" }, { code: "generation", label: "编制中" }, { code: "attention", label: "需处理" }, { code: "completed", label: "已完成" }, { code: "completed_month", label: "本月完成" }];
const views = { projects: { title: "我的项目", description: "从资料到成果，在同一个项目中持续推进。", action: "进入项目", tab: "overview" }, sources: { title: "项目资料", description: "资料按项目归集，保留原文件、解析结果和来源。选择项目查看或补充资料。", action: "查看资料", tab: "sources" }, outputs: { title: "文档成果", description: "技术方案、可研报告与汇报 PPT 按项目管理，当前成果和历史版本分别查看。", action: "查看成果", tab: "outputs" }, history: { title: "版本记录", description: "每一次输入、蓝图、生成和审核均保留在所属项目中。选择项目追溯记录。", action: "查看版本", tab: "history" } };
export default function ProductProjects({ view = "projects" }: { view?: keyof typeof views }) {
  const params = new URLSearchParams(window.location.search);
  const filter = params.get("filter") || "all";
  const query = params.get("q") || "";
  const page = Number(params.get("page")) || 1;
  const [search, setSearch] = useState(query);
  const requestQuery = new URLSearchParams({ filter, q: query, page: String(page), page_size: "12" }).toString();
  const { data, error, reload } = useProductOverview(requestQuery);
  const config = views[view];
  const link = (nextFilter = filter, nextPage = 1, nextQuery = query) => `/centers/product/${view}?${new URLSearchParams({ filter: nextFilter, page: String(nextPage), q: nextQuery })}`;
  return <div className="pd-workspace"><div className="pd-page-title"><div><h2>{config.title}</h2><p>{config.description}</p></div><CenterLink href="/centers/product/new" className="button primary"><ProductIcon name="plus"/>新建项目</CenterLink></div>
    <section className="pd-panel"><div className="pd-list-tools"><nav className="pd-filter-tabs" aria-label="项目状态筛选">{filters.map(item => <CenterLink key={item.code} href={link(item.code)} current={filter === item.code} className={filter === item.code ? "active" : ""}>{item.label}</CenterLink>)}</nav><form className="pd-search" onSubmit={event => { event.preventDefault(); goProduct(link(filter, 1, search.trim())); }}><ProductIcon name="search"/><input aria-label="搜索项目名称" value={search} onChange={event => setSearch(event.target.value)} maxLength={200} placeholder="搜索项目名称…"/><button type="submit" className="text-button">搜索</button></form></div>
      {!data ? <LoadState error={error} reload={reload}/> : data.projects.length ? <><div className="pd-table-wrap"><table className="pd-table"><thead><tr><th>项目名称</th><th>当前阶段</th><th>状态</th><th>项目所有者</th><th>最近更新</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{data.projects.map(task => <tr key={task.id}><td><CenterLink href={projectUrl(task.id, config.tab)} className="pd-table-project"><span className="pd-project-icon"><ProductIcon name="folder"/></span><strong>{task.title}</strong></CenterLink></td><td>{stageNames[task.stage] || "阶段待确认"}</td><td><StatusBadge task={task}/></td><td>{task.owner_name || "未提供"}{task.reviewer_name && <small>历史审核人：{task.reviewer_name}</small>}</td><td>{formatDate(task.updated_at)}</td><td><CenterLink href={projectUrl(task.id, config.tab)} className="pd-table-action">{config.action} ›</CenterLink></td></tr>)}</tbody></table></div><div className="pd-pagination"><span>共 {data.pagination.total} 个项目 · 第 {page} / {data.pagination.pages} 页</span><div>{page > 1 && <CenterLink href={link(filter, page - 1)} className="button secondary">上一页</CenterLink>}{page < data.pagination.pages && <CenterLink href={link(filter, page + 1)} className="button secondary">下一页</CenterLink>}</div></div></> : <EmptyState title={query || filter !== "all" ? "没有符合条件的项目" : "尚未创建项目"} detail={query || filter !== "all" ? "调整关键词或状态筛选后再试。" : "新建项目后，资料、蓝图和文档会归集在一起。"} create={!query && filter === "all"}/>}
    </section>
  </div>;
}
