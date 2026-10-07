import { useEffect, useRef, useState } from 'react';
import { apiRequest } from '../api';
import { CenterLink } from '../centers/shared';
import { get, mutate, message, Requirement, Jd, IntakeResponse } from './recruitment-api';
import './recruitment-dialog.css';

const channels = [['boss', 'BOSS直聘'], ['zhaopin', '智联招聘'], ['51job', '前程无忧'], ['liepin', '猎聘']];

export default function RecruitmentJobs({ legacy = false }: { legacy?: boolean }) {
  const requested = new URLSearchParams(window.location.search).get('task') || '';
  const dialog = useRef<HTMLDialogElement>(null), lock = useRef(false);
  const intake = useRef<IntakeResponse | null>(null);
  const [open, setOpen] = useState(true), [text, setText] = useState('');
  const [requirement, setRequirement] = useState<Requirement | null>(null);
  const [general, setGeneral] = useState<Jd | null>(null), [displayed, setDisplayed] = useState<Jd | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ id: string; title: string; revisions: { id: string; version: number; body: string }[] }[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    intake.current = null; setRequirement(null); setGeneral(null); setDisplayed(null); setText(''); setError(''); setOpen(true);
    async function load() {
      if (legacy) {
        const rows = await apiRequest<typeof history>('/api/hr/jobs/', { signal: controller.signal });
        if (!controller.signal.aborted) setHistory(rows);
      } else if (requested) {
        const row = await get<Requirement>(`requests/${requested}/`, controller.signal);
        const versions = await get<Jd[]>(`requests/${row.id}/jd-versions/`, controller.signal);
        if (controller.signal.aborted) return;
        const current = versions.find(item => item.id === row.current_jd_id && item.channel === 'general') || null;
        setRequirement(row); setGeneral(current); setDisplayed(current);
      }
    }
    void load().catch(reason => { if (!controller.signal.aborted) setError(message(reason)); });
    return () => controller.abort();
  }, [legacy, requested]);

  useEffect(() => {
    const element = dialog.current;
    if (!element || legacy) return;
    if (open && !element.open) { element.showModal(); element.querySelector('textarea')?.focus(); }
    else if (!open && element.open) element.close();
    return () => { if (element.open) element.close(); };
  }, [open, legacy]);

  useEffect(() => {
    if (!text.trim() && !busy) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    const guard = (event: Event) => { event.preventDefault(); setOpen(true); setError('请先完成生成，或清空未提交的招聘说明后离开。'); };
    window.addEventListener('beforeunload', unload);
    window.addEventListener('portal:navigation-guard', guard);
    return () => { window.removeEventListener('beforeunload', unload); window.removeEventListener('portal:navigation-guard', guard); };
  }, [text, busy]);

  async function work(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setError(message(reason)); }
    finally { lock.current = false; setBusy(false); }
  }

  async function generate() {
    const description = text.trim();
    if (!description) return;
    if (intake.current?.request.original_text !== description) {
      intake.current = await mutate<IntakeResponse>('requests/intake/', { text: description });
    }
    const row = intake.current.request;
    setRequirement(row); setGeneral(null); setDisplayed(null);
    const result = await mutate<Jd>(`requests/${row.id}/generate-jd/`, { expected_version: row.input_version });
    setGeneral(result); setDisplayed(result); setText(''); intake.current = null;
  }

  async function adaptTo(channel: string) {
    if (!requirement || !general || general.stale) return;
    const base = `requests/${requirement.id}/jd-versions/${general.id}/`;
    if (general.state === 'draft') {
      const confirmed = await mutate<Jd>(base + 'confirm/', { expected_version: requirement.input_version });
      setGeneral(confirmed);
      setRequirement({ ...requirement, official_jd_id: confirmed.id, official_jd_stale: false });
    }
    setDisplayed(await mutate<Jd>(base + 'adapt/', { expected_version: requirement.input_version, channel }));
  }

  if (legacy) return <section className="hr-card"><h2>历史 JD · 只读</h2>{error && <p role="alert">{error}</p>}
    {!history.length && <p>暂无历史 JD</p>}{history.map(row => <details key={row.id}><summary>{row.title}</summary>{row.revisions.map(revision => <article key={revision.id}><h3>版本 {revision.version}</h3><pre>{revision.body}</pre></article>)}</details>)}</section>;

  const canAdapt = general && !general.stale && (general.state === 'draft' || (general.state === 'confirmed' && requirement?.official_jd_id === general.id && !requirement.official_jd_stale));
  return <><header className="hr-title"><div><h1>JD 生成</h1><p>描述你想招聘的人选，使用内置模型生成 JD。</p></div>
    <CenterLink href="/centers/hr/history" className="hr-outline">招聘历史</CenterLink></header>
    <section className="hr-card"><h2>{displayed ? 'JD 已生成' : '你想招聘什么岗位？'}</h2>
      <p>用一句话说明岗位、职责和工作地点，生成通用 JD 后可一键适配招聘平台。</p>
      <button className="hr-primary" onClick={() => setOpen(true)}>{displayed ? '查看 JD / 继续生成' : '打开 JD 对话框'}</button>
    </section>
    <dialog ref={dialog} className="hr-card hr-jd-dialog" aria-labelledby="hr-jd-dialog-title" onCancel={event => { event.preventDefault(); if (!busy) setOpen(false); }}>
      <div className="hr-card-head"><div><h2 id="hr-jd-dialog-title">JD 生成助手</h2><p className="hr-muted">内置模型自动整理需求，无需选择模型。</p></div>
        <button aria-label="关闭 JD 对话框" disabled={busy} onClick={() => setOpen(false)}>关闭</button></div>
      {error && <p className="hr-error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form aria-busy={busy} onSubmit={event => { event.preventDefault(); void work(generate); }}>
        <label>招聘说明<textarea autoFocus rows={4} maxLength={40000} value={text} disabled={busy} onChange={event => setText(event.target.value)} placeholder="例如：想招一位交付经理，负责项目交付，工作地点在西安，熟悉 Circle 等技术背景。" /></label>
        <div className="hr-card-head"><p className="hr-muted">仅使用已提供的信息；薪资、福利等未知内容标注待补充。</p>
          <button className="hr-primary" disabled={busy || !text.trim()}>{busy ? '正在处理…' : '生成通用 JD'}</button></div>
      </form>
      {displayed && <section className="hr-jd-answer" aria-label="生成结果">
        <div className="hr-card-head"><h3>{displayed.channel === 'general' ? '通用 JD' : `${channels.find(([key]) => key === displayed.channel)?.[1] || displayed.channel} JD`}</h3>
          <button disabled={busy} onClick={() => void work(async () => { await navigator.clipboard.writeText(displayed.body); setNotice('JD 正文已复制。'); })}>复制正文</button></div>
        <label>JD 正文<textarea rows={12} readOnly value={displayed.body} /></label>
        {general?.stale && <p className="hr-warning">此 JD 已过期，请重新描述需求生成。</p>}
        <p className="hr-muted">点击平台选项生成对应文案，不会自动发布。获准招聘记录长期归档。</p>
        <div className="hr-actions">
          {displayed.channel !== 'general' && <button disabled={busy} onClick={() => setDisplayed(general)}>查看通用 JD</button>}
          {channels.map(([channel, label]) => <button key={channel} disabled={busy || !canAdapt || !!text.trim()} onClick={() => void work(() => adaptTo(channel))}>生成{label} JD</button>)}
        </div>
      </section>}
    </dialog></>;
}
