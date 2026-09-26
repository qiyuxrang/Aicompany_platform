import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecruitmentJobs from './RecruitmentJobs';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const row = { id: 'r1', position_name: '项目交付经理', headcount: 2, responsibilities: '交付', required_requirements: '管理', preferred_requirements: '', education_requirement: '本科', experience_requirement: '三年', skill_requirements: ['SQL'], work_location: '西安', notes: '', input_version: 1, current_jd_id: 'jd1', official_jd_id: null, missing_items: [], updated_at: '' };
const jd = { id: 'jd1', request_id: 'r1', version: 1, input_version: 1, state: 'draft', source: 'skill', body: '岗位正文', channel: 'general', stale: false };
beforeEach(() => {
  window.history.replaceState({}, '', '/centers/hr/job?task=r1');
  api.apiRequest.mockReset();
  api.apiRequest.mockImplementation((path: string) => Promise.resolve(path.endsWith('jd-versions/') ? [jd] : path.endsWith('requests/') ? [row] : row));
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
    path.endsWith('/intake/') ? { request: intake, jd } : path.endsWith('/generate-jd/') ? { ...jd, source: 'skill', body: '网关生成的通用正文' } : path.endsWith('jd-versions/') ? [jd] : path.endsWith('requests/') ? [intake] : intake));
  render(<RecruitmentJobs />);
  expect(screen.queryByLabelText('招聘平台')).toBeNull();
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: intake.original_text } });
  fireEvent.click(screen.getByRole('button', { name: '整理需求并生成通用 JD' }));
  await screen.findByDisplayValue('网关生成的通用正文');
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/intake/', expect.objectContaining({ body: JSON.stringify({ text: intake.original_text }) }));
  expect(api.apiRequest).toHaveBeenCalledWith('/api/hr/recruitment/requests/r1/generate-jd/', expect.objectContaining({ body: JSON.stringify({ expected_version: 1 }) }));
  expect(screen.getByDisplayValue('工资一万')).toBeTruthy();
  expect(screen.getByRole('option', { name: 'BOSS直聘' })).toBeTruthy();
  expect(screen.getByRole('option', { name: '智联招聘' })).toBeTruthy();
});
it('网关失败保留已经持久化的原文草稿，不伪造模型成功', async () => {
  window.history.replaceState({}, '', '/centers/hr/job');
  api.apiRequest.mockImplementation((path: string) => path.endsWith('generate-jd/') ? Promise.reject(new Error('模型网关不可用')) : Promise.resolve(path.endsWith('/intake/') ? { request: row, jd } : path.endsWith('jd-versions/') ? [{ ...jd, source: 'text' }] : path.endsWith('requests/') ? [row] : row));
  render(<RecruitmentJobs />);
  fireEvent.change(screen.getByLabelText('招聘说明'), { target: { value: '想招交付经理' } });
  fireEvent.click(screen.getByRole('button', { name: '整理需求并生成通用 JD' }));
  expect((await screen.findByRole('alert')).textContent).toContain('模型网关不可用');
  expect(screen.getByDisplayValue('岗位正文')).toBeTruthy();
  expect(screen.getByText(/尚非模型生成/)).toBeTruthy();
});

it('结构化需求有未保存修改时不能确认原 JD', async () => {
  render(<RecruitmentJobs />); await screen.findByDisplayValue('岗位正文');
  fireEvent.change(screen.getByLabelText('岗位职责'), { target: { value: '变化后的职责' } });
  expect((screen.getByRole('button', { name: '确认 JD' }) as HTMLButtonElement).disabled).toBe(true);
});
