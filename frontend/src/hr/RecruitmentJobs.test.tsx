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
  fireEvent.click(screen.getByRole('link', { name: '历史 JD' }));
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
