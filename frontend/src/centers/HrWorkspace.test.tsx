import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import HrWorkspace from './HrWorkspace';
const hrUser = { id: 1, username: 'hr', display_name: 'HR', roles: [{ code: 'hr', name: '人事' }], must_change_password: false, is_platform_admin: false };
const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), apiRequest: mocks.apiRequest }));
beforeEach(() => {
  window.history.replaceState({}, '', '/centers/hr'); mocks.apiRequest.mockReset(); mocks.apiRequest.mockResolvedValue([]);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('工作台显示真实空状态，不伪造转正待办', async () => {
  render(<HrWorkspace section="overview" user={hrUser} />);
  expect(await screen.findByText('暂无筛选批次。')).toBeTruthy();
  expect(screen.getByText('转正问卷暂缓接入')).toBeTruthy();
  expect(screen.getByRole('link', { name: /新建招聘需求/ }).getAttribute('href')).toBe('/centers/hr/job');
});
it('JD 入口直接打开对话框，无需加载岗位或模型列表', async () => {
  render(<HrWorkspace section="job" user={hrUser} />);
  expect(await screen.findByRole('dialog', { name: 'JD 生成助手' })).toBeTruthy();
  expect(screen.getByLabelText('招聘说明')).toBeTruthy();
  expect(mocks.apiRequest).not.toHaveBeenCalled();
});
it('筛选页仅使用有效 JD 创建批次', async () => {
  render(<HrWorkspace section="resumes" user={hrUser} />);
  expect(screen.queryByLabelText('正式 JD')).toBeNull();
  expect(screen.queryByLabelText('选择批次')).toBeNull();
  expect((screen.getByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(true);
});
it.each(['job', 'resumes', 'results', 'probation'])('管理预览 %s 不读取业务', section => {
  window.history.replaceState({}, '', '/preview/hr/' + section);
  render(<HrWorkspace section={section} user={hrUser} />);
  expect(screen.getByText(/仅页面预览/)).toBeTruthy();
  expect(mocks.apiRequest).not.toHaveBeenCalled();
});
it('普通员工没有招聘权限', () => {
  render(<HrWorkspace section="job" user={{ ...hrUser, roles: [] }} />);
  expect(screen.getByRole('alert').textContent).toContain('没有 HR');
  expect(mocks.apiRequest).not.toHaveBeenCalled();
});
it('后端失败清除概览而非显示零数据', async () => {
  mocks.apiRequest.mockRejectedValue(new Error('service unavailable'));
  render(<HrWorkspace section="overview" user={hrUser} />);
  expect((await screen.findByRole('alert')).textContent).toContain('service unavailable');
  expect(screen.queryByText('暂无筛选批次。')).toBeNull();
});
