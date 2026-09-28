import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecruitmentJobs from './RecruitmentJobs';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const row = { id: 'r1', position_name: '项目交付经理', headcount: 2, responsibilities: '交付', required_requirements: '管理', preferred_requirements: '', education_requirement: '本科', experience_requirement: '三年', skill_requirements: ['SQL'], work_location: '西安', notes: '', input_version: 1, current_jd_id: 'jd1', official_jd_id: null, missing_items: [], updated_at: '' };
const models = { route: { code: 'hr_jd_draft', name: 'JD', module: 'hr' }, default_model_id: 'model-1', models: [
  { id: 'model-1', name: '默认模型', config_version: 'v1', capabilities: { text: true, vision: false }, max_output_tokens: 1000, is_default: true },
  { id: 'model-2', name: '备选模型', config_version: 'v2', capabilities: { text: true, vision: false }, max_output_tokens: 1000, is_default: false },
] };
const jd = { id: 'jd1', request_id: 'r1', version: 1, input_version: 1, state: 'draft', source: 'skill', body: '岗位正文', channel: 'general', stale: false };
beforeEach(() => {
  window.history.replaceState({}, '', '/centers/hr/job?task=r1');
  api.apiRequest.mockReset();
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.includes('/api/models/routes/') ? models : path.endsWith('jd-versions/') ? [jd] : path.endsWith('requests/') ? [row] : row));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('岗位深链加载正确正文，未保存文本不能确认', async () => {
  render(<RecruitmentJobs />);
  expect((await screen.findByDisplayValue('岗位正文'))).toBeTruthy();
  expect((screen.getByRole('button', { name: '确认 JD' }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(screen.getByLabelText('JD 正文'), { target: { value: '未保存正文' } });
  expect((screen.getByRole('button', { name: '确认 JD' }) as HTMLButtonElement).disabled).toBe(true);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('link', { name: '招聘历史' }));
  expect(confirm).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/centers/hr/job');
  const navigation = new Event('portal:navigation-guard', { cancelable: true });
  fireEvent(window, navigation);
  expect(navigation.defaultPrevented).toBe(true);
});
it('从有效岗位切换无权深链后清除旧正文', async () => {
  const view = render(<RecruitmentJobs />);
  await screen.findByDisplayValue('岗位正文');
  window.history.replaceState({}, '', '/centers/hr/job?task=foreign');
  view.rerender(<RecruitmentJobs />);
  await screen.findByRole('alert');
  expect(screen.queryByLabelText('JD 正文')).toBeNull();
});

it('无权深链不显示其他岗位正文', async () => {
  window.history.replaceState({}, '', '/centers/hr/job?task=foreign');
  render(<RecruitmentJobs />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('没有访问权限'));
  expect(screen.queryByLabelText('JD 正文')).toBeNull();
});

it('中文需求先入库再通过网关生成通用 JD，保留结构化表单', async () => {
  window.history.replaceState({}, '', '/centers/hr/job');
  const intake = { ...row, original_text: '想招交付经理，工资一万，五险一金', intake_source: 'text', salary: '工资一万', social_insurance: '五险一金', current_jd_id: 'jd1' };
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => Promise.resolve(
    path.includes('/api/models/routes/') ? models : path.endsWith('/intake/') ? { request: intake, jd } : path.endsWith('/generate-jd/') ? { ...jd, source: 'skill', body: '网关生成的通用正文', requirements: { position_name: '项目交付经理', salary: '工资一万', social_insurance: '五险一金' } } : path.endsWith('jd-versions/') ? [jd] : path.endsWith('requests/') ? [intake] : intake));
  render(<RecruitmentJobs />);
  expect(screen.queryByRole('button', { name: '生成BOSS直聘 JD' })).toBeNull();
  await screen.findByRole('option', { name: '备选模型' });
  fireEvent.change(screen.getByLabelText('JD 生成模型'), { target: { value: 'model-2' } });
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: intake.original_text } });
  fireEvent.click(screen.getByRole('button', { name: '整理需求并生成通用 JD' }));
  await screen.findByDisplayValue('网关生成的通用正文');
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/intake/', expect.objectContaining({ body: JSON.stringify({ text: intake.original_text }) }));
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/r1/generate-jd/', expect.objectContaining({ body: JSON.stringify({ expected_version: 1, model_selection: { model_id: 'model-2', config_version: 'v2' } }) }));
  const reply = screen.getByText('通用 JD 回复').parentElement!;
  expect(within(reply).getByText('工资一万')).toBeTruthy();
  expect(within(reply).getAllByText('待补充').length).toBeGreaterThan(0);
  expect(within(reply).getByText('网关生成的通用正文')).toBeTruthy();
  expect(within(reply).getByRole('button', { name: '生成BOSS直聘 JD' })).toBeTruthy();
  expect(within(reply).getByRole('button', { name: '生成智联招聘 JD' })).toBeTruthy();
  expect(screen.getByText('人工修订结构化字段（可选）').closest('details')?.open).toBe(false);
});
it('网关失败保留已经持久化的原文草稿，不伪造模型成功', async () => {
  window.history.replaceState({}, '', '/centers/hr/job');
  api.apiRequest.mockImplementation((path: string) => path.includes('/api/models/routes/') ? Promise.resolve(models) : path.endsWith('generate-jd/') ? Promise.reject(new Error('模型网关不可用')) : Promise.resolve(path.endsWith('/intake/') ? { request: row, jd } : path.endsWith('jd-versions/') ? [{ ...jd, source: 'text' }] : path.endsWith('requests/') ? [row] : row));
  render(<RecruitmentJobs />);
  await screen.findByRole('option', { name: '备选模型' });
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: '想招交付经理' } });
  fireEvent.click(screen.getByRole('button', { name: '整理需求并生成通用 JD' }));
  expect((await screen.findByRole('alert')).textContent).toContain('模型网关不可用');
  expect(screen.getByDisplayValue('岗位正文')).toBeTruthy();
  expect(screen.getByText(/尚非模型生成/)).toBeTruthy();
  expect(screen.queryByText('通用 JD 回复')).toBeNull();
  expect(screen.queryByRole('button', { name: '生成BOSS直聘 JD' })).toBeNull();
});

