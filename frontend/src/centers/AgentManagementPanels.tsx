import { FormEvent, useEffect, useRef, useState } from 'react';
import { isApiError } from '../api';
import {
  AgentEmployee, AgentPage, AgentUsage, createAgentEmployee, getAgentUsage,
  listAgentEmployees, nextAgentPage, updateAgentEmployee,
} from '../agent-api';

const departments = [
  { code: 'product', name: '产品' },
  { code: 'hr', name: '人事' },
  { code: 'finance', name: '财务' },
  { code: 'engineering', name: '工程' },
];

const errorText = (error: unknown) => isApiError(error)
  ? error.status === 403 ? `访问被拒绝：${error.message}` : error.message
  : '暂时无法读取或提交，请检查连接后重试。';
const mergeEmployees = (current: AgentEmployee[], incoming: AgentEmployee[]) =>
  [...new Map([...current, ...incoming].map(employee => [employee.id, employee])).values()];

function EmployeeRow({ employee, onSave }: {
  employee: AgentEmployee;
  onSave: (id: number, changes: { display_name?: string; department_code?: string; is_active?: boolean }) => Promise<void>;
}) {
  const [displayName, setDisplayName] = useState(employee.display_name);
  const [departmentCode, setDepartmentCode] = useState(employee.department_code);
  const [isActive, setIsActive] = useState(employee.is_active);
  const [saving, setSaving] = useState(false);
  const changed = displayName.trim() !== employee.display_name.trim() || departmentCode !== employee.department_code || isActive !== employee.is_active;

  useEffect(() => {
    setDisplayName(employee.display_name);
    setDepartmentCode(employee.department_code);
    setIsActive(employee.is_active);
  }, [employee.display_name, employee.department_code, employee.is_active]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const changes = {
      ...(displayName.trim() !== employee.display_name.trim() ? { display_name: displayName.trim() } : {}),
      ...(departmentCode !== employee.department_code ? { department_code: departmentCode } : {}),
      ...(isActive !== employee.is_active ? { is_active: isActive } : {}),
    };
    if (!Object.keys(changes).length) return;
    setSaving(true);
    try {
      await onSave(employee.id, changes);
    } catch {
      setDisplayName(employee.display_name);
      setDepartmentCode(employee.department_code);
      setIsActive(employee.is_active);
    } finally {
      setSaving(false);
    }
  };

  return <article className="agent-management-record">
    <h3>{employee.display_name || employee.username} · 员工 ID {employee.id}</h3>
    <p className="agent-muted">账号：{employee.username} · 当前状态：{employee.is_active ? '已启用' : '已停用'}</p>
    <form className="agent-actions" onSubmit={submit}>
      <label>显示名称<input aria-label={`员工 ${employee.id} 显示名称`} maxLength={80} value={displayName} disabled={saving} onChange={event => setDisplayName(event.target.value)}/></label>
      <label>所属部门<select aria-label={`员工 ${employee.id} 所属部门`} value={departmentCode} disabled={saving} onChange={event => setDepartmentCode(event.target.value)}>{departments.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
      <label className="agent-check"><input aria-label={`员工 ${employee.id} 启用状态`} type="checkbox" checked={isActive} disabled={saving} onChange={event => setIsActive(event.target.checked)}/>启用账号</label>
      <button className="button secondary" disabled={saving || !changed}>保存员工 {employee.id} 修改</button>
    </form>
  </article>;
}

export default function AgentManagementPanels({ preview = false }: { preview?: boolean }) {
  const [employees, setEmployees] = useState<AgentPage<AgentEmployee>>({ items: [] });
  const [employeesLoading, setEmployeesLoading] = useState(false);
  const [employeeError, setEmployeeError] = useState('');
  const [employeeNotice, setEmployeeNotice] = useState('');
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [departmentCode, setDepartmentCode] = useState('');
  const [creating, setCreating] = useState(false);
  const [departmentFilter, setDepartmentFilter] = useState('');
  const [ownerFilter, setOwnerFilter] = useState('');
  const [startFilter, setStartFilter] = useState('');
  const [endFilter, setEndFilter] = useState('');
  const [usage, setUsage] = useState<AgentUsage | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState('');
  const employeeRequest = useRef<AbortController | null>(null);
  const usageRequest = useRef<AbortController | null>(null);

  const loadEmployees = async (page = 1, append = false) => {
    employeeRequest.current?.abort();
    const controller = new AbortController();
    employeeRequest.current = controller;
    setEmployeesLoading(true);
    setEmployeeError('');
    setEmployeeNotice('');
    try {
      const result = await listAgentEmployees(page, controller.signal);
      if (!controller.signal.aborted) setEmployees(current => ({
        ...result,
        items: append ? mergeEmployees(current.items, result.items) : result.items,
      }));
    } catch (error) {
      if (!controller.signal.aborted) {
        setEmployees({ items: [], total: 0, page });
        setEmployeeError(errorText(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        if (employeeRequest.current === controller) employeeRequest.current = null;
        setEmployeesLoading(false);
      }
    }
  };

  useEffect(() => {
    if (preview) {
      employeeRequest.current?.abort();
      usageRequest.current?.abort();
      setEmployees({ items: [] });
      setUsage(null);
      setEmployeeError('');
      setUsageError('');
      return;
    }
    void loadEmployees();
    return () => {
      employeeRequest.current?.abort();
      usageRequest.current?.abort();
    };
  }, [preview]);

  const createEmployee = async (event: FormEvent) => {
    event.preventDefault();
    if (creating || !departments.some(item => item.code === departmentCode)) return;
    setCreating(true);
    setEmployeeError('');
    setEmployeeNotice('');
    try {
      const created = await createAgentEmployee(username.trim(), displayName.trim(), departmentCode);
      setEmployees(current => ({
        ...current,
        total: (current.total ?? current.items.length) + 1,
        items: mergeEmployees([created], current.items),
      }));
      setEmployeeNotice(created.is_active
        ? '员工账号已创建；服务端返回状态为已启用，请由管理员核对登录开通状态。'
        : '员工账号已创建，当前停用且未设置登录密码；待管理员开通登录。');
      setUsername('');
      setDisplayName('');
      setDepartmentCode('');
    } catch (error) {
      setEmployees({ items: [] });
      setEmployeeError(errorText(error));
    } finally {
      setCreating(false);
    }
  };

  const saveEmployee = async (id: number, changes: { display_name?: string; department_code?: string; is_active?: boolean }) => {
    setEmployeeError('');
    setEmployeeNotice('');
    try {
      const updated = await updateAgentEmployee(id, changes);
      setEmployees(current => ({ ...current, items: current.items.map(employee => employee.id === id ? updated : employee) }));
      setEmployeeNotice(`员工 ${id} 信息已按服务端结果更新。`);
    } catch (error) {
      setEmployees({ items: [] });
      setEmployeeError(`修改未成功：${errorText(error)}`);
      throw error;
    }
  };

  const queryUsage = async (event: FormEvent) => {
    event.preventDefault();
    usageRequest.current?.abort();
    setUsage(null);
    setUsageError('');
    const ownerId = ownerFilter.trim();
    if (ownerId && (!/^[0-9]+$/.test(ownerId) || !Number.isSafeInteger(Number(ownerId)) || Number(ownerId) < 1)) {
      setUsageError('员工 ID 须为正整数。');
      return;
    }
    const start = startFilter ? new Date(startFilter) : null;
    const end = endFilter ? new Date(endFilter) : null;
    if ((start && !Number.isFinite(start.getTime())) || (end && !Number.isFinite(end.getTime()))) {
      setUsageError('时间范围无效，请重新选择。');
      return;
    }
    if (start && end && start > end) {
      setUsageError('开始时间不能晚于结束时间。');
      return;
    }
    const filters = {
      ...(departmentFilter ? { department_code: departmentFilter } : {}),
      ...(ownerId ? { owner_id: ownerId } : {}),
      ...(start ? { start: start.toISOString() } : {}),
      ...(end ? { end: end.toISOString() } : {}),
    };
    const controller = new AbortController();
    usageRequest.current = controller;
    setUsageLoading(true);
    try {
      const result = await getAgentUsage(filters, controller.signal);
      if (!controller.signal.aborted) setUsage(result);
    } catch (error) {
      if (!controller.signal.aborted) {
        setUsage(null);
        setUsageError(errorText(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        if (usageRequest.current === controller) usageRequest.current = null;
        setUsageLoading(false);
      }
    }
  };

  if (preview) return null;

  return <>
    <details className="agent-panel">
      <summary>受限员工账号管理</summary>
      <p className="agent-muted">仅管理普通员工的显示名称、所属部门和启停状态；新建账号默认停用且无登录密码，待管理员开通登录。</p>
      <form className="agent-actions" onSubmit={createEmployee}>
        <label>员工账号<input aria-label="新员工账号" autoComplete="off" maxLength={150} required value={username} disabled={creating || employeesLoading} onChange={event => setUsername(event.target.value)}/></label>
        <label>显示名称<input aria-label="新员工显示名称" maxLength={80} value={displayName} disabled={creating || employeesLoading} onChange={event => setDisplayName(event.target.value)}/></label>
        <label>所属部门<select aria-label="新员工所属部门" required value={departmentCode} disabled={creating || employeesLoading} onChange={event => setDepartmentCode(event.target.value)}><option value="">请选择</option>{departments.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
        <button className="button secondary" disabled={creating || employeesLoading}>{creating ? '正在创建…' : '创建员工账号'}</button>
      </form>
      {employeeError && <p role="alert" className="notice error">{employeeError}</p>}
      {employeeNotice && <p role="status" className="notice info">{employeeNotice}</p>}
      <div className="agent-actions">
        <button type="button" className="button secondary" disabled={employeesLoading} onClick={() => void loadEmployees()}>刷新员工列表</button>
        {employeesLoading && <span role="status">正在读取员工账号…</span>}
      </div>
      {!employeesLoading && !employeeError && employees.items.length === 0 && <p className="agent-muted">暂无普通员工账号。</p>}
      {employees.items.map(employee => <EmployeeRow key={employee.id} employee={employee} onSave={saveEmployee}/>)}
      {nextAgentPage(employees) && <button type="button" disabled={employeesLoading} onClick={() => void loadEmployees(nextAgentPage(employees)!, true)}>加载更多员工</button>}
    </details>

    <details className="agent-panel">
      <summary>调用用量</summary>
      <p className="agent-muted">仅显示实际调用次数和服务端返回的 Token；调用次数不代表员工在线时长。</p>
      <form className="agent-actions" onSubmit={queryUsage} onChange={() => { setUsage(null); setUsageError(''); }}>
        <label>部门<select aria-label="用量部门" value={departmentFilter} disabled={usageLoading} onChange={event => setDepartmentFilter(event.target.value)}><option value="">全部授权部门</option>{departments.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
        <label>员工 ID<input aria-label="用量员工 ID" type="number" min="1" step="1" inputMode="numeric" value={ownerFilter} disabled={usageLoading} onChange={event => setOwnerFilter(event.target.value)}/></label>
        <label>开始时间<input aria-label="用量开始时间" type="datetime-local" value={startFilter} disabled={usageLoading} onChange={event => setStartFilter(event.target.value)}/></label>
        <label>结束时间<input aria-label="用量结束时间" type="datetime-local" value={endFilter} disabled={usageLoading} onChange={event => setEndFilter(event.target.value)}/></label>
        <button className="button secondary" disabled={usageLoading}>{usageLoading ? '正在查询…' : '查询用量'}</button>
      </form>
      {usageError && <p role="alert" className="notice error">{usageError}</p>}
      {usageLoading && <p role="status">正在读取调用用量…</p>}
      {usage && <section aria-label="调用用量结果">
        <p>实际调用：{usage.calls.toLocaleString('zh-CN')} 次</p>
        <p>Token：输入 {usage.prompt_tokens == null ? '未知' : usage.prompt_tokens.toLocaleString('zh-CN')}，输出 {usage.completion_tokens == null ? '未知' : usage.completion_tokens.toLocaleString('zh-CN')}</p>
        <p>其中 {usage.unknown_usage_calls.toLocaleString('zh-CN')} 次调用的 Token 用量未知；未将未知用量按 0 计入。</p>
      </section>}
    </details>
  </>;
}
