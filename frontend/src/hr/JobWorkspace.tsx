import { FormEvent, useEffect, useState } from "react";
import { isApiError } from "../api";
import { EmptyPanel, Field, SectionHeader } from "../centers/shared";
import { confirmJob, createJob, generateJob, JobInput, JobTask, listJobs, reviseJob, updateJob } from "./hr-api";

const emptyInput: JobInput = { title: "", department: "", objective: "", responsibilities: "", requirements: "" };
const fieldLabels: Record<keyof JobInput, string> = {
  title: "岗位名称", department: "所属部门", objective: "岗位目标", responsibilities: "岗位职责", requirements: "任职要求",
};
const missingLabels: Record<string, string> = { title: "岗位名称", objective: "岗位目标", responsibilities: "岗位职责", requirements: "任职要求" };

export default function JobWorkspace({ mode }: { mode: "profile" | "job" }) {
  const requestedTask = new URLSearchParams(window.location.search).get("task") || "";
  const [tasks, setTasks] = useState<JobTask[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<JobInput>(emptyInput);
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fatal, setFatal] = useState("");
  const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
  const selected = tasks.find(task => task.id === selectedId) ?? null;
  const bodyDirty = !!selected && body !== (selected.current_revision?.body ?? "");

  const selectTask = (task: JobTask) => {
    setSelectedId(task.id);
    setValues({ title: task.title, department: task.department, objective: task.objective, responsibilities: task.responsibilities, requirements: task.requirements });
    setBody(task.current_revision?.body ?? "");
    setError("");
  };
  const replaceTask = (task: JobTask) => {
    setTasks(current => current.some(item => item.id === task.id) ? current.map(item => item.id === task.id ? task : item) : [task, ...current]);
    selectTask(task);
  };
  const report = (caught: unknown) => setError(isApiError(caught) ? caught.message : caught instanceof Error ? caught.message : "人事服务请求失败。");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFatal("");
    listJobs(controller.signal).then(items => {
      setTasks(items);
      if (requestedTask) {
        const match = items.find(item => item.id === requestedTask);
        if (match) selectTask(match);
        else { setSelectedId(""); setValues(emptyInput); setBody(""); setFatal("指定岗位任务不存在或当前账号无权访问。"); }
      } else if (items[0]) selectTask(items[0]);
      setResolvedQuery(requestedTask);
    }).catch(caught => { if (!controller.signal.aborted) {
      if (requestedTask) setFatal("指定岗位任务不存在或当前账号无权访问。"); else report(caught);
      setResolvedQuery(requestedTask);
    } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [requestedTask]);
  useEffect(() => {
    if (!bodyDirty) return;
    const block = (event: Event) => { event.preventDefault(); setError("JD 正文存在未保存修改，请先保存人工修订。"); };
    window.addEventListener("portal:navigation-guard", block);
    return () => window.removeEventListener("portal:navigation-guard", block);
  }, [bodyDirty]);

  const run = async (operation: () => Promise<JobTask>) => {
    setBusy(true); setError("");
    try { replaceTask(await operation()); } catch (caught) { report(caught); } finally { setBusy(false); }
  };
  const submitCreate = (event: FormEvent) => {
    event.preventDefault();
    void run(() => createJob(values));
  };
  const guardBody = () => {
    if (!bodyDirty) return false;
    setError("JD 正文存在未保存修改，请先保存人工修订。");
    return true;
  };
  const startCreate = () => {
    if (guardBody()) return;
    setSelectedId(""); setValues(emptyInput); setBody(""); setError("");
  };

  if (resolvedQuery !== requestedTask) return <section className="center-panel" role="status">正在定位岗位任务…</section>;
  if (fatal) return <section className="center-panel" role="alert"><h2>无法打开岗位任务</h2><p>{fatal}</p><a href="/centers/hr/job">安全返回岗位任务列表</a></section>;

  return <>
    <SectionHeader title={mode === "profile" ? "岗位需求与人才画像" : "标准岗位说明"} description="岗位需求、JD 草稿、人工修订和确认版本均持久保存在服务端。" />
    {error && <p className="notice error" role="alert">{error}</p>}
    <section className="center-panel" aria-label="JD任务列表">
      <h3>岗位任务</h3>
      <button type="button" className="button secondary" disabled={busy} onClick={startCreate}>新建岗位任务</button>
      {loading ? <p role="status">正在加载岗位任务…</p> : tasks.length === 0 ? <EmptyPanel title="暂无岗位任务">先创建岗位需求，刷新或重新进入后仍可继续。</EmptyPanel> : <>
        <label htmlFor="hr-job-task">选择岗位任务</label>
        <select id="hr-job-task" value={selectedId} onChange={event => { const task = tasks.find(item => item.id === event.target.value); if (task && !guardBody()) selectTask(task); }}>
          {!selectedId && <option value="">正在新建岗位任务</option>}
          {tasks.map(task => <option key={task.id} value={task.id}>{task.title || "未命名岗位"} · {task.state}</option>)}
        </select>
      </>}
    </section>
    <form className="center-panel" onSubmit={selected ? event => event.preventDefault() : submitCreate}>
      <h3>{selected ? "编辑岗位需求" : "创建岗位需求"}</h3>
      <div className="center-grid">
        {(Object.keys(fieldLabels) as (keyof JobInput)[]).map(key => <Field key={key} id={`hr-job-${key}`} label={fieldLabels[key]}>
          {key === "title" || key === "department"
            ? <input id={`hr-job-${key}`} value={values[key]} maxLength={200} onChange={event => setValues({ ...values, [key]: event.target.value })} />
            : <textarea id={`hr-job-${key}`} value={values[key]} rows={3} maxLength={12000} onChange={event => setValues({ ...values, [key]: event.target.value })} />}
        </Field>)}
      </div>
      {selected?.missing_fields.length ? <p role="status">缺项：{selected.missing_fields.map(item => missingLabels[item] ?? item).join("、")}</p> : selected ? <p role="status">岗位需求必填项完整。</p> : null}
      <div className="center-actions">
        {selected
          ? <button className="button primary" type="button" disabled={busy} onClick={() => { if (!guardBody()) void run(() => updateJob(selected, values)); }}>保存岗位需求</button>
          : <button className="button primary" disabled={busy}>创建岗位需求</button>}
      </div>
    </form>
    {selected && mode === "job" && <section className="center-panel" aria-label="JD版本操作">
      <h3>JD 草稿与人工确认</h3>
      <p>状态：{selected.state}；任务版本：{selected.version}；输入版本：{selected.input_version}</p>
      <button type="button" disabled={busy || selected.missing_fields.length > 0} onClick={() => { if (!guardBody()) void run(() => generateJob(selected)); }}>生成确定性 JD 草稿</button>
      <Field id="hr-job-body" label="JD正文"><textarea id="hr-job-body" rows={12} value={body} onChange={event => setBody(event.target.value)} /></Field>
      <div className="center-actions">
        <button type="button" disabled={busy || !selected.current_revision || selected.current_revision_stale || !body.trim()} onClick={() => void run(() => reviseJob(selected, body))}>保存人工修订</button>
        <button type="button" disabled={busy || bodyDirty || !selected.current_revision || selected.current_revision_stale} onClick={() => { const revision = selected.current_revision; if (!guardBody() && revision) void run(() => confirmJob(selected, revision.id)); }}>确认当前版本为正式 JD</button>
      </div>
      {bodyDirty && <p role="status">JD 正文存在未保存修改，请先保存人工修订，再确认或执行其他操作。</p>}
      {selected.official_revision && <div className="center-preview"><h4>正式 JD · 版本 {selected.official_revision.version}</h4><p>{selected.official_revision.body}</p><p>确认时间：{selected.official_revision.confirmed_at ?? "未提供"}</p></div>}
      <ol className="center-checklist" aria-label="JD版本历史">{selected.revisions.map(revision => <li key={revision.id}>版本 {revision.version} · {revision.kind} · 输入版本 {revision.input_version}</li>)}</ol>
    </section>}
  </>;
}
