import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import type { AgentWork } from '../agent-api';
import AgentWorkspace from './AgentWorkspace';

const api = vi.hoisted(() => Object.fromEntries(['listAgentConversations', 'listAgentProjects', 'listAgentWork', 'listAgentMessages', 'getAgentWork', 'getAgentEvents', 'createAgentConversation', 'createAgentProject', 'sendAgentMessage', 'changeAgentRequirement', 'commandAgentWork', 'listAgentSkills', 'listAgentInstallations', 'saveAgentInstallation', 'removeAgentInstallation', 'uploadAgentAttachment', 'listAgentManagementWork', 'listAgentEmployees', 'createAgentEmployee', 'updateAgentEmployee', 'getAgentUsage'].map(name => [name, vi.fn()])));
vi.mock('../agent-api', async original => ({ ...await original<typeof import('../agent-api')>(), ...api }));
const work = (overrides: Partial<AgentWork> = {}): AgentWork => ({ id: 'w1', conversation_id: 'c1', goal: '整理方案', state: 'running', current_requirement_version: 2,
  requirements: [{ version: 2, content: '保留原始清单', received_at: '2026-09-30', applied_at: null }],
  ...overrides });
beforeEach(() => {
  Object.values(api).forEach(mock => mock.mockReset());
  window.history.replaceState({}, '', '/centers/product/assistant');
  ['listAgentConversations', 'listAgentProjects', 'listAgentWork', 'listAgentMessages', 'listAgentSkills', 'listAgentInstallations', 'listAgentManagementWork', 'listAgentEmployees'].forEach(name => api[name].mockResolvedValue({ items: [] }));
  api.getAgentWork.mockResolvedValue(work()); api.getAgentEvents.mockResolvedValue({ items: [], has_more: false });
  api.createAgentConversation.mockResolvedValue({ id: 'c1' });
  api.sendAgentMessage.mockResolvedValue({ id: 'm1', role: 'user', content: '问题', attachment_references: [], created_at: '2026-09-30' });
});
afterEach(() => { cleanup(); vi.useRealTimers(); window.history.replaceState({}, '', '/'); });

it('asks directly without creating a project or work and preserves the deep link', async () => {
  render(<AgentWorkspace department="product"/>);
  await userEvent.type(screen.getByLabelText('输入消息'), '整理一下资料');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByText(/消息已接收 · m1/)).toBeTruthy();
  expect(api.createAgentProject).not.toHaveBeenCalled();
  expect(api.listAgentEmployees).not.toHaveBeenCalled();
  expect(api.createAgentConversation).toHaveBeenCalledWith(expect.any(String), undefined);
  expect(api.sendAgentMessage).toHaveBeenCalledWith('c1', '整理一下资料', [], expect.any(String), undefined, false);
  expect(window.location.search).toBe('?conversation=c1');
  expect(screen.queryByText('已完成')).toBeNull();
  expect(await screen.findByText(/消息已接收.*执行状态未提供，尚不能确认是否已提交。/)).toBeTruthy();
});

it('creates a project using only its name', async () => {
  api.createAgentProject.mockResolvedValue({ id: 'p1', name: '横沟项目' });
  render(<AgentWorkspace department="product"/>);
  await userEvent.click(screen.getByText('项目归集（可选）'));
  await userEvent.type(screen.getByLabelText('项目名称'), '横沟项目');
  await userEvent.click(screen.getByRole('button', { name: '创建项目' }));
  expect(await screen.findByText('项目已创建：横沟项目')).toBeTruthy();
  expect(api.createAgentProject).toHaveBeenCalledWith('横沟项目');
  expect(api.sendAgentMessage).not.toHaveBeenCalled();
});

it('shows received rather than applied and allows corrections during running', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  api.sendAgentMessage.mockResolvedValue({ id: 'm2', work_id: 'w1', execution_state: 'pending' });
  api.changeAgentRequirement.mockResolvedValue({ version: 3, status: 'received', applied: false, execution_state: 'submitted' });
  render(<AgentWorkspace department="product"/>);
  expect(await screen.findByText('要求 v2 · 已接收，待生效')).toBeTruthy();
  expect(screen.queryByText(/要求 v2 · 已生效/)).toBeNull();
  await userEvent.click(screen.getByLabelText('作为当前工作的要求更正'));
  await userEvent.type(screen.getByLabelText('更正当前工作要求'), '增加原文引用');
  await userEvent.click(screen.getByRole('button', { name: '提交更正' }));
  await waitFor(() => expect(api.changeAgentRequirement).toHaveBeenCalledWith('w1', 2, 'm2', '增加原文引用'));
  expect(api.sendAgentMessage).toHaveBeenCalledWith('c1', '增加原文引用', [], expect.any(String), 'w1', true);
  expect(await screen.findByText(/消息已接收.*已提交至运行环境；是否完成请以工作记录为准。/)).toBeTruthy();
  expect(screen.queryByText(/要求 v3 · 已生效/)).toBeNull();
});

