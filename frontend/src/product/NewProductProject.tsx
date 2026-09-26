import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ApiError } from "../api";
import { CenterLink, Field } from "../centers/shared";
import { createTask, getTask, updateTask, uploadSource, type CreateTask, type DocumentTask } from "./product-api";
import { formatBytes, goProduct, LoadState, outputNames, ProductIcon, productError, projectUrl, useProductOverview, useUnsavedWarning } from "./workbench-shared";

interface Submission { body: CreateTask; key: string; task: DocumentTask | null; remaining: File[]; start: boolean }
export default function NewProductProject() {
  const { data, error: loadError, reload } = useProductOverview();
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [background, setBackground] = useState("");
  const [conditions, setConditions] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [pending, setPending] = useState(false);
  const [createdId, setCreatedId] = useState("");
  const [leaving, setLeaving] = useState(false);
  const submission = useRef<Submission | null>(null);
  const lock = useRef(false);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useUnsavedWarning(!leaving && Boolean(title || requirements || files.length || pending));
  useEffect(() => () => request.current?.abort(), []);

  function addFiles(next: File[]) {
    if (pending || busy || !data) return;
    const accepted = data.capabilities.upload_extensions;
    const invalid = next.find(file => !accepted.some(ext => file.name.toLowerCase().endsWith(ext)) || file.size > data.capabilities.upload_max_bytes || file.size === 0);
    if (invalid) { setError(`“${invalid.name}”格式、大小或内容不符合要求。支持 ${accepted.join("、")}，单个文件最多 ${formatBytes(data.capabilities.upload_max_bytes)}，不可为空。`); return; }
    setFiles(current => [...current, ...next.filter(file => !current.some(old => old.name === file.name && old.size === file.size && old.lastModified === file.lastModified))]);
    setError("");
  }
  function leave(id: string) {
    flushSync(() => setLeaving(true));
    goProduct(projectUrl(id));
  }
  async function submit(start: boolean) {
    if (lock.current || !data) return;
    if (!submission.current && (!title.trim() || !requirements.trim())) { setError("请填写项目名称和建设目标。"); return; }
    lock.current = true; setBusy(true); setError("");
    const controller = new AbortController(); request.current = controller;
    if (!submission.current) submission.current = { key: crypto.randomUUID(), task: null, remaining: [...files], start,
      body: { title: title.trim(), input: { project: title.trim(), requirements: requirements.trim(), background: background.trim(), conditions: conditions.split("\n").map(value => value.trim()).filter(Boolean), items: [] }, ...(reviewer ? { reviewer_id: Number(reviewer) } : {}) } };
    const record = submission.current;
    setPending(true);
    try {
      if (!record.task) { setProgress("正在创建项目…"); record.task = await createTask(record.body, record.key, controller.signal); setCreatedId(record.task.id); }
      let currentTask: DocumentTask = record.task;
      while (record.remaining.length) {
        const file = record.remaining[0];
        const previousSourceIds = new Set(currentTask.sources.map(source => source.id));
        setProgress(`正在上传 ${file.name}…`);
        try {
          currentTask = await uploadSource(currentTask.id, file, currentTask.version, controller.signal);
        } catch (reason) {
          // A lost HTTP response may follow a successful upload. Reconcile only an
          // exact, newly created name/size/hash snapshot; never blindly repeat it.
          if (controller.signal.aborted) throw reason;
          const fresh: DocumentTask | null = await getTask(currentTask.id, controller.signal).catch(() => null);
          const hash = crypto.subtle ? Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))).map(byte => byte.toString(16).padStart(2, "0")).join("") : "";
          if (fresh && hash && fresh.sources.some(source => !previousSourceIds.has(source.id) && source.original_name === file.name && source.size === file.size && source.sha256 === hash)) currentTask = fresh;
          else throw reason;
        }
        record.task = currentTask;
        record.remaining = record.remaining.slice(1);
        setFiles([...record.remaining]);
      }
      if (record.start && Array.isArray(currentTask.actions) && currentTask.actions.includes("queue_blueprint") && !currentTask.blockers?.queue_blueprint) {
        setProgress("正在提交蓝图生成任务…");
        await updateTask(currentTask.id, "queue/", { expected_version: currentTask.version, action: "blueprint" }, controller.signal);
      }
      if (!controller.signal.aborted) leave(currentTask.id);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(productError(reason));
        if (reason instanceof ApiError && reason.status === 400 && !record.task) { submission.current = null; setPending(false); }
      }
    } finally { lock.current = false; if (!controller.signal.aborted) { setBusy(false); setProgress(""); } }
  }
  if (!data) return <LoadState error={loadError} reload={reload}/>;
  const canGenerate = data.capabilities.model_generation;
  return <div className="pd-workspace"><div className="pd-page-title"><div><CenterLink href="/centers/product/projects" className="pd-back-link">← 返回项目</CenterLink><h2>新建项目</h2><p>填写项目目标并上传资料，三类成果共用一份项目底稿。</p></div><span className="pd-badge info">第一步 · 项目资料</span></div>
    <div className="pd-new-layout"><form className="pd-panel pd-project-form" onSubmit={event => { event.preventDefault(); void submit(canGenerate); }}>
      <div className="pd-panel-heading"><h3>项目基本信息</h3><span className="pd-muted">带 * 为必填项</span></div>
      {error && <div className="pd-feedback" role="alert">{error}</div>}
      {pending && !busy && <p role="note" className="pd-service-note">{createdId ? `项目已保存，仍有 ${files.length} 个附件待处理。重试不会重新创建项目或上传已确认成功的附件。` : "正在确认上次创建结果，重试将复用相同请求，避免重复项目。"}</p>}
      <fieldset disabled={busy || pending} className="pd-form-fields">
        <Field id="new-project-title" label="项目名称 *"><input id="new-project-title" value={title} onChange={event => setTitle(event.target.value)} required maxLength={200} placeholder="例如：榆林选煤厂供配电系统建设项目"/></Field>
        <Field id="new-project-requirements" label="建设目标 *" hint="说明需要解决的问题、主要建设范围和期望成果。"><textarea id="new-project-requirements" required value={requirements} onChange={event => setRequirements(event.target.value)} maxLength={10000} rows={4} placeholder="请描述项目建设目标与范围…"/></Field>
        <div className="pd-form-columns"><Field id="new-project-background" label="项目背景"><textarea id="new-project-background" value={background} onChange={event => setBackground(event.target.value)} maxLength={10000} rows={3} placeholder="现状、使用场景或已有基础…"/></Field><Field id="new-project-conditions" label="约束条件" hint="每行一项，生成蓝图时保留这些条件。"><textarea id="new-project-conditions" value={conditions} onChange={event => setConditions(event.target.value)} maxLength={10000} rows={3} placeholder="例如：不改变现有设备数量"/></Field></div>
        <Field id="new-project-reviewer" label="蓝图与成果审核人"><select id="new-project-reviewer" value={reviewer} onChange={event => setReviewer(event.target.value)}><option value="">稍后指定审核人</option>{data.reviewers.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></Field>
        <div className="pd-panel-heading"><h3>上传资料 <small>可选</small></h3><span className="pd-muted">{files.length} 个待上传</span></div>
        <div className="pd-dropzone" onDragOver={event => { event.preventDefault(); }} onDrop={event => { event.preventDefault(); addFiles(Array.from(event.dataTransfer.files)); }}><ProductIcon name="upload"/><strong>选择文件，或拖拽文件到此处</strong><span>支持 {data.capabilities.upload_extensions.join("、")} · 单个文件最多 {formatBytes(data.capabilities.upload_max_bytes)}</span><button className="button secondary" type="button" onClick={() => input.current?.click()}>选择项目资料</button><input ref={input} type="file" className="sr-only" aria-label="选择项目资料文件" multiple accept={data.capabilities.upload_extensions.join(",")} onChange={event => { addFiles(Array.from(event.target.files || [])); event.target.value = ""; }}/></div>
        {files.length > 0 && <ul className="pd-upload-list" aria-label="待上传项目资料">{files.map((file, index) => <li key={`${file.name}-${index}`}><ProductIcon name="file"/><div><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></div><button type="button" className="text-button" aria-label={`移除 ${file.name}`} onClick={() => setFiles(values => values.filter((_, i) => i !== index))}>移除</button></li>)}</ul>}
      </fieldset>
      <div className="pd-form-footer"><span role="status" aria-live="polite">{progress || "资料保存在所属项目中，不会自动正式发布。"}</span><div className="pd-actions">{createdId && !busy && <button type="button" className="button secondary" onClick={() => leave(createdId)}>打开已创建项目</button>}{!pending && canGenerate && <button type="button" className="button secondary" disabled={busy} onClick={() => void submit(false)}>仅保存项目</button>}<button className="button primary" disabled={busy}>{busy ? "正在保存…" : pending ? "重试并继续" : canGenerate ? "保存并生成蓝图" : "创建项目"}<ProductIcon name="arrow"/></button></div></div>
    </form>
    <aside className="pd-new-aside"><section className="pd-panel"><span className="pd-stat-icon blue"><ProductIcon name="layers"/></span><h3>一次提交，持续复用</h3><p>技术方案、可研报告与汇报 PPT 共享项目事实；资料变化后，相关成果按版本重新核对。</p><ul>{Object.values(outputNames).map(name => <li key={name}><ProductIcon name="check"/>{name}</li>)}</ul></section><section className="pd-panel pd-subtle"><h3>接下来会发生什么</h3><ol><li>解析已上传资料，保留缺项与来源。</li><li>形成蓝图，确认建设目标、章节和条件。</li><li>经授权审核人确认后进入成果编制。</li></ol><p>生成在后台持续处理，离开页面后可从项目列表继续查看。</p></section>{!canGenerate && <section className="pd-service-note"><ProductIcon name="shield"/><p>当前尚未配置模型与预算授权。项目仍可保存、上传资料和人工编制蓝图；不会发送资料给模型。</p></section>}</aside></div>
  </div>;
}
