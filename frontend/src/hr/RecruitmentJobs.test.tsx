import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecruitmentJobs from './RecruitmentJobs';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const row = { id: 'r1', position_name: '交付经理', original_text: '想招交付经理，地点西安，熟悉 Circle', input_version: 1, current_jd_id: 'jd1', official_jd_id: null };
const jd = { id: 'jd1', request_id: 'r1', version: 1, input_version: 1, state: 'draft', source: 'skill', body: '通用岗位正文', channel: 'general', stale: false };

beforeEach(() => {
  window.history.replaceState({}, '', '/centers/hr/job');
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  api.apiRequest.mockReset();
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => {
    if (path.endsWith('/intake/')) return Promise.resolve({ request: row, jd: { ...jd, source: 'text' } });
    if (path.endsWith('/generate-jd/')) return Promise.resolve(jd);
    if (path.endsWith('/confirm/')) return Promise.resolve({ ...jd, state: 'confirmed' });
    if (path.endsWith('/adapt/')) return Promise.resolve({ ...jd, id: 'platform1', channel: JSON.parse(String(options?.body)).channel, body: '平台正文' });
    return Promise.resolve(path.endsWith('jd-versions/') ? [jd] : row);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function generate() {
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: row.original_text } });
  fireEvent.click(screen.getByRole('button', { name: '生成通用 JD' }));
  await screen.findByDisplayValue(jd.body);
}

it('进入即弹出对话框，不展示模型、岗位和结构化表单', () => {
  render(<RecruitmentJobs />);
  expect(screen.getByRole('dialog', { name: 'JD 生成助手' })).toBeTruthy();
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(screen.queryByText('人工修订结构化字段（可选）')).toBeNull();
  expect(screen.queryByRole('button', { name: '生成BOSS直聘 JD' })).toBeNull();
  expect(api.apiRequest).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '生成通用 JD' }) as HTMLButtonElement).disabled).toBe(true);
});

it('中文需求直接生成通用正文，后台默认模型且无需额外输入', async () => {
  render(<RecruitmentJobs />);
  await generate();
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/intake/', expect.objectContaining({ body: JSON.stringify({ text: row.original_text }) }));
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/r1/generate-jd/', expect.objectContaining({ body: JSON.stringify({ expected_version: 1 }) }));
  expect(api.apiRequest.mock.calls.some(([path]) => path.includes('/models/'))).toBe(false);
  expect((screen.getByLabelText('JD 正文') as HTMLTextAreaElement).readOnly).toBe(true);
  for (const name of ['BOSS直聘', '智联招聘', '前程无忧', '猎聘']) expect(screen.getByRole('button', { name: `生成${name} JD` })).toBeTruthy();
});

it('平台一键生成先确认通用 JD，之后仍可点击其他平台', async () => {
  render(<RecruitmentJobs />); await generate();
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  await screen.findByDisplayValue('平台正文');
  fireEvent.click(screen.getByRole('button', { name: '生成智联招聘 JD' }));
  await screen.findByRole('heading', { name: '智联招聘 JD' });
  const calls = api.apiRequest.mock.calls;
  expect(calls.filter(([path]) => path.endsWith('/confirm/'))).toHaveLength(1);
  expect(calls.findIndex(([path]) => path.endsWith('/confirm/'))).toBeLessThan(calls.findIndex(([path]) => path.endsWith('/adapt/')));
  expect(calls.filter(([path]) => path.endsWith('/adapt/')).map(([path, options]) => [path, JSON.parse(options.body)])).toEqual([
    ['/api/hr/recruitment/requests/r1/jd-versions/jd1/adapt/', { expected_version: 1, channel: 'boss' }],
    ['/api/hr/recruitment/requests/r1/jd-versions/jd1/adapt/', { expected_version: 1, channel: 'zhaopin' }],
  ]);
  fireEvent.click(screen.getByRole('button', { name: '查看通用 JD' }));
  expect(screen.getByDisplayValue(jd.body)).toBeTruthy();
});

it('生成失败保留说明，重试复用已经创建的需求', async () => {
  const original = api.apiRequest.getMockImplementation()!;
  let fail = true;
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => path.endsWith('/generate-jd/') && fail ? Promise.reject(new Error('内置模型暂不可用')) : original(path, options));
  render(<RecruitmentJobs />);
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: row.original_text } });
  fireEvent.click(screen.getByRole('button', { name: '生成通用 JD' }));
  expect((await screen.findByRole('alert')).textContent).toContain('内置模型暂不可用');
  expect(screen.getByDisplayValue(row.original_text)).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: '生成通用 JD' }));
  await screen.findByDisplayValue(jd.body);
  expect(api.apiRequest.mock.calls.filter(([path]) => path.endsWith('/intake/'))).toHaveLength(1);
});

it('确认失败不适配，适配失败保留通用正文并可重试', async () => {
  const original = api.apiRequest.getMockImplementation()!;
  let failure = '/confirm/';
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => path.endsWith(failure) ? Promise.reject(new Error('操作失败')) : original(path, options));
  render(<RecruitmentJobs />); await generate();
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  await screen.findByRole('alert');
  expect(api.apiRequest.mock.calls.some(([path]) => path.endsWith('/adapt/'))).toBe(false);
  failure = '/adapt/';
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  await screen.findByRole('alert');
  expect(screen.getByDisplayValue(jd.body)).toBeTruthy();
  failure = 'never';
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  await screen.findByDisplayValue('平台正文');
  expect(api.apiRequest.mock.calls.filter(([path]) => path.endsWith('/confirm/'))).toHaveLength(2);
});

it('关闭再打开保留说明，未提交说明阻止应用内导航', async () => {
  render(<RecruitmentJobs />);
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: row.original_text } });
  fireEvent.click(screen.getByRole('button', { name: '关闭 JD 对话框' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '打开 JD 对话框' }));
  expect(screen.getByDisplayValue(row.original_text)).toBeTruthy();
  const event = new Event('portal:navigation-guard', { cancelable: true });
  fireEvent(window, event);
  expect(event.defaultPrevented).toBe(true);
});

it('历史深链加载通用正文，切换到无权记录清除旧内容', async () => {
  window.history.replaceState({}, '', '/centers/hr/job?task=r1');
  const view = render(<RecruitmentJobs />);
  await screen.findByDisplayValue(jd.body);
  api.apiRequest.mockRejectedValue(new Error('对象不存在或没有访问权限'));
  window.history.replaceState({}, '', '/centers/hr/job?task=foreign');
  view.rerender(<RecruitmentJobs />);
  await screen.findByRole('alert');
  expect(screen.queryByLabelText('JD 正文')).toBeNull();
});

it('过期通用 JD 不允许适配', async () => {
  window.history.replaceState({}, '', '/centers/hr/job?task=r1');
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('jd-versions/') ? [{ ...jd, stale: true }] : row));
  render(<RecruitmentJobs />);
  await screen.findByDisplayValue(jd.body);
  expect((screen.getByRole('button', { name: '生成BOSS直聘 JD' }) as HTMLButtonElement).disabled).toBe(true);
});

it('旧版历史只读，不弹出生成对话框', async () => {
  api.apiRequest.mockResolvedValue([{ id: 'old', title: '旧岗位', revisions: [{ id: 'old-jd', version: 1, body: '旧版正文' }] }]);
  render(<RecruitmentJobs legacy />);
  await waitFor(() => expect(screen.getByText('旧版正文')).toBeTruthy());
  expect(screen.queryByRole('dialog')).toBeNull();
});