it('shows an explicit unconfigured result as not executed', async () => {
  const message = { id: 'm1', role: 'user' as const, content: '检查资料', attachment_references: [], created_at: '2026-09-30', execution_state: 'runtime_unconfigured' };
  api.sendAgentMessage.mockResolvedValue(message);
  api.listAgentMessages.mockResolvedValue({ items: [message] });
  render(<AgentWorkspace department="product"/>);
  await userEvent.type(screen.getByLabelText('输入消息'), '检查资料');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByText(/消息已接收.*未执行：运行环境未配置，请联系管理员。/)).toBeTruthy();
  expect(await screen.findByText('未执行：运行环境未配置，请联系管理员。')).toBeTruthy();
});

it('preserves unknown dispatch in history and reconnect never resends or creates a root', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1');
  const message = { id: 'm1', role: 'user' as const, content: '核对资料', attachment_references: [], created_at: '2026-09-30', execution_state: 'dispatch_unknown' };
  api.sendAgentMessage.mockResolvedValue(message);
  api.listAgentMessages.mockResolvedValue({ items: [message] });
  render(<AgentWorkspace department="product"/>);
  await userEvent.type(await screen.findByLabelText('输入消息'), '核对资料');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByText(/消息已接收.*提交结果不确定；请先核对历史和工作记录，不要重复发送。/)).toBeTruthy();
  expect(await screen.findByText('提交结果不确定；请先核对历史和工作记录，不要重复发送。')).toBeTruthy();
  await waitFor(() => expect(api.listAgentMessages.mock.calls.length).toBeGreaterThanOrEqual(2));
  fireEvent(window, new Event('online'));
  await waitFor(() => expect(api.listAgentMessages.mock.calls.length).toBeGreaterThanOrEqual(3));
  expect(api.sendAgentMessage).toHaveBeenCalledTimes(1);
  expect(api.createAgentConversation).not.toHaveBeenCalled();
});

it('keeps idempotency identity after an unknown send result', async () => {
  api.sendAgentMessage.mockRejectedValueOnce(new ApiError(502, '响应丢失')).mockResolvedValueOnce({ id: 'm1' });
  render(<AgentWorkspace department="product"/>);
  await userEvent.type(screen.getByLabelText('输入消息'), '同一请求');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  await screen.findByRole('alert');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  await waitFor(() => expect(api.sendAgentMessage).toHaveBeenCalledTimes(2));
  expect(api.sendAgentMessage.mock.calls[0]).toEqual(api.sendAgentMessage.mock.calls[1]);
  expect(api.createAgentConversation).toHaveBeenCalledTimes(1);
});

it('catches up ordered public events and reconnects without restarting or cancelling work', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  const event = (seq: number) => ({ seq, event_key: String(seq), type: 'status', payload: { summary: `公开记录${seq}`, private_reasoning: '私有推理' }, created_at: '2026-09-30' });
  api.getAgentEvents.mockResolvedValueOnce({ items: [event(2), event(1)], has_more: true }).mockResolvedValueOnce({ items: [event(2), event(3)], has_more: false }).mockResolvedValue({ items: [], has_more: false });
  const view = render(<AgentWorkspace department="product"/>);
  await screen.findByText('公开执行记录（3）');
  expect(api.getAgentEvents).toHaveBeenNthCalledWith(2, 'w1', 2, expect.anything());
  fireEvent(window, new Event('online'));
  await waitFor(() => expect(api.getAgentEvents).toHaveBeenLastCalledWith('w1', 3, expect.anything()));
  expect(screen.queryByText('私有推理')).toBeNull();
  expect(api.sendAgentMessage).not.toHaveBeenCalled();
  view.unmount();
  expect(api.commandAgentWork).not.toHaveBeenCalled();
});

it('hides revoked private content and displays the access error', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  api.listAgentMessages.mockResolvedValueOnce({ items: [{ id: 'private', role: 'user', content: '私人资料', attachment_references: [], created_at: '2026-09-30' }] });
  render(<AgentWorkspace department="product"/>);
  await screen.findByText('私人资料');
  await screen.findByText('要求 v2 · 已接收，待生效');
  api.listAgentMessages.mockRejectedValue(new ApiError(403, '对象授权已撤销'));
  fireEvent(window, new Event('online'));
  expect((await screen.findByRole('alert')).textContent).toContain('对象授权已撤销');
  expect(screen.queryByText('私人资料')).toBeNull();
  expect((screen.getByLabelText('输入消息') as HTMLTextAreaElement).disabled).toBe(true);
});

it('cancel stays at server confirmed stopping until reconciliation completes', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  let finish: (value: AgentWork) => void = () => {};
  api.commandAgentWork.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<AgentWorkspace department="product"/>);
  await userEvent.click(await screen.findByRole('button', { name: '取消工作' }));
  expect(screen.getByText('正在执行')).toBeTruthy();
  expect(screen.queryByText('已取消')).toBeNull();
  api.getAgentWork.mockResolvedValue(work({ state: 'stopping' }));
  await act(async () => finish(work({ state: 'stopping' })));
  expect(await screen.findByText('停止处理中')).toBeTruthy();
  expect(api.commandAgentWork).toHaveBeenCalledWith('w1', 'cancel', 2, expect.any(String));
});

