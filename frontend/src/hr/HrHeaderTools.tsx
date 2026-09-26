import { useEffect, useState } from 'react';
import { CenterLink } from '../centers/shared';
import { Batch, Requirement, get, message } from './recruitment-api';

export default function HrHeaderTools() {
  const [query, setQuery] = useState(''), [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<Requirement[]>([]), [batches, setBatches] = useState<Batch[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    const c = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const [requirements, batches] = await Promise.all([get<Requirement[]>('requests/', c.signal), get<Batch[]>('batches/', c.signal)]);
        if (!c.signal.aborted) { setJobs(requirements); setBatches(batches); setError(''); }
      } catch (e) { if (!c.signal.aborted) { setJobs([]); setBatches([]); setError(message(e)); } }
      finally { if (!c.signal.aborted) timer = setTimeout(load, 30000); }
    };
    void load(); return () => { c.abort(); clearTimeout(timer); };
  }, []);
  const pending = jobs.filter(row => row.current_jd_id && row.current_jd_id !== row.official_jd_id);
  const failures = batches.filter(row => row.failed > 0);
  const count = pending.length + failures.length;
  return <div className="hr-header-tools">
    <div className="hr-header-search"><label className="sr-only" htmlFor="hr-global-search">搜索招聘岗位</label>
      <input id="hr-global-search" value={query} placeholder="搜索…" onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') setQuery(''); }} />
      {query.trim() && <div className="hr-header-popover" aria-label="岗位搜索结果">
        {error ? <p role="alert">{error}</p> : jobs.filter(row => row.position_name.includes(query.trim())).length ? jobs.filter(row => row.position_name.includes(query.trim())).slice(0, 10).map(row =>
          <div key={row.id} onClick={() => setQuery('')}><CenterLink href={`/centers/hr/job?task=${row.id}`}>{row.position_name}</CenterLink></div>) : <p>没有匹配岗位</p>}
      </div>}
    </div>
    <div className="hr-header-notices"><button type="button" className="hr-bell" aria-label={`人事待办 ${count} 项`} aria-expanded={open} onClick={() => setOpen(!open)}>♧{count > 0 && <span>{count}</span>}</button>
      {open && <section className="hr-header-popover" aria-label="人事待办"><h2>待处理事项</h2>
        {error ? <p role="alert">{error}</p> : <>
          {pending.map(row => <div key={row.id} onClick={() => setOpen(false)}><CenterLink href={`/centers/hr/job?task=${row.id}`}>{row.position_name} · JD 待确认</CenterLink></div>)}
          {failures.map(row => <div key={row.id} onClick={() => setOpen(false)}><CenterLink href={`/centers/hr/resumes?batch=${row.id}`}>{row.position_name} · {row.failed} 份处理失败</CenterLink></div>)}
          {!count && <p>暂无待处理事项</p>}
        </>}
        <button type="button" onClick={() => setOpen(false)}>关闭</button>
      </section>}
    </div>
  </div>;
}
