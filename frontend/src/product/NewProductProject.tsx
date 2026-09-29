import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ApiError } from "../api";
import { CenterLink, Field } from "../centers/shared";
import { createTask, getTask, updateTask, uploadSource, type CreateTask, type DocumentTask } from "./product-api";
import { formatBytes, goProduct, LoadState, outputNames, ProductIcon, productError, projectUrl, useProductOverview, useUnsavedWarning } from "./workbench-shared";
import './project-analysis.css';
import "./product-clean-layout.css";

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
  const [uploadProgress, setUploadProgress] = useState({ completed: 0, total: 0 });
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
    if (!submission.current && (!equipment || !files.length)) { setError('请上传一份设备清单和至少一份项目背景材料。'); return; }
    lock.current = true; setBusy(true); setError("");
    const controller = new AbortController(); request.current = controller;
    const projectName = title.trim() || equipment?.name.replace(/\.[^.]+$/, '').slice(0, 200) || '资料生成项目';
    if (!submission.current) {
      setUploadProgress({ completed: 0, total: files.length + 1 });
      submission.current = { key: crypto.randomUUID(), task: null, remaining: [...(equipment ? [equipment] : []), ...files], equipment, start,
        body: { title: projectName, intake_mode: 'equipment_background', input: { project: projectName, requirements: requirements.trim() || '根据上传的设备清单和甲方调研、项目需求及背景材料，完整编制项目蓝图、技术方案、可行性研究报告和汇报PPT。保持来源一致，不编造缺失参数，资料不足处明确列为待确认。', background: background.trim(), conditions: conditions.split("\n").map(value => value.trim()).filter(Boolean), items: [] } } };
    }
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
        setUploadProgress(value => ({ ...value, completed: value.total - record.remaining.length }));
        setFiles(record.remaining.filter(item => item !== record.equipment));
        if (!record.remaining.includes(record.equipment as File)) setEquipment(null);
      }
      if (record.start) {
        currentTask = await getTask(currentTask.id, controller.signal);
        record.task = currentTask;
        if (!['QUEUED', 'RUNNING', 'COMPLETED'].includes(currentTask.state) && !currentTask.blueprint) {
          setProgress("资料已保存，正在提交知识检索和蓝图生成…");
          await updateTask(currentTask.id, "queue/", { expected_version: currentTask.version, action: "start" }, controller.signal);
        }
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
  return <div className="pd-workspace pd-clean-layout"><div className="pd-page-title"><div><CenterLink href="/centers/product/projects" className="pd-back-link">← 历史项目</CenterLink><h1>上传资料生成文档</h1><p>上传设备清单和项目背景，自动建立项目。</p></div><span className="pd-badge info">设备清单 + 项目资料</span></div>
    <div className="pd-new-layout"><form className="pd-panel pd-project-form" onSubmit={event => { event.preventDefault(); void submit(canGenerate); }}>
      <div className="pd-panel-heading"><h3>上传项目资料</h3></div>
      {error && <div className="pd-feedback" role="alert">{error}</div>}
      {pending && !busy && <p role="note" className="pd-service-note">{createdId ? `项目已保存，剩余 ${submission.current?.remaining.length || 0} 份资料待上传；如资料已齐全，重试将继续提交生成。已确认成功的附件不会重复上传。` : "正在确认上次创建结果，重试将复用相同请求，避免重复项目。"}</p>}
      <fieldset disabled={busy || pending} className="pd-form-fields">
        <details className="pd-optional-project-fields"><summary>补充项目名称与要求（选填）</summary>
        <Field id="new-project-title" label="项目名称" hint="不填写时使用设备清单文件名作为历史项目名称。"><input id="new-project-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={200} placeholder="例如：榆林选煤厂供配电系统建设项目"/></Field>
        <Field id="new-project-requirements" label="额外编制要求" hint="甲方需求已在背景文件中说明时，可不重复填写。"><textarea id="new-project-requirements" value={requirements} onChange={event => setRequirements(event.target.value)} maxLength={10000} rows={4} placeholder="补充需要强调的建设目标或编制要求…"/></Field>
        <div className="pd-form-columns"><Field id="new-project-background" label="项目背景"><textarea id="new-project-background" value={background} onChange={event => setBackground(event.target.value)} maxLength={10000} rows={3} placeholder="现状、使用场景或已有基础…"/></Field><Field id="new-project-conditions" label="约束条件" hint="每行一项，生成蓝图时保留这些条件。"><textarea id="new-project-conditions" value={conditions} onChange={event => setConditions(event.target.value)} maxLength={10000} rows={3} placeholder="例如：不改变现有设备数量"/></Field></div>
        </details>
        <div className="pd-panel-heading"><h3>设备清单 <small>1 份</small></h3></div>
        <Field id="equipment-source" label="设备清单文件" hint="用于读取设备名称、数量和单位。"><input id="equipment-source" type="file" accept={data.capabilities.upload_extensions.join(',')} onChange={event => { chooseEquipment(event.target.files?.[0] || null); event.target.value = ''; }}/></Field>
        {equipment && <p className="pd-service-note">{equipment.name} · {formatBytes(equipment.size)} <button className="text-button" type="button" onClick={() => setEquipment(null)}>移除设备清单</button></p>}
        <div className="pd-panel-heading"><h3>项目背景材料 <small>支持多份</small></h3><span className="pd-muted">{files.length} 个待上传</span></div>
        <div className="pd-dropzone" onDragOver={event => { event.preventDefault(); }} onDrop={event => { event.preventDefault(); addFiles(Array.from(event.dataTransfer.files)); }}><ProductIcon name="upload"/><strong>选择或拖拽项目背景材料</strong><span>支持 {data.capabilities.upload_extensions.join("、")} · 单个文件最多 {formatBytes(data.capabilities.upload_max_bytes)}</span><button className="button secondary" type="button" onClick={() => input.current?.click()}>选择项目资料</button><input ref={input} type="file" className="sr-only" aria-label="选择项目资料文件" multiple accept={data.capabilities.upload_extensions.join(",")} onChange={event => { addFiles(Array.from(event.target.files || [])); event.target.value = ""; }}/></div>
        {files.length > 0 && <ul className="pd-upload-list" aria-label="待上传项目资料">{files.map((file, index) => <li key={`${file.name}-${index}`}><ProductIcon name="file"/><div><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></div><button type="button" className="text-button" aria-label={`移除 ${file.name}`} onClick={() => setFiles(values => values.filter((_, i) => i !== index))}>移除</button></li>)}</ul>}
      </fieldset>
      {busy && <section className="pd-upload-progress" role="status" aria-live="polite" aria-busy="true"><span className="pd-spinner"/><div><strong>{progress}</strong><progress className="pd-native-progress" max={uploadProgress.total || 1} value={uploadProgress.completed} aria-label="资料上传解析进度" aria-valuetext={`${uploadProgress.completed} / ${uploadProgress.total} 份资料已完成`}/><p>已完成 {uploadProgress.completed} / {uploadProgress.total} 份资料</p></div></section>}
      <div className="pd-form-footer"><span role="status" aria-live="polite">{busy ? '完成后自动打开项目进度。' : "完成后自动保存，可在成果页下载。"}</span><div className="pd-actions">{createdId && !busy && <button type="button" className="button secondary" onClick={() => leave(createdId)}>查看项目进度</button>}<button className="button primary" disabled={busy}>{busy ? "正在提交…" : pending ? "重试并继续" : canGenerate ? "上传并开始生成" : "上传并保存资料"}<ProductIcon name="arrow"/></button></div></div>
    </form>
    <aside className="pd-new-aside"><section className="pd-panel"><h3>将生成</h3><ul>{Object.values(outputNames).map(name => <li key={name}><ProductIcon name="file"/>{name}</li>)}</ul></section><section className="pd-panel pd-subtle"><h3>流程</h3><ol><li>解析资料</li><li>审核蓝图</li><li>生成文档</li></ol></section>{!data.capabilities.retrieval && <section className="pd-service-note" role="note"><ProductIcon name="shield"/><p><strong>仅使用项目资料</strong><br/>知识库未启用。</p></section>}{!canGenerate && <section className="pd-service-note"><ProductIcon name="shield"/><p>生成授权未开通，资料仍可保存。</p></section>}</aside></div>
  </div>;
}
