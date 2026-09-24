import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { Field } from "../centers/shared";
import { artifactDownload, artifactHistoryDownload, artifactPreview, continueConversation, createConversationTask, draftDownload, draftHistoryDownload, getDraftOutputs, getTask, getTaskHistory, listTasks, updateTask, uploadSource, verifyArtifact } from "./product-api";
import type { BlueprintPayload, ChapterPayload, ContentChecks, DocumentTask, DraftOutput, OutputFamily, ReportRevision, ReviewCategory, TaskHistory, TaskInput, TaskSummary } from "./product-api";
import "./product-workspace.css";

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const emptyInput = (): TaskInput => ({ project: "", requirements: "", items: [], background: "", conditions: [] });
function editableInput(value: TaskInput): TaskInput {
  return { project: value.project, requirements: value.requirements, background: value.background, conditions: value.conditions,
    items: value.items.map(({ row_id, name, quantity, unit }) => ({ row_id, name, quantity, unit })) };
}
const emptyBlueprint: BlueprintPayload = { purpose: "", audience: "", chapters: [], conditions: [], missing: [], conflicts: [], template_version: "" };
const emptyChapter: ChapterPayload = { chapter_id: "", title: "", paragraphs: [], source_ids: [] };
const categories: { value: ReviewCategory; label: string }[] = [
  { value: "fact", label: "事实" }, { value: "inference", label: "推断" },
  { value: "conflict", label: "冲突" }, { value: "missing", label: "缺项" },
];
const contentCheckLabels: Record<keyof ContentChecks, string> = {
  facts: "事实", quantities: "数量", terms: "术语", sources: "来源", completeness: "完整性", conditions: "条件",
};
const contentCheckKeys = Object.keys(contentCheckLabels) as (keyof ContentChecks)[];
const emptyContentChecks = (): ContentChecks => ({ facts: false, quantities: false, terms: false, sources: false, completeness: false, conditions: false });
const toggleValue = (values: string[], value: string, checked: boolean) => checked ? [...values, value] : values.filter(item => item !== value);
interface ResolutionDraft { category: ReviewCategory | ""; reason: string; source_ids: string[] }
interface VerificationDraft {
  artifactId: string;
  sha256: string;
  pages: Record<string, { passed: boolean; comment: string }>;
  content_checks: ContentChecks;
}
const emptyVerification = (): VerificationDraft => ({ artifactId: "", sha256: "", pages: {}, content_checks: emptyContentChecks() });
const labels: Record<string, string> = {
  draft: "草稿", queued: "排队中", running: "后台处理中", blocked: "已阻塞", failed: "失败", cancelled: "已取消",
  completed: "已完成", succeeded: "已完成", approved: "已批准", pending_review: "待审核", awaiting_review: "待审核",
  waiting_input: "等待补充输入", waiting_review: "等待审核", intake: "需求录入", writing: "正文写作",
  content_check: "内容检查", final_review: "最终审核", blueprint: "蓝图", write: "正文", render: "Word 草稿",
  input: "需求录入", blueprint_review: "蓝图待审核", artifact_review: "文档待审核",
};
const outputLabels = { "technical-solution": "技术方案", feasibility: "可行性研究报告", presentation: "汇报 PPT" } as const;
const outputReviewLabels = { stale: "已过期", approved: "已批准", pending_review: "待人工审核" } as const;
const status = (value: string) => labels[value.toLowerCase()] || `待确认状态（${value || "未提供"}）`;
const active = (task: TaskSummary) => ["queued", "running"].includes(task.state.toLowerCase());
function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      const reason = error.code === "model_authorization_required" || error.code === "formal_release_blocked" ? "缺少模型外发或正式发布授权，操作被阻塞。" : "版本冲突或操作前置条件未满足。";
      return `${reason}${error.message} 请重新加载服务端状态后核对再保存；未保存编辑已保留，不会自动覆盖。`;
    }
    if (error.status === 403 || error.status === 503) return `缺少业务授权或服务授权，操作被阻塞，请联系负责人。${error.message}`;
  }
  return error instanceof Error ? error.message : "请求失败，请重试。";
}
function jsonObject<Value>(text: string): Value {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error("JSON 格式不正确，请检查双引号、逗号和括号。"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("请填写 JSON 对象，不是数组或空值。");
  return value as Value;
}

export default function DocumentWorkspace() {
  if (window.location.pathname.startsWith("/preview/")) return <section className="center-panel"><h2>项目成果流水线</h2><p role="note">此预览没有业务权限，不读取任务或资料。平台管理员不会自动获得业务权限，请从已授权业务入口进入。</p></section>;
  return <TaskWorkspace />;
}

function TaskWorkspace() {
  const params = new URLSearchParams(window.location.search);
  const requestedTask = params.get("task") || "";
  const requestedArtifact = params.get("artifact") || "";
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pendingMessage, setPendingMessage] = useState("");
  const [pendingTask, setPendingTask] = useState<DocumentTask | null>(null);
  const [error, setError] = useState("");
  const [fatal, setFatal] = useState("");
  const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const createKey = useRef<string | null>(null);
  const createLock = useRef(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setFatal("");
    void listTasks(controller.signal).then(result => { if (!controller.signal.aborted) {
      setTasks(result.tasks);
      if (requestedArtifact && !requestedTask) {
        setSelected(""); setFatal("成果深链缺少所属任务，无法安全定位。");
      } else if (requestedTask) {
        const match = result.tasks.find(task => task.id === requestedTask);
        if (match) { setSelected(match.id); setError(""); }
        else { setSelected(""); setFatal("指定产品任务不存在或当前账号无权访问。"); }
      } else setError("");
      setResolvedQuery(`${requestedTask}\n${requestedArtifact}`);
    } })
      .catch(reason => { if (!controller.signal.aborted) {
        if (requestedTask || requestedArtifact) setFatal("指定产品任务或成果不存在，或当前账号无权访问。");
        else setError(errorMessage(reason));
        setResolvedQuery(`${requestedTask}\n${requestedArtifact}`);
      } });
    return () => controller.abort();
  }, [refresh, requestedTask, requestedArtifact]);
  async function create() {
    if (createLock.current) return;
    createLock.current = true;
    setBusy(true); setError("");
    const controller = new AbortController();
    request.current = controller;
    let taskForRetry = pendingTask;
    try {
      const requestMessage = pendingMessage || message.trim() || (files.length
        ? `请根据已上传资料编制技术方案、可研报告和汇报 PPT：${files.map(file => file.name).join("、")}`
        : "");
      if (!requestMessage) throw new Error("请描述要完成的工作，或粘贴资料路径。");
      let task = taskForRetry;
      if (!task) {
        createKey.current ??= crypto.randomUUID();
        setPendingMessage(requestMessage);
        const result = await createConversationTask({ message: requestMessage }, createKey.current, controller.signal);
        if (controller.signal.aborted) return;
        task = result.task;
        taskForRetry = task;
        setPendingTask(task);
        setTasks(current => [task!, ...current.filter(item => item.id !== task!.id)]);
      }
      let remaining = [...files];
      while (remaining.length) {
        task = await uploadSource(task.id, remaining[0], task.version, controller.signal);
        taskForRetry = task;
        remaining = remaining.slice(1);
        setFiles(remaining); setPendingTask(task);
        setTasks(current => [task!, ...current.filter(item => item.id !== task!.id)]);
      }
      createKey.current = null;
      setPendingMessage(""); setPendingTask(null); setSelected(task.id);
      setTasks(current => [task, ...current.filter(item => item.id !== task.id)]);
      setRefresh(value => value + 1);
      setMessage(""); setFiles([]);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(errorMessage(reason));
        if (reason instanceof ApiError && reason.status === 400 && !taskForRetry) { setPendingMessage(""); createKey.current = null; }
      }
    }
    finally { createLock.current = false; if (!controller.signal.aborted) setBusy(false); }
  }
  if (resolvedQuery !== `${requestedTask}\n${requestedArtifact}`) return <section className="center-panel" role="status">正在定位产品任务…</section>;
  if (fatal) return <section className="center-panel" role="alert"><h2>无法打开产品任务</h2><p>{fatal}</p><a href="/centers/product/documents">安全返回产品任务列表</a></section>;
  return <section className="product-conversation-workspace">
    <header className="product-workspace-head">
      <div><span className="product-kicker">产品事业部</span><h2>项目成果流水线</h2><p>设备清单和项目背景只提交一次，后续三个成果共享同一份项目底稿。</p></div>
      <button className="button secondary" disabled={busy} onClick={() => { setSelected(""); setError(""); setMessage(""); setFiles([]); setPendingMessage(""); setPendingTask(null); createKey.current = null; }}>＋ 新建任务</button>
    </header>
    <section className="product-task-switcher" aria-label="任务切换">
      {error && <p role="alert">{error}</p>}
      <label htmlFor="document-task">选择任务</label>
      <select id="document-task" value={selected} onChange={event => setSelected(event.target.value)}>
        <option value="">请选择任务</option>
        {tasks.map(task => <option key={task.id} value={task.id}>{task.title} · {status(task.state)}</option>)}
      </select>
      <button className="text-button" onClick={() => setRefresh(value => value + 1)}>刷新任务列表</button>
    </section>
    {!selected ? <section className="product-conversation-empty">
      <div className="assistant-message"><span aria-hidden="true">✦</span><div><strong>创建一个项目成果任务</strong><p>上传设备清单和项目背景，或直接粘贴服务器已授权的资料路径。我会将它们作为三个成果的共同依据。</p></div></div>
      <form className="product-composer" onSubmit={event => { event.preventDefault(); void create(); }}>
        {pendingMessage && !busy && <p role="note">{pendingTask ? `任务已创建，仍有 ${files.length} 个附件待上传；继续不会重复创建任务或上传已成功附件。` : "上次请求结果尚未确认，重试会复用相同内容和幂等键。"}</p>}
        <label className="sr-only" htmlFor="conversation-message">描述任务、文件或资料路径</label>
        <textarea id="conversation-message" rows={4} placeholder="补充项目名称、建设目标或特殊要求；也可以直接粘贴已授权的文件路径……" value={message} disabled={busy || !!pendingMessage} onChange={event => setMessage(event.target.value)} />
        {files.length > 0 && <ul className="product-file-chips" aria-label="待上传附件">{files.map(file => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}</ul>}
        <div className="product-composer-actions">
          <label className="button secondary" htmlFor="conversation-files">＋ 添加文件</label>
          <input className="sr-only" id="conversation-files" type="file" multiple accept=".csv,.txt" onChange={event => setFiles(Array.from(event.target.files || []))} />
          <button className="button primary" disabled={busy || (!pendingTask && !message.trim() && files.length === 0)}>{busy ? (pendingTask ? "正在上传…" : "正在创建…") : pendingTask ? "继续上传剩余资料" : "发送并创建任务"}</button>
        </div>
      </form>
      <p className="product-empty-note">当前附件解析支持 UTF-8 CSV/TXT；路径必须在服务器已授权目录。对话中的“同意”不会替代正式审批。</p>
    </section> : <TaskEditor key={`${selected}-${requestedArtifact}`} id={selected} requestedArtifact={requestedArtifact} deepLinked={!!requestedTask} onFatal={setFatal} />}
  </section>;
}

