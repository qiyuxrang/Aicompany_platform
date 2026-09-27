import { useEffect, useRef, useState } from 'react';
import RequirementFacts from './RequirementFacts';
import { Batch, Jd, Result, get, mutate, uploadSequential, selectResumeFiles, screeningCounts, retentionNotice, uploadJd, IntakeResponse, root, statusText, message } from './recruitment-api';
import ModelSelector from '../ModelSelector';
import type { ModelSelection } from '../model-selection-api';

export default function RecruitmentScreening({ results = false }: { results?: boolean }) {
  const [batches, setBatches] = useState<Batch[]>([]), [jds, setJds] = useState<Jd[]>([]);
  const [selected, setSelected] = useState(new URLSearchParams(window.location.search).get('batch') || ''), [jd, setJd] = useState(''), [files, setFiles] = useState<File[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null), [rows, setRows] = useState<Result[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [sort, setSort] = useState('score'), [verdict, setVerdict] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [screeningModel, setScreeningModel] = useState<ModelSelection | null>(null);
  const [imported, setImported] = useState<IntakeResponse | null>(null), [importBody, setImportBody] = useState('');
  const lock = useRef(false), createKey = useRef(crypto.randomUUID());
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const requestedBatch = new URLSearchParams(window.location.search).get('batch') || '';
  useEffect(() => { setSelected(requestedBatch); setFiles([]); setImported(null); setNotice(''); }, [requestedBatch]);
  async function reload() { setBatches(await get<Batch[]>('batches/')); }
  async function work(action: () => Promise<void>, refreshProgress = true) {
    if (lock.current) return; lock.current = true; setBusy(true); setError('');
    try { await action(); } catch (e) { setError(message(e)); } finally { lock.current = false; setBusy(false); if (refreshProgress) setBatches(current => [...current]); }
  }
  useEffect(() => { const c = new AbortController();
    Promise.all([get<Batch[]>('batches/', c.signal), get<Jd[]>('confirmed-jds/', c.signal)])
      .then(([bs, js]) => { if (!c.signal.aborted) { setBatches(bs); setJds(js); } })
      .catch(e => { if (!c.signal.aborted) setError(message(e)); });
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (lock.current) return;
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
  function addFiles(incoming: File[]) {
    const next = selectResumeFiles(files, incoming); setFiles(next.files);
    setNotice(`已选 ${next.files.length} 份；跳过重复 ${next.duplicates} 份${next.rejected.length ? `；不支持、空文件或超过2MiB：${next.rejected.join('、')}` : ''}`);
  }
  async function runScreening() {
    let current = batch;
    if (!current) {
      current = await mutate<Batch>('batches/', { jd_version_id: jd, ...(screeningModel ? { model_selection: screeningModel } : {}) }, 'POST', { 'Idempotency-Key': createKey.current });
      setBatch(current); const created = current; setBatches(items => [created, ...items.filter(item => item.id !== created.id)]); setSelected(current.id); createKey.current = crypto.randomUUID();
    }
    if (files.length) {
      const queued = [...files];
      current = await uploadSequential(current, queued, (next, sent) => {
        setBatch(next); setFiles(queued.slice(sent)); setNotice(`已上传 ${sent}/${queued.length} 份，服务端按内容去重。`);
      });
    }
    setBatch(current);
    if (!current.total) throw new Error('请先选择至少一份有效简历。');
    await mutate(`batches/${current.id}/run/`, { expected_version: current.version });
    setNotice('筛选已提交，后台正在处理。'); await reload();
  }
  const displayedJd = batch ? (batch.jd_body || jds.find(item => item.id === batch.jd_version_id)?.body) : jds.find(item => item.id === jd)?.body;
  const counts = batch ? screeningCounts(batch) : null;
  const params = `sort_by=${sort}&verdict=${verdict}`;
  return <><header className="hr-title"><div><h1>{results ? '筛选结果' : '简历筛选'}</h1><p>{results ? '查看往期批次、逐条证据和版本记录。' : '选择正式 JD，批量上传简历，由后台逐份处理。'}</p></div></header>
    {error && <p role="alert" className="hr-error">{error}</p>}
    <p className="hr-muted">{retentionNotice}</p>
    {!results && <section className="hr-card" aria-busy={busy}><div className="hr-screening-layout">
      <div><h2>筛选依据 · JD</h2><label>正式 JD<select value={batch?.jd_version_id || jd} disabled={busy || !!selected || !!imported} onChange={e => { setJd(e.target.value); setImported(null); createKey.current = crypto.randomUUID(); }}><option value="">请选择当前有效 JD</option>{jds.map(item => <option key={item.id} value={item.id}>JD v{item.version} · {item.body.slice(0, 50)}</option>)}</select></label>
      <ModelSelector route="hr_match_summary" value={screeningModel} onChange={setScreeningModel} label="简历筛选模型" disabled={busy || !!selected} />
      <label>上传 JD 文件（TXT、DOCX、PDF）<input type="file" accept=".txt,.docx,.pdf" disabled={busy || !!selected} onChange={e => {
        if (imported && importBody !== imported.jd.body && !window.confirm('放弃未保存的 JD 修改并重新上传？')) { e.target.value = ''; return; }
        const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
        if (selectResumeFiles([], [file]).rejected.length) { setError('JD 须为非空 TXT、DOCX、PDF，且不超过2MiB。'); return; }
        void work(async () => { const result = await uploadJd(file); setImported(result); setImportBody(result.jd.body); setJd(''); setNotice('JD 已解析为草稿，请核对正文和提取要求后确认。'); });
      }} /></label>
      {imported && <div><label>上传 JD 草稿正文<textarea rows={12} disabled={busy} value={importBody} onChange={e => setImportBody(e.target.value)} /></label>
        <p className="hr-muted">文件导入不会直接变成正式 JD；确认的是当前已保存版本。年龄只作备注，不参与筛选。</p>
        {imported.jd.missing_items?.length ? <p className="hr-warning">待补充：{imported.jd.missing_items.map(item => item.reason).join('；')}</p> : null}
        <RequirementFacts requirements={imported.jd.requirements} /><div className="hr-actions"><button disabled={busy || !importBody.trim() || importBody === imported.jd.body} onClick={() => void work(async () => {
          const next = await mutate<Jd>(`requests/${imported.request.id}/jd-versions/`, { expected_version: imported.request.input_version, base_jd_id: imported.jd.id, body: importBody });
          setImported({ ...imported, jd: next }); setImportBody(next.body);
        })}>保存 JD 修改</button>
        <button disabled={busy || importBody !== imported.jd.body} onClick={() => void work(async () => {
          const next = await mutate<Jd>(`requests/${imported.request.id}/jd-versions/${imported.jd.id}/confirm/`, { expected_version: imported.request.input_version });
          setJds(current => [next, ...current.filter(item => item.request_id !== next.request_id)]); setJd(next.id); setImported(null); createKey.current = crypto.randomUUID(); setNotice('JD 已确认，可选择简历并进行筛选。');
        })}>确认上传 JD</button><button disabled={busy} onClick={() => { if (importBody !== imported.jd.body && !window.confirm('放弃未保存的 JD 修改？')) return; setImported(null); setNotice('已离开导入草稿，可在招聘历史中继续处理。'); }}>改选已有 JD</button></div>
      </div>}
      <article aria-label={imported ? "已保存 JD 草稿正文" : "筛选 JD 正文"} className="hr-jd-body">{(imported ? imported.jd.body : displayedJd) || '选择已确认通用 JD 后，这里显示完整正文；历史批次以其绑定版本为准。'}</article></div>
      <div><h2>简历操作</h2><label className="hr-upload">选择简历<input type="file" multiple accept=".txt,.docx,.pdf" disabled={busy || (!!selected && (!batch || batch.status !== 'pending' || batch.stale))} onChange={e => { addFiles(Array.from(e.target.files || [])); e.target.value = ''; }} /></label>
      <label>选择简历文件夹<input type="file" multiple {...{ webkitdirectory: '' }} disabled={busy || (!!selected && (!batch || batch.status !== 'pending' || batch.stale))} onChange={e => { addFiles(Array.from(e.target.files || [])); e.target.value = ''; }} /></label>
      <p className="hr-muted">文件夹包含浏览器递归返回的子目录文件。支持 TXT、DOCX、PDF，单份最大2MiB；每批最多200份不同内容，每组最多20份顺序上传，服务端按内容去重并校验批次上限。图片型 PDF 需平台视觉模型。</p>
      <ul className="hr-file-list">{files.map((file, i) => <li key={`${file.webkitRelativePath || file.name}-${i}`}>{file.webkitRelativePath || file.name}</li>)}</ul>
      <button disabled={busy || !files.length} onClick={() => { setFiles([]); setNotice(''); }}>清空待上传文件</button></div>
    </div><div className="hr-screening-run"><button className="hr-primary" disabled={busy || (!!selected && !batch) || (!batch && (!jd || !files.length)) || (!!batch && (batch.status !== 'pending' || batch.stale || (!batch.total && !files.length)))} onClick={() => void work(runScreening)}>{busy ? '正在提交…' : '进行筛选'}</button></div>
    {notice && <p role="status">{notice}</p>}</section>}
    <section className="hr-card"><div className="hr-card-head"><h2>{results ? '历史筛选记录' : '筛选批次'}</h2><button disabled={busy} onClick={() => void work(reload)}>刷新</button></div>
      <label>选择批次<select value={selected} disabled={busy} onChange={e => { setSelected(e.target.value); setBatch(null); setImported(null); setFiles([]); setError(''); setNotice(''); }}><option value="">新建筛选批次</option>{batches.map(item => <option key={item.id} value={item.id}>{item.position_name} · JD v{item.jd_version} · {new Date(item.updated_at).toLocaleString()} · {statusText(item.status)}</option>)}</select></label>
      {!batches.length && <p className="hr-muted">暂无筛选记录，请先创建批次。</p>}
      {batch && <><div className="hr-metrics"><span>总数 <b>{batch.total}</b></span><span>已筛选 <b>{counts?.screened}</b></span><span>待筛选 <b>{counts?.pending ?? '未记录'}</b></span><span>预筛选 <b>{counts?.prescreened ?? '未记录'}</b></span><span>失败 <b>{batch.failed}</b></span><span>{statusText(batch.status)}</span>{batch.model_selection?.model_name && <span>模型 <b>{batch.model_selection.model_name}</b></span>}</div>
        <progress value={batch.progress} max={100} aria-label="批次处理进度" /><span> {batch.progress}%</span>
        {batch.stale && <p className="hr-warning">关联岗位需求已变化：本批次为历史结果，不代表当前要求。</p>}
        <p className="hr-muted">已筛选：模型处理完成；待筛选：已排队或处理中；预筛选：文件接收校验通过、尚未启动模型。失败单独统计，重试排队后计入待筛选。{batch.expires_at && ` 保留至 ${new Date(batch.expires_at).toLocaleString()}。`}</p>
        {!results && <><div className="hr-actions"><button disabled={busy || !batch.failed || batch.stale || ['queued', 'running'].includes(batch.status)} onClick={() => void work(async () => { await mutate(`batches/${batch.id}/retry/`, { expected_version: batch.version }); await reload(); })}>重试失败项</button></div>
          <div className="hr-table-wrap"><table><thead><tr><th>简历</th><th>大小</th><th>状态</th><th>异常</th></tr></thead><tbody>{batch.artifacts?.map(item => <tr key={item.id}><td>{item.filename}</td><td>{Math.ceil(item.size / 1024)} KB</td><td>{statusText(item.processing_status)}</td><td>{item.error_code || '—'}</td></tr>)}</tbody></table></div></>}
        {results && <><div className="hr-actions"><label>排序<select value={sort} onChange={e => setSort(e.target.value)}><option value="score">辅助分</option><option value="hard_gap_count">硬条件缺口</option><option value="unknown_count">UNKNOWN 数量</option></select></label>
        <label>匹配状态<select value={verdict} onChange={e => setVerdict(e.target.value)}><option value="">全部</option>{['MATCH', 'PARTIAL', 'UNKNOWN', 'NOT_MATCH'].map(v => <option key={v}>{v}</option>)}</select></label>
        <a className="hr-outline" href={root + `batches/${batch.id}/export/?${params}`}>导出 CSV</a></div>
        <div className="hr-table-wrap"><table><thead><tr><th>简历</th><th>处理状态</th><th>辅助分</th><th>硬缺口</th><th>UNKNOWN</th><th>操作</th></tr></thead><tbody>{rows.map(item => <tr key={item.id}><td>{item.filename}</td><td>{statusText(item.processing_status)}</td><td>{item.score ?? '—'}</td><td>{item.hard_gap_count}</td><td>{item.unknown_count ?? '—'}</td><td><button onClick={() => void work(async () => { setDetail(null); const result = await get<Record<string, unknown>>(`resumes/${item.id}/`); if (selectedRef.current === selected) setDetail(result); }, false)}>查看证据</button> <a href={root + `resumes/${item.id}/download/`}>下载原件</a></td></tr>)}</tbody></table></div>
        <p className="hr-muted">辅助匹配不是录用或淘汰决定；缺少证据显示 UNKNOWN。</p></>}
      </>}
    </section>
    {detail && <section className="hr-card"><h2>证据矩阵与解析记录</h2>
      <div className="hr-table-wrap"><table><thead><tr><th>岗位要求</th><th>判断</th><th>引用证据</th><th>来源位置</th></tr></thead><tbody>
        {((detail.match as { matrix?: Result['matrix'] })?.matrix || []).map(item => <tr key={item.id}><td>{item.text}</td><td>{item.verdict}</td><td style={{ whiteSpace: 'normal' }}>{item.evidence.map(e => e.quote).join('；') || '无有效证据'}</td><td>{item.evidence.map(e => e.locator).join('；')}</td></tr>)}
      </tbody></table></div><details><summary>查看结构化解析和来源记录</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details><button onClick={() => setDetail(null)}>关闭详情</button></section>}
  </>;
}
