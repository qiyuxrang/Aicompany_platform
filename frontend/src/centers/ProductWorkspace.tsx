import DocumentWorkspace from "../product/DocumentWorkspace";
import ProductDashboard from "../product/ProductDashboard";
import NewProductProject from "../product/NewProductProject";
import ProductProjects from "../product/ProductProjects";
import ProductProjectDetail from "../product/ProductProjectDetail";
import ProductTemplates from "../product/ProductTemplates";
import ProductKnowledge from "../product/ProductKnowledge";
import "../product/product-division.css";
import { CenterLink, SectionHeader, WorkspaceProps } from "./shared";

export default function ProductWorkspace({ section }: WorkspaceProps) {
  if (section === "documents") return <DocumentWorkspace />;
  const preview = window.location.pathname.startsWith("/preview/");
  if (section === "overview") return <ProductDashboard preview={preview}/>;
  if (preview) return <section className="pd-panel"><h2>产品事业部页面预览</h2><p>此页面不读取业务数据。平台管理员不会自动获得业务权限。</p><CenterLink href="/centers/product" className="button secondary">返回工作台预览</CenterLink></section>;
  if (section === "new") return <NewProductProject/>;
  const task = new URLSearchParams(window.location.search).get("task");
  if (section === "projects" && task) return <ProductProjectDetail key={task} id={task}/>;
  if (section === "projects" || section === "sources" || section === "outputs" || section === "history") return <ProductProjects key={`${section}-${window.location.search}`} view={section}/>;
  if (section === "templates") return <ProductTemplates/>;
  if (section === "knowledge") return <ProductKnowledge/>;

  return <>
    <SectionHeader eyebrow="产品事业部" title="产品工作台" description="设备清单和项目背景只提交一次，三个成果在同一任务内持续复用。" />
    <div className="center-feature-grid">
      <article className="center-feature-card">
        <span className="status success">统一业务入口</span>
        <h3>项目成果流水线</h3>
        <p>上传设备清单和项目背景，统一形成技术方案、可研报告和汇报 PPT；补充资料后继续沿用同一任务。</p>
        <CenterLink className="button primary" href="/centers/product/documents">进入流水线</CenterLink>
      </article>
    </div>
  </>;
}