it('GM management results render only summaries and typed references, not employee chats', async () => {
  api.listAgentManagementWork.mockResolvedValue({ items: [{ ...work(), summary: '工作摘要', messages: [{ content: '员工完整聊天' }] }] });
  render(<AgentWorkspace department="business" manager/>);
  expect(await screen.findByText('受限员工账号管理')).toBeTruthy();
  expect(screen.getByText('调用用量')).toBeTruthy();
  await waitFor(() => expect(api.listAgentEmployees).toHaveBeenCalledTimes(1));
  await userEvent.click(screen.getByText('部门工作记录 · 只读'));
  await userEvent.click(screen.getByRole('button', { name: '查询工作记录' }));
  expect(await screen.findByText('工作摘要')).toBeTruthy();
  expect(screen.queryByText('员工完整聊天')).toBeNull();
  expect(api.listAgentMessages).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '取消工作' })).toBeNull();
});

it('preview never reads private data and real endpoint failures remain visible', async () => {
  const preview = render(<AgentWorkspace department="product" preview/>);
  expect(api.listAgentConversations).not.toHaveBeenCalled(); preview.unmount();
  expect(api.listAgentEmployees).not.toHaveBeenCalled();
  api.listAgentConversations.mockRejectedValue(new ApiError(503, 'Agent 功能未启用。'));
  render(<AgentWorkspace department="product"/>);
  expect((await screen.findByRole('alert')).textContent).toContain('Agent 功能未启用');
});

it('attachments unavailable remains an error and never adds a pretend uploaded reference', async () => {
  api.uploadAgentAttachment.mockRejectedValue(new ApiError(503, '附件授权接入尚未完成。', 'attachments_unavailable'));
  render(<AgentWorkspace department="product"/>);
  await userEvent.upload(screen.getByLabelText('添加附件'), new File(['content'], '清单.txt', { type: 'text/plain' }));
  expect((await screen.findByRole('alert')).textContent).toContain('附件授权接入尚未完成');
  expect(screen.queryByRole('button', { name: '移除附件 清单.txt' })).toBeNull();
  expect(api.sendAgentMessage).not.toHaveBeenCalled();
});

it('skill installation state is re-read from the server and is not toggled optimistically', async () => {
  api.listAgentSkills.mockResolvedValue({ items: [{ id: 'review', name: '资料检查', version: 'v1', digest: 'a'.repeat(64) }] });
  api.saveAgentInstallation.mockRejectedValue(new ApiError(403, '技能授权已撤回'));
  render(<AgentWorkspace department="product"/>);
  await userEvent.click(screen.getByRole('button', { name: '本人技能' }));
  await userEvent.click(await screen.findByRole('button', { name: '安装并启用' }));
  expect((await screen.findByRole('alert')).textContent).toContain('技能授权已撤回');
  expect(screen.queryByText(/本人已启用/)).toBeNull();
});

it('owner blueprint confirmation links to the existing product page, without approving in chat', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  api.getAgentWork.mockResolvedValue(work({ state: 'waiting_confirmation', business_references: [{ domain_type: 'document_task', object_id: 'task-1', revision: 3, digest: 'hash', public_summary: '方案任务' }] }));
  render(<AgentWorkspace department="product"/>);
  expect((await screen.findByRole('link', { name: '打开蓝图并核对精确版本' })).getAttribute('href')).toBe('/centers/product/documents?task=task-1');
  expect(api.commandAgentWork).not.toHaveBeenCalled();
});

it('finance draft requires exact owner publishing in the existing ledger, not chat approval', async () => {
  window.history.replaceState({}, '', '/centers/finance/assistant?conversation=c1&work=w1');
  api.getAgentWork.mockResolvedValue(work({ state: 'waiting_confirmation' }));
  render(<AgentWorkspace department="finance"/>);
  expect((await screen.findByRole('link', { name: '打开本人台账核对精确版本' })).getAttribute('href')).toBe('/centers/finance');
  expect(api.commandAgentWork).not.toHaveBeenCalled();
});

it('terminated work continues only after explicit action and follows the returned new work identity', async () => {
  window.history.replaceState({}, '', '/centers/product/assistant?conversation=c1&work=w1');
  api.getAgentWork.mockResolvedValue(work({ state: 'terminated' }));
  api.commandAgentWork.mockResolvedValue(work({ id: 'w2', conversation_id: 'c2', state: 'queued' }));
  render(<AgentWorkspace department="product"/>);
  const retry = await screen.findByRole('button', { name: '明确发起新一轮工作' });
  expect(api.commandAgentWork).not.toHaveBeenCalled();
  api.getAgentWork.mockResolvedValue(work({ id: 'w2', conversation_id: 'c2', state: 'queued' }));
  await userEvent.click(retry);
  await waitFor(() => expect(window.location.search).toBe('?conversation=c2&work=w2'));
  expect(api.commandAgentWork).toHaveBeenCalledWith('w1', 'retry', 2, expect.any(String));
});
