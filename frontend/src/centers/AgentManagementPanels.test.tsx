import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import type { AgentEmployee } from '../agent-api';
import AgentManagementPanels from './AgentManagementPanels';

const mockApi = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), apiRequest: mockApi.request }));
const request = mockApi.request;

const employee = (id: number, overrides: Partial<AgentEmployee> = {}): AgentEmployee => ({
  id, username: `user${id}`, display_name: `员工${id}`, department_code: 'product', is_active: false, roles: ['product'], ...overrides,
});
const employeeListPath = '/api/agent/employees/?page=1&page_size=20';
const usageResult = (overrides = {}) => ({ calls: 0, unknown_usage_calls: 0, prompt_tokens: 0, completion_tokens: 0, ...overrides });

const bodyFor = (path: string, method: string) => {
  const options = request.mock.calls.find(([url, init]) => String(url) === path && (init as RequestInit | undefined)?.method === method)?.[1] as RequestInit | undefined;
  return options?.body ? JSON.parse(String(options.body)) : undefined;
};

beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (url: string) => {
    if (String(url).startsWith('/api/agent/employees/')) return { items: [], total: 0 };
    if (String(url).startsWith('/api/agent/management/usage/')) return usageResult();
    throw new Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => cleanup());

it('creates only a limited employee account and explains login is pending', async () => {
  request.mockImplementation(async (url: string, init?: RequestInit) => {
    if (String(url) === employeeListPath) return { items: [], total: 0 };
    if (String(url) === '/api/agent/employees/' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      return employee(12, { username: body.username, display_name: body.display_name, department_code: body.department_code });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<AgentManagementPanels/>);
  await screen.findByText('暂无普通员工账号。');
  await userEvent.type(screen.getByLabelText('新员工账号'), 'worker12');
  await userEvent.type(screen.getByLabelText('新员工显示名称'), '王工');
  await userEvent.selectOptions(screen.getByLabelText('新员工所属部门'), 'engineering');
  await userEvent.click(screen.getByRole('button', { name: '创建员工账号' }));

  expect((await screen.findByRole('status')).textContent).toContain('待管理员开通登录');
  expect(bodyFor('/api/agent/employees/', 'POST')).toEqual({ username: 'worker12', display_name: '王工', department_code: 'engineering' });
  expect(await screen.findByText(/当前状态：已停用/)).toBeTruthy();
  expect(screen.queryByLabelText(/密码|角色/)).toBeNull();
});

it('sends only changed employee fields and trusts the server response', async () => {
  request.mockImplementation(async (url: string, init?: RequestInit) => {
    if (String(url) === employeeListPath) return { items: [employee(41, { is_active: true })], total: 1 };
    if (String(url) === '/api/agent/employees/41/' && init?.method === 'PATCH') {
      const body = JSON.parse(String(init.body));
      return employee(41, { display_name: body.display_name, department_code: body.department_code, is_active: true });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<AgentManagementPanels/>);
  const name = await screen.findByLabelText('员工 41 显示名称');
  await userEvent.clear(name);
  await userEvent.type(name, '李华');
  await userEvent.selectOptions(screen.getByLabelText('员工 41 所属部门'), 'finance');
  await userEvent.click(screen.getByRole('button', { name: '保存员工 41 修改' }));

  await screen.findByText('员工 41 信息已按服务端结果更新。');
  expect(bodyFor('/api/agent/employees/41/', 'PATCH')).toEqual({ display_name: '李华', department_code: 'finance' });
});

it('clears employee data after a denied update and shows no false success', async () => {
  request.mockImplementation(async (url: string, init?: RequestInit) => {
    if (String(url) === employeeListPath) return { items: [employee(8)], total: 1 };
    if (String(url) === '/api/agent/employees/8/' && init?.method === 'PATCH') throw new ApiError(403, '当前权限不允许此修改。');
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<AgentManagementPanels/>);
  const active = await screen.findByLabelText('员工 8 启用状态');
  await userEvent.click(active);
  await userEvent.click(screen.getByRole('button', { name: '保存员工 8 修改' }));

  expect((await screen.findByRole('alert')).textContent).toContain('访问被拒绝：当前权限不允许此修改。');
  expect(bodyFor('/api/agent/employees/8/', 'PATCH')).toEqual({ is_active: true });
  expect(screen.queryByText(/员工 ID 8/)).toBeNull();
  expect(screen.queryByText(/信息已按服务端结果更新/)).toBeNull();
});

it('shows unknown Token usage without replacing it with zero', async () => {
  request.mockImplementation(async (url: string) => {
    if (String(url).startsWith('/api/agent/employees/')) return { items: [], total: 0 };
    if (String(url).startsWith('/api/agent/management/usage/')) return usageResult({ calls: 3, unknown_usage_calls: 2, prompt_tokens: null, completion_tokens: null });
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<AgentManagementPanels/>);
  await screen.findByText('暂无普通员工账号。');
  await userEvent.click(screen.getByText('调用用量'));
  await userEvent.click(screen.getByRole('button', { name: '查询用量' }));

  expect(await screen.findByText('实际调用：3 次')).toBeTruthy();
  expect(screen.getByText('Token：输入 未知，输出 未知')).toBeTruthy();
  expect(screen.getByText('其中 2 次调用的 Token 用量未知；未将未知用量按 0 计入。')).toBeTruthy();
});

it('does not request data in preview and aborts reads when preview becomes active', async () => {
  const view = render(<AgentManagementPanels preview/>);
  expect(request).not.toHaveBeenCalled();
  request.mockImplementation((url: string, init?: RequestInit) => {
    if (String(url).startsWith('/api/agent/employees/')) return new Promise((resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
    throw new Error(`Unexpected request: ${url}`);
  });
  view.rerender(<AgentManagementPanels/>);
  const options = request.mock.calls[0]?.[1] as RequestInit;
  view.rerender(<AgentManagementPanels preview/>);
  expect(options.signal?.aborted).toBe(true);
  expect(request).toHaveBeenCalledTimes(1);
});

it('paginates employees by stable ID and does not reload on ordinary rerenders', async () => {
  const firstPage = Array.from({ length: 20 }, (_, index) => employee(index + 1));
  request.mockImplementation(async (url: string) => {
    if (String(url).startsWith('/api/agent/employees/')) {
      return new URLSearchParams(String(url).split('?')[1]).get('page') === '2'
        ? { items: [employee(20, { display_name: '員工20更新' }), employee(21)], total: 21 }
        : { items: firstPage, total: 21 };
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  const view = render(<AgentManagementPanels/>);
  await screen.findByLabelText('员工 20 启用状态');
  view.rerender(<AgentManagementPanels/>);
  expect(request.mock.calls.filter(([url]) => String(url).startsWith('/api/agent/employees/'))).toHaveLength(1);
  await userEvent.click(screen.getByRole('button', { name: '加载更多员工' }));
  await screen.findByLabelText('员工 21 启用状态');
  expect(request.mock.calls.some(([url]) => String(url) === '/api/agent/employees/?page=2&page_size=20')).toBe(true);
  expect(screen.getAllByRole('heading', { name: /员工 ID 20/ })).toHaveLength(1);
});

it('omits empty usage filters and converts selected local times to timezone-aware values', async () => {
  render(<AgentManagementPanels/>);
  await screen.findByText('暂无普通员工账号。');
  await userEvent.click(screen.getByText('调用用量'));
  await userEvent.click(screen.getByRole('button', { name: '查询用量' }));
  await screen.findByText('实际调用：0 次');
  expect(request.mock.calls.some(([url]) => String(url) === '/api/agent/management/usage/')).toBe(true);

  await userEvent.selectOptions(screen.getByLabelText('用量部门'), 'hr');
  fireEvent.change(screen.getByLabelText('用量员工 ID'), { target: { value: '42' } });
  fireEvent.change(screen.getByLabelText('用量开始时间'), { target: { value: '2026-09-29T10:00' } });
  fireEvent.change(screen.getByLabelText('用量结束时间'), { target: { value: '2026-09-30T11:00' } });
  await userEvent.click(screen.getByRole('button', { name: '查询用量' }));

  await waitFor(() => expect(request.mock.calls.some(([url]) => String(url).startsWith('/api/agent/management/usage/?department_code=hr&owner_id=42&'))).toBe(true));
  const filteredUrl = String(request.mock.calls.find(([url]) => String(url).startsWith('/api/agent/management/usage/?department_code=hr&owner_id=42&'))?.[0]);
  const params = new URLSearchParams(filteredUrl.split('?')[1]);
  expect(params.get('start')).toBe(new Date('2026-09-29T10:00').toISOString());
  expect(params.get('end')).toBe(new Date('2026-09-30T11:00').toISOString());
});
