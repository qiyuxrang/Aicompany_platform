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
  const button = await screen.findByRole('button', { name: '进行筛选' });
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
  expect((await screen.findByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(true);
});
it('进度读取失败隐藏旧结果和下载入口', async () => {
  api.apiRequest.mockImplementation((path: string) => path.endsWith('progress/') ? Promise.reject(new Error('进度不可用')) : Promise.resolve([]));
  render(<RecruitmentScreening results />);
  expect((await screen.findByRole('alert')).textContent).toContain('进度不可用');
  expect(screen.queryByRole('link', { name: '导出 CSV' })).toBeNull();
});

it('JD 全文紧邻递归文件夹选择和集中筛选操作', async () => {
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : { ...batch, jd_body: '完整筛选要求：熟悉SQL', pending: 0, prescreened: 1 }));
  render(<RecruitmentScreening />);
  expect(await screen.findByText('完整筛选要求：熟悉SQL')).toBeTruthy();
  expect(screen.getByLabelText('选择简历文件夹').hasAttribute('webkitdirectory')).toBe(true);
  expect(screen.getByText(/尚未启动模型/)).toBeTruthy();
});
it('连续分组上传使用响应版本，最后才进行筛选', async () => {
  const versions: string[] = [];
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => {
    if (path.endsWith('/resumes/')) {
      const form = options?.body as FormData; versions.push(String(form.get('expected_version')));
      return Promise.resolve({ batch: { ...batch, total: 22, version: versions.length + 2 } });
    }
    return Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : batch);
  });
  render(<RecruitmentScreening />);
  await screen.findByText('待上传/待开始');
  const files = Array.from({ length: 21 }, (_, i) => new File([`resume ${i}`], `${i}.txt`));
  fireEvent.change(screen.getByLabelText('选择简历文件夹'), { target: { files } });
  fireEvent.click(screen.getByRole('button', { name: '进行筛选' }));
  await waitFor(() => expect(versions).toEqual(['2', '3']));
  await waitFor(() => expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/batches/batch-1/run/', expect.objectContaining({ body: JSON.stringify({ expected_version: 4 }) })));
});

it('上传 JD 先展示正文与快照，保存修改后确认该版本才可创建批次', async () => {
  window.history.replaceState({}, '', '/centers/hr/resumes');
  const request = { id: 'r1', input_version: 4 };
  const draft = { id: 'jd-import', request_id: 'r1', version: 1, body: '原始JD', state: 'draft', channel: 'general', requirements: { skill_requirements: ['SQL'] } };
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => Promise.resolve(
    path.endsWith('upload-jd/') ? { request, jd: draft } : path.endsWith('jd-versions/') ? { ...draft, id: 'jd-edited', version: 2, body: '已修改JD' } : path.endsWith('/confirm/') ? { ...draft, id: 'jd-edited', version: 2, body: '已修改JD', state: 'confirmed' } : path.endsWith('batches/') && options?.method === 'POST' ? { ...batch, total: 0 } : path.endsWith('/resumes/') ? { batch } : path.endsWith('batches/') || path.endsWith('confirmed-jds/') ? [] : batch));
  render(<RecruitmentScreening />);
  fireEvent.change(screen.getByLabelText('上传 JD 文件（TXT、DOCX、PDF）'), { target: { files: [new File(['原始JD'], 'jd.docx')] } });
  await screen.findByDisplayValue('原始JD'); expect(screen.getByText('SQL')).toBeTruthy();
  expect((screen.getByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('上传 JD 草稿正文'), { target: { value: '已修改JD' } });
  expect((screen.getByRole('button', { name: '确认上传 JD' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '保存 JD 修改' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '确认上传 JD' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '确认上传 JD' }));
  await screen.findByText('JD 已确认，可选择简历并进行筛选。');
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/r1/jd-versions/jd-edited/confirm/', expect.objectContaining({ body: JSON.stringify({ expected_version: 4 }) }));
  fireEvent.change(screen.getByLabelText('选择简历'), { target: { files: [new File(['resume'], 'resume.txt')] } });
  fireEvent.click(screen.getByRole('button', { name: '进行筛选' }));
  await waitFor(() => expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/batches/', expect.objectContaining({ body: JSON.stringify({ jd_version_id: 'jd-edited' }) })));
});
it('分组上传失败保留剩余文件且不运行模型批次', async () => {
  let calls = 0;
  api.apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/resumes/')) { calls++; return calls === 1 ? Promise.resolve({ batch: { ...batch, version: 3, total: 21 } }) : Promise.reject(new Error('批次上限200份')); }
    return Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : batch);
  });
  render(<RecruitmentScreening />); await screen.findByText('待上传/待开始');
  await waitFor(() => expect((screen.getByLabelText('选择简历') as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('选择简历'), { target: { files: Array.from({ length: 21 }, (_, i) => new File([String(i)], `resume-${i}.txt`)) } });
  await waitFor(() => expect((screen.getByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '进行筛选' }));
  expect((await screen.findByRole('alert')).textContent).toContain('200');
  expect(screen.getByText('resume-20.txt')).toBeTruthy(); expect(screen.queryByText('resume-0.txt')).toBeNull();
  expect(api.apiRequest.mock.calls.some(([path]) => path.endsWith('/run/'))).toBe(false);
});
it('查看结果详情不会被刷新立即清除', async () => {
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : path.includes('/summary/') ? [{ id: 'a1', filename: 'resume.txt', processing_status: 'completed', score: 80, unknown_count: 0, hard_gap_count: 0 }] : path.endsWith('/resumes/a1/') ? { match: { matrix: [{ id: 'm1', text: 'SQL能力', verdict: 'MATCH', evidence: [{ quote: '使用SQL三年', locator: '第1页' }] }] } } : batch));
  render(<RecruitmentScreening results />);
  fireEvent.click(await screen.findByRole('button', { name: '查看证据' }));
  await screen.findByText('使用SQL三年');
  await waitFor(() => expect(screen.getByText('SQL能力')).toBeTruthy());
});
