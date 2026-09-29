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

it('普通页面不读取批次列表且可单独刷新当前进度', async () => {
  api.apiRequest.mockImplementation((path: string) => path === '/api/hr/recruitment/batches/' ? Promise.reject(new Error('列表不可用')) : Promise.resolve({ ...batch, pending: 0, prescreened: 1 }));
  render(<RecruitmentScreening />);
  expect(await screen.findByRole('heading', { name: '处理进度' })).toBeTruthy();
  expect(screen.getByLabelText('上传 JD 文件（TXT、DOCX、PDF）')).toBeTruthy();
  expect(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）')).toBeTruthy();
  expect(screen.queryByLabelText('正式 JD')).toBeNull();
  expect(screen.queryByLabelText('简历筛选模型')).toBeNull();
  expect(screen.queryByLabelText('选择批次')).toBeNull();
  expect(screen.queryByLabelText('选择简历文件夹')).toBeNull();
  expect(screen.getByText(/尚未启动模型/)).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(api.apiRequest.mock.calls.some(([path]) => path === '/api/hr/recruitment/batches/')).toBe(false);
  const progressCalls = api.apiRequest.mock.calls.filter(([path]) => path.endsWith('/progress/')).length;
  fireEvent.click(screen.getByRole('button', { name: '刷新进度' }));
  await waitFor(() => expect(api.apiRequest.mock.calls.filter(([path]) => path.endsWith('/progress/')).length).toBeGreaterThan(progressCalls));
  expect(screen.queryByText(/每组20份/)).toBeNull();
  expect(screen.getByRole('link', { name: '查看筛选结果' }).getAttribute('href')).toBe('/centers/hr/results?batch=batch-1');
});
it('终态批次开始新筛选会清空当前任务和待上传文件', async () => {
  let progressReads = 0;
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('/progress/') && ++progressReads > 1 ? { ...batch, status: 'completed', completed: 1, progress: 100 } : batch));
  render(<RecruitmentScreening />);
  await screen.findByRole('heading', { name: '处理进度' });
  fireEvent.change(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）'), { target: { files: [new File(['resume'], '待上传.txt')] } });
  expect(screen.getByText('待上传.txt')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '刷新进度' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '开始新的筛选' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '开始新的筛选' }));
  expect(screen.queryByText('待上传.txt')).toBeNull();
  expect(screen.queryByRole('heading', { name: '处理进度' })).toBeNull();
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
  fireEvent.change(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）'), { target: { files } });
  await waitFor(() => expect((screen.getByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '进行筛选' }));
  await waitFor(() => expect(versions).toEqual(['2', '3']));
  await waitFor(() => expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/batches/batch-1/run/', expect.objectContaining({ body: JSON.stringify({ expected_version: 4 }) })));
});

it('上传 JD 自动确认并省略模型选择创建批次', async () => {
  window.history.replaceState({}, '', '/centers/hr/resumes');
  const request = { id: 'r1', input_version: 4 };
  const draft = { id: 'jd-import', request_id: 'r1', version: 1, body: '原始JD', state: 'draft', channel: 'general', requirements: { skill_requirements: ['SQL'] } };
  const confirmed = { ...draft, state: 'confirmed' };
  const completed = { ...batch, jd_version_id: confirmed.id, status: 'completed', total: 1, completed: 1, progress: 100 };
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => Promise.resolve(
    path.endsWith('upload-jd/') ? { request, jd: draft } : path.endsWith('/confirm/') ? confirmed : path.endsWith('batches/') && options?.method === 'POST' ? { ...batch, jd_version_id: confirmed.id, total: 0 } : path.endsWith('/resumes/') ? { batch: { ...batch, jd_version_id: confirmed.id, total: 1, version: 3 } } : path.endsWith('/progress/') ? completed : batch));
  render(<RecruitmentScreening />);
  fireEvent.change(screen.getByLabelText('上传 JD 文件（TXT、DOCX、PDF）'), { target: { files: [new File(['原始JD'], 'jd.docx')] } });
  await screen.findByText('JD 已上传并确认，可批量上传简历。');
  expect(screen.getByText('jd.docx')).toBeTruthy();
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/r1/jd-versions/jd-import/confirm/', expect.objectContaining({ body: JSON.stringify({ expected_version: 4 }) }));
  fireEvent.change(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）'), { target: { files: [new File(['resume'], 'resume.txt')] } });
  expect(screen.getByText('jd.docx')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '进行筛选' }));
  await waitFor(() => expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/batches/', expect.objectContaining({ body: JSON.stringify({ jd_version_id: 'jd-import' }) })));
  expect(api.apiRequest.mock.calls.some(([path]) => path.includes('/api/models/routes/'))).toBe(false);
  await waitFor(() => expect((screen.getByRole('button', { name: '开始新的筛选' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '开始新的筛选' }));
  expect(screen.queryByText('jd.docx')).toBeNull();
  expect(screen.queryByRole('heading', { name: '处理进度' })).toBeNull();
  expect((screen.getByLabelText('上传 JD 文件（TXT、DOCX、PDF）') as HTMLInputElement).disabled).toBe(false);
  expect((screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）') as HTMLInputElement).disabled).toBe(false);
});
it('分组上传失败保留剩余文件且不运行模型批次', async () => {
  let calls = 0;
  api.apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/resumes/')) { calls++; return calls === 1 ? Promise.resolve({ batch: { ...batch, version: 3, total: 21 } }) : Promise.reject(new Error('批次上限200份')); }
    return Promise.resolve(path.endsWith('batches/') ? [batch] : path.endsWith('confirmed-jds/') ? [] : batch);
  });
  render(<RecruitmentScreening />); await screen.findByText('待上传/待开始');
  await waitFor(() => expect((screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）') as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）'), { target: { files: Array.from({ length: 21 }, (_, i) => new File([String(i)], `resume-${i}.txt`)) } });
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

it('JD 自动确认失败时不能创建筛选批次', async () => {
  window.history.replaceState({}, '', '/centers/hr/resumes');
  const request = { id: 'r1', input_version: 1 };
  const draft = { id: 'jd-1', body: '正文' };
  api.apiRequest.mockImplementation((path: string) => path.endsWith('upload-jd/') ? Promise.resolve({ request, jd: draft }) : path.endsWith('/confirm/') ? Promise.reject(new Error('JD 确认失败')) : Promise.resolve([]));
  render(<RecruitmentScreening />);
  fireEvent.change(screen.getByLabelText('上传 JD 文件（TXT、DOCX、PDF）'), { target: { files: [new File(['JD'], 'jd.txt')] } });
  await screen.findByText('JD 确认失败');
  fireEvent.change(screen.getByLabelText('批量上传简历（TXT、DOCX、PDF）'), { target: { files: [new File(['resume'], 'resume.txt')] } });
  expect((screen.getByRole('button', { name: '进行筛选' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.apiRequest.mock.calls.some(([path, options]) => path.endsWith('/batches/') && options?.method === 'POST')).toBe(false);
});
it('历史结果保留批次选择并显示批次绑定模型', async () => {
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('batches/') ? [batch] : path.includes('/summary/') ? [] : { ...batch, model_selection: { model_id: 'model-2', config_version: 'v2', model_name: '历史模型' } }));
  render(<RecruitmentScreening results />);
  expect(await screen.findByText('历史模型')).toBeTruthy();
  expect(screen.getByLabelText('选择批次')).toBeTruthy();
  expect(screen.queryByLabelText('简历筛选模型')).toBeNull();
});
