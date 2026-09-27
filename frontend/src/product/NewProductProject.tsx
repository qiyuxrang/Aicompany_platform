import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ApiError } from "../api";
import { CenterLink, Field } from "../centers/shared";
import { createTask, getTask, updateTask, uploadSource, type CreateTask, type DocumentTask } from "./product-api";
import { formatBytes, goProduct, LoadState, outputNames, ProductIcon, productError, projectUrl, useProductOverview, useUnsavedWarning } from "./workbench-shared";

interface Submission { body: CreateTask; key: string; task: DocumentTask | null; remaining: File[]; equipment: File | null; start: boolean }
export default function NewProductProject() {
  const { data, error: loadError, reload } = useProductOverview();
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [background, setBackground] = useState("");
  const [conditions, setConditions] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [equipment, setEquipment] = useState<File | null>(null);
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
  useUnsavedWarning(!leaving && Boolean(title || requirements || files.length || equipment || pending));
  useEffect(() => () => request.current?.abort(), []);

  function addFiles(next: File[]) {
    if (pending || busy || !data) return;
    const accepted = data.capabilities.upload_extensions;
    const invalid = next.find(file => !accepted.some(ext => file.name.toLowerCase().endsWith(ext)) || file.size > data.capabilities.upload_max_bytes || file.size === 0);
    if (invalid) { setError(`“${invalid.name}”格式、大小或内容不符合要求。支持 ${accepted.join("、")}，单个文件最多 ${formatBytes(data.capabilities.upload_max_bytes)}，不可为空。`); return; }
    setFiles(current => [...current, ...next.filter(file => !current.some(old => old.name === file.name && old.size === file.size && old.lastModified === file.lastModified))]);
    setError("");
  }
  function chooseEquipment(file: File | null) {
    if (!file || !data || pending || busy) return;
    if (!data.capabilities.upload_extensions.some(ext => file.name.toLowerCase().endsWith(ext)) || !file.size || file.size > data.capabilities.upload_max_bytes) {
      setError('设备清单格式、大小或内容不符合要求，请选择受支持的非空文件。'); return;
    }
    setEquipment(file); setError('');
  }
  function leave(id: string) {
    flushSync(() => setLeaving(true));
    goProduct(projectUrl(id));
  }
  async function submit(start: boolean) {
    if (lock.current || !data) return;
    if (!submission.current && (!title.trim() || !requirements.trim())) { setError("请填写项目名称和建设目标。"); return; }
    if (!submission.current && start && (!equipment || !files.length)) { setError('开始生成前，请上传一份设备清单和至少一份项目背景材料。'); return; }
    lock.current = true; setBusy(true); setError("");
    const controller = new AbortController(); request.current = controller;
    if (!submission.current) submission.current = { key: crypto.randomUUID(), task: null, remaining: [...(equipment ? [equipment] : []), ...files], equipment, start,
      body: { title: title.trim(), intake_mode: 'equipment_background', input: { project: title.trim(), requirements: requirements.trim(), background: background.trim(), conditions: conditions.split("\n").map(value => value.trim()).filter(Boolean), items: [] } } };
    const record = submission.current;
    setPending(true);
    try {
      if (!record.task) { setProgress("正在创建项目…"); record.task = await createTask(record.body, record.key, controller.signal); setCreatedId(record.task.id); }
      let currentTask: DocumentTask = record.task;
      while (record.remaining.length) {
        const file = record.remaining[0];
        const previousSourceIds = new Set(currentTask.sources.map(source => source.id));
        setProgress(`正在上传并解析 ${file.name}…`);
        try {
          currentTask = await uploadSource(currentTask.id, file, currentTask.version, controller.signal, file === record.equipment ? 'equipment' : 'background');
        } catch (reason) {
          // A lost HTTP response may follow a successful upload. Reconcile only an
          // exact, newly created name/size/hash snapshot; never blindly repeat it.
          if (controller.signal.aborted) throw reason;
          const fresh: DocumentTask | null = await getTask(currentTask.id, controller.signal).catch(() => null);
          const hash = crypto.subtle ? Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))).map(byte => byte.toString(16).padStart(2, "0")).join("") : "";
          if (fresh && hash && fresh.sources.some(source => !previousSourceIds.has(source.id) && source.original_name === file.name && source.size === file.size && source.sha256 === hash && source.purpose === (file === record.equipment ? 'equipment' : 'background'))) currentTask = fresh;
          else throw reason;
        }
        record.task = currentTask;
        record.remaining = record.remaining.slice(1);
        setFiles(record.remaining.filter(item => item !== record.equipment));
        if (!record.remaining.includes(record.equipment as File)) setEquipment(null);
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
        <div className="pd-panel-heading"><h3>设备清单 <small>1 份</small></h3></div>
        <Field id="equipment-source" label="设备清单文件" hint="这份文件提供设备名称、数量和单位；与背景材料分开管理。"><input id="equipment-source" type="file" accept={data.capabilities.upload_extensions.join(',')} onChange={event => { chooseEquipment(event.target.files?.[0] || null); event.target.value = ''; }}/></Field>
        {equipment && <p className="pd-service-note">{equipment.name} · {formatBytes(equipment.size)} <button className="text-button" type="button" onClick={() => setEquipment(null)}>移除设备清单</button></p>}
        <div className="pd-panel-heading"><h3>项目背景材料 <small>支持多份</small></h3><span className="pd-muted">{files.length} 个待上传</span></div>
        <div className="pd-dropzone" onDragOver={event => { event.preventDefault(); }} onDrop={event => { event.preventDefault(); addFiles(Array.from(event.dataTransfer.files)); }}><ProductIcon name="upload"/><strong>选择甲方调研、项目现状或需求材料，也可拖拽到此处</strong><span>支持 {data.capabilities.upload_extensions.join("、")} · 单个文件最多 {formatBytes(data.capabilities.upload_max_bytes)}</span><button className="button secondary" type="button" onClick={() => input.current?.click()}>选择项目资料</button><input ref={input} type="file" className="sr-only" aria-label="选择项目资料文件" multiple accept={data.capabilities.upload_extensions.join(",")} onChange={event => { addFiles(Array.from(event.target.files || [])); event.target.value = ""; }}/></div>
        {files.length > 0 && <ul className="pd-upload-list" aria-label="待上传项目资料">{files.map((file, index) => <li key={`${file.name}-${index}`}><ProductIcon name="file"/><div><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></div><button type="button" className="text-button" aria-label={`移除 ${file.name}`} onClick={() => setFiles(values => values.filter((_, i) => i !== index))}>移除</button></li>)}</ul>}
      </fieldset>
      <div className="pd-form-footer"><span role="status" aria-live="polite">{progress || "资料保存在所属项目中，不会自动正式发布。"}</span><div className="pd-actions">{createdId && !busy && <button type="button" className="button secondary" onClick={() => leave(createdId)}>打开已创建项目</button>}{!pending && canGenerate && <button type="button" className="button secondary" disabled={busy} onClick={() => void submit(false)}>仅保存项目</button>}<button className="button primary" disabled={busy}>{busy ? "正在保存…" : pending ? "重试并继续" : canGenerate ? "开始生成" : "创建项目"}<ProductIcon name="arrow"/></button></div></div>
    </form>
    <aside className="pd-new-aside"><section className="pd-panel"><span className="pd-stat-icon blue"><ProductIcon name="layers"/></span><h3>一次提交，持续复用</h3><p>技术方案、可研报告与汇报 PPT 共享项目事实；资料变化后，相关成果按版本重新核对。</p><ul>{Object.values(outputNames).map(name => <li key={name}><ProductIcon name="check"/>{name}</li>)}</ul></section><section className="pd-panel pd-subtle"><h3>接下来会发生什么</h3><ol><li>解析背景材料并分析设备清单。</li><li>经过 RAGFlow 知识检索节点后形成蓝图。</li><li>人工审核蓝图，最多支持 3 轮修改。</li><li>蓝图通过后自动生成技术方案、可研报告与 PPT。</li></ol><p>生成在后台持续处理，离开页面后可从项目列表继续查看。</p></section>{!data.capabilities.retrieval && <section className="pd-service-note" role="note"><ProductIcon name="shield"/><p><strong>RAGFlow 等待接入</strong><br/>本次任务使用测试预览链路，只依据当前项目资料展示效果，不会显示或伪造知识库命中。</p></section>}{!canGenerate && <section className="pd-service-note"><ProductIcon name="shield"/><p>当前尚未取得模型调用与资料外发授权。项目仍可保存、上传资料和人工编制蓝图；不会发送资料给模型。</p></section>}</aside></div>
  </div>;
}
