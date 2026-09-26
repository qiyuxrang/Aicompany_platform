import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecruitmentScreening from './RecruitmentScreening';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const batch = { id: 'batch-1', jd_version_id: 'jd-1', jd_version: 1, position_name: '交付经理', version: 2, status: 'pending', stale: false, total: 1, completed: 0, failed: 0, progress: 0, updated_at: '2026-09-26T01:00:00Z', artifacts: [] };
beforeEach(() => {
  window.history.replaceState({}, '', '/centers/hr/resumes?batch=batch-1');
  api.apiRequest.mockReset();
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : path.includes('/summary/') ? [] : batch));
});
afterEach(cleanup);
it('开始筛选提交后端版本且禁用并发提交', async () => {
  render(<RecruitmentScreening />);
  const button = await screen.findByRole('button', { name: '开始筛选' });
  fireEvent.click(button);
  await waitFor(() => expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/batches/batch-1/run/', expect.objectContaining({ method: 'POST', body: JSON.stringify({ expected_version: 2 }) })));
});
it('历史结果使用正确批次并显示未知分数而非零分', async () => {
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : path.includes('/summary/') ? [{ id: 'resume-1', filename: '合成简历.txt', processing_status: 'failed', score: null, unknown_count: null, hard_gap_count: 0, matrix: [], error_code: 'timeout' }] : batch));
  render(<RecruitmentScreening results />);
  expect(await screen.findByText('合成简历.txt')).toBeTruthy();
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  expect(screen.getByRole('link', { name: '导出 CSV' }).getAttribute('href')).toContain('/batches/batch-1/export/');
});
it('过期 JD 禁止开始筛选', async () => {
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : { ...batch, stale: true }));
  render(<RecruitmentScreening />);
  expect((await screen.findByRole('button', { name: '开始筛选' }) as HTMLButtonElement).disabled).toBe(true);
});
it('进度读取失败隐藏旧结果和下载入口', async () => {
  api.apiRequest.mockImplementation((path: string) => path.endsWith('progress/') ? Promise.reject(new Error('进度不可用')) : Promise.resolve([]));
  render(<RecruitmentScreening results />);
  expect((await screen.findByRole('alert')).textContent).toContain('进度不可用');
  expect(screen.queryByRole('link', { name: '导出 CSV' })).toBeNull();
});
