import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import BusinessBoards from './BusinessBoards';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), ...api }));
function fixture(department = 'engineering', available = true) {
  return {
    department, title: department === 'finance' ? '财务部看板' : department === 'presales' ? '售前部门看板' : '工程部看板',
    available, snapshot_id: available ? 'snapshot-1' : '',
    fields: { project_id: '项目编号', project_name: '项目名称', status: '状态' },
    statuses: ['实施中', '已验收'], records: available ? [{ project_id: 'P1', project_name: '一期项目', status: '实施中' }] : [],
    metrics: [{ key: 'projects', label: '项目总数', value: available ? 1 : null, unit: '项' }],
    distribution: [{ label: '实施中', count: available ? 1 : 0 }], total: available ? 1 : 0, filtered_count: available ? 1 : 0,
    source: available ? { name: '实际台账.csv', as_of: '2026-09-01', imported_at: '2026-09-02T08:00:00Z', kind: 'csv_snapshot' } : null,
    scope: '当前账号导入的台账快照。', currency: 'CNY',
  };
}
beforeEach(() => { api.apiRequest.mockReset(); });
afterEach(cleanup);
it('three department tabs show actual source, state distribution and project details', async () => {
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(fixture(url.includes('/finance/') ? 'finance' : url.includes('/presales/') ? 'presales' : 'engineering')));
  render(<BusinessBoards />);
  expect(await screen.findByText('一期项目')).toBeTruthy();
  expect(screen.getByText(/实际台账.csv.*2026-09-01/)).toBeTruthy();
  expect(screen.getByRole('meter', { name: '实施中 1 项' })).toBeTruthy();
  await userEvent.click(screen.getByRole('tab', { name: '财务部看板' }));
  expect(await screen.findByRole('tabpanel', { name: '财务部看板' })).toBeTruthy();
  expect(screen.getByText(/不等同于会计确认/)).toBeTruthy();
  await userEvent.click(screen.getByRole('tab', { name: '售前部门看板' }));
  expect(await screen.findByRole('tabpanel', { name: '售前部门看板' })).toBeTruthy();
});
it('preview never reads or imports business data', async () => {
  render(<BusinessBoards preview />);
  for (const name of ['工程部看板', '财务部看板', '售前部门看板']) await userEvent.click(screen.getByRole('tab', { name }));
  expect(api.apiRequest).not.toHaveBeenCalled();
  expect(screen.queryByLabelText('台账 CSV')).toBeNull();
});
it('unconfigured data is a dash rather than fabricated zero and offers an empty template', async () => {
  api.apiRequest.mockResolvedValue(fixture('engineering', false));
  render(<BusinessBoards />);
  expect(await screen.findByText('尚未导入工程部台账')).toBeTruthy();
  expect(screen.getByText('—')).toBeTruthy();
  expect(screen.getByRole('link', { name: '下载工程部看板模板' }).getAttribute('href')).toBe('/api/business/boards/engineering/template/');
});
it('uploads FormData with the current snapshot version and refreshes after success', async () => {
  api.apiRequest.mockResolvedValue(fixture('engineering', false));
  render(<BusinessBoards />);
  await screen.findByLabelText('台账 CSV');
  await userEvent.upload(screen.getByLabelText('台账 CSV'), new File(['项目编号,项目名称\nP1,一期'], '台账.csv', { type: 'text/csv' }));
  api.apiRequest.mockResolvedValue(fixture());
  await userEvent.click(screen.getByRole('button', { name: '导入并更新看板' }));
  expect(await screen.findByText('已导入 1 条台账记录。')).toBeTruthy();
  const call = api.apiRequest.mock.calls.find(([, options]) => options?.method === 'POST');
  expect(call?.[1].body).toBeInstanceOf(FormData);
  expect(call?.[1].body.get('expected_snapshot_id')).toBe('');
  expect(call?.[1].body.get('file').name).toBe('台账.csv');
  expect(await screen.findByText('一期项目')).toBeTruthy();
});
it('server-side filtering preserves the source while showing filtered counts', async () => {
  api.apiRequest.mockResolvedValue(fixture());
  render(<BusinessBoards />);
  await screen.findByText('一期项目');
  await userEvent.type(screen.getByLabelText('搜索项目'), '一期');
  await userEvent.selectOptions(screen.getByLabelText('状态'), '实施中');
  await userEvent.click(screen.getByRole('button', { name: '查询' }));
  await waitFor(() => expect(api.apiRequest.mock.calls.some(([url]) => String(url).includes('q=%E4%B8%80%E6%9C%9F&status='))).toBe(true));
});
it('late response from an abandoned department cannot replace the current board', async () => {
  let resolve!: (value: unknown) => void;
  api.apiRequest.mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValue(fixture('finance'));
  render(<BusinessBoards />);
  await userEvent.click(screen.getByRole('tab', { name: '财务部看板' }));
  await screen.findByRole('tabpanel', { name: '财务部看板' });
  await act(async () => resolve(fixture('engineering')));
  expect(screen.queryByRole('tabpanel', { name: '工程部看板' })).toBeNull();
});
it('failed refresh hides previous business data', async () => {
  api.apiRequest.mockResolvedValueOnce(fixture());
  render(<BusinessBoards />);
  await screen.findByText('一期项目');
  api.apiRequest.mockRejectedValue(new Error('权限已撤销'));
  await userEvent.click(screen.getByRole('button', { name: '刷新看板' }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('一期项目')).toBeNull();
});
it('money display does not round away cents in large aggregates', async () => {
  const data = { ...fixture('finance'), metrics: [{ key: 'contract', label: '合同金额', value: '1999999999999999.99', unit: '元' }] };
  api.apiRequest.mockResolvedValue(data);
  render(<BusinessBoards initial="finance" />);
  expect(await screen.findByText('1,999,999,999,999,999.99')).toBeTruthy();
});
