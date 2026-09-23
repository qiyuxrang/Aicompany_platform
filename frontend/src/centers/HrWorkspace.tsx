import JobWorkspace from "../hr/JobWorkspace";
import ProbationWorkspace from "../hr/ProbationWorkspace";
import type { CurrentUser } from "../api";
import { CenterLink, SectionHeader, type WorkspaceProps } from "./shared";

const features = [
  { section: "profile", title: "岗位需求与人才画像", description: "持久保存岗位目标、职责与任职要求。" },
  { section: "job", title: "标准岗位说明", description: "缺项检查、确定性草稿、人工修订与 HR 确认。" },
  { section: "channels", title: "招聘平台版本", description: "外部招聘平台尚未接入，仅展示边界。" },
  { section: "resumes", title: "简历整理与筛选", description: "等待 D-01 数据许可与 D-04 评分基线。" },
  { section: "probation", title: "转正工作流", description: "确定性人工状态机，不依赖 AI。" },
] as const;

function BlockedPage({ kind }: { kind: "channels" | "resumes" }) {
  const resumes = kind === "resumes";
  return <>
    <SectionHeader title={resumes ? "简历整理与筛选" : "招聘平台版本"} description={resumes ? "正式简历处理尚未获数据与评分授权。" : "外部招聘平台接口尚未接入。"} />
    <section className="center-panel" aria-label={resumes ? "简历处理阻断状态" : "招聘平台接入状态"}>
      <span className="status warning">{resumes ? "D-01 / D-04 待批准" : "外部接入待确认"}</span>
      <h3>当前不提供业务操作</h3>
      <p>{resumes
        ? "未确认简历来源许可、保存删除边界和正式评分基线前，不接收、不读取、不上传简历，也不产生评分或录用结论。"
        : "岗位正文以已确认的正式 JD 为准；本页不会发布、同步或模拟任何招聘平台结果。"}</p>
    </section>
  </>;
}

export default function HrWorkspace({ section, user }: WorkspaceProps & { user: CurrentUser }) {
  if (window.location.pathname.startsWith("/preview/")) return <>
    <SectionHeader title="人事工作台预览" description="管理预览不读取岗位、JD、简历或转正数据，也不授予人事权限。" />
    <section className="center-panel" aria-label="人事预览边界"><span className="status muted">仅页面预览</span><p>请从已授权的人事工作台进入真实流程。</p></section>
  </>;
  const isHr = user.roles.some(role => role.code === "hr");
  if (!isHr && section !== "overview" && section !== "probation") return <>
    <SectionHeader title="无权访问" description="用人经理权限仅用于处理分配给自己的转正事项。" />
    <section className="center-panel" role="alert"><p>当前账号没有 HR 岗位、JD、招聘渠道或简历权限。</p></section>
  </>;
  if (section === "profile" || section === "job") return <JobWorkspace mode={section} />;
  if (section === "probation") return <ProbationWorkspace canManage={isHr} />;
  if (section === "channels" || section === "resumes") return <BlockedPage kind={section} />;

  return <>
    <SectionHeader title="人事工作台" description="JD 与转正使用服务端持久流程；简历与外部招聘平台保持明确阻断。" />
    <div className="center-feature-grid">
      {features.filter(feature => isHr || feature.section === "probation").map(feature => <CenterLink key={feature.section} href={`/centers/hr/${feature.section}`} className="center-feature-card"><h3>{feature.title}</h3><p>{feature.description}</p></CenterLink>)}
    </div>
    <section className="center-panel" aria-label="人事流程范围"><h3>当前可用范围</h3><ul className="center-checklist">{isHr && <li>JD：服务端保存、缺项检查、确定性草稿、人工修订、正式确认。</li>}<li>转正：{isHr ? "draft → collecting → manager_pending → hr_pending → archived。" : "仅查看分配给自己的事项并执行主管审批。"}</li>{isHr && <li>简历：D-01 与 D-04 未批准，不接收文件。</li>}</ul></section>
  </>;
}
