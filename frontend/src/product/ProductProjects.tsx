import { useState } from "react";
import { CenterLink } from "../centers/shared";
import { EmptyState, formatDate, goProduct, LoadState, ProductIcon, projectUrl, stageNames, useProductOverview } from "./workbench-shared";
import "./project-workbench.css";
import "./product-clean-layout.css";

const filters = [{ code: "all", label: "全部项目" }, { code: "active", label: "进行中" }, { code: "review", label: "待确认" }, { code: "completed", label: "已完成" }, { code: "completed_month", label: "本月完成" }];
const views = {
  projects: { title: "历史项目", description: "继续处理项目或查看成果。", action: "查看详情", tab: "blueprint" },
  sources: { title: "项目资料", description: "按项目查看上传资料。", action: "查看资料", tab: "sources" },
  outputs: { title: "文档成果", description: "查看当前成果和版本。", action: "查看成果", tab: "technical-solution" },
  history: { title: "版本记录", description: "追溯项目版本记录。", action: "查看版本", tab: "history" },
} as const;

const errorLabels: Record<string, string> = {
  model_authorization_required: "等待授权",
  revision_requested: "待修改",
  source_permission_changed: "权限需确认",
};

function ProjectStatus({ state, errorCode }: { state: string; errorCode?: string }) {
  const tone = state === "COMPLETED" ? "good" : ["FAILED", "WAITING_INPUT"].includes(state) ? "warning" : state === "CANCELLED" ? "neutral" : "info";
  const errorLabel = errorCode ? errorLabels[errorCode] || "请查看详情" : "";
  const label = state === "COMPLETED" ? "已完成" : state === "CANCELLED" ? "已取消" : "进行中";
  return <span className="pd-status-cell"><span className={`pd-badge ${tone}`}>{label}</span>{(errorLabel || state === "FAILED" || state === "WAITING_INPUT") && <small className="pd-status-note">{errorLabel || "处理已暂停，请查看详情"}</small>}</span>;
}

export default function ProductProjects({ view = "projects" }: { view?: keyof typeof views }) {
  const params = new URLSearchParams(window.location.search);
  const requestedFilter = params.get("filter") || "all";
  const filter = filters.some(item => item.code === requestedFilter) ? requestedFilter : "all";
  const query = params.get("q") || "";
  const page = Number(params.get("page")) || 1;
  const [search, setSearch] = useState(query);
  const requestQuery = new URLSearchParams({ filter, q: query, page: String(page), page_size: "12" }).toString();
  const { data, error, reload } = useProductOverview(requestQuery);
  const config = views[view];
  const returnTo = `${window.location.pathname}${window.location.search}`;
  const link = (nextFilter = filter, nextPage = 1, nextQuery = query) => `/centers/product/${view}?${new URLSearchParams({ filter: nextFilter, page: String(nextPage), q: nextQuery })}`;
  const projectLink = (id: string) => `${projectUrl(id, config.tab)}&returnTo=${encodeURIComponent(returnTo)}`;

  return <div className="pd-workspace pd-project-list pd-clean-layout">
    <header className="pd-page-title pd-project-list-header">
      <div><span className="pd-eyebrow">项目工作台</span><h1>{config.title}</h1><p>{config.description}</p></div>
      <CenterLink href="/centers/product/new" className="button primary"><ProductIcon name="plus"/>新建项目</CenterLink>
    </header>
    <section className="pd-project-index" aria-labelledby="project-index-title">
      <div className="pd-list-tools">
        <div><h3 id="project-index-title">项目清单</h3>{data && <span className="pd-result-count">{data.pagination.total} 个项目</span>}</div>
        <form className="pd-search" role="search" onSubmit={event => { event.preventDefault(); goProduct(link(filter, 1, search.trim())); }}>
          <ProductIcon name="search"/><input aria-label="搜索项目名称" value={search} onChange={event => setSearch(event.target.value)} maxLength={200} placeholder="搜索项目名称"/><button type="submit" className="text-button">搜索</button>
        </form>
      </div>
      <nav className="pd-filter-tabs" aria-label="项目状态筛选">{filters.map(item => <CenterLink key={item.code} href={link(item.code)} current={filter === item.code} className={filter === item.code ? "active" : ""}>{item.label}</CenterLink>)}</nav>
      {!data ? <LoadState error={error} reload={reload}/> : data.projects.length ? <>
        <div className="pd-table-wrap pd-project-table-wrap"><table className="pd-table pd-project-table"><thead><tr><th>项目名称</th><th>当前阶段</th><th>状态</th><th>项目所有者</th><th>最近更新</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{data.projects.map(task => <tr key={task.id}>
          <td data-label="项目名称"><CenterLink href={projectLink(task.id)} className="pd-table-project"><span className="pd-project-icon"><ProductIcon name="folder"/></span><strong>{task.title}</strong></CenterLink></td>
          <td data-label="当前阶段">{stageNames[task.stage] || "阶段未记录"}</td>
          <td data-label="状态"><ProjectStatus state={task.state} errorCode={task.error_code}/></td>
          <td data-label="项目所有者">{task.owner_name || "未提供"}{task.reviewer_name && <small>审核人：{task.reviewer_name}</small>}</td>
          <td data-label="最近更新">{formatDate(task.updated_at)}</td>
          <td data-label="操作"><CenterLink href={projectLink(task.id)} className="pd-table-action">{config.action}<ProductIcon name="arrow"/></CenterLink></td>
        </tr>)}</tbody></table></div>
        <div className="pd-pagination"><span>第 {page} / {data.pagination.pages} 页</span><div>{page > 1 && <CenterLink href={link(filter, page - 1)} className="button secondary">上一页</CenterLink>}{page < data.pagination.pages && <CenterLink href={link(filter, page + 1)} className="button secondary">下一页</CenterLink>}</div></div>
      </> : <EmptyState
        title={query || filter !== "all" ? "没有符合条件的项目" : "尚未创建项目"}
        detail={query || filter !== "all" ? "调整关键词或状态筛选后再试。" : "新建项目后即可开始蓝图与成果编制。"}
        create={!query && filter === "all"}
      />}
    </section>
  </div>;
}
