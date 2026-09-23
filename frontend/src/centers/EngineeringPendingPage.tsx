import { CenterLink, SectionHeader, type WorkspaceProps } from "./shared";

const sectionNames: Record<string, string> = {
  overview: "工程部工作台",
  estimate: "成本测算",
  quota: "套用定额",
};

export default function EngineeringPendingPage({ section }: WorkspaceProps) {
  const title = sectionNames[section] ?? "工程部工作台";
  return <>
    <SectionHeader title={title} description="工程部业务仍在独立开发，本平台当前不接收清单、定额或价格数据。" />
    <section className="center-panel" aria-label="工程部接入状态">
      <span className="status warning">开发中 · 已隔离</span>
      <h3>暂不提供业务操作</h3>
      <p>这里仅保留后续接入位置，不创建内存记录，不调用工程服务，也不产生测算、定额推荐或正式成果。</p>
      {section !== "overview" && <CenterLink className="button secondary" href="/centers/cost">返回工程部概览</CenterLink>}
    </section>
  </>;
}