it('结构化需求有未保存修改时不能确认原 JD', async () => {
  render(<RecruitmentJobs />); await screen.findByDisplayValue('岗位正文');
  fireEvent.change(screen.getByLabelText('岗位职责'), { target: { value: '变化后的职责' } });
  expect((screen.getByRole('button', { name: '确认 JD' }) as HTMLButtonElement).disabled).toBe(true);
});

it('模型不可用时不创建需求也不生成 JD', async () => {
  window.history.replaceState({}, '', '/centers/hr/job');
  api.apiRequest.mockImplementation((path: string) => path.includes('/api/models/routes/') ? Promise.reject(new Error('模型列表不可用')) : Promise.resolve([]));
  render(<RecruitmentJobs />);
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: '招聘交付经理' } });
  await screen.findByText('模型列表不可用');
  expect((screen.getByRole('button', { name: '整理需求并生成通用 JD' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.apiRequest.mock.calls.some(([path]) => path.endsWith('/intake/') || path.endsWith('/generate-jd/'))).toBe(false);
});

function mockPlatformFlow(failAdapt = false) {
  let current = { ...row, intake_source: 'text', original_text: '招聘项目交付经理', current_jd_id: 'jd1', official_jd_id: null as string | null };
  let versions = [{ ...jd, requirements: { position_name: '项目交付经理', salary: '' } }];
  api.apiRequest.mockImplementation((path: string) => {
    if (path.includes('/api/models/routes/')) return Promise.resolve(models);
    if (path.endsWith('/confirm/')) {
      current = { ...current, official_jd_id: 'jd1' };
      versions = [{ ...versions[0], state: 'confirmed' }];
      return Promise.resolve(versions[0]);
    }
    if (path.endsWith('/adapt/')) return failAdapt ? Promise.reject(new Error('平台生成失败')) : Promise.resolve({ ...jd, id: 'platform1', channel: 'boss', body: '平台正文', state: 'draft' });
    return Promise.resolve(path.endsWith('jd-versions/') ? versions : path.endsWith('requests/') ? [current] : current);
  });
}

it('一键 BOSS 先确认当前通用草稿，再携正式 ID 适配', async () => {
  mockPlatformFlow();
  render(<RecruitmentJobs />);
  await screen.findByRole('button', { name: '生成BOSS直聘 JD' });
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  await screen.findByDisplayValue('平台正文');
  const calls = api.apiRequest.mock.calls;
  const confirm = calls.findIndex(([path]) => path.endsWith('/confirm/'));
  const adapt = calls.findIndex(([path]) => path.endsWith('/adapt/'));
  expect(confirm).toBeGreaterThan(-1);
  expect(adapt).toBeGreaterThan(confirm);
  expect(calls[confirm][1].body).toBe(JSON.stringify({ expected_version: 1 }));
  expect(calls[adapt][0]).toContain('/jd-versions/jd1/adapt/');
  expect(JSON.parse(calls[adapt][1].body)).toMatchObject({ expected_version: 1, channel: 'boss', model_selection: { model_id: 'model-1', config_version: 'v1' } });
});

it('智联按钮直接生成智联平台版本，未保存修改禁止快捷操作', async () => {
  mockPlatformFlow();
  render(<RecruitmentJobs />);
  await screen.findByRole('button', { name: '生成智联招聘 JD' });
  fireEvent.change(screen.getByLabelText('JD 正文'), { target: { value: '未保存的正文' } });
  expect((screen.getByRole('button', { name: '生成智联招聘 JD' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('JD 正文'), { target: { value: '岗位正文' } });
  fireEvent.click(screen.getByRole('button', { name: '生成智联招聘 JD' }));
  await waitFor(() => expect(api.apiRequest.mock.calls.some(([path, options]) => path.endsWith('/adapt/') && JSON.parse(options.body).channel === 'zhaopin')).toBe(true));
});

it('适配失败后仍显示已确认状态，不假报平台成功', async () => {
  mockPlatformFlow(true);
  render(<RecruitmentJobs />);
  await screen.findByRole('button', { name: '生成BOSS直聘 JD' });
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  expect((await screen.findByRole('alert')).textContent).toContain('平台生成失败');
  expect(screen.getByRole('option', { name: 'v1 · 通用版 · 已确认' })).toBeTruthy();
  expect((screen.getByRole('button', { name: '确认 JD' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByDisplayValue('平台正文')).toBeNull();
});

it('确认失败不调用平台适配接口', async () => {
  mockPlatformFlow();
  const original = api.apiRequest.getMockImplementation()!;
  api.apiRequest.mockImplementation((path: string, options?: RequestInit) => path.endsWith('/confirm/') ? Promise.reject(new Error('确认冲突')) : original(path, options));
  render(<RecruitmentJobs />);
  await screen.findByRole('button', { name: '生成BOSS直聘 JD' });
  fireEvent.click(screen.getByRole('button', { name: '生成BOSS直聘 JD' }));
  expect((await screen.findByRole('alert')).textContent).toContain('确认冲突');
  expect(api.apiRequest.mock.calls.some(([path]) => path.endsWith('/adapt/'))).toBe(false);
});
