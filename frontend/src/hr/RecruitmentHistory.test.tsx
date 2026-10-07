import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecruitmentHistory from './RecruitmentHistory';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const request = { id: 'r1', position_name: '交付经理', created_at: '2026-09-20T01:00:00Z' };
const history = { request, messages: [{ id: 1, role: 'user', content: '需要负责项目交付', input_version: 1, created_at: request.created_at }, { id: 2, role: 'user', content: JSON.stringify({ salary: '一万元', benefits: '双休' }), input_version: 1, created_at: request.created_at }], jd_versions: [{ id: 'jd1', version: 1, channel: 'general', state: 'confirmed', body: '版本一正文' }], batches: [{ id: 'b1', position_name: '交付经理', jd_version: 1, completed: 1, failed: 0, status: 'completed', status_counts: { completed: 1 }, results: [{ id: 'a1', filename: '张某.txt', processing_status: 'completed', score: 80, hard_gap_count: 0, unknown_count: 1, matrix: [{ id: 'm1', text: '交付经验', verdict: 'MATCH', evidence: [{ quote: '三年交付经验', locator: '第2段' }] }] }] }] };
beforeEach(() => { window.history.replaceState({}, '', '/centers/hr/history?task=r1'); api.apiRequest.mockReset(); api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('requests/') ? [request] : history)); });
afterEach(cleanup);
it('历史展示对话、中文字段、JD 版本、简历结果与证据而非只有JSON', async () => {
  render(<RecruitmentHistory />);
  await screen.findByText('需要负责项目交付');
  expect(screen.getByText('一万元')).toBeTruthy(); expect(screen.getByText('薪资')).toBeTruthy();
  expect(screen.getByText('版本一正文')).toBeTruthy(); expect(screen.getByText('张某.txt')).toBeTruthy();
  expect(screen.getByText(/三年交付经验（第2段）/)).toBeTruthy();
  expect(screen.getByText(/旧规则下已失效的资料仍不可访问/)).toBeTruthy();
});
it('切换到无权记录清除旧对话及结果', async () => {
  const view = render(<RecruitmentHistory />); await screen.findByText('张某.txt');
  api.apiRequest.mockRejectedValueOnce(new Error('无访问权限'));
  window.history.replaceState({}, '', '/centers/hr/history?task=foreign'); view.rerender(<RecruitmentHistory />);
  await screen.findByRole('alert'); expect(screen.queryByText('张某.txt')).toBeNull();
});
it('离开加载中的记录后不显示滞后的历史结果', async () => {
  let finish!: (value: unknown) => void;
  api.apiRequest.mockImplementation((path: string) => path.endsWith('requests/') ? Promise.resolve([request]) : new Promise(resolve => { finish = resolve; }));
  render(<RecruitmentHistory />); await screen.findByRole('option', { name: /交付经理/ });
  fireEvent.change(screen.getByLabelText('选择招聘记录'), { target: { value: '' } });
  finish(history); await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  expect(screen.queryByText('张某.txt')).toBeNull();
});
