import { FormEvent, useEffect, useRef, useState } from 'react';
import { isApiError } from '../api';
import {
  AgentAttachment, AgentConversation, AgentEvent, AgentInstallation, AgentManagementWork, AgentMessage, AgentPage, AgentProject,
  AgentReference, AgentSkill, AgentState, AgentWork, agentReferencePath, agentRequestId, changeAgentRequirement,
  commandAgentWork, createAgentConversation, createAgentProject, getAgentEvents, getAgentWork,
  listAgentConversations, listAgentInstallations, listAgentManagementWork, listAgentMessages, listAgentProjects,
  listAgentSkills, listAgentWork, mergeAgentEvents, removeAgentInstallation, saveAgentInstallation,
  sendAgentMessage, uploadAgentAttachment, nextAgentPage,
} from '../agent-api';
import { retentionNotice } from '../hr/recruitment-api';
import { CenterLink } from './shared';
import AgentManagementPanels from './AgentManagementPanels';
import './agent-workspace.css';

const stateLabels: Record<AgentState, string> = { queued: '等待执行', running: '正在执行', waiting_input: '等待补充', waiting_confirmation: '等待本人确认', stopping: '停止处理中', completed: '已完成', failed: '失败', cancelled: '已取消', terminated: '已终止' };
const errorText = (error: unknown) => isApiError(error) ? error.message : '暂时无法读取或提交，请检查连接后重试。';
const denied = (error: unknown) => isApiError(error) && [401, 403, 404].includes(error.status);
const timeText = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN') : '未记录';
const mergeItems = <T extends { id: string }>(current: T[], next: T[]) => [...new Map([...current, ...next].map(item => [item.id, item])).values()];
const messageExecutionStatus = (message: AgentMessage) => {
  if (message.execution_state === 'runtime_unconfigured' || message.execution_state === 'blocked' && message.execution_reason === 'runtime_unconfigured') return '未执行：运行环境未配置，请联系管理员。';
  if (message.execution_state === 'unavailable') return '未执行：助手服务未启用，请联系管理员。';
  if (message.execution_state === 'blocked') return '未提交：系统拦截，请联系管理员核查。';
  if (message.execution_state === 'dispatch_unknown') return '提交结果不确定；请先核对历史和工作记录，不要重复发送。';
  if (message.execution_state === 'submitted') return '已提交至运行环境；是否完成请以工作记录为准。';
  if (message.execution_state === 'pending') return '执行状态待确认；目前尚不能确认是否已提交。';
  return '执行状态未提供，尚不能确认是否已提交。';
};

function References({ title, items, management = false }: { title: string; items?: AgentReference[]; management?: boolean }) {
  return <section className="agent-references"><h3>{title}</h3>{!items?.length ? <p className="agent-muted">暂无已归档的{title}。</p> : items.map(item => {
    const view = agentReferencePath(item, 'view', management);
    const download = agentReferencePath(item, 'download', management);
    return <article key={`${item.domain_type}:${item.object_id}:${item.revision}`}><div><strong>{item.public_summary || '业务记录'}</strong><p>版本 {/^[A-Za-z0-9_-]{1,64}$/.test(String(item.revision)) ? String(item.revision).length > 16 ? String(item.revision).slice(0,12) + '…' : String(item.revision) : '未提供'} · {item.stale ? '已失效，仅供历史核对' : item.current === true ? '当前版本' : item.current === false ? '历史版本' : '版本状态未提供'}</p></div><div className="agent-actions">
      {view && <a href={view} target="_blank" rel="noreferrer">查看原文</a>}{download && <a href={download} target="_blank" rel="noreferrer">下载</a>}
      {!view && !download && <span className="agent-muted">此类型的原文入口尚未接入</span>}
    </div></article>;
  })}</section>;
}

