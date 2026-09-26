import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import HrHeaderTools from './HrHeaderTools';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
beforeEach(() => {
  api.apiRequest.mockReset();
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('requests/') ? [
    { id: 'job-1', position_name: '项目经理', current_jd_id: 'jd-1', official_jd_id: null },
  ] : [{ id: 'batch-1', position_name: '项目经理', failed: 2 }]));
});
afterEach(cleanup);
it('搜索岗位使用受权数据并链接到正确岗位', async () => {
  render(<HrHeaderTools />);
  await screen.findByRole('button', { name: '人事待办 2 项' });
  fireEvent.change(screen.getByLabelText('搜索招聘岗位'), { target: { value: '项目' } });
  expect(screen.getByRole('link', { name: '项目经理' }).getAttribute('href')).toBe('/centers/hr/job?task=job-1');
});
it('待办只展示真实 JD 和失败批次', async () => {
  render(<HrHeaderTools />);
  fireEvent.click(await screen.findByRole('button', { name: '人事待办 2 项' }));
  expect(screen.getByRole('link', { name: '项目经理 · 2 份处理失败' }).getAttribute('href')).toBe('/centers/hr/resumes?batch=batch-1');
  expect(screen.getByRole('link', { name: '项目经理 · JD 待确认' })).toBeTruthy();
});
