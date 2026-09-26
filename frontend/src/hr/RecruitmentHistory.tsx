import { useEffect, useState } from 'react';
import { requirementLabels } from './RequirementFacts';
import { CenterLink } from '../centers/shared';
import { get, message, Requirement, RecruitmentHistoryData, retentionNotice, screeningCounts, statusText } from './recruitment-api';


const channels: Record<string, string> = { general: '通用版', boss: 'BOSS直聘', zhaopin: '智联招聘' };
function MessageBody({ content }: { content: string }) {
  try {
    const value: unknown = JSON.parse(content);
    if (value && typeof value === 'object' && !Array.isArray(value)) return <dl>{Object.entries(value).map(([key, text]) => <div key={key}><dt>{requirementLabels[key] || key}</dt><dd>{Array.isArray(text) ? text.join('、') : String(text ?? '未填写')}</dd></div>)}</dl>;
  } catch { /* Conversation prose is the normal case. */ }
  return <p>{content}</p>;
}
export default function RecruitmentHistory() {
  const requested = new URLSearchParams(window.location.search).get('task') || '';
  const [requests, setRequests] = useState<Requirement[]>([]), [selected, setSelected] = useState(requested);
  const [data, setData] = useState<RecruitmentHistoryData | null>(null), [error, setError] = useState('');
  const [loading, setLoading] = useState(false), [listLoading, setListLoading] = useState(true);
  useEffect(() => { setSelected(requested); }, [requested]);
  useEffect(() => {
    const c = new AbortController();
    get<Requirement[]>('requests/', c.signal).then(rows => { if (!c.signal.aborted) { setRequests(rows); setListLoading(false); } })
      .catch(e => { if (!c.signal.aborted) { setError(message(e)); setListLoading(false); } });
    return () => c.abort();
  }, []);
  useEffect(() => {
    setData(null); setError(''); if (!selected) { setLoading(false); return; }
    const c = new AbortController(); setLoading(true);
    get<RecruitmentHistoryData>(`requests/${selected}/history/`, c.signal).then(next => { if (!c.signal.aborted) setData(next); })
      .catch(e => { if (!c.signal.aborted) setError(message(e)); }).finally(() => { if (!c.signal.aborted) setLoading(false); });
    return () => c.abort();
  }, [selected]);
  return <><header className="hr-title"><div><h1>招聘历史</h1><p>按招聘需求回看对话、JD 版本和真实筛选结果。</p></div><CenterLink className="hr-outline" href="/centers/hr/job">返回招聘与 JD</CenterLink></header>
    <p className="hr-muted">{retentionNotice}</p>
    {error && <p role="alert" className="hr-error">{error}</p>}
    <section className="hr-card"><label>选择招聘记录<select value={selected} onChange={e => setSelected(e.target.value)}><option value="">请选择招聘需求</option>{requests.map(row => <option value={row.id} key={row.id}>{row.position_name || '未命名需求'} · {new Date(row.created_at || row.updated_at).toLocaleString()}</option>)}</select></label>
      {!loading && !listLoading && !requests.length && !error && <p>暂无保留期内的招聘记录。</p>}
      {(loading || listLoading) && <p role="status">正在加载招聘记录…</p>}
    </section>
    {data && <><section className="hr-card"><h2>{data.request.position_name || '未命名需求'}</h2><div className="hr-history-meta"><span>创建：{data.request.created_at ? new Date(data.request.created_at).toLocaleString() : '未记录'}</span><span>保留至：{data.request.expires_at ? new Date(data.request.expires_at).toLocaleString() : '自创建时间起15天'}</span></div><CenterLink href={`/centers/hr/job?task=${data.request.id}`}>继续处理此需求</CenterLink></section>
      <section className="hr-card"><h2>对话记录</h2>{!data.messages.length && <p>暂无对话记录。</p>}{data.messages.map(item => <article key={item.id} className="hr-history-message"><header><strong>{item.role === 'user' ? '你' : item.role === 'assistant' ? '招聘助手' : item.role}</strong> · {new Date(item.created_at).toLocaleString()} · 需求 v{item.input_version}</header><MessageBody content={item.content} /></article>)}</section>
      <section className="hr-card"><h2>JD 版本</h2>{!data.jd_versions.length && <p>暂无 JD 版本。</p>}{data.jd_versions.map(jd => <details key={jd.id}><summary>JD v{jd.version} · {channels[jd.channel] || jd.channel} · {jd.stale ? '已过期' : statusText(jd.state)}</summary><pre>{jd.body}</pre></details>)}</section>
      <section className="hr-card"><h2>筛选批次与结果</h2>{!data.batches.length && <p>暂无筛选批次。</p>}{data.batches.map(batch => {
        const counts = screeningCounts(batch);
        return <article key={batch.id}><h3>{batch.position_name || '筛选批次'} · JD v{batch.jd_version} · {statusText(batch.status)}</h3><p>已筛选 {counts.screened} · 待筛选 {counts.pending ?? '未记录'} · 预筛选 {counts.prescreened ?? '未记录'} · 失败 {batch.failed}</p>
          <div className="hr-table-wrap"><table><thead><tr><th>简历</th><th>处理状态</th><th>辅助分</th><th>硬条件缺口</th><th>未知项</th><th>结果证据</th></tr></thead><tbody>{batch.results.map(result => <tr key={result.id}><td>{result.filename}</td><td>{statusText(result.processing_status)}</td><td>{result.score ?? '—'}</td><td>{result.hard_gap_count ?? '—'}</td><td>{result.unknown_count ?? '—'}</td><td>{result.matrix?.length ? <details><summary>查看匹配依据</summary>{result.matrix.map(item => <p key={item.id}>{item.text}：{item.verdict}<br />{item.evidence.map(e => `${e.quote}（${e.locator}）`).join('；') || '无有效证据'}</p>)}</details> : result.error_code || '暂无结果证据'}</td></tr>)}</tbody></table></div>
          <CenterLink href={`/centers/hr/results?batch=${batch.id}`}>查看完整批次结果</CenterLink>
        </article>;
      })}<p className="hr-muted">预筛选仅指文件校验通过未启动模型；辅助分不代表录用或淘汰决定。</p></section>
    </>}
  </>;
}