function ManagementRecords() {
  const [department, setDepartment] = useState('');
  const [page, setPage] = useState<AgentPage<AgentManagementWork>>({ items: [] });
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const load = async (more = false) => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError('');
    if (!more) setPage({ items: [] });
    try {
      const next = await listAgentManagementWork(department, more ? nextAgentPage(page) : undefined, controller.signal);
      if (!controller.signal.aborted) setPage(current => ({ ...next, items: more ? mergeItems(current.items, next.items) : next.items }));
    } catch (caught) { if (!controller.signal.aborted) { setError(errorText(caught)); if (denied(caught)) setPage({ items: [] }); } }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <details className="agent-panel"><summary>部门工作记录 · 只读</summary><p className="agent-muted">查看授权工作摘要、成果及业务原件。</p>
    <form className="agent-actions" onSubmit={event => { event.preventDefault(); void load(); }}>
      <label>部门<select value={department} disabled={busy} onChange={event => { setDepartment(event.target.value); setPage({ items: [] }); }}><option value="">全部授权部门</option><option value="product">产品</option><option value="hr">人事</option><option value="finance">财务</option><option value="engineering">工程</option></select></label>
      <button className="button secondary" disabled={busy}>查询工作记录</button>
    </form>
    {error && <p role="alert" className="notice error">{error}</p>}
    {busy && <p role="status">正在读取授权工作记录…</p>}
    {page.items.map(work => <details key={work.id} className="agent-management-record"><summary>{work.summary || '工作摘要待提供'} · {stateLabels[work.state] || '状态待核实'}</summary><p>{`员工 ${work.owner_id}`} · {work.department_code || '部门未提供'}</p><p>{work.summary || '暂无工作摘要'}</p><References management title="业务原件与成果" items={work.business_references}/></details>)}
    {nextAgentPage(page) && <button disabled={busy} onClick={() => void load(true)}>加载更多工作记录</button>}
  </details>;
}

