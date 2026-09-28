import { useEffect, useRef, useState } from "react";
import { ApiError, apiEventStream, apiRequest } from "../api";
import { ProductIcon } from "./workbench-shared";
import "./product-knowledge.css";

interface KnowledgeStatus { available: boolean; code: string; help: string }
interface Dataset { id: string; name: string; document_count: number }
interface Session { id: string; title: string; version: number }
interface Source { id: string; dataset_id: string; document_id: string; title: string; content: string }
interface Turn { question: string; answer: string; sources: Source[]; outcome: "answered" | "empty"; request_id: string }
interface Conversation extends Session { turns: Turn[] }
interface Attempt { question: string; version: number; request_id: string }
const root = "/api/product/knowledge/";
const conversationPath = (id: string) => `${root}conversations/${encodeURIComponent(id)}/`;

export default function ProductKnowledge() {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [datasetError, setDatasetError] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [selected, setSelected] = useState("");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [question, setQuestion] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState("");
  const controller = useRef<AbortController | null>(null);
  const datasetController = useRef<AbortController | null>(null);
  const turnsRef = useRef<HTMLDivElement | null>(null);
  const questionRef = useRef<HTMLTextAreaElement | null>(null);
  const followStream = useRef(false);
  const focusAfterOpen = useRef(false);
  const locked = useRef(false);
  const visibleSessions = sessions.filter(item => (item.title || "未命名会话").toLocaleLowerCase().includes(historyQuery.trim().toLocaleLowerCase()));

  function begin(label: string) {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    locked.current = true;
    setBusy(label); setError(""); setNotice("");
    return next.signal;
  }
  function finish(signal: AbortSignal) {
    if (!signal.aborted) { locked.current = false; setBusy(""); }
  }
  function clearConversation() {
    followStream.current = false;
    setConversation(null); setAttempt(null); setQuestion(""); setDraft("");
  }
  function fail(reason: unknown, signal: AbortSignal) {
    if (signal.aborted) return;
    if (reason instanceof ApiError && [401, 403].includes(reason.status)) {
      clearConversation(); setSessions([]); setSelected("");
      datasetController.current?.abort(); setDatasets(null);
      setStatus({ available: false, code: reason.code || "access_revoked", help: "访问权限已变化，旧回答与引用已清除。请确认授权后重新检查。" });
    }
    setError(reason instanceof Error ? `${reason.message}${reason instanceof ApiError && reason.code ? `（${reason.code}）` : ""}` : "请求失败，请重试。");
  }
  async function checkStatus(signal: AbortSignal) {
    const value = await apiRequest<KnowledgeStatus>(`${root}status/`, { signal });
    if (signal.aborted) return false;
    setStatus(value);
    if (!value.available) { clearConversation(); setSessions([]); setSelected(""); datasetController.current?.abort(); setDatasets(null); }
    return value.available;
  }
  function accept(value: Conversation) {
    setConversation(value); setSelected(value.id);
    setSessions(previous => [{ id: value.id, title: value.title, version: value.version }, ...previous.filter(item => item.id !== value.id)]);
  }
  async function loadDatasets() {
    datasetController.current?.abort();
    const next = new AbortController();
    datasetController.current = next;
    setDatasets(null); setDatasetError("");
    try {
      const result = await apiRequest<{ datasets: Dataset[] }>(`${root}datasets/`, { signal: next.signal });
      if (!next.signal.aborted) setDatasets(result.datasets);
    } catch (reason) {
      if (!next.signal.aborted) setDatasetError(reason instanceof Error ? reason.message : "读取知识库列表失败，请重试。");
    }
  }
  async function initialize() {
    const signal = begin("正在检查知识库服务…");
    datasetController.current?.abort();
    clearConversation(); setSelected(""); setSessions([]); setStatus(null); setDatasets(null); setDatasetError("");
    try {
      if (!await checkStatus(signal)) return;
      const conversations = apiRequest<{ conversations: Session[] }>(`${root}conversations/`, { signal });
      void loadDatasets();
      const result = await conversations;
      if (!signal.aborted) setSessions(result.conversations);
    } catch (reason) { fail(reason, signal); }
    finally { finish(signal); }
  }
  useEffect(() => {
    void initialize();
    return () => { controller.current?.abort(); datasetController.current?.abort(); };
    // All requests are owned by this mount; switching sessions cancels the previous request.
  }, []);
  useEffect(() => {
    if (focusAfterOpen.current && conversation && !busy) {
      questionRef.current?.focus({ preventScroll: true });
      focusAfterOpen.current = false;
    }
  }, [busy, conversation]);
  useEffect(() => {
    if (followStream.current && turnsRef.current) turnsRef.current.scrollTop = turnsRef.current.scrollHeight;
  }, [draft, conversation?.turns.length]);

  async function openSession(id?: string) {
    if (!status?.available) return;
    const signal = begin(id ? "正在读取会话历史…" : "正在新建会话…");
    setHistoryQuery("");
    setHistoryExpanded(false);
    clearConversation(); setSelected(id || "");
    try {
      if (!await checkStatus(signal)) return;
      const value = await apiRequest<Conversation>(id ? conversationPath(id) : `${root}conversations/`, id ? { signal } : { method: "POST", body: "{}", signal });
      if (!signal.aborted) { focusAfterOpen.current = true; accept(value); }
    } catch (reason) { fail(reason, signal); }
    finally { finish(signal); }
  }
  async function send(retry = false) {
    if (locked.current || !status?.available || (!conversation && !!selected) || (!retry && attempt) || (retry && !conversation)) return;
    if (!retry && !question.trim()) return;
    let payload = retry ? attempt : null;
    let id = conversation?.id || "";
    const signal = begin("正在检索资料并生成回答…");
    followStream.current = true;
    setDraft("");
    try {
      if (!await checkStatus(signal)) return;
      let current = conversation;
      if (!current) {
        setBusy("正在新建会话…");
        current = await apiRequest<Conversation>(`${root}conversations/`, { method: "POST", body: "{}", signal });
        if (signal.aborted) return;
        accept(current);
      }
      id = current.id;
      payload = retry ? attempt : { question: question.trim(), version: current.version, request_id: crypto.randomUUID() };
      if (!payload) return;
      setAttempt(payload);
      setBusy("正在检索资料并生成回答…");
      const value = await apiEventStream<Conversation>(conversationPath(id), { ...payload, stream: true }, signal,
        delta => { if (!signal.aborted) setDraft(previous => previous + delta); });
      if (!signal.aborted) { accept(value); setAttempt(null); setQuestion(""); setDraft(""); }
    } catch (reason) {
      if (signal.aborted) return;
      setDraft("");
      if (!payload) { fail(reason, signal); return; }
      const failedAttempt = payload;
      if (reason instanceof ApiError && reason.status === 409 && reason.code === "history_limit") {
        setAttempt(null);
        fail(reason, signal);
        setNotice("此对话已达到历史上限，请新建会话继续提问。");
      } else if (reason instanceof ApiError && reason.status === 409) {
        setConversation(null); setAttempt(null); setQuestion(failedAttempt.question);
        setBusy("会话已变化，正在重新读取历史…");
        try {
          const value = await apiRequest<Conversation>(conversationPath(id), { signal });
          if (!signal.aborted) {
            accept(value);
            if (value.turns.some(turn => turn.request_id === failedAttempt.request_id)) {
              setQuestion(""); setNotice("历史已更新，本次问题已处理，请查看回答。");
            } else if (value.version > failedAttempt.version) {
              setNotice("历史已更新，请核对后重新发送；这将创建新的请求。");
            } else {
              setAttempt(failedAttempt);
              setError("对话仍在处理或上次请求尚未确认，请稍后重试本次问题");
            }
          }
        } catch (reloadError) { fail(reloadError, signal); }
      } else {
        if (reason instanceof ApiError && reason.status === 400) setAttempt(null);
        fail(reason, signal);
      }
    } finally { finish(signal); }
  }

  return <div className="pd-workspace pk-workspace">
    <header className="pk-heading"><div><span className="pd-muted">产品事业部 / 知识库</span><h2>知识库问答</h2><p>围绕已授权资料提问，结合引用原文核对回答。</p></div><span className={`pd-badge ${status?.available ? "good" : "warning"}`}>{status ? status.available ? "服务可用" : "服务不可用" : busy ? "检查服务中" : "服务状态未确认"}</span></header>
    {status && !status.available && <div className="pd-panel" role="status"><h3>知识库暂不可用</h3><p>{status.help || "请联系管理员检查知识库服务与授权。"}</p><small>{status.code}</small></div>}
    <div className="pk-layout">
      <aside className="pd-panel pk-sidebar" aria-label="知识库会话">
        <button className="button primary" disabled={!status?.available || !!busy} onClick={() => void openSession()}><ProductIcon name="plus"/>新建会话</button>
        <section className="pk-datasets" aria-label="已授权知识库">
          <h3>已授权知识库</h3>
          {status?.available && !datasets && !datasetError && <p className="pd-muted" role="status">正在加载知识库列表…</p>}
          {datasetError && <p className="pd-service-note" role="alert">知识库列表读取失败：{datasetError}</p>}
          {datasets && !datasets.length && <p className="pd-muted">暂无已授权知识库。</p>}
          {datasets && !!datasets.length && <ul>{datasets.map(item => <li key={item.id}><span>{item.name}</span><small>{item.document_count} 篇文档</small></li>)}</ul>}
        </section>
        <button type="button" className="button secondary pk-mobile-history-toggle" aria-controls="knowledge-history-list" aria-expanded={historyExpanded} onClick={() => setHistoryExpanded(previous => !previous)}>{historyExpanded ? "收起历史会话" : "查看历史会话"}<span>{sessions.length} 个会话</span></button>
        <section id="knowledge-history-list" className={`pk-history${historyExpanded ? " expanded" : ""}`}>
          <div className="pk-history-heading"><h3>历史会话</h3><small>{sessions.length} 个会话</small></div>
          {!!sessions.length && <input className="pk-history-search" type="search" aria-label="搜索历史会话" placeholder="按标题搜索会话" value={historyQuery} onChange={event => setHistoryQuery(event.target.value)}/>}
          {!sessions.length && !busy && <p className="pd-muted">暂无会话。服务可用时可新建会话开始提问。</p>}
          <nav aria-label="历史会话">{visibleSessions.map(item => <button key={item.id} className={selected === item.id ? "selected" : ""} aria-pressed={selected === item.id} title={item.title || "未命名会话"} disabled={!status?.available} onClick={() => void openSession(item.id)}><ProductIcon name="file"/><span><span className="pk-session-title">{item.title || "未命名会话"}</span><small>版本 {item.version}</small></span></button>)}</nav>
          {!!historyQuery && !visibleSessions.length && <p className="pk-no-matches">没有匹配的会话，请换个关键词。</p>}
        </section>
        <button className="button secondary" disabled={!!busy} onClick={() => void initialize()}>重新检查服务</button>
      </aside>
      <section className={`pd-panel pk-conversation${!conversation?.turns.length && !draft && !busy ? " pk-empty-conversation" : ""}`} aria-label="知识库问答内容" aria-busy={!!busy}>
        <h3>{conversation?.title || "开始知识库对话"}</h3>
        {error && <div className="pd-feedback" role="alert"><p>{error}</p>{status?.available && !busy && (attempt ? <button className="button secondary" onClick={() => void send(true)}>重试本次问题</button> : selected ? <button className="button secondary" onClick={() => void openSession(selected)}>重新加载历史</button> : <button className="button secondary" onClick={() => void initialize()}>重新加载会话列表</button>)}</div>}
        {notice && <p className="pd-service-note" role="status">{notice}</p>}
        <div className="pk-turns" aria-label="问答历史" ref={turnsRef} onScroll={event => { const element = event.currentTarget; followStream.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
          {!conversation && !busy && <div className="pd-empty"><ProductIcon name="search"/><h4>直接提问，或打开历史会话</h4><p>首次发送会自动新建会话，回答仅来自已授权资料。</p></div>}
          {conversation && !conversation.turns.length && <div className="pd-empty"><h4>这个会话还没有问题</h4><p>描述你想查找的要求、设备或方案，回答后可展开引用原文。</p></div>}
          {conversation?.turns.map((turn, index) => <article className="pk-turn" key={turn.request_id}>
            <h4>问题 {index + 1}</h4><p className="pk-question">{turn.question}</p>
            <h4>回答</h4>{turn.outcome === "empty" ? <p className="pd-service-note">未找到足够的相关资料。请补充关键词或确认资料授权范围。</p> : <p className="pk-answer">{turn.answer}</p>}
            {!!turn.sources.length && <div className="pk-sources"><h4>引用来源 · {turn.sources.length}</h4>{turn.sources.map((source, sourceIndex) => <details key={`${source.id}-${sourceIndex}`}><summary>[{source.id}] {source.title || "未命名来源"}</summary><blockquote>{source.content}</blockquote><small>来源 {source.id} · 文档 {source.document_id} · 数据集 {source.dataset_id}</small></details>)}</div>}
          </article>)}
          {draft && !!busy && <article className="pk-turn" aria-label="正在生成回答"><h4>回答生成中</h4><p className="pk-answer">{draft}</p><small className="pd-muted">引用正在核验，完成后才会保存。</small></article>}
        </div>
        {busy && <p role="status" className="pk-pending">{busy}</p>}
        {attempt && <p className="pk-question">{busy ? "待回答" : "待重试"}：{attempt.question}</p>}
        <form className="pk-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <label htmlFor="knowledge-question">你的问题</label>
          <textarea id="knowledge-question" ref={questionRef} rows={3} maxLength={2000} value={question} disabled={!status?.available || (!conversation && !!selected) || !!busy || !!attempt} onChange={event => setQuestion(event.target.value)} placeholder="输入问题，发送时自动新建会话…" aria-describedby="knowledge-keyboard" onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send(); }
          }}/>
          <div className="pd-actions"><small id="knowledge-keyboard">Enter 发送 · Shift+Enter 换行</small><button className="button primary" disabled={!status?.available || (!conversation && !!selected) || !!busy || !!attempt || !question.trim()}>发送问题<ProductIcon name="arrow"/></button></div>
        </form>
      </section>
    </div>
  </div>;
}
