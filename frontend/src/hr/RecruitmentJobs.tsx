import { useEffect, useRef, useState } from 'react';
import { apiRequest } from '../api';
import { CenterLink } from '../centers/shared';
import { get, mutate, message, Requirement, Jd } from './recruitment-api';

const fields = [['position_name', '岗位名称'], ['headcount', '招聘人数'], ['work_location', '工作地点'],
  ['education_requirement', '学历要求'], ['experience_requirement', '经验要求'], ['skill_requirements', '技能要求（每行一项）'],
  ['responsibilities', '岗位职责'], ['required_requirements', '必备要求'], ['preferred_requirements', '加分要求'], ['notes', '备注']] as const;
const empty = Object.fromEntries(fields.map(([key]) => [key, ''])) as Record<typeof fields[number][0], string>;
const channels = [['general', '通用版'], ['boss', 'BOSS直聘'], ['zhaopin', '智联招聘'], ['51job', '前程无忧'], ['liepin', '猎聘'], ['custom', '自定义']];

export default function RecruitmentJobs({ legacy = false }: { legacy?: boolean }) {
  const requested = new URLSearchParams(window.location.search).get('task') || '';
  const initialForm = useRef(JSON.stringify(empty));
  const selectionVersion = useRef(0);
  const mounted = useRef(true);
  const allowNavigation = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; selectionVersion.current += 1; }; }, []);
  const [rows, setRows] = useState<Requirement[]>([]), [selected, setSelected] = useState<Requirement | null>(null);
  const [form, setForm] = useState(empty), [versions, setVersions] = useState<Jd[]>([]);
  const [jd, setJd] = useState<Jd | null>(null), [body, setBody] = useState('');
  const [channel, setChannel] = useState('boss'), [custom, setCustom] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ id: string; title: string; revisions: { id: string; version: number; body: string }[] }[]>([]);
  async function load() { setRows(await get<Requirement[]>('requests/')); }
  async function choose(row: Requirement) {
    const selection = ++selectionVersion.current;
    setSelected(row); setJd(null); setVersions([]); setBody('');
    const nextForm = Object.fromEntries(fields.map(([key]) => [key, Array.isArray(row[key]) ? row[key].join('\n') : String(row[key] ?? '')])) as typeof empty;
    initialForm.current = JSON.stringify(nextForm); setForm(nextForm);
    const list = await get<Jd[]>(`requests/${row.id}/jd-versions/`);
    if (!mounted.current || selection !== selectionVersion.current) return;
    setVersions(list);
    const current = list.find(item => item.id === row.current_jd_id); if (current) { setJd(current); setBody(current.body); }
  }
  async function work(action: () => Promise<void>) {
    if (busy) return; setBusy(true); setError('');
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    selectionVersion.current += 1; setSelected(null); setJd(null); setVersions([]); setBody(''); setError('');
    initialForm.current = JSON.stringify(empty); setForm(empty);
    if (legacy) apiRequest<typeof history>('/api/hr/jobs/', { signal: controller.signal }).then(setHistory).catch(e => { if (!controller.signal.aborted) setError(message(e)); });
    else get<Requirement[]>('requests/', controller.signal).then(async data => {
      if (controller.signal.aborted) return;
      setRows(data);
      if (requested) {
        const row = data.find(item => item.id === requested);
        if (!row) throw new Error('指定岗位不存在或没有访问权限。');
        await choose(row);
      }
    }).catch(e => { if (!controller.signal.aborted) setError(message(e)); });
    return () => controller.abort();
  }, [legacy, requested]);
  const dirty = JSON.stringify(form) !== initialForm.current || (!!jd && body !== jd.body);
  useEffect(() => {
    if (!dirty || legacy) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    const guard = (event: Event) => {
      if (allowNavigation.current) { allowNavigation.current = false; return; }
      event.preventDefault(); setError('存在未保存修改，请先保存或切换岗位时明确放弃。');
    };
    const navigate = (event: MouseEvent) => {
      const link = (event.target as Element).closest?.('a');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (!window.confirm('有未保存修改，确定离开吗？')) { event.preventDefault(); event.stopPropagation(); }
      else { allowNavigation.current = true; window.setTimeout(() => { allowNavigation.current = false; }, 0); }
    };
    window.addEventListener('beforeunload', unload); document.addEventListener('click', navigate, true);
    window.addEventListener('portal:navigation-guard', guard);
    return () => { window.removeEventListener('beforeunload', unload); document.removeEventListener('click', navigate, true); window.removeEventListener('portal:navigation-guard', guard); };
  }, [dirty, legacy]);
  const base = selected ? `requests/${selected.id}/` : '';
  async function refreshJd(result: Jd) {
    setJd(result); setBody(result.body); setVersions(await get<Jd[]>(base + 'jd-versions/'));
    const current = await get<Requirement>(base); setSelected(current); await load();
  }
  if (legacy) return <section className="hr-card"><h2>历史 JD · 只读</h2>{error && <p role="alert">{error}</p>}
    {!history.length && <p>暂无历史 JD</p>}{history.map(row => <details key={row.id}><summary>{row.title}</summary>{row.revisions.map(rev => <article key={rev.id}><h3>版本 {rev.version}</h3><pre>{rev.body}</pre></article>)}</details>)}</section>;
  return <><header className="hr-title"><div><h1>招聘与 JD</h1><p>统一岗位事实，生成并确认 JD，再适配招聘平台。</p></div>
    <CenterLink href="/centers/hr/history" className="hr-outline">历史 JD</CenterLink></header>
    {error && <div className="hr-error" role="alert">{error}</div>}
    <section className="hr-card"><div className="hr-card-head"><h2>招聘需求</h2><button disabled={busy} onClick={() => { if (dirty && !window.confirm('放弃未保存修改？')) return; selectionVersion.current += 1; initialForm.current = JSON.stringify(empty); setSelected(null); setForm(empty); setJd(null); setVersions([]); setBody(''); }}>＋ 新建招聘需求</button></div>
      <label>选择岗位<select value={selected?.id || ''} disabled={busy} onChange={e => { const row = rows.find(r => r.id === e.target.value); if (row && (!dirty || window.confirm('放弃未保存修改并切换岗位？'))) void work(() => choose(row)); }}>
        <option value="">新招聘需求</option>{rows.map(row => <option key={row.id} value={row.id}>{row.position_name || '未命名岗位'}</option>)}</select></label>
      <form onSubmit={e => { e.preventDefault(); void work(async () => {
        const payload = { ...form, headcount: form.headcount ? Number(form.headcount) : undefined,
          skill_requirements: form.skill_requirements.split('\n').map(x => x.trim()).filter(Boolean) };
        const result = await mutate<Requirement>(selected ? base : 'requests/', selected ? { ...payload, expected_version: selected.input_version } : payload, selected ? 'PATCH' : 'POST');
        await load(); await choose(result);
      }); }}>
        <div className="hr-form-grid">{fields.map(([key, label]) => <label key={key}>{label}
          {['responsibilities', 'required_requirements', 'preferred_requirements', 'notes', 'skill_requirements'].includes(key)
            ? <textarea disabled={busy} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} rows={3} />
            : <input disabled={busy} type={key === 'headcount' ? 'number' : 'text'} min={1} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} />}</label>)}</div>
        <button className="hr-primary" disabled={busy}>{busy ? '处理中…' : '保存招聘需求'}</button>
      </form>
      {selected?.missing_items.length ? <p className="hr-warning">待补齐：{selected.missing_items.map(x => x.reason).join('；')}</p> : null}
    </section>
    {selected && <section className="hr-card"><div className="hr-card-head"><h2>JD 版本</h2>
      <button className="hr-primary" disabled={busy || dirty || !!selected.missing_items.length} onClick={() => void work(async () => refreshJd(await mutate<Jd>(base + 'generate-jd/', { expected_version: selected.input_version })))}>生成 JD 草稿</button></div>
      <label>版本<select value={jd?.id || ''} disabled={busy} onChange={e => { const item = versions.find(x => x.id === e.target.value); if (item && (!dirty || window.confirm('放弃未保存修改并切换版本？'))) { setJd(item); setBody(item.body); } }}><option value="">请选择</option>{versions.map(x => <option key={x.id} value={x.id}>v{x.version} · {channels.find(c => c[0] === x.channel)?.[1]} · {x.stale ? '已过期' : x.state === 'confirmed' ? '已确认' : '草稿'}</option>)}</select></label>
      {jd && <><label>JD 正文<textarea rows={15} value={body} disabled={busy || jd.state !== 'draft' || jd.stale || jd.channel !== 'general'} onChange={e => setBody(e.target.value)} /></label>
        <div className="hr-actions"><button disabled={busy || jd.state !== 'draft' || jd.stale || jd.channel !== 'general'} onClick={() => void work(async () => refreshJd(await mutate<Jd>(base + 'jd-versions/', { expected_version: selected.input_version, base_jd_id: jd.id, body })))}>保存修改版本</button>
        <button disabled={busy || jd.state !== 'draft' || jd.stale || jd.channel !== 'general' || body !== jd.body} onClick={() => void work(async () => refreshJd(await mutate<Jd>(base + `jd-versions/${jd.id}/confirm/`, { expected_version: selected.input_version })))}>确认 JD</button>
        <button onClick={() => void work(async () => { await navigator.clipboard.writeText(body); })}>复制正文</button></div></>}
      <div className="hr-actions"><label>招聘平台<select value={channel} onChange={e => setChannel(e.target.value)}>{channels.slice(1).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
        {channel === 'custom' && <label>自定义平台<input value={custom} onChange={e => setCustom(e.target.value)} /></label>}
        <button disabled={busy || dirty || !selected.official_jd_id} onClick={() => void work(async () => refreshJd(await mutate<Jd>(base + `jd-versions/${selected.official_jd_id}/adapt/`, { expected_version: selected.input_version, channel, custom_label: custom })))}>生成平台版本</button></div>
      <p className="hr-muted">平台版仅适配文案，不会自动发布到招聘网站。匹配使用已确认通用 JD。</p>
    </section>}</>;
}