function TaskEditor({ id, requestedArtifact, deepLinked, onFatal }: { id: string; requestedArtifact: string; deepLinked: boolean; onFatal: (message: string) => void }) {
  const [task, setTask] = useState<DocumentTask | null>(null);
  const [outputs, setOutputs] = useState<DraftOutput[]>([]);
  const [outputsError, setOutputsError] = useState("");
  const [history, setHistory] = useState<TaskHistory | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [title, setTitle] = useState("");
  const [input, setInput] = useState("");
  const [blueprint, setBlueprint] = useState("");
  const [chapter, setChapter] = useState(pretty(emptyChapter));
  const [chapterFamily, setChapterFamily] = useState<Exclude<OutputFamily, "presentation">>("technical-solution");
  const [comment, setComment] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [artifactId, setArtifactId] = useState("");
  const [assignedReviewer, setAssignedReviewer] = useState("");
  const [assignmentReason, setAssignmentReason] = useState("");
  const [issueResolutions, setIssueResolutions] = useState<Record<string, ResolutionDraft>>({});
  const [statementCategory, setStatementCategory] = useState<ReviewCategory | "">("");
  const [statementText, setStatementText] = useState("");
  const [statementSources, setStatementSources] = useState<string[]>([]);
  const [verification, setVerification] = useState<VerificationDraft>(emptyVerification);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const [pollRound, setPollRound] = useState(0);
  const [conversationMessage, setConversationMessage] = useState("");
  const [conversationFiles, setConversationFiles] = useState<File[]>([]);
  const [conversationAccepted, setConversationAccepted] = useState(false);
  const [conversationError, setConversationError] = useState("");
  const locked = useRef(false);
  const initial = useRef(false);
  const mounted = useRef(false);
  const mutation = useRef<AbortController | null>(null);
  const reading = useRef<AbortController | null>(null);
  const related = useRef<AbortController | null>(null);
  function receive(result: DocumentTask) {
    if (requestedArtifact && !result.artifacts.some(item => String(item.id) === requestedArtifact)) {
      setTask(null); onFatal("指定成果不存在、无权访问或不属于当前产品任务。");
      return;
    }
    setTask(result);
    related.current?.abort();
    setOutputs([]); setHistory(null); setOutputsError(""); setHistoryError("");
    const controller = new AbortController();
    related.current = controller;
    void getDraftOutputs(id, controller.signal).then(data => {
      if (!controller.signal.aborted && data.task_version === result.version) setOutputs(Array.isArray(data.outputs) ? data.outputs : []);
    }).catch(reason => { if (!controller.signal.aborted) { setOutputs([]); setOutputsError(errorMessage(reason)); } });
    void getTaskHistory(id, controller.signal).then(data => {
      if (!controller.signal.aborted && data.task_version === result.version) setHistory(data);
    }).catch(reason => { if (!controller.signal.aborted) { setHistory(null); setHistoryError(errorMessage(reason)); } });
    if (!initial.current) {
      initial.current = true;
      setTitle(result.title); setInput(pretty(editableInput(result.input || emptyInput()))); setBlueprint(pretty(result.blueprint?.payload || emptyBlueprint));
      setAssignedReviewer(result.reviewer_id == null ? "" : String(result.reviewer_id));
      if (requestedArtifact) {
        const artifact = result.artifacts.find(item => String(item.id) === requestedArtifact)!;
        setArtifactId(requestedArtifact);
        setVerification({ artifactId: requestedArtifact, sha256: artifact.sha256, pages: {}, content_checks: emptyContentChecks() });
      }
    }
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; reading.current?.abort(); related.current?.abort(); mutation.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    reading.current = controller;
    void getTask(id, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      receive(result);
    })
      .catch(reason => { if (!controller.signal.aborted) deepLinked ? onFatal("指定产品任务或成果不存在，或当前账号无权访问。") : setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [id, requestedArtifact, deepLinked, onFatal]);
  useEffect(() => {
    if (!task || !active(task) || busy || conflict) return;
    const controller = new AbortController();
    reading.current = controller;
    const timer = window.setTimeout(() => {
      void getTask(id, controller.signal).then(result => { if (!controller.signal.aborted) receive(result); })
        .catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
        .finally(() => { if (!controller.signal.aborted) setPollRound(value => value + 1); });
    }, 3000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [task, id, busy, conflict, pollRound]);
  async function perform(work: (signal: AbortSignal) => Promise<unknown>, reloading = false) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); setNotice(""); reading.current?.abort();
    const controller = new AbortController(); mutation.current = controller;
    try {
      await work(controller.signal);
      if (controller.signal.aborted) return;
      const result = await getTask(id, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      receive(result);
      if (reloading) setConflict(false);
      setNotice("服务端状态已更新；编辑区保留原内容。蓝图生成后请点击“载入服务端蓝图”。");
    } catch (reason) {
      if (!controller.signal.aborted && mounted.current) {
        setError(errorMessage(reason));
        if (reason instanceof ApiError && reason.status === 409) setConflict(true);
      }
    } finally { locked.current = false; if (mounted.current) setBusy(false); }
  }
  if (!task) return <section className="center-panel">{error ? <><p role="alert">{error}</p><button disabled={busy} onClick={() => void perform(async () => {}, true)}>重新加载服务端状态</button></> : <p role="status">正在加载任务…</p>}</section>;
  const allows = (action: string) => Array.isArray(task.actions) ? task.actions.includes(action) : task.actions?.[action] === true;
  const disabled = (action: string) => busy || conflict || !allows(action) || Boolean(task.blockers?.[action]);
  const send = (endpoint: string, body: object) => perform(signal => updateTask(id, endpoint, { expected_version: task.version, ...body }, signal));
  const saveJson = (endpoint: string, text: string, kind: "input" | "payload" | "chapter", extra: object = {}) => perform(signal => {
    const parsed = jsonObject<object>(text);
    return updateTask(id, endpoint, { ...(kind === "chapter" ? parsed : { [kind]: parsed }), ...extra, expected_version: task.version, ...(kind === "input" ? { title } : {}) }, signal);
  });
  const inputIssues = task.input_issues || [];
  const reviewSources = Array.from(new Map([
    ...task.sources,
    ...(task.input?.items || []).filter(item => String(item.row_id)).map(item => ({ id: String(item.row_id), original_name: `清单行 ${item.row_id}：${item.name}` })),
    ...(task.input?.knowledge_sources || []).map(source => ({ id: source.id, original_name: `知识来源：${source.location}` })),
  ].map(source => [source.id, source])).values());
  const reviewArtifacts = task.artifacts.filter(item => !item.family || item.family === "technical-solution");
  const taskVersion = task.version;
  const artifact = task.artifacts.find(item => String(item.id) === artifactId);
  const evidence = artifact?.render_evidence?.kind === "candidate" ? artifact.render_evidence : undefined;
  const currentVerification = !!artifact && verification.artifactId === artifact.id && verification.sha256 === artifact.sha256;
  const evidenceComplete = !!evidence && evidence.pages.length > 0 && evidence.page_count === evidence.pages.length;
  const verificationReady = currentVerification && evidenceComplete
    && !!comment.trim()
    && evidence.pages.every(page => verification.pages[String(page.page)]?.passed && verification.pages[String(page.page)]?.comment.trim())
    && contentCheckKeys.every(key => verification.content_checks[key]);
  const reviewerId = Number(assignedReviewer.trim());
  const reviewerReady = /^\d+$/.test(assignedReviewer.trim()) && Number.isSafeInteger(reviewerId) && reviewerId > 0 && !!assignmentReason.trim();
  const inputReviewReady = inputIssues.length > 0 && inputIssues.every(issue => {
    const resolution = issueResolutions[issue.issue_hash];
    return resolution?.category && resolution.reason.trim() && resolution.source_ids.length > 0;
  });
  const statementReady = !!statementCategory && !!statementText.trim() && statementSources.length > 0;
  function updateResolution(issueHash: string, changes: Partial<ResolutionDraft>) {
    setIssueResolutions(current => {
      const resolution = current[issueHash] || { category: "", reason: "", source_ids: [] };
      return { ...current, [issueHash]: { ...resolution, ...changes } };
    });
  }
  function selectArtifact(nextId: string) {
    const next = reviewArtifacts.find(item => item.id === nextId);
    setArtifactId(nextId);
    setVerification({ artifactId: nextId, sha256: next?.sha256 || "", pages: {}, content_checks: emptyContentChecks() });
  }
  function submitInputReview() {
    if (!inputReviewReady) return;
    void send("input-review/", { resolutions: inputIssues.map(issue => ({ issue_hash: issue.issue_hash,
      category: issueResolutions[issue.issue_hash].category as ReviewCategory,
      reason: issueResolutions[issue.issue_hash].reason.trim(), source_ids: issueResolutions[issue.issue_hash].source_ids })) });
  }
  function submitVerification() {
    if (!artifact || !evidence || !verificationReady) return;
    void perform(signal => verifyArtifact(artifact.id, {
      expected_version: taskVersion, sha256: artifact.sha256,
      pages: evidence.pages.map(page => ({ page: page.page, sha256: page.sha256, passed: true, comment: verification.pages[String(page.page)].comment.trim() })),
      content_checks: verification.content_checks,
      comment: comment.trim(),
    }, signal));
  }
  async function continueTask() {
    if (locked.current || !task || (!conversationMessage.trim() && conversationFiles.length === 0)) return;
    locked.current = true; setBusy(true); setConversationError(""); reading.current?.abort();
    const controller = new AbortController(); mutation.current = controller;
    try {
      let nextTask = task;
      if (conversationMessage.trim() && !conversationAccepted) {
        const result = await continueConversation(id, { expected_version: nextTask.version, message: conversationMessage.trim() }, controller.signal);
        nextTask = result.task; setTask(nextTask); setConversationAccepted(true);
      }
      let remaining = [...conversationFiles];
      while (remaining.length) {
        nextTask = await uploadSource(id, remaining[0], nextTask.version, controller.signal);
        remaining = remaining.slice(1); setConversationFiles(remaining); setTask(nextTask);
      }
      setConversationMessage(""); setConversationAccepted(false);
      setNotice("补充要求和资料已保存；正式审批仍需在专业工作台完成。");
    } catch (reason) {
      if (!controller.signal.aborted && mounted.current) setConversationError(errorMessage(reason));
    } finally { locked.current = false; if (mounted.current) setBusy(false); }
  }
  function decision(target: "blueprint" | "report" | "artifact", choice: "approve" | "revise", record?: ReportRevision) {
    const revision = target === "blueprint" ? task?.blueprint : target === "report" ? record : artifact;
    if (revision) void send("decisions/", { target, target_id: revision.id, sha256: revision.sha256, decision: choice, comment });
  }
  const progress = task.artifacts.length ? 4 : task.blueprint ? 3 : task.sources.length ? 2 : 1;
  return <>
  <section className="product-task-view" aria-label="对话任务工作区">
    <main className="product-dialogue">
      <div className="assistant-message"><span aria-hidden="true">✦</span><div><strong>任务已创建：{task.title}</strong><p>请求已记录；系统只会按后端授权和当前状态推进，不会把对话文字当作审批。</p></div></div>
      <div className="user-message"><p>{task.input?.requirements || task.input?.background || "已通过资料创建任务。"}</p></div>
      <div className="assistant-message"><span aria-hidden="true">✓</span><div><strong>{status(task.state)}</strong><p>当前阶段：{status(task.stage)} · 服务端版本 {task.version}</p>{task.error_code && <p role="alert">处理受阻：{task.error_code}</p>}</div></div>
      {Object.keys(task.blockers || {}).length > 0 && <div className="assistant-message"><span aria-hidden="true">!</span><div><strong>还需要授权或确认</strong>{Object.values(task.blockers).map(blocker => <p key={`${blocker.code}-${blocker.detail}`}>{blocker.detail}</p>)}</div></div>}
      <form className="product-composer product-followup-composer" onSubmit={event => { event.preventDefault(); void continueTask(); }}>
        {conversationError && <p role="alert">{conversationError}</p>}
        {conversationAccepted && conversationFiles.length > 0 && <p role="note">要求已保存，仍有 {conversationFiles.length} 个附件待上传；重试不会再次提交要求。</p>}
        <label className="sr-only" htmlFor={`task-message-${id}`}>继续补充要求或资料路径</label>
        <textarea id={`task-message-${id}`} rows={3} placeholder="继续补充要求、资料路径，或只添加附件……" value={conversationMessage} disabled={busy || conversationAccepted} onChange={event => setConversationMessage(event.target.value)} />
        {conversationFiles.length > 0 && <ul className="product-file-chips" aria-label="待补充附件">{conversationFiles.map(file => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}</ul>}
        <div className="product-composer-actions"><label className="button secondary" htmlFor={`task-files-${id}`}>＋ 添加资料</label><input className="sr-only" id={`task-files-${id}`} type="file" multiple accept=".csv,.txt" onChange={event => setConversationFiles(Array.from(event.target.files || []))} /><button className="button primary" disabled={busy || (!conversationMessage.trim() && conversationFiles.length === 0)}>{busy ? "正在保存…" : conversationAccepted ? "继续上传剩余资料" : "发送"}</button></div>
      </form>
    </main>
    <aside className="product-task-aside" aria-label="任务概览">
      <section><h3>共享项目底稿 <span>{task.sources.length}</span></h3>{task.sources.length ? <><ul>{task.sources.map(source => <li key={source.id}>{source.original_name}</li>)}</ul><p>以上资料由三个成果共同复用。</p></> : <p>尚未添加附件；路径资料由后端校验后形成快照。</p>}</section>
      <section><h3>流水线进度</h3><ol className="product-progress">{["资料接收", "事实与边界", "成果编制", "统一审校"].map((label, index) => <li className={index < progress ? "done" : ""} key={label}>{label}</li>)}</ol></section>
      <section><h3>项目成果</h3>{outputsError && <p role="alert">成果状态读取失败：{outputsError}</p>}{(["technical-solution", "feasibility", "presentation"] as const).map(family => {
        const latest = outputs.filter(item => item.family === family).at(-1);
        return <article className={`product-output-card ${latest?.current ? "ready" : "locked"}`} key={family}>
          <strong>{outputLabels[family]}</strong>
          <span>{latest ? `${latest.draft ? "草稿" : "成果"} v${latest.version} · ${outputReviewLabels[latest.review_status]} · ${latest.sha256.slice(0, 12)}` : "尚未生成"}</span>
          {latest?.current && <a href={family === "technical-solution" ? artifactDownload(latest.id) : draftDownload(latest.id)}>下载当前草稿</a>}
          {latest?.content_version != null && <small>内容 v{latest.content_version} · {latest.content_approved ? "已批准" : "未批准"}</small>}
          {latest?.source_versions.length ? <small>来源：{latest.source_versions.map(source => `${outputLabels[source.family as keyof typeof outputLabels] || source.family} v${source.version}`).join("、")}</small> : null}
        </article>;
      })}</section>
    </aside>
  </section>
  <details className="center-panel product-advanced-workbench">
    <summary className="product-advanced-head"><span><span className="product-kicker">专业工作台</span><strong>{task.title}</strong></span><span className="status info">{status(task.stage)}</span></summary>
    <p role="status">状态：{status(task.state)}；阶段：{status(task.stage)}；服务端版本：{task.version}</p>
    {task.state.toLowerCase() === "blocked" && <p role="alert">任务已阻塞，请检查业务/模型服务授权与缺失条件；前端不会绕过授权。</p>}
    {task.error_code && <p role="alert">后端错误代码：{task.error_code}；如涉及授权或服务阻塞，请联系负责人补齐授权。</p>}
    {Object.entries(task.blockers || {}).length > 0 && <section className="center-note" aria-label="当前操作阻断">
      <strong>当前操作阻断</strong>
      <ul>{Object.entries(task.blockers).map(([action, blocker]) => <li key={action}>{blocker.detail}（{blocker.code}）</li>)}</ul>
    </section>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void perform(async () => {}, true)}>重新加载服务端状态（保留编辑）</button>
    <p>仅后端 actions 允许的操作可用；不能用勾选框代替正式批准。重新加载后请比较服务端输入与本地编辑再保存。</p>
    <details><summary>查看服务端输入、问题与审批记录</summary><pre>{pretty({ input: task.input, issues: task.issues, input_issues: inputIssues, impact: task.impact || {}, approvals: task.approvals })}</pre></details>
    <details><summary>查看影响分析</summary><pre>{pretty(task.impact || {})}</pre></details>
    <button disabled={busy} onClick={() => { setTitle(task.title); setInput(pretty(editableInput(task.input || emptyInput()))); }}>载入服务端输入（替换本地编辑）</button>
    <Field id="draft-title" label="草稿标题"><input id="draft-title" value={title} onChange={event => setTitle(event.target.value)} /></Field>
    <Field id="draft-input" label="草稿输入 JSON"><textarea id="draft-input" rows={12} value={input} onChange={event => setInput(event.target.value)} /></Field>
    <button disabled={disabled("edit")} onClick={() => void saveJson("", input, "input")}>保存服务端草稿</button>
    <h3>审核人与输入判断</h3>
    <p>分类说明：事实为来源直接支持；推断为基于来源的判断；冲突为来源不一致；缺项为现有来源不足。核对和补充判断都必须关联当前任务资料。</p>
    <Field id="assigned-reviewer" label="指定审核人 ID"><input id="assigned-reviewer" type="number" min="1" value={assignedReviewer} disabled={disabled("assign_reviewer")} onChange={event => setAssignedReviewer(event.target.value)} /></Field>
    <Field id="assignment-reason" label="指定或改派原因"><textarea id="assignment-reason" value={assignmentReason} disabled={disabled("assign_reviewer")} onChange={event => setAssignmentReason(event.target.value)} /></Field>
    <button disabled={disabled("assign_reviewer") || !reviewerReady} onClick={() => void send("reviewer/", { reviewer_id: reviewerId, reason: assignmentReason.trim() })}>指定或改派审核人</button>
    {inputIssues.length ? <>
      <ul aria-label="输入问题核对">{inputIssues.map((issue, index) => {
        const resolution = issueResolutions[issue.issue_hash] || { category: "", reason: "", source_ids: [] };
        return <li key={issue.issue_hash}>
          <p>问题 {index + 1}</p><pre>{pretty(issue)}</pre>
          <label htmlFor={`issue-category-${issue.issue_hash}`}>问题 {index + 1} 分类</label>
          <select id={`issue-category-${issue.issue_hash}`} value={resolution.category} disabled={disabled("review_input")} onChange={event => updateResolution(issue.issue_hash, { category: event.target.value as ReviewCategory | "" })}>
            <option value="">请选择分类</option>{categories.map(category => <option key={category.value} value={category.value}>{category.label}</option>)}
          </select>
          <Field id={`issue-reason-${issue.issue_hash}`} label={`问题 ${index + 1} 核对依据`}><textarea id={`issue-reason-${issue.issue_hash}`} value={resolution.reason} disabled={disabled("review_input")} onChange={event => updateResolution(issue.issue_hash, { reason: event.target.value })} /></Field>
          <fieldset disabled={disabled("review_input")}><legend>问题 {index + 1} 来源</legend>{reviewSources.map(source => <label key={source.id}><input type="checkbox" checked={resolution.source_ids.includes(source.id)} onChange={event => updateResolution(issue.issue_hash, { source_ids: toggleValue(resolution.source_ids, source.id, event.target.checked) })} />{source.original_name}（{source.id}）</label>)}</fieldset>
        </li>;
      })}</ul>
      <button disabled={disabled("review_input") || !inputReviewReady} onClick={submitInputReview}>提交全部输入问题核对</button>
    </> : <p>当前没有待核对输入问题。</p>}
    <label htmlFor="statement-category">补充判断分类</label><select id="statement-category" value={statementCategory} disabled={disabled("add_statement")} onChange={event => setStatementCategory(event.target.value as ReviewCategory | "")}><option value="">请选择分类</option>{categories.map(category => <option key={category.value} value={category.value}>{category.label}</option>)}</select>
    <Field id="statement-text" label="补充判断说明"><textarea id="statement-text" value={statementText} disabled={disabled("add_statement")} onChange={event => setStatementText(event.target.value)} /></Field>
    <fieldset disabled={disabled("add_statement")}><legend>补充判断来源</legend>{reviewSources.map(source => <label key={source.id}><input type="checkbox" checked={statementSources.includes(source.id)} onChange={event => setStatementSources(current => toggleValue(current, source.id, event.target.checked))} />{source.original_name}（{source.id}）</label>)}</fieldset>
    <button disabled={disabled("add_statement") || !statementReady} onClick={() => void send("statements/", { category: statementCategory, text: statementText.trim(), source_ids: statementSources })}>提交补充判断</button>
    <h3>试用资料上传</h3>
    <p>仅隔离试用 UTF-8 CSV 设备清单与 TXT 背景，不代表全部资料类型解析已获批准；上传不会自动覆盖正在编辑的输入。</p>
    <label htmlFor="source-file">选择试用资料</label><input id="source-file" type="file" accept=".csv,.txt" onChange={event => setFile(event.target.files?.[0] || null)} />
    <button disabled={disabled("add_source") || !file} onClick={() => { if (file) void perform(signal => uploadSource(id, file, task.version, signal)); }}>上传资料</button>
    <ul aria-label="已上传资料">{task.sources.map(source => <li key={source.id}>{source.original_name}（引用 ID：{source.id}）</li>)}</ul>
    <button disabled={disabled("queue_retrieve")} onClick={() => void send("queue/", { action: "retrieve" })}>发起授权检索（默认未授权，可能被拒）</button>
    <h3>方案蓝图</h3>
    <button disabled={disabled("queue_blueprint")} onClick={() => void send("queue/", { action: "blueprint" })}>生成蓝图</button>
    <button disabled={busy || !task.blueprint} onClick={() => setBlueprint(pretty(task.blueprint?.payload))}>载入服务端蓝图</button>
    <p>载入蓝图会替换本地蓝图编辑；自动刷新不会替换。purpose 为目的，audience 为受众；所有示例仅说明结构，不是业务事实。</p>
    <details><summary>蓝图 JSON 中文结构示例（不会自动填入）</summary><pre>{pretty({ purpose: "填写已确认的目的", audience: "填写真实受众", chapters: [{ id: "章节标识", title: "章节名称", scope: "本章范围", source_ids: [] }], conditions: [{ text: "待确认条件", type: "human" }], missing: [], conflicts: [], template_version: "填写后端支持的模板版本" })}</pre><p>conditions.type 只允许 program（程序校验）、model（模型辅助）、human（人工确认）；source_ids 引用已上传资料 ID。</p></details>
    <Field id="blueprint-json" label="蓝图 JSON"><textarea id="blueprint-json" rows={14} value={blueprint} onChange={event => setBlueprint(event.target.value)} /></Field>
    <button disabled={disabled("save_blueprint")} onClick={() => void saveJson("blueprint/", blueprint, "payload")}>保存蓝图</button>
    <Field id="decision-comment" label="审核意见"><textarea id="decision-comment" value={comment} onChange={event => setComment(event.target.value)} /></Field>
    <p>批准/退回绑定服务端版本及 SHA256，不会批准尚未保存的编辑内容。</p>
    <button disabled={disabled("review_blueprint") || !task.blueprint} onClick={() => decision("blueprint", "approve")}>批准服务端蓝图</button>
    <button disabled={disabled("review_blueprint") || !task.blueprint} onClick={() => decision("blueprint", "revise")}>退回蓝图</button>
    <h3>正文与人工章节</h3>
    <button disabled={disabled("queue_write")} onClick={() => void send("queue/", { action: "write" })}>生成正文</button>
    <ul aria-label="章节修订">{task.chapters.map(revision => <li key={revision.id}><button disabled={busy} onClick={() => { setChapterFamily(revision.family); setChapter(pretty(revision.payload)); }}>编辑 {outputLabels[revision.family]} · {revision.payload.title || revision.id}（版本 {revision.version ?? "未提供"}）</button></li>)}</ul>
    <Field id="chapter-json" label="章节 JSON" hint="chapter_id 对应章节标识；title 标题；paragraphs 正文字符串数组；source_ids 引用资料 ID 数组。手动选择章节才载入，自动刷新不覆盖。"><textarea id="chapter-json" rows={10} value={chapter} onChange={event => setChapter(event.target.value)} /></Field>
    <label htmlFor="chapter-family">保存目标成果</label><select id="chapter-family" value={chapterFamily} onChange={event => setChapterFamily(event.target.value as Exclude<OutputFamily, "presentation">)}><option value="technical-solution">技术方案</option><option value="feasibility">可行性研究报告</option></select>
    <button disabled={disabled("save_chapter")} onClick={() => void saveJson(chapterFamily === "technical-solution" ? "chapters/" : "report-chapters/", chapter, "chapter", chapterFamily === "feasibility" ? { family: "feasibility" } : {})}>保存所选成果章节</button>
    <h3>结构化内容审核</h3>
    <p>技术方案与可研内容分别审核；按钮是否可用完全由后端 review_report 决定。</p>
    <ul aria-label="结构化内容版本">{(task.reports || []).map(report => <li key={report.id}>
      {outputLabels[report.family]}内容 v{report.version} · {report.current ? "当前" : "已过期"} · SHA256：{report.sha256} · {report.approved ? "已批准" : "待审核"}
      <button disabled={!report.current || disabled("review_report")} onClick={() => decision("report", "approve", report)}>批准{outputLabels[report.family]}内容 v{report.version}</button>
      <button disabled={!report.current || disabled("review_report")} onClick={() => decision("report", "revise", report)}>退回{outputLabels[report.family]}内容 v{report.version}</button>
    </li>)}</ul>
    <h3>Word / PPT 草稿与历史版本</h3>
    <button disabled={disabled("queue_render")} onClick={() => void send("queue/", { action: "render" })}>生成 Word 草稿</button>
    <button disabled={disabled("queue_three_drafts")} onClick={() => void send("queue/", { action: "three_drafts" })}>生成技术方案与可研 Word 待审核草稿</button>
    <button disabled={disabled("queue_presentation")} onClick={() => void send("queue/", { action: "presentation" })}>从已批准内容生成 PPT 草稿</button>
    <button disabled={disabled("queue_candidate")} onClick={() => void send("queue/", { action: "candidate" })}>生成正式候选并后台 Office 渲染（非发布）</button>
    <p>检索与正式候选默认可能因未授权被拒；候选生成或渲染成功也不表示已经批准或发布。</p>
    {historyError && <p role="alert">版本链读取失败：{historyError}</p>}
    <ul aria-label="成果历史版本">{outputs.map(item => <li key={item.id}>
      {outputLabels[item.family]} v{item.version} · {item.current ? "当前" : "已过期"} · SHA256：{item.sha256}
      <a href={item.family === "technical-solution" ? item.current ? artifactDownload(item.id) : artifactHistoryDownload(item.id) : item.current ? draftDownload(item.id) : draftHistoryDownload(item.id)} download>
        下载{item.current ? "当前" : "历史草稿（已过期）"}{outputLabels[item.family]} v{item.version}
      </a>
    </li>)}</ul>
    <details><summary>查看版本与来源链</summary><p>服务端记录为应用级只读历史，不宣称法证级不可篡改。</p><ul aria-label="版本来源链">{(history?.timeline || []).filter(item => item.event === "revision").map(item => <li key={item.id}>{item.family ? `${outputLabels[item.family] || item.family} · ` : ""}{item.kind} v{item.version} · SHA256：{item.sha256} · 原因：{item.reason || "未提供"} · 操作者：{item.actor?.name || "未提供"} · 来源：{item.source_refs?.join("、") || "无"}</li>)}</ul></details>
    <label htmlFor="artifact-review">选择审核技术方案版本</label><select id="artifact-review" value={artifactId} onChange={event => selectArtifact(event.target.value)}><option value="">请选择版本</option>{reviewArtifacts.map(item => <option key={item.id} value={item.id}>版本 {item.version}</option>)}</select>
    {artifact && <>
      <h3>正式候选逐页核验</h3>
      {!evidence ? <p>所选版本没有公开的正式候选渲染证据，不能提交核验。</p> : <>
        <p>证据状态：{evidence.status}；页数：{evidence.page_count}。必须逐页打开鉴权预览、手动勾选通过并填写非空意见。</p>
        <ul aria-label="候选页核验">{evidence.pages.map(page => {
          const pageDraft = currentVerification ? verification.pages[String(page.page)] || { passed: false, comment: "" } : { passed: false, comment: "" };
          return <li key={page.page}>
            <a href={artifactPreview(artifact.id, page.page)} target="_blank" rel="noreferrer">鉴权预览第 {page.page} 页</a> · SHA256：{page.sha256}
            <img src={artifactPreview(artifact.id, page.page)} alt={`正式候选第 ${page.page} 页预览`} loading="lazy" />
            <label><input type="checkbox" checked={pageDraft.passed} disabled={disabled("verify_artifact")} onChange={event => setVerification(current => ({ ...current, pages: { ...current.pages, [String(page.page)]: { ...pageDraft, passed: event.target.checked } } }))} />第 {page.page} 页已人工核对并通过</label>
            <Field id={`page-comment-${page.page}`} label={`第 ${page.page} 页核验意见`}><textarea id={`page-comment-${page.page}`} value={pageDraft.comment} disabled={disabled("verify_artifact")} onChange={event => setVerification(current => ({ ...current, pages: { ...current.pages, [String(page.page)]: { ...pageDraft, comment: event.target.value } } }))} /></Field>
          </li>;
        })}</ul>
        <fieldset disabled={disabled("verify_artifact")}><legend>全文内容核验（必须逐项手动确认）</legend>{contentCheckKeys.map(key => <label key={key}><input type="checkbox" checked={currentVerification && verification.content_checks[key]} onChange={event => setVerification(current => ({ ...current, content_checks: { ...current.content_checks, [key]: event.target.checked } }))} />{contentCheckLabels[key]}已核对</label>)}</fieldset>
        <button disabled={disabled("verify_artifact") || !verificationReady} onClick={submitVerification}>提交正式候选核验</button>
      </>}
    </>}
    <p>正式候选核验与批准是两项独立操作；核验不会自动批准。</p>
    <button disabled={disabled("review_artifact") || !artifact} onClick={() => decision("artifact", "approve")}>批准所选文档</button>
    <button disabled={disabled("review_artifact") || !artifact} onClick={() => decision("artifact", "revise")}>退回所选文档</button>
    <h3>后台任务控制</h3>
    <button disabled={disabled("cancel")} onClick={() => void send("cancel/", {})}>取消任务</button>
    <button disabled={disabled("retry")} onClick={() => void send("retry/", {})}>重试任务</button>
  </details></>;
}
