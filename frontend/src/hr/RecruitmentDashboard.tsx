import { useEffect, useState } from 'react';
import { CenterLink } from '../centers/shared';
import Icon from '../Icon';
import { get, Requirement, Batch, message, statusText } from './recruitment-api';

export default function RecruitmentDashboard() {
  const [data, setData] = useState<{ jobs: Requirement[]; batches: Batch[] } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const c = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try { const [jobs, batches] = await Promise.all([get<Requirement[]>('requests/', c.signal), get<Batch[]>('batches/', c.signal)]);
        if (!c.signal.aborted) { setData({ jobs, batches }); setError(''); }
      } catch (e) { if (!c.signal.aborted) { setData(null); setError(message(e)); } }
      finally { if (!c.signal.aborted) timer = setTimeout(load, 5000); }
    }
    void load(); return () => { c.abort(); clearTimeout(timer); };
  }, []);
  const pending = data?.jobs.filter(job => job.current_jd_id && job.current_jd_id !== job.official_jd_id) || [];
  const completed = data?.batches.filter(b => b.status === 'completed') || [];
  const failed = data?.batches.reduce((sum, b) => sum + b.failed, 0) || 0;
  return <><header className="hr-title"><div className="hr-heading-icon" aria-hidden="true">⌂</div><div><h1>人事工作台</h1><p>专注招聘与人才评估，让优秀的人才加入团队。</p></div><CenterLink href="/centers/hr/job" className="hr-primary">＋ 新建招聘需求</CenterLink></header>
    {error && <p role="alert" className="hr-error">读取任务失败：{error}</p>}
    {!data && !error && <div role="status" className="hr-card">正在加载任务进度…</div>}
    {data && <><section className="hr-card"><div className="hr-card-head"><h2>待办事项 <span className="hr-count red">{pending.length + failed}</span></h2><CenterLink href="/centers/hr/results">查看全部 ›</CenterLink></div>
      <div className="hr-todos">{[
        { color: 'orange', icon: '▤', badge: '待确认', title: pending[0]?.position_name || '暂无待确认 JD', desc: pending.length ? `${pending.length} 份 JD 草稿等待 HR 确认` : '新建招聘需求后开始编写', href: 'job', action: '去确认' },
        { color: 'green', icon: '✓', badge: '已完成', title: completed[0]?.position_name || '暂无已完成批次', desc: `${completed.length} 个批次已完成，可查看记录`, href: 'results', action: '查看结果' },
        { color: 'red', icon: '!', badge: '异常处理', title: `${failed} 份简历处理失败`, desc: failed ? '请查看原因后重试失败项' : '当前没有处理失败的简历', href: 'resumes', action: '去处理' },
        { color: 'purple', icon: '♙', badge: '后续接入', title: '转正问卷暂缓接入', desc: '旧转正记录保留，本期不发送钉钉问卷', href: 'probation', action: '查看历史' },
      ].map(card => <article className="hr-todo" key={card.href}><div className={`hr-todo-icon ${card.color}`}><Icon name={card.color === 'red' ? 'issues' : card.color === 'purple' ? 'people' : card.color === 'green' ? 'usage' : 'modules'} /></div><div><span className={`hr-badge ${card.color}`}>{card.badge}</span><h3>{card.title}</h3><p>{card.desc}</p></div><footer><span>{card.color === 'orange' && pending[0] ? new Date(pending[0].updated_at).toLocaleDateString() : card.color === 'green' && completed[0] ? new Date(completed[0].updated_at).toLocaleDateString() : '—'}</span><CenterLink className="hr-outline" href={`/centers/hr/${card.href}`}>{card.action}</CenterLink></footer></article>)}</div>
    </section>
    <div className="hr-dashboard-middle"><section className="hr-card"><div className="hr-card-head"><h2>招聘中的岗位 <span className="hr-count">{data.jobs.length}</span></h2><CenterLink href="/centers/hr/job">查看全部 ›</CenterLink></div>
      <div className="hr-table-wrap"><table><thead><tr><th>岗位名称</th><th>招聘人数</th><th>JD 状态</th><th>简历数</th><th>已完成</th><th>异常</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{data.jobs.slice(0, 5).map(job => {
        const batches = data.batches.filter(b => b.jd_version_id === job.official_jd_id);
        return <tr key={job.id}><td><strong>{job.position_name || '未命名岗位'}</strong></td><td>{job.headcount ?? '—'}</td><td><span className={`hr-badge ${job.official_jd_id ? 'green' : 'orange'}`}>{job.official_jd_stale ? '已过期' : job.official_jd_id ? '已确认版本' : job.current_jd_id ? '待确认' : '待生成'}</span></td><td>{batches.reduce((s, b) => s + b.total, 0)}</td><td>{batches.reduce((s, b) => s + b.completed, 0)}</td><td className="hr-danger">{batches.reduce((s, b) => s + b.failed, 0)}</td><td>{new Date(job.updated_at).toLocaleString()}</td><td><CenterLink className="hr-outline" href={`/centers/hr/job?task=${job.id}`}>进入岗位</CenterLink></td></tr>;
      })}</tbody></table>{!data.jobs.length && <p className="hr-empty">暂无招聘岗位，点击“新建招聘需求”开始。</p>}</div></section>
      <section className="hr-card"><div className="hr-card-head"><h2>转正工作流</h2></div><div className="hr-probation-placeholder"><span className="hr-todo-icon purple"><Icon name="people" /></span><div>360° 问卷与钉钉<p className="hr-muted">按当前范围延后实施</p></div></div><div className="hr-probation-placeholder"><span className="hr-todo-icon green"><Icon name="people" /></span><div>既有转正记录<p className="hr-muted">保留原始历史与权限</p></div></div><CenterLink className="hr-outline full" href="/centers/hr/probation">查看既有转正流程 →</CenterLink></section></div>
    <section className="hr-card"><div className="hr-card-head"><h2>最近筛选批次 <span className="hr-count">{data.batches.length}</span></h2><CenterLink href="/centers/hr/results">查看全部 ›</CenterLink></div><div className="hr-table-wrap"><table><thead><tr><th>批次名称</th><th>关联 JD</th><th>简历总数</th><th>已完成</th><th>失败</th><th>处理进度</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{data.batches.slice(0, 5).map((batch, index) => <tr key={batch.id}><td><strong>{batch.position_name} · 批次 {data.batches.length - index}</strong></td><td><span className="hr-count">JD v{batch.jd_version}</span></td><td>{batch.total}</td><td>{batch.completed}</td><td>{batch.failed}</td><td><progress value={batch.progress} max={100} /> {batch.progress}%</td><td><span className={`hr-badge ${batch.status === 'completed' ? 'green' : 'orange'}`}>{statusText(batch.status)}</span></td><td>{new Date(batch.updated_at).toLocaleString()}</td><td><CenterLink href={`/centers/hr/results?batch=${batch.id}`} className="hr-outline">查看结果</CenterLink></td></tr>)}</tbody></table>{!data.batches.length && <p className="hr-empty">暂无筛选批次。</p>}</div></section></>}
  </>;
}
