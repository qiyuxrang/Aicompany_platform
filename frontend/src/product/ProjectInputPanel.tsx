import { useState } from "react";
import { Field } from "../centers/shared";
import type { DocumentTask, TaskInput } from "./product-api";
import { LoadState, useProductOverview, useUnsavedWarning } from "./workbench-shared";

export default function ProjectInputPanel({ task, disabled, onAction }: { task: DocumentTask; disabled: (action: string) => boolean; onAction: (endpoint: string, body: object) => Promise<unknown> }) {
  const [draft, setDraft] = useState<TaskInput | null>(null);
  const [title, setTitle] = useState(task.title);
  const [baseVersion, setBaseVersion] = useState(task.version);
  const [assigning, setAssigning] = useState(false);
  const [reviewer, setReviewer] = useState(String(task.reviewer_id || ""));
  const [reason, setReason] = useState("");
  const { data, error, reload } = useProductOverview("", assigning);
  useUnsavedWarning(Boolean(draft) || Boolean(assigning && reason));
  const stale = !!draft && task.version !== baseVersion;
  function edit() {
    const input = task.input;
    setDraft(input ? { project: input.project, requirements: input.requirements, background: input.background, conditions: [...input.conditions], items: input.items.map(({ row_id, name, quantity, unit }) => ({ row_id, name, quantity, unit })) } : { project: task.title, requirements: "", background: "", conditions: [], items: [] });
    setTitle(task.title); setBaseVersion(task.version);
  }
  return <section className="pd-panel"><div className="pd-panel-heading"><h3>项目底稿与审核人</h3><span className="pd-muted">输入版本 v{task.input_version}</span></div>
    {!draft ? <div className="pd-actions"><button className="button secondary" disabled={disabled("edit")} onClick={edit}>编辑项目底稿</button><button className="button secondary" disabled={disabled("assign_reviewer")} onClick={() => { setAssigning(value => !value); setReviewer(String(task.reviewer_id || "")); }}>指定或改派审核人</button></div> : <form onSubmit={async event => { event.preventDefault(); if (!stale && await onAction("", { title: title.trim(), input: { ...draft, conditions: draft.conditions.map(text => text.trim()).filter(Boolean) } })) setDraft(null); }}>
      {stale && <p className="pd-feedback" role="alert">项目已更新，本地编辑已保留。请取消编辑并重新载入后核对。</p>}
      <fieldset className="pd-form-fields" disabled={stale || disabled("edit")}>
        <Field id="edit-project-title" label="项目名称"><input id="edit-project-title" required maxLength={200} value={title} onChange={event => { setTitle(event.target.value); setDraft({ ...draft, project: event.target.value }); }}/></Field>
        <Field id="edit-project-requirements" label="建设目标"><textarea id="edit-project-requirements" required maxLength={10000} value={draft.requirements} onChange={event => setDraft({ ...draft, requirements: event.target.value })}/></Field>
        <div className="pd-form-columns"><Field id="edit-project-background" label="项目背景"><textarea id="edit-project-background" maxLength={50000} value={draft.background} onChange={event => setDraft({ ...draft, background: event.target.value })}/></Field><Field id="edit-project-conditions" label="约束条件（每行一项）"><textarea id="edit-project-conditions" maxLength={10000} value={draft.conditions.join("\n")} onChange={event => setDraft({ ...draft, conditions: event.target.value.split("\n") })}/></Field></div>
        <h4>设备清单</h4><div className="pd-table-wrap"><table className="pd-table"><thead><tr><th>原行号</th><th>设备名称</th><th>数量</th><th>单位</th><th>操作</th></tr></thead><tbody>{draft.items.map((item, index) => <tr key={index}>{(["row_id", "name", "quantity", "unit"] as const).map(field => <td key={field}><input aria-label={`设备第${index + 1}行-${field}`} value={item[field]} onChange={event => setDraft({ ...draft, items: draft.items.map((value, i) => i === index ? { ...value, [field]: event.target.value } : value) })}/></td>)}<td><button type="button" className="text-button" onClick={() => setDraft({ ...draft, items: draft.items.filter((_, i) => i !== index) })}>移除</button></td></tr>)}</tbody></table></div><button className="text-button" type="button" onClick={() => setDraft({ ...draft, items: [...draft.items, { row_id: "", name: "", quantity: "", unit: "" }] })}>＋ 添加设备行</button>
      </fieldset><p className="pd-muted">修改会保存新的输入版本，保留来源及编辑痕迹，相关蓝图与成果需要重新确认。</p><div className="pd-actions"><button className="button primary" disabled={stale || disabled("edit")}>保存项目底稿</button><button type="button" className="button secondary" onClick={() => { if (window.confirm("放弃当前未保存的项目底稿？")) setDraft(null); }}>取消编辑</button></div>
    </form>}
    {assigning && <div className="pd-reviewer-form">{!data ? <LoadState error={error} reload={reload}/> : <form onSubmit={async event => { event.preventDefault(); if (await onAction("reviewer/", { reviewer_id: Number(reviewer), reason: reason.trim() })) { setAssigning(false); setReason(""); } }}><Field id="project-reviewer" label="授权审核人"><select id="project-reviewer" disabled={disabled("assign_reviewer")} value={reviewer} onChange={event => setReviewer(event.target.value)} required><option value="">选择审核人</option>{data.reviewers.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></Field><Field id="reviewer-change-reason" label="指定或改派原因"><input id="reviewer-change-reason" required maxLength={2000} disabled={disabled("assign_reviewer")} value={reason} onChange={event => setReason(event.target.value)}/></Field><button className="button primary" disabled={disabled("assign_reviewer") || !reviewer || !reason.trim()}>保存审核人</button><p className="pd-muted">改派会重新核对既有批准的有效性，不会把原审核人的签认转移给新审核人。</p></form>}</div>}
  </section>;
}
