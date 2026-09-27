import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, isApiError } from '../api';
import {
  addLedgerRecord, BusinessLedger, deleteLedgerRecord, getLedger, getLedgerVersion, importLedger,
  LedgerDepartment, LedgerDepartmentAccess, LedgerVersion, LedgerVersionDetail, listLedgerPermissions,
  listLedgerVersions, returnLedger, transitionLedger, updateLedgerRecord,
  updateLedgerMetadata,
} from './business-ledger-api';
import './business-ledger-workspace.css';

const stateLabels = { draft: '草稿', submitted: '待发布', published: '已发布' } as const;
const localToday = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};
const failureMessage = (error: unknown) => {
  if (error instanceof ApiError && error.status === 409) return '数据已被其他人员更新。已为你重新加载最新版本，请核对后再次提交。';
  return isApiError(error) ? error.message : error instanceof Error ? error.message : '操作未完成，请稍后重试。';
};
const requiredRecordFields = new Set(['project_id', 'project_name', 'status', 'progress', 'contract_amount', 'received_amount', 'amount']);
const numericRecordFields = new Set(['progress', 'contract_amount', 'received_amount', 'amount']);
const dateRecordFields = new Set(['planned_end', 'due_date']);

export default function BusinessLedgerWorkspace({ preview = false }: { preview?: boolean }) {
  const [access, setAccess] = useState<LedgerDepartmentAccess[]>([]);
  const [department, setDepartment] = useState<LedgerDepartment | ''>('');
  const [ledger, setLedger] = useState<BusinessLedger | null>(null);
  const [versions, setVersions] = useState<LedgerVersion[]>([]);
  const [versionDetail, setVersionDetail] = useState<LedgerVersionDetail | null>(null);
  const [editingId, setEditingId] = useState('');
  const [record, setRecord] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [asOf, setAsOf] = useState(localToday);
  const [sourceName, setSourceName] = useState('手工录入');
  const [returnReason, setReturnReason] = useState('');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(!preview);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const editable = !!ledger && ledger.state !== 'submitted' && ledger.permissions.can_edit;
  const fieldEntries = useMemo(() => Object.entries(ledger?.fields || {}), [ledger?.fields]);

  const resetEditor = (next?: BusinessLedger) => {
    setEditingId(''); setVersionDetail(null);
    setRecord(Object.fromEntries(Object.keys(next?.fields || {}).map(key => [key, ''])));
    if (next) { setAsOf(next.as_of || localToday()); setSourceName(next.source_name || '手工录入'); }
  };
  const loadLedger = async (code: LedgerDepartment, signal?: AbortSignal) => {
    const [next, history] = await Promise.all([getLedger(code, signal), listLedgerVersions(code, signal)]);
    setLedger(next); setVersions(history); resetEditor(next);
  };
  const reloadAfterConflict = async (code: LedgerDepartment) => {
    try { await loadLedger(code); } catch { /* Preserve the original conflict message. */ }
  };

  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    setLoading(true);
    listLedgerPermissions(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const allowed = result.departments || [];
      setAccess(allowed);
      if (allowed[0]) setDepartment(current => current || allowed[0].department);
    }).catch(error => { if (!controller.signal.aborted) setError(failureMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [preview]);

  useEffect(() => {
    if (!department || preview) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setNotice(''); setLedger(null);
    loadLedger(department, controller.signal).catch(error => { if (!controller.signal.aborted) setError(failureMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [department, preview]);

  const execute = async (operation: (current: BusinessLedger) => Promise<BusinessLedger>, message: string) => {
    if (!ledger || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const next = await operation(ledger);
      setLedger(next); resetEditor(next); setReturnReason(''); setNotice(message);
      setVersions(await listLedgerVersions(next.department));
    } catch (caught) {
      setError(failureMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) await reloadAfterConflict(ledger.department);
    } finally { setBusy(false); }
  };

  const saveRecord = (event: FormEvent) => {
    event.preventDefault();
    if (!ledger) return;
    const operation = editingId
      ? (current: BusinessLedger) => updateLedgerRecord(current, editingId, record)
      : (current: BusinessLedger) => addLedgerRecord(current, record);
    void execute(operation, editingId ? '记录已更新并保存在草稿中。' : '记录已加入草稿。');
  };
  const edit = (row: Record<string, string>) => {
    setEditingId(row.project_id || row.opportunity_id || '');
    setRecord(Object.fromEntries(fieldEntries.map(([key]) => [key, row[key] || ''])));
    setError(''); setNotice('');
  };

  if (preview) return <section className="center-panel"><h2>部门台账录入</h2><p>实际页面按登录账号显示工程、财务或售前台账权限。预览不会读取、录入或发布业务数据。</p></section>;

  return <section className="ledger-workspace" aria-label="部门台账录入工作台">
    <header className="ledger-head"><div><p className="eyebrow">经营数据治理</p><h2>部门台账录入</h2><p>录入人员维护草稿，发布人员确认后，总经理看板才会更新。</p></div>
      <button className="button secondary" disabled={!department || loading || busy} onClick={() => department && void loadLedger(department)}>刷新</button></header>
    {error && <p className="notice error" role="alert">{error}</p>}
    {notice && <p className="notice info" role="status">{notice}</p>}
    {loading && <p role="status">正在读取台账权限和最新版本…</p>}
    {!loading && !access.length && <div className="center-empty"><h3>没有台账录入权限</h3><p>总经理请在企业台账页面查看已发布数据；录入权限由平台管理员按部门分配。</p></div>}
    {!!access.length && <>
      <div className="ledger-tabs" role="tablist" aria-label="可维护台账">{access.map(item => <button key={item.department} role="tab" aria-selected={department === item.department} onClick={() => setDepartment(item.department)} disabled={busy}>{item.title}</button>)}</div>
      {ledger && <div role="tabpanel" aria-label={ledger.title}>
        <div className="ledger-status-row"><span className={`ledger-state ${ledger.state}`}>{stateLabels[ledger.state]}</span><span>版本 {ledger.revision}</span><span>截止日期 {ledger.as_of || '未填写'}</span><span>更新于 {ledger.updated_at ? new Date(ledger.updated_at).toLocaleString('zh-CN') : '尚未保存'}</span></div>
        {ledger.last_return_reason && <p className="notice warning">上次退回原因：{ledger.last_return_reason}</p>}

        <div className="ledger-actions">
          {ledger.permissions.can_submit && ledger.state === 'draft' && <button className="button primary" disabled={busy || !ledger.records.length} onClick={() => void execute(current => transitionLedger(current, 'submit'), '台账已提交，等待发布审核。')}>提交审核</button>}
          {ledger.permissions.can_publish && ledger.state === 'submitted' && <button className="button primary" disabled={busy} onClick={() => void execute(current => transitionLedger(current, 'publish'), '台账已发布，总经理看板已使用本版本。')}>发布到看板</button>}
          {ledger.permissions.can_publish && ledger.state === 'submitted' && <label className="ledger-return">退回原因<input value={returnReason} maxLength={500} onChange={event => setReturnReason(event.target.value)} /><button disabled={busy || !returnReason.trim()} onClick={() => void execute(current => returnLedger(current, returnReason.trim()), '台账已退回修改。')}>退回</button></label>}
        </div>

        {editable && <div className="ledger-editor-grid">
          <form className="center-panel ledger-record-form" onSubmit={saveRecord}>
            <h3>{editingId ? '编辑记录' : '新增记录'}</h3>
            <div className="ledger-metadata"><label>台账截止日期<input type="date" max={localToday()} required value={asOf} onChange={event => setAsOf(event.target.value)} /></label><label>数据来源名称<input required maxLength={200} value={sourceName} onChange={event => setSourceName(event.target.value)} /></label><button type="button" disabled={busy || !asOf || !sourceName.trim()} onClick={() => void execute(current => updateLedgerMetadata(current, { as_of: asOf, source_name: sourceName.trim() }), '台账来源和截止日期已更新。')}>保存台账信息</button></div>
            <div className="ledger-fields">{fieldEntries.map(([key, label]) => <label key={key}>{label}
              {key === 'status' && ledger.statuses.length
                ? <select required value={record[key] || ''} onChange={event => setRecord({ ...record, [key]: event.target.value })}><option value="">请选择</option>{ledger.statuses.map(item => <option key={item}>{item}</option>)}</select>
                : <input type={numericRecordFields.has(key) ? 'number' : dateRecordFields.has(key) ? 'date' : 'text'} min={numericRecordFields.has(key) ? 0 : undefined} max={key === 'progress' ? 100 : undefined} step={numericRecordFields.has(key) ? '.01' : undefined} required={requiredRecordFields.has(key)} value={record[key] || ''} maxLength={numericRecordFields.has(key) || dateRecordFields.has(key) ? undefined : 200} onChange={event => setRecord({ ...record, [key]: event.target.value })} />}
            </label>)}</div>
            <div className="center-actions"><button className="button primary" disabled={busy}>{editingId ? '保存修改' : '新增记录'}</button>{editingId && <button type="button" className="button secondary" onClick={() => resetEditor(ledger)}>取消编辑</button>}</div>
          </form>
          <form className="center-panel ledger-import" onSubmit={event => { event.preventDefault(); if (file) void execute(current => importLedger(current, file, asOf), 'CSV 已导入草稿，请核对后提交。'); }}>
            <h3>批量导入</h3><p>CSV 导入会替换当前部门的整份草稿。建议先下载模板并保留本地副本。</p>
            <a href={`/api/business/boards/${ledger.department}/template/`}>下载{ledger.title}模板</a>
            <label>CSV 文件<input ref={fileInput} type="file" accept=".csv,text/csv" disabled={busy} onChange={event => setFile(event.target.files?.[0] || null)} /></label>
            <label>台账截止日期<input type="date" max={localToday()} required value={asOf} onChange={event => setAsOf(event.target.value)} /></label>
            <button className="button secondary" disabled={busy || !file}>导入到草稿</button>
          </form>
        </div>}

        <section className="center-panel ledger-records"><h3>当前记录 <small>{ledger.records.length} 条</small></h3>
          <div className="business-table-scroll"><table><thead><tr>{fieldEntries.map(([, label]) => <th key={label}>{label}</th>)}{editable && <th>操作</th>}</tr></thead><tbody>{ledger.records.map((row, index) => {
            const id = row.project_id || row.opportunity_id || String(index);
            return <tr key={id}>{fieldEntries.map(([key]) => <td key={key}>{row[key] || '—'}{key === 'progress' && row[key] ? '%' : ''}</td>)}{editable && <td><button type="button" onClick={() => edit(row)}>编辑</button> <button type="button" className="danger-button" onClick={() => { if (window.confirm('确认从草稿中删除这条记录？')) void execute(current => deleteLedgerRecord(current, id), '记录已从草稿删除。'); }}>删除</button></td>}</tr>;
          })}</tbody></table></div>{!ledger.records.length && <p className="hr-muted">当前草稿暂无记录。</p>}
        </section>

        <details className="center-panel ledger-history"><summary>发布与修改历史（{versions.length}）</summary>{versions.length ? <ol>{versions.map(item => <li key={item.id}><strong>版本 {item.revision}</strong><span>{stateLabels[item.state]}</span><span>{item.record_count ?? '—'} 条</span><span>{item.as_of || '未填写截止日期'}</span><span>{item.actor?.name ? `操作人 ${item.actor.name}` : ''}</span><button type="button" disabled={busy} onClick={() => { setBusy(true); setError(''); getLedgerVersion(ledger.department, item.id).then(setVersionDetail).catch(caught => setError(failureMessage(caught))).finally(() => setBusy(false)); }}>查看版本</button></li>)}</ol> : <p>暂无历史版本。</p>}
          {versionDetail && <div className="ledger-version-detail"><h4>历史版本 {versionDetail.revision} · {stateLabels[versionDetail.state]}</h4><p>来源：{versionDetail.source_name} · 截止日期：{versionDetail.as_of || '未填写'} · 留痕：{versionDetail.checksum.slice(0, 12)}…</p><div className="business-table-scroll"><table><thead><tr>{fieldEntries.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead><tbody>{versionDetail.records.map((row, index) => <tr key={row.project_id || index}>{fieldEntries.map(([key]) => <td key={key}>{row[key] || '—'}</td>)}</tr>)}</tbody></table></div><button type="button" onClick={() => setVersionDetail(null)}>关闭历史版本</button></div>}
        </details>
      </div>}
    </>}
  </section>;
}
