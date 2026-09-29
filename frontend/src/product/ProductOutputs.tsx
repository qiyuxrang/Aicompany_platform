import { useEffect, useState } from "react";
import { CenterLink } from "../centers/shared";
import { getProductOutputHistory, type OutputFamily, type ProductOutputHistory } from "./product-api";
import { DocumentSymbol, EmptyState, formatDate, goProduct, LoadState, outputNames, productError, ProductIcon } from "./workbench-shared";
import "./project-workbench.css";
import "./product-records.css";
import "./product-clean-layout.css";

const families: { code: "all" | OutputFamily; label: string }[] = [
  { code: "all", label: "全部类型" },
  { code: "technical-solution", label: "技术方案" },
  { code: "feasibility", label: "可研报告" },
  { code: "presentation", label: "汇报 PPT" },
];

export default function ProductOutputs() {
  const params = new URLSearchParams(window.location.search);
  const requestedFamily = params.get("family") || "all";
  const family: "all" | OutputFamily = families.some(item => item.code === requestedFamily) ? requestedFamily as "all" | OutputFamily : "all";
  const query = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [search, setSearch] = useState(query);
  const [data, setData] = useState<ProductOutputHistory | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const requestQuery = new URLSearchParams({ q: query, family, page: String(page), page_size: "12" }).toString();
  const link = (nextFamily = family, nextPage = 1, nextQuery = query) => `/centers/product/outputs?${new URLSearchParams({ q: nextQuery, family: nextFamily, page: String(nextPage) })}`;
  useEffect(() => setSearch(query), [query]);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    void getProductOutputHistory(requestQuery, controller.signal).then(value => {
      if (!value || !Array.isArray(value.outputs) || !value.pagination) throw new Error("服务端成果数据不完整，请重新加载。");
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(productError(reason));
    });
    return () => controller.abort();
  }, [requestQuery, refresh]);

  return <div className="pd-workspace pd-project-list pd-clean-layout pr-output-history">
    <header className="pd-page-title pd-project-list-header">
      <div><span className="pd-eyebrow">成果归档</span><h1>文档成果</h1><p>按类型查看并下载历史版本。</p></div>
      <CenterLink href="/centers/product/new" className="button primary"><ProductIcon name="upload"/>上传资料</CenterLink>
    </header>

    <section className="pd-project-index" aria-labelledby="output-index-title">
      <div className="pd-list-tools">
        <div><h3 id="output-index-title">成果版本</h3>{data && <span className="pd-result-count">{data.pagination.total} 个版本</span>}</div>
        <form className="pd-search" role="search" onSubmit={event => { event.preventDefault(); goProduct(link(family, 1, search.trim())); }}>
          <ProductIcon name="search"/><input aria-label="搜索项目名称" value={search} onChange={event => setSearch(event.target.value)} maxLength={200} placeholder="搜索项目名称"/><button type="submit" className="text-button">搜索</button>
        </form>
      </div>
      <nav className="pd-filter-tabs" aria-label="成果类型筛选">{families.map(item => <CenterLink key={item.code} href={link(item.code)} current={family === item.code} className={family === item.code ? "active" : ""}>{item.label}</CenterLink>)}</nav>

      {!data ? <LoadState error={error} reload={() => setRefresh(value => value + 1)}/> : data.outputs.length ? <>
        <div className="pd-table-wrap pd-project-table-wrap"><table className="pd-table pd-project-table pr-output-table"><thead><tr><th>项目名称</th><th>成果类型</th><th>版本</th><th>生成时间</th><th>状态</th><th><span className="sr-only">下载</span></th></tr></thead><tbody>{data.outputs.map(output => <tr key={output.id}>
          <td data-label="项目名称"><strong className="pr-output-title">{output.title}</strong></td>
          <td data-label="成果类型"><span className="pr-family-cell"><DocumentSymbol family={output.family}/><span>{outputNames[output.family]}</span></span></td>
          <td data-label="版本"><span className="pr-version">v{output.version}</span></td>
          <td data-label="生成时间">{formatDate(output.created_at)}</td>
          <td data-label="状态"><span className={`pd-badge ${output.review_status === "approved" ? "good" : "neutral"}`}>{output.review_status === "approved" ? "已批准" : output.current ? "当前版本" : "历史版本"}</span></td>
          <td data-label="下载"><a href={output.download_url} className="button secondary pr-download" download aria-label={`下载${output.title} ${outputNames[output.family]} v${output.version}`}><ProductIcon name="download"/>下载</a></td>
        </tr>)}</tbody></table></div>
        <div className="pd-pagination"><span>第 {data.pagination.page} / {data.pagination.pages} 页</span><div>{data.pagination.page > 1 && <CenterLink href={link(family, data.pagination.page - 1)} className="button secondary">上一页</CenterLink>}{data.pagination.page < data.pagination.pages && <CenterLink href={link(family, data.pagination.page + 1)} className="button secondary">下一页</CenterLink>}</div></div>
      </> : <EmptyState
        title={query || family !== "all" ? "没有符合条件的成果" : "尚无历史成果"}
        detail={query || family !== "all" ? "调整项目名称或成果类型后再试。" : "上传项目资料并生成成果后，各版本会统一归档在这里。"}
      />}
    </section>
  </div>;
}
