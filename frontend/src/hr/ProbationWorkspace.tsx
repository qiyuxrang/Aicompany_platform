import { FormEvent, useEffect, useState } from "react";
import { isApiError } from "../api";
import { EmptyPanel, Field, SectionHeader } from "../centers/shared";
import { createProbation, listProbations, ProbationAction, ProbationCase, transitionProbation } from "./hr-api";

const stateLabels: Record<ProbationCase["state"], string> = {
  draft: "草稿", collecting: "材料收集中", manager_pending: "主管待审批", hr_pending: "HR待归档", archived: "已归档",
};
const actionLabels: Record<ProbationAction, string> = {
  start_collecting: "开始收集材料", submit_to_manager: "提交主管审批", manager_approve: "主管批准", hr_archive: "HR确认并归档",
};

export default function ProbationWorkspace({ canManage = true }: { canManage?: boolean }) {
  const requestedCase = new URLSearchParams(window.location.search).get("case") || "";
  const [cases, setCases] = useState<ProbationCase[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState({ employee_name: "", position: "", assigned_manager_id: "", materials: "", notes: "" });
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fatal, setFatal] = useState("");
  const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
  const selected = cases.find(item => item.id === selectedId) ?? null;

  const report = (caught: unknown) => setError(isApiError(caught) ? caught.message : caught instanceof Error ? caught.message : "转正服务请求失败。");
  const replaceCase = (item: ProbationCase) => {
    setCases(current => current.some(entry => entry.id === item.id) ? current.map(entry => entry.id === item.id ? item : entry) : [item, ...current]);
    setSelectedId(item.id);
    setComment("");
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setFatal("");
    listProbations(controller.signal).then(items => {
      setCases(items);
      if (requestedCase) {
        const match = items.find(item => item.id === requestedCase);
        if (match) setSelectedId(match.id);
        else { setSelectedId(""); setFatal("指定转正事项不存在或当前账号无权访问。"); }
      } else if (items[0]) setSelectedId(items[0].id);
      setResolvedQuery(requestedCase);
    }).catch(caught => { if (!controller.signal.aborted) {
      if (requestedCase) setFatal("指定转正事项不存在或当前账号无权访问。"); else report(caught);
      setResolvedQuery(requestedCase);
    } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [requestedCase]);

  const create = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    if (!/^\d+$/.test(form.assigned_manager_id) || Number(form.assigned_manager_id) <= 0) { setError("主管 ID 必须为正整数。"); return; }
    setBusy(true);
    try {
      const item = await createProbation({
        employee_name: form.employee_name, position: form.position, assigned_manager_id: Number(form.assigned_manager_id),
        materials: form.materials.split(/\r?\n/).map(value => value.trim()).filter(Boolean), notes: form.notes,
      });
      replaceCase(item);
      setForm({ employee_name: "", position: "", assigned_manager_id: "", materials: "", notes: "" });
    } catch (caught) { report(caught); } finally { setBusy(false); }
  };
  const transition = async (action: ProbationAction) => {
    if (!selected) return;
    setBusy(true); setError("");
    try { replaceCase(await transitionProbation(selected, action, comment)); } catch (caught) { report(caught); } finally { setBusy(false); }
  };

  if (resolvedQuery !== requestedCase) return <section className="center-panel" role="status">正在定位转正事项…</section>;
  if (fatal) return <section className="center-panel" role="alert"><h2>无法打开转正事项</h2><p>{fatal}</p><a href="/centers/hr/probation">安全返回转正事项列表</a></section>;

  return <>
    <SectionHeader title="转正工作流" description="按确定性状态机办理；AI不可用不影响人工提交、审批和归档。" />
    {error && <p className="notice error" role="alert">{error}</p>}
    {canManage && <form className="center-panel" onSubmit={create}>
      <h3>创建转正事项</h3>
      <div className="center-grid">
        <Field id="probation-employee" label="员工姓名"><input id="probation-employee" required value={form.employee_name} onChange={event => setForm({ ...form, employee_name: event.target.value })} /></Field>
        <Field id="probation-position" label="转正岗位"><input id="probation-position" required value={form.position} onChange={event => setForm({ ...form, position: event.target.value })} /></Field>
        <Field id="probation-manager" label="主管 ID"><input id="probation-manager" required inputMode="numeric" value={form.assigned_manager_id} onChange={event => setForm({ ...form, assigned_manager_id: event.target.value })} /></Field>
        <Field id="probation-materials" label="材料清单" hint="每行一项；空行不会提交。"><textarea id="probation-materials" rows={4} value={form.materials} onChange={event => setForm({ ...form, materials: event.target.value })} /></Field>
        <Field id="probation-notes" label="备注"><textarea id="probation-notes" rows={3} value={form.notes} onChange={event => setForm({ ...form, notes: event.target.value })} /></Field>
      </div>
      <button className="button primary" disabled={busy}>创建转正事项</button>
    </form>}
    <section className="center-panel" aria-label="转正事项列表">
      <h3>转正事项</h3>
      {loading ? <p role="status">正在加载转正事项…</p> : cases.length === 0 ? <EmptyPanel title="暂无转正事项">创建后由服务端保存，刷新后可继续。</EmptyPanel> : <>
        <label htmlFor="probation-case">选择转正事项</label>
        <select id="probation-case" value={selectedId} onChange={event => { setSelectedId(event.target.value); setComment(""); setError(""); }}>
          {cases.map(item => <option key={item.id} value={item.id}>{item.employee_name} · {stateLabels[item.state]}</option>)}
        </select>
      </>}
      {selected && <div className="center-preview">
        <h4>{selected.employee_name} · {selected.position}</h4>
        <p>状态：{stateLabels[selected.state]}；版本：{selected.version}；主管 ID：{selected.assigned_manager_id}</p>
        <p>材料：{selected.materials.join("、") || "暂无"}</p>
        <p>辅助方式：{selected.assistant_mode === "manual" ? "人工流程" : selected.assistant_mode}；原因：{selected.assistant_reason || "未提供"}</p>
        <Field id="probation-comment" label="处理意见"><textarea id="probation-comment" rows={3} value={comment} onChange={event => setComment(event.target.value)} /></Field>
        <div className="center-actions">{selected.actions.map(action => <button key={action} type="button" disabled={busy || ((action === "manager_approve" || action === "hr_archive") && !comment.trim())} onClick={() => void transition(action)}>{actionLabels[action]}</button>)}</div>
        {selected.actions.length === 0 && <p>当前身份或状态没有可执行操作。</p>}
        <ol aria-label="转正流转记录">{selected.transitions.map((item, index) => <li key={`${item.created_at}-${index}`}>{stateLabels[item.to_state as ProbationCase["state"]] ?? item.to_state} · {item.comment || "无意见"}</li>)}</ol>
        <ol aria-label="转正修改历史">{selected.revisions.map((revision, index) => <li key={`${revision.created_at}-${index}`}>案例版本 {revision.case_version} · 修改字段：{revision.changed_fields.join("、") || "无"} · 修改人 {revision.actor_id} · {revision.created_at}</li>)}</ol>
      </div>}
    </section>
  </>;
}