export default function AgentWorkspace({ preview = false, department, manager = false }: { preview?: boolean; department: string; manager?: boolean }) {
  const query = new URLSearchParams(window.location.search);
  const [conversationId, setConversationId] = useState(query.get('conversation') || '');
  const [workId, setWorkId] = useState(query.get('work') || '');
  const [conversations, setConversations] = useState<AgentPage<AgentConversation>>({ items: [] });
  const [projects, setProjects] = useState<AgentPage<AgentProject>>({ items: [] });
  const [history, setHistory] = useState<AgentPage<AgentWork>>({ items: [] });
  const [messages, setMessages] = useState<AgentPage<AgentMessage>>({ items: [] });
  const [work, setWork] = useState<AgentWork | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [text, setText] = useState(''), [projectId, setProjectId] = useState(''), [projectName, setProjectName] = useState('');
  const [attachments, setAttachments] = useState<AgentAttachment[]>([]);
  const [correcting, setCorrecting] = useState(false), [busy, setBusy] = useState(false), [uploading, setUploading] = useState(false);
  const [showSkills, setShowSkills] = useState(false), [skills, setSkills] = useState<AgentSkill[]>([]), [installations, setInstallations] = useState<AgentInstallation[]>([]);
  const [libraryError, setLibraryError] = useState(''), [syncError, setSyncError] = useState(''), [actionError, setActionError] = useState(''), [skillError, setSkillError] = useState('');
  const [notice, setNotice] = useState(''), [refresh, setRefresh] = useState(0), [loading, setLoading] = useState(false), [blocked, setBlocked] = useState(false);
  const submission = useRef<{ signature: string; key: string; conversation?: string; message?: AgentMessage } | null>(null);
  const actionRequest = useRef<{ signature: string; key: string } | null>(null);
  const active = useRef(true);
  const selected = useRef({ conversationId, workId }); selected.current = { conversationId, workId };
  const productTask = work?.business_references?.find(reference => reference.domain_type === 'document_task' && /^[A-Za-z0-9_-]+$/.test(reference.object_id));
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  const open = (conversation = '', work = '') => {
    setConversationId(conversation); setWorkId(work); setCorrecting(false); setNotice(''); setActionError('');
    const url = new URL(window.location.href);
    url.searchParams.delete('conversation'); url.searchParams.delete('work');
    if (conversation) url.searchParams.set('conversation', conversation);
    if (work) url.searchParams.set('work', work);
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  };
  useEffect(() => {
    const restore = () => { const params = new URLSearchParams(window.location.search); setConversationId(params.get('conversation') || ''); setWorkId(params.get('work') || ''); };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);

  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    setLibraryError('');
    void Promise.allSettled([listAgentConversations(undefined, controller.signal), listAgentProjects(undefined, controller.signal), listAgentWork(undefined, controller.signal)]).then(results => {
      if (controller.signal.aborted) return;
      const [conversationResult, projectResult, workResult] = results;
      if (conversationResult.status === 'fulfilled') setConversations(conversationResult.value); else setConversations({ items: [] });
      if (projectResult.status === 'fulfilled') setProjects(projectResult.value); else setProjects({ items: [] });
      if (workResult.status === 'fulfilled') setHistory(workResult.value); else setHistory({ items: [] });
      setLibraryError(results.filter(result => result.status === 'rejected').map(result => errorText(result.reason)).join('；'));
    });
    return () => controller.abort();
  }, [preview, refresh]);

  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    let running = false;
    let messagePage: number | undefined;
    let publicEvents: AgentEvent[] = [];
    setMessages({ items: [] }); setWork(null); setEvents([]); setSyncError(''); setBlocked(false);
    const sync = async () => {
      if (running || controller.signal.aborted) return;
      running = true; setLoading(true);
      try {
        if (conversationId) {
          let moreMessages = true;
          while (moreMessages && !controller.signal.aborted) {
            const next = await listAgentMessages(conversationId, messagePage, controller.signal);
            if (controller.signal.aborted) return;
            setMessages(current => ({ items: mergeItems(current.items, next.items) }));
            const latestWork = [...next.items].reverse().find(message => message.work_id)?.work_id;
            if (!workId && latestWork) { open(conversationId, latestWork); return; }
            moreMessages = !!nextAgentPage(next);
            if (nextAgentPage(next)) messagePage = nextAgentPage(next);
          }
        }
        if (workId) {
          const next = await getAgentWork(workId, controller.signal);
          if (controller.signal.aborted) return;
          setWork(next);
          if (!conversationId && next.conversation_id) { open(next.conversation_id, workId); return; }
          let more = true;
          while (more && !controller.signal.aborted) {
            const page = await getAgentEvents(workId, publicEvents.at(-1)?.seq ?? 0, controller.signal);
            if (controller.signal.aborted) return;
            const merged = mergeAgentEvents(publicEvents, page.items);
            if (page.has_more && merged.length === publicEvents.length) throw new Error('Event cursor did not advance');
            publicEvents = merged; setEvents(merged); more = page.has_more;
          }
        }
        setSyncError(''); setBlocked(false);
      } catch (caught) {
        if (!controller.signal.aborted) {
          setSyncError(`${errorText(caught)} 页面同步中断，不代表后台工作已停止。`);
          if (denied(caught)) { setWork(null); setMessages({ items: [] }); setEvents([]); publicEvents = []; setBlocked(true); }
        }
      } finally { running = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void sync();
    const timer = window.setInterval(() => { if (!document.hidden) void sync(); }, 5000);
    const reconnect = () => { if (!document.hidden) void sync(); };
    window.addEventListener('online', reconnect); window.addEventListener('focus', reconnect); document.addEventListener('visibilitychange', reconnect);
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener('online', reconnect); window.removeEventListener('focus', reconnect); document.removeEventListener('visibilitychange', reconnect); };
  }, [conversationId, workId, preview, refresh]);

  useEffect(() => {
    if (!showSkills || preview) return;
    const controller = new AbortController(); setSkillError('');
    void Promise.all([listAgentSkills(controller.signal), listAgentInstallations(controller.signal)]).then(([catalog, personal]) => {
      if (!controller.signal.aborted) { setSkills(catalog.items); setInstallations(personal.items); }
    }).catch(caught => { if (!controller.signal.aborted) { setSkills([]); setInstallations([]); setSkillError(errorText(caught)); } });
    return () => controller.abort();
  }, [showSkills, preview]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || uploading || blocked || !text.trim()) return;
    const snapshot = { text: text.trim(), attachments: attachments.map(({ type, id, sha256 }) => ({ type, id, sha256 })), workId, correcting, version: work?.current_requirement_version };
    const signature = JSON.stringify(snapshot);
    if (submission.current?.signature !== signature) submission.current = { signature, key: agentRequestId() };
    const pending = submission.current;
    setBusy(true); setActionError(''); setNotice('');
    try {
      const conversation = conversationId || pending.conversation || (await createAgentConversation(pending.key, projectId || undefined)).id;
      pending.conversation = conversation;
      const message = pending.message || await sendAgentMessage(conversation, snapshot.text, snapshot.attachments, pending.key, workId || undefined, snapshot.correcting);
      pending.message = message;
      let displayedMessage = message;
      if (correcting && work) {
        const requirement = await changeAgentRequirement(work.id, work.current_requirement_version, message.id, snapshot.text);
        displayedMessage = { ...message, execution_state: requirement.execution_state, execution_reason: undefined };
        pending.message = displayedMessage;
      }
      if (!active.current) return;
      setText(''); setAttachments([]); setCorrecting(false); submission.current = null;
      setMessages(current => ({ items: mergeItems(conversationId === conversation ? current.items : [], [displayedMessage]) }));
      open(conversation, displayedMessage.work_id || workId);
      setNotice(`消息已接收 · ${displayedMessage.id}；${messageExecutionStatus(displayedMessage)}${correcting ? ' 要求是否生效以“要求与更正”中的生效时间为准。' : ''}`);
      setRefresh(value => value + 1);
    } catch (caught) {
      if (active.current) {
        setActionError(`${errorText(caught)} 输入已保留；再次提交相同内容将复用请求标识。`);
        if (isApiError(caught) && caught.status === 409) setRefresh(value => value + 1);
        if (denied(caught)) setBlocked(true);
      }
    } finally { if (active.current) setBusy(false); }
  };
  const createProject = async (event: FormEvent) => {
    event.preventDefault(); if (busy || !projectName.trim()) return;
    const name = projectName.trim();
    setBusy(true); setActionError('');
    try {
      const project = await createAgentProject(name);
      if (!active.current) return;
      setProjects(current => ({ ...current, items: mergeItems(current.items, [project]) }));
      setProjectId(project.id); setProjectName(''); setNotice(`项目已创建：${project.name}`);
    } catch (caught) { if (active.current) setActionError(errorText(caught)); }
    finally { if (active.current) setBusy(false); }
  };
  const command = async (action: 'cancel' | 'retry') => {
    if (!work || busy || blocked) return;
    const signature = `${work.id}:${action}:${work.current_requirement_version}`;
    if (actionRequest.current?.signature !== signature) actionRequest.current = { signature, key: agentRequestId() };
    setBusy(true); setActionError('');
    try {
      const next = await commandAgentWork(work.id, action, work.current_requirement_version, actionRequest.current.key);
      if (!active.current || selected.current.workId !== work.id) return;
      if (next.id !== work.id) open(next.conversation_id || '', next.id);
      setWork(next); actionRequest.current = null; setNotice('请求已接收，执行状态以工作记录为准。'); setRefresh(value => value + 1);
    } catch (caught) { if (active.current) { setActionError(errorText(caught)); if (denied(caught)) { setWork(null); setBlocked(true); } } }
    finally { if (active.current) setBusy(false); }
  };
  const install = async (skill: AgentSkill, operation: 'enable' | 'disable' | 'remove') => {
    setBusy(true); setSkillError('');
    try {
      if (operation === 'remove') await removeAgentInstallation(skill.id);
      else await saveAgentInstallation(skill.id, operation === 'enable');
      const next = await listAgentInstallations(); if (active.current) setInstallations(next.items);
    } catch (caught) { if (active.current) setSkillError(errorText(caught)); }
    finally { if (active.current) setBusy(false); }
  };
  const more = async (kind: 'conversations' | 'work' | 'messages' | 'projects') => {
    setBusy(true); setActionError('');
    try {
      if (kind === 'conversations') { const page = await listAgentConversations(nextAgentPage(conversations)); if (active.current) setConversations(current => ({ ...page, items: mergeItems(current.items, page.items) })); }
      if (kind === 'work') { const page = await listAgentWork(nextAgentPage(history)); if (active.current) setHistory(current => ({ ...page, items: mergeItems(current.items, page.items) })); }
      if (kind === 'projects') { const page = await listAgentProjects(nextAgentPage(projects)); if (active.current) setProjects(current => ({ ...page, items: mergeItems(current.items, page.items) })); }
      if (kind === 'messages') { const id = conversationId; const page = await listAgentMessages(id, nextAgentPage(messages)); if (active.current && selected.current.conversationId === id) setMessages(current => ({ ...page, items: mergeItems(current.items, page.items) })); }
    } catch (caught) { if (active.current) { setActionError(errorText(caught)); if (denied(caught)) { setMessages({ items: [] }); setBlocked(true); } } }
    finally { if (active.current) setBusy(false); }
  };

  if (preview) return <section className="center-panel"><h2>智能助手预览</h2><p>使用已授权的部门账号进入后，可提交要求、查看本人历史与成果。管理预览不读取会话或启动工作。</p></section>;
  return <section className="agent-workspace" aria-label="智能助手工作区">
    <header className="agent-header"><div><p className="eyebrow">{manager ? '总经理' : department === 'hr' ? '人事' : department === 'finance' ? '财务' : '产品'}工作助手</p><h1>把要做的事告诉助手</h1><p>可以直接提问，也可以带上资料开始工作。</p></div><div className="agent-actions"><button className="button secondary" disabled={busy} onClick={() => open()}>新会话</button><button className="button secondary" aria-expanded={showSkills} onClick={() => setShowSkills(value => !value)}>本人技能</button></div></header>
    {department === 'hr' && <p className="agent-muted">{retentionNotice}</p>}
    {libraryError && <p className="notice error" role="alert">历史或项目读取失败：{libraryError} <button onClick={() => setRefresh(value => value + 1)}>重新读取</button></p>}
    {showSkills && <section className="agent-panel"><h3>本人技能</h3><p className="agent-muted">不安装也可开始工作。启用或卸载仅影响本人后续工作。</p>{skillError && <p role="alert" className="notice error">{skillError}</p>}{!skills.length && !skillError && <p>暂无可用技能，或目录正在读取。</p>}<div className="agent-skills">{skills.map(skill => {
      const personal = installations.find(item => item.skill_id === skill.id);
      return <article key={`${skill.id}:${skill.version}`}><h4>{skill.name}</h4><p>{skill.description}</p><p className="agent-muted">审核目录版本 {skill.version}{personal && ` · 本人安装版本 ${personal.version}`} · {personal ? personal.enabled ? '本人已启用' : '本人已停用' : '未安装'}</p><div className="agent-actions"><button disabled={busy} onClick={() => void install(skill, personal?.enabled ? 'disable' : 'enable')}>{personal?.enabled ? '停用' : personal ? '启用' : '安装并启用'}</button>{personal && <button disabled={busy} onClick={() => void install(skill, 'remove')}>卸载</button>}</div></article>;
    })}</div></section>}
    <details className="agent-panel"><summary>本人历史与项目</summary><div className="agent-history-grid"><section><h3>会话</h3>{!conversations.items.length && !libraryError && <p>暂无会话记录。</p>}{conversations.items.map(item => <button disabled={busy} key={item.id} className="agent-history-item" aria-pressed={conversationId === item.id} onClick={() => open(item.id, item.work_id || '')}>{item.title || '未命名会话'}<small>{timeText(item.updated_at || item.created_at)}</small></button>)}{nextAgentPage(conversations) && <button disabled={busy} onClick={() => void more('conversations')}>更多会话</button>}</section><section><h3>工作记录</h3>{!history.items.length && !libraryError && <p>暂无工作记录，普通问答不计为工作成果。</p>}{history.items.map(item => <button disabled={busy} className="agent-history-item" key={item.id} onClick={() => open(item.conversation_id || '', item.id)}>{item.goal}<small>{stateLabels[item.state] || '状态待核实'}</small></button>)}{nextAgentPage(history) && <button disabled={busy} onClick={() => void more('work')}>更多工作</button>}</section></div></details>
    {manager && <><ManagementRecords/><AgentManagementPanels/></>}
    <div className="agent-layout"><div className="agent-conversation">
      {syncError && <p className="notice error" role="alert">{syncError} <button onClick={() => setRefresh(value => value + 1)}>重新同步</button></p>}
      {loading && <p className="agent-muted" role="status">正在同步记录…</p>}
      <div className="agent-messages" aria-label="本人会话记录">{messages.items.filter(item => item.role === 'user' || item.role === 'assistant').sort((left, right) => left.created_at.localeCompare(right.created_at)).map(message => <article key={message.id} className={`agent-message ${message.role}`}><header><strong>{message.role === 'user' ? '你' : '助手'}</strong><time>{timeText(message.created_at)}</time></header><p>{message.content}</p>{message.role === 'user' && <small className="agent-muted">{messageExecutionStatus(message)}</small>}{message.attachment_references?.map((reference, index) => reference.type === 'agent_attachment' && <a className="agent-attachment" key={reference.id} href={`/api/agent/attachments/${encodeURIComponent(reference.id)}/download/`} target="_blank" rel="noreferrer">附件 {index + 1}</a>)}</article>)}{nextAgentPage(messages) && <button disabled={busy} onClick={() => void more('messages')}>加载更多消息</button>}</div>
      <form className="agent-composer" onSubmit={send}>
        <label htmlFor="agent-message">{correcting ? '更正当前工作要求' : '输入消息'}</label><textarea id="agent-message" value={text} onChange={event => setText(event.target.value)} placeholder="例如：根据这些资料整理方案，并列出仍需确认的信息" rows={5} required disabled={busy || blocked}/>
        <div className="agent-attachments">{attachments.map(item => <span className="agent-attachment" key={item.id}>{item.name}<button type="button" disabled={busy} aria-label={`移除附件 ${item.name}`} onClick={() => setAttachments(current => current.filter(attachment => attachment.id !== item.id))}>×</button></span>)}</div>
        <div className="agent-actions"><label className="agent-upload">添加附件<input aria-label="添加附件" type="file" disabled={busy || uploading || blocked} onChange={async event => {
          const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
          setUploading(true); setActionError('');
          try { const uploaded = await uploadAgentAttachment(file); if (active.current) setAttachments(current => mergeItems(current, [uploaded])); }
          catch (caught) { if (active.current) setActionError(`附件未能确认上传：${errorText(caught)}`); }
          finally { if (active.current) setUploading(false); }
        }}/></label>{uploading && <span role="status">正在上传附件…</span>}
          {work && ['queued', 'running', 'waiting_input', 'waiting_confirmation', 'failed'].includes(work.state) && <label className="agent-check"><input type="checkbox" checked={correcting} disabled={busy || blocked} onChange={event => setCorrecting(event.target.checked)}/>作为当前工作的要求更正</label>}
          <button className="button primary" disabled={busy || uploading || blocked || !text.trim()}>{busy ? '正在提交…' : correcting ? '提交更正' : '发送'}</button>
        </div><p className="agent-muted">关闭页面不会取消后台工作。重新进入可继续读取记录。</p>
      </form>
      {actionError && <p className="notice error" role="alert">{actionError}</p>}{notice && <p className="notice info" role="status">{notice}</p>}
      {!conversationId && <details className="agent-panel"><summary>项目归集（可选）</summary><label>已有项目<select value={projectId} onChange={event => setProjectId(event.target.value)} disabled={busy}><option value="">不关联项目</option>{projects.items.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>{nextAgentPage(projects) && <button disabled={busy} onClick={() => void more('projects')}>更多项目</button>}<form className="agent-project-form" onSubmit={createProject}><label>项目名称<input value={projectName} onChange={event => setProjectName(event.target.value)} maxLength={120} required disabled={busy}/></label><button className="button secondary" disabled={busy || !projectName.trim()}>创建项目</button></form></details>}
    </div><aside className="agent-work-record" aria-label="当前工作记录">
      {!work ? <section className="agent-panel"><h3>工作记录</h3><p className="agent-muted">{blocked ? '当前记录不可访问，请核对授权。' : workId ? '正在读取工作事实。' : '实际工作开始后，这里显示要求、进度和成果。'}</p></section> : <>
        <section className="agent-panel"><div className="agent-status-head"><h3>{work.goal}</h3><span className={`agent-state ${work.state}`}>{stateLabels[work.state] || '状态待核实'}</span></div><p>{work.public_summary || '暂无执行摘要'}</p>{work.stop_reason && <p className="notice warning">{work.stop_reason}</p>}<p className="agent-muted">当前要求版本 {work.current_requirement_version}</p><div className="agent-actions">
          {!manager && department === 'product' && productTask && <CenterLink href={`/centers/product/documents?task=${encodeURIComponent(productTask.object_id)}`}>{work.state === 'waiting_confirmation' ? '打开蓝图并核对精确版本' : '打开产品工作详情'}</CenterLink>}
          {!manager && department === 'finance' && work.state === 'waiting_confirmation' && <CenterLink href="/centers/finance">打开本人台账核对精确版本</CenterLink>}
          {!['completed', 'failed', 'cancelled', 'terminated', 'stopping'].includes(work.state) && <button disabled={busy || blocked} onClick={() => void command('cancel')}>取消工作</button>}
          {['failed', 'cancelled', 'terminated'].includes(work.state) && <button disabled={busy || blocked} onClick={() => void command('retry')}>{work.state === 'terminated' ? '明确发起新一轮工作' : '明确重试（新一轮）'}</button>}
        </div>{['failed', 'cancelled', 'terminated'].includes(work.state) && <p>继续将建立新一轮工作并关联本轮历史，旧运行不会自动重启。</p>}</section>
        <section className="agent-panel"><h3>要求与更正</h3>{work.requirements?.length ? work.requirements.map(requirement => <article className="agent-requirement" key={requirement.version}><strong>要求 v{requirement.version} · {requirement.applied_at ? '已生效' : requirement.received_at ? '已接收，待生效' : '待核实'}</strong><p>{requirement.content}</p><small>接收：{timeText(requirement.received_at)}<br/>生效：{timeText(requirement.applied_at)}</small></article>) : <p className="agent-muted">要求版本详情尚未提供。</p>}</section>
        {!!work.children?.length && <details className="agent-panel"><summary>子任务（{work.children.length}）</summary>{work.children.map(child => <article key={child.id}><h4>{child.title} · {stateLabels[child.state] || '状态待核实'}</h4><p>{child.summary || '暂无公开摘要'}</p></article>)}</details>}
        <section className="agent-panel"><References title="成果" items={work.result_references}/><References title="来源" items={work.business_references}/></section>
        <details className="agent-panel"><summary>公开执行记录（{events.length}）</summary><ol className="agent-events">{events.map(event => <li key={event.event_key}><time>{timeText(event.created_at)}</time><p>{event.payload.summary || '状态记录已更新'}</p></li>)}</ol></details>
      </>}
    </aside></div>
  </section>;
}
