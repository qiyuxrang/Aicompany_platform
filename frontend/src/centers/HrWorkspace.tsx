import ProbationWorkspace from '../hr/ProbationWorkspace';
import RecruitmentDashboard from '../hr/RecruitmentDashboard';
import RecruitmentHistory from '../hr/RecruitmentHistory';
import RecruitmentJobs from '../hr/RecruitmentJobs';
import RecruitmentScreening from '../hr/RecruitmentScreening';
import type { CurrentUser } from '../api';
import { SectionHeader, type WorkspaceProps } from './shared';
import '../hr/recruitment.css';

export default function HrWorkspace({ section, user }: WorkspaceProps & { user: CurrentUser }) {
  if (window.location.pathname.startsWith('/preview/')) return <section className="hr-card">
    <h2>人事工作台预览</h2><p>仅页面预览；不读取招聘、简历或转正业务数据，也不授予人事权限。</p>
  </section>;
  const isHr = user.roles.some(role => role.code === 'hr');
  if (!isHr && section !== 'probation') return <>
    <SectionHeader title="无权访问" description="用人经理仅可处理分配给自己的既有转正事项。" />
    <p role="alert">当前账号没有 HR 岗位、JD、招聘渠道或简历权限。</p>
  </>;
  if (section === 'probation') return <><p className="hr-warning">360° 问卷及钉钉接入已按要求延后；以下为既有转正流程。</p><ProbationWorkspace canManage={isHr} /></>;
  if (section === 'history') return <><RecruitmentHistory key="history" /><details className="hr-card"><summary>旧版岗位记录</summary><RecruitmentJobs key="legacy-history" legacy /></details></>;
  if (['job', 'profile', 'channels'].includes(section)) return <RecruitmentJobs key="jobs" />;
  if (section === 'resumes') return <RecruitmentScreening key="screening" />;
  if (section === 'results') return <RecruitmentScreening key="results" results />;
  return <RecruitmentDashboard />;
}
