import { useEffect, useRef, useState } from 'react';
import { Batch, Jd, Result, get, mutate, upload, root, statusText, message } from './recruitment-api';

export default function RecruitmentScreening({ results = false }: { results?: boolean }) {
  const [batches, setBatches] = useState<Batch[]>([]), [jds, setJds] = useState<Jd[]>([]);
  const [selected, setSelected] = useState(new URLSearchParams(window.location.search).get('batch') || ''), [jd, setJd] = useState(''), [files, setFiles] = useState<File[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null), [rows, setRows] = useState<Result[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [sort, setSort] = useState('score'), [verdict, setVerdict] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const lock = useRef(false), createKey = useRef(crypto.randomUUID());
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const requestedBatch = new URLSearchParams(window.location.search).get('batch') || '';
  useEffect(() => { setSelected(requestedBatch); }, [requestedBatch]);
  async function reload() { setBatches(await get<Batch[]>('batches/')); }
  async function work(action: () => Promise<void>) {
    if (lock.current) return; lock.current = true; setBusy(true); setError('');
    try { await action(); } catch (e) { setError(message(e)); } finally { lock.current = false; setBusy(false); }
  }
  useEffect(() => { const c = new AbortController();
    Promise.all([get<Batch[]>('batches/', c.signal), get<Jd[]>('confirmed-jds/', c.signal)])
      .then(([bs, js]) => { setBatches(bs); setJds(js); })
      .catch(e => { if (!c.signal.aborted) setError(message(e)); });
    return () => c.abort();
  }, []);
  useEffect(() => {
    setBatch(null); setRows([]); setDetail(null); if (!selected) return;
    const c = new AbortController(); let timer: ReturnType<typeof setTimeout>; let stopped = false;
    const load = async () => {
      try {
        const next = await get<Batch>(`batches/${selected}/progress/`, c.signal);
        const list = results ? await get<Result[]>(`batches/${selected}/summary/?sort_by=${sort}&verdict=${verdict}`, c.signal) : [];
        if (stopped) return; setBatch(next); setRows(list);
        if (['queued', 'running'].includes(next.status)) timer = setTimeout(load, 3000);
      } catch (e) { if (!stopped) { setBatch(null); setRows([]); setError(message(e)); } }
    };
    void load(); return () => { stopped = true; c.abort(); clearTimeout(timer); };
  }, [selected, results, sort, verdict, batches]);
  const params = `sort_by=${sort}&verdict=${verdict}`;
  return <><header className="hr-title"><div><h1>{results ? '筛选结果' : '简历筛选'}</h1><p>{results ? '查看往期批次、逐条证据和版本记录。' : '选择正式 JD，批量上传简历，由后台逐份处理。'}</p></div></header>
    {error && <p role="alert" className="hr-error">{error}</p>}
    {!results && <section className="hr-card"><h2>创建筛选批次</h2><div className="hr-actions"><label>正式 JD<select value={jd} onChange={e => { setJd(e.target.value); createKey.current = crypto.randomUUID(); }}><option value="">请选择当前有效 JD</option>{jds.map(item => <option key={item.id} value={item.id}>JD v{item.version} · {item.body.slice(0, 50)}</option>)}</select></label>
      <button className="hr-primary" disabled={busy || !jd} onClick={() => void work(async () => {
        const next = await mutate<Batch>('batches/', { jd_version_id: jd }, 'POST', { 'Idempotency-Key': createKey.current });
        await reload(); setSelected(next.id); createKey.current = crypto.randomUUID();
      })}>创建批次</button></div></section>}
    <section className="hr-card"><div className="hr-card-head"><h2>{results ? '历史筛选记录' : '筛选批次'}</h2><button disabled={busy} onClick={() => void work(reload)}>刷新</button></div>
      <label>选择批次<select value={selected} onChange={e => { setSelected(e.target.value); setError(''); }}><option value="">请选择</option>{batches.map(item => <option key={item.id} value={item.id}>{item.position_name} · JD v{item.jd_version} · {new Date(item.updated_at).toLocaleString()} · {statusText(item.status)}</option>)}</select></label>
      {!batches.length && <p className="hr-muted">暂无筛选记录，请先创建批次。</p>}
      {batch && <><div className="hr-metrics"><span>总数 <b>{batch.total}</b></span><span>已完成 <b>{batch.completed}</b></span><span>失败 <b>{batch.failed}</b></span><span>{statusText(batch.status)}</span></div>
        <progress value={batch.progress} max={100} aria-label="批次处理进度" /><span> {batch.progress}%</span>
        {batch.stale && <p className="hr-warning">关联岗位需求已变化：本批次为历史结果，不代表当前要求。</p>}
        {!results && <><label className="hr-upload">选择简历（每次最多20份）<input type="file" multiple accept=".txt,.docx,.pdf" disabled={busy || batch.status !== 'pending'} onChange={e => setFiles(Array.from(e.target.files || []))} /></label>
          <p>{files.map(file => file.name).join('、') || '支持 TXT、DOCX、PDF；单份最大 2MiB。图片型 PDF 需平台视觉模型。'}</p>
          <div className="hr-actions"><button disabled={busy || !files.length || batch.status !== 'pending'} onClick={() => void work(async () => { await upload(batch, files); setFiles([]); await reload(); })}>上传简历</button>
          <button className="hr-primary" disabled={busy || !batch.total || batch.stale || batch.status !== 'pending'} onClick={() => void work(async () => { await mutate(`batches/${batch.id}/run/`, { expected_version: batch.version }); await reload(); })}>开始筛选</button>
          <button disabled={busy || !batch.failed || batch.stale || ['queued', 'running'].includes(batch.status)} onClick={() => void work(async () => { await mutate(`batches/${batch.id}/retry/`, { expected_version: batch.version }); await reload(); })}>重试失败项</button></div>
          <div className="hr-table-wrap"><table><thead><tr><th>简历</th><th>大小</th><th>状态</th><th>异常</th></tr></thead><tbody>{batch.artifacts?.map(item => <tr key={item.id}><td>{item.filename}</td><td>{Math.ceil(item.size / 1024)} KB</td><td>{statusText(item.processing_status)}</td><td>{item.error_code || '—'}</td></tr>)}</tbody></table></div></>}
        {results && <><div className="hr-actions"><label>排序<select value={sort} onChange={e => setSort(e.target.value)}><option value="score">辅助分</option><option value="hard_gap_count">硬条件缺口</option><option value="unknown_count">UNKNOWN 数量</option></select></label>
        <label>匹配状态<select value={verdict} onChange={e => setVerdict(e.target.value)}><option value="">全部</option>{['MATCH', 'PARTIAL', 'UNKNOWN', 'NOT_MATCH'].map(v => <option key={v}>{v}</option>)}</select></label>
        <a className="hr-outline" href={root + `batches/${batch.id}/export/?${params}`}>导出 CSV</a></div>
        <div className="hr-table-wrap"><table><thead><tr><th>简历</th><th>处理状态</th><th>辅助分</th><th>硬缺口</th><th>UNKNOWN</th><th>操作</th></tr></thead><tbody>{rows.map(item => <tr key={item.id}><td>{item.filename}</td><td>{statusText(item.processing_status)}</td><td>{item.score ?? '—'}</td><td>{item.hard_gap_count}</td><td>{item.unknown_count ?? '—'}</td><td><button onClick={() => void work(async () => { setDetail(null); const result = await get<Record<string, unknown>>(`resumes/${item.id}/`); if (selectedRef.current === selected) setDetail(result); })}>查看证据</button> <a href={root + `resumes/${item.id}/download/`}>下载原件</a></td></tr>)}</tbody></table></div>
        <p className="hr-muted">辅助匹配不是录用或淘汰决定；缺少证据显示 UNKNOWN。</p></>}
      </>}
    </section>
    {detail && <section className="hr-card"><h2>证据矩阵与解析记录</h2>
      <div className="hr-table-wrap"><table><thead><tr><th>岗位要求</th><th>判断</th><th>引用证据</th><th>来源位置</th></tr></thead><tbody>
        {((detail.match as { matrix?: Result['matrix'] })?.matrix || []).map(item => <tr key={item.id}><td>{item.text}</td><td>{item.verdict}</td><td style={{ whiteSpace: 'normal' }}>{item.evidence.map(e => e.quote).join('；') || '无有效证据'}</td><td>{item.evidence.map(e => e.locator).join('；')}</td></tr>)}
      </tbody></table></div><details><summary>查看结构化解析和来源记录</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details><button onClick={() => setDetail(null)}>关闭详情</button></section>}
  </>;
}
