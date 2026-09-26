import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HrWorkspace from '../centers/HrWorkspace';
import type { CurrentUser } from '../api';
const api = vi.hoisted(() => ({ apiRequest: vi.fn((_path: string) => Promise.resolve([])) }));
vi.mock('../api', () => api);
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('history 分支展示招聘历史并保留可展开的旧版只读JD', async () => {
  window.history.replaceState({}, '', '/centers/hr/history');
  render(<HrWorkspace section="history" user={{ roles: [{ code: 'hr' }] } as CurrentUser} />);
  expect(screen.getByRole('heading', { name: '招聘历史' })).toBeTruthy();
  await screen.findByText('暂无保留期内的招聘记录。');
  expect(api.apiRequest.mock.calls.some(call => String(call[0]).includes('/api/hr/jobs/'))).toBe(true);
  expect(screen.getByText('旧版岗位记录')).toBeTruthy();
});
it('无HR角色不发起招聘历史数据请求', () => {
  window.history.replaceState({}, '', '/centers/hr/history');
  render(<HrWorkspace section="history" user={{ roles: [{ code: 'manager' }] } as CurrentUser} />);
  expect(screen.getByRole('alert').textContent).toContain('没有 HR'); expect(api.apiRequest).not.toHaveBeenCalled();
});
it('preview 不发起招聘业务请求', () => {
  window.history.replaceState({}, '', '/preview/centers/hr/history');
  render(<HrWorkspace section="history" user={{ roles: [{ code: 'hr' }] } as CurrentUser} />);
  expect(screen.getByText(/不读取招聘/)).toBeTruthy(); expect(api.apiRequest).not.toHaveBeenCalled();
});
