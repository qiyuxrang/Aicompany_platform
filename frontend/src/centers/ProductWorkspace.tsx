import DocumentWorkspace from "../product/DocumentWorkspace";
import { CenterLink, SectionHeader, WorkspaceProps } from "./shared";

export default function ProductWorkspace({ section }: WorkspaceProps) {
  if (section === "documents") return <DocumentWorkspace />;

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
