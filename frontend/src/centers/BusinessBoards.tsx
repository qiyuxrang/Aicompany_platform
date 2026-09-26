import { FormEvent, useEffect, useRef, useState } from 'react';
import { apiRequest, isApiError } from '../api';
import './business-boards.css';

type Department = 'engineering' | 'finance' | 'presales';
type Board = {
  department: Department; title: string; available: boolean; snapshot_id: string;
  fields: Record<string, string>; statuses: string[];
  metrics: { key: string; label: string; value: number | string | null; unit: string }[];
  distribution: { label: string; count: number }[]; records: Record<string, string>[];
  total: number; filtered_count: number; scope: string; currency: string;
  source: { name: string; as_of: string; imported_at: string; kind: string } | null;
};
const departments: [Department, string][] = [['engineering', '工程部看板'], ['finance', '财务部看板'], ['presales', '售前部门看板']];
const explanations: Record<Department, string> = {
  engineering: '交付中包含待开工、实施中、待验收和整改中；交付后包含已验收和质保中。逾期按台账截止日期判断。',
  finance: '金额统一为人民币元。待收余额＝合同金额－已收金额；逾期待收按台账截止日期与应收日期比较，不等同于会计确认的应收账款。',
  presales: '跟进中不含已赢单与已丢单；预计金额取台账原值，不按阶段乘以推测成交概率。',
};
function validBoard(value: unknown): value is Board {
  if (!value || typeof value !== 'object') return false;
  const b = value as Board;
  return typeof b.available === 'boolean' && typeof b.snapshot_id === 'string' && typeof b.title === 'string'
    && !!b.fields && typeof b.fields === 'object' && Object.values(b.fields).every(x => typeof x === 'string')
    && Array.isArray(b.statuses) && b.statuses.every(x => typeof x === 'string')
    && Number.isInteger(b.total) && b.total >= 0 && Number.isInteger(b.filtered_count) && b.filtered_count >= 0
    && Array.isArray(b.records) && b.records.every(x => x && typeof x === 'object' && Object.values(x).every(v => typeof v === 'string'))
    && Array.isArray(b.metrics) && b.metrics.every(x => x && typeof x.label === 'string' && typeof x.key === 'string' && typeof x.unit === 'string'
      && (x.value === null || typeof x.value === 'string' && /^\d+(\.\d{1,2})?$/.test(x.value) || typeof x.value === 'number' && Number.isFinite(x.value)))
    && Array.isArray(b.distribution) && b.distribution.every(x => x && typeof x.label === 'string' && Number.isInteger(x.count) && x.count >= 0)
    && (b.source === null || !!b.source && typeof b.source.name === 'string' && typeof b.source.as_of === 'string' && Number.isFinite(Date.parse(b.source.imported_at)));
}
function display(value: number | string | null, unit: string) {
  if (value === null) return '—';
  // Keep decimal money exact even when many large project amounts are aggregated.
  const [whole, fraction = ''] = String(value).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return unit === '元' ? `${grouped}.${fraction.padEnd(2, '0')}` : grouped;
}
function localToday() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export default function BusinessBoards({ initial = 'engineering', preview = false }: { initial?: Department; preview?: boolean }) {
  const [department, setDepartment] = useState<Department>(initial);
  const [data, setData] = useState<Board | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false), [importing, setImporting] = useState(false);
  const [search, setSearch] = useState(''), [status, setStatus] = useState('');
  const [filter, setFilter] = useState({ q: '', status: '' }), [refresh, setRefresh] = useState(0);
  const [file, setFile] = useState<File | null>(null), [asOf, setAsOf] = useState(localToday);
  const requestVersion = useRef(0), fileInput = useRef<HTMLInputElement>(null), importLock = useRef(false);
  useEffect(() => { setDepartment(initial); }, [initial]);
  useEffect(() => {
    const version = ++requestVersion.current;
    if (preview) { setData(null); return; }
    const controller = new AbortController();
    setData(null); setError(''); setLoading(true);
    const params = new URLSearchParams(filter);
    apiRequest<unknown>(`/api/business/boards/${department}/?${params}`, { signal: controller.signal }).then(result => {
      if (!validBoard(result) || result.department !== department) throw new Error('看板数据格式无效，请稍后重试。');
      if (!controller.signal.aborted && requestVersion.current === version) setData(result);
    }).catch(e => { if (!controller.signal.aborted && requestVersion.current === version) setError(isApiError(e) ? e.message : e instanceof Error ? e.message : '无法读取看板。'); })
      .finally(() => { if (!controller.signal.aborted && requestVersion.current === version) setLoading(false); });
    return () => { controller.abort(); requestVersion.current += 1; };
  }, [department, filter, refresh, preview]);
  const choose = (next: Department) => {
    setDepartment(next); setSearch(''); setStatus(''); setFilter({ q: '', status: '' }); setFile(null); setNotice('');
    if (fileInput.current) fileInput.current.value = '';
  };
  async function importSnapshot(event: FormEvent) {
    event.preventDefault();
    if (!file || !data || importLock.current || preview) return;
    importLock.current = true; setImporting(true); setError(''); setNotice('');
    const version = requestVersion.current;
    const body = new FormData(); body.append('file', file); body.append('as_of', asOf); body.append('expected_snapshot_id', data.snapshot_id);
    try {
      const result = await apiRequest<unknown>(`/api/business/boards/${department}/`, { method: 'POST', body });
      if (!validBoard(result) || result.department !== department) throw new Error('看板返回格式无效，请刷新核对。');
      if (requestVersion.current !== version) return;
      setFile(null); if (fileInput.current) fileInput.current.value = '';
      setSearch(''); setStatus(''); setFilter({ q: '', status: '' });
      setNotice(`已导入 ${result.total} 条台账记录。`); setRefresh(x => x + 1);
    } catch (e) {
      if (requestVersion.current === version) setError(isApiError(e) ? e.message : e instanceof Error ? e.message : '导入未完成。');
    } finally { importLock.current = false; setImporting(false); }
  }
  return <section className="business-bi" aria-label="企业台账 BI 看板">
    <div className="business-bi-head"><div><p className="eyebrow">企业台账</p><h2>经营数据，一处查看</h2><p>按部门查看指标、状态分布和项目明细。</p></div>
      {!preview && <button className="button secondary" disabled={loading || importing} onClick={() => setRefresh(x => x + 1)}>刷新看板</button>}</div>
    <div role="tablist" aria-label="部门看板" className="business-tabs">{departments.map(([key, label]) => <button key={key} role="tab" aria-selected={department === key} disabled={importing} onClick={() => choose(key)}>{label}</button>)}</div>
    {preview ? <div className="center-empty"><h3>看板预览</h3><p>包含工程、财务和售前三类看板。预览不读取业务数据；请使用已授权的总经理账号进入。</p></div> : <>
      {loading && <p role="status">正在读取台账…</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {notice && <p className="notice info" role="status">{notice}</p>}
      {data && <div role="tabpanel" aria-label={data.title}>
        <div className="business-metrics">{data.metrics.map(item => <article key={item.key}><span>{item.label}</span><strong>{display(item.value, item.unit)}<small>{item.unit}</small></strong></article>)}</div>
        {data.source ? <p className="business-source">来源：{data.source.name} · 台账截止 {data.source.as_of} · 导入于 {new Date(data.source.imported_at).toLocaleString('zh-CN')}</p>
          : <div className="center-empty"><h3>尚未导入{data.title.replace('看板', '')}台账</h3><p>先下载模板、填写实际台账并导入。未提供的数据以“—”展示，不会填入样例数字。</p></div>}
        <form className="business-filters" onSubmit={e => { e.preventDefault(); setFilter({ q: search, status }); }}>
          <label>搜索项目<input placeholder="项目编号、名称或负责人" value={search} maxLength={100} onChange={e => setSearch(e.target.value)} /></label>
          {!!data.statuses.length && <label>状态<select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{data.statuses.map(x => <option key={x}>{x}</option>)}</select></label>}
          <button className="button secondary" disabled={loading || importing}>查询</button>
        </form>
        {data.available && <div className="business-detail-grid">
          <section className="business-distribution"><h3>状态分布</h3>{data.distribution.map(item => <div className="business-bar" key={item.label}>
            <div><span>{item.label}</span><strong>{item.count}</strong></div><meter min={0} max={Math.max(1, data.filtered_count)} value={item.count} aria-label={`${item.label} ${item.count} 项`} /></div>)}</section>
          <section className="business-records"><h3>项目明细 <small>{data.filtered_count} / {data.total} 条</small></h3><div className="business-table-scroll"><table><thead><tr>{Object.values(data.fields).map(label => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>{data.records.map(row => <tr key={row.project_id}>{Object.keys(data.fields).map(key => <td key={key}>{row[key] || '—'}{key === 'progress' ? '%' : ''}</td>)}</tr>)}</tbody></table></div>
            {!data.records.length && <p>没有符合筛选条件的记录。</p>}</section>
        </div>}
        <p className="business-definition">{explanations[department]}</p>
        <details className="business-import" open={!data.available}><summary>导入台账快照</summary><p>CSV 为 UTF-8 编码，最多 2,000 行、2 MiB；一次导入该部门的完整台账。导入不修改原业务系统。金额列均为人民币元。</p>
          <a href={`/api/business/boards/${department}/template/`}>下载{data.title}模板</a>
          {!!data.statuses.length && <p>可用状态：{data.statuses.join('、')}。</p>}
          <form onSubmit={e => void importSnapshot(e)}><label>台账 CSV<input ref={fileInput} type="file" accept=".csv,text/csv" disabled={importing} onChange={e => setFile(e.target.files?.[0] || null)} /></label>
            <label>台账截止日期<input type="date" required max={localToday()} value={asOf} disabled={importing} onChange={e => setAsOf(e.target.value)} /></label>
            <button className="button primary" disabled={!file || importing || loading}>{importing ? '正在导入…' : '导入并更新看板'}</button></form>
        </details><p className="business-scope">{data.scope}</p>
      </div>}
    </>}
  </section>;
}
