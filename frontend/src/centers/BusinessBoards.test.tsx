import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BusinessOverview } from './BusinessBoards';
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
it('home shows all real distributions in finance, product, engineering order', async () => {
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(fixture(url.includes('/finance/') ? 'finance' : url.includes('/presales/') ? 'presales' : 'engineering')));
  render(<BusinessOverview />);
  await waitFor(() => expect(screen.getAllByRole('meter', { name: '实施中 1 项' })).toHaveLength(3));
  expect(api.apiRequest.mock.calls.map(([url]) => url)).toEqual([
    '/api/business/boards/finance/', '/api/business/boards/presales/', '/api/business/boards/engineering/',
  ]);
  expect(screen.getAllByRole('meter', { name: '实施中 1 项' })).toHaveLength(3);
  expect(screen.getByRole('heading', { name: '数据分析 / BI报表看板' })).toBeTruthy();
  expect(screen.getByText('财务条目分布')).toBeTruthy();
  expect(screen.getByText('产品条目分布')).toBeTruthy();
  expect(screen.queryByText(/结清状态分布|项目状态分布/)).toBeNull();
  expect(screen.getAllByRole('article').map(card => card.getAttribute('aria-label'))).toEqual(['财务部看板', '产品事业部看板', '工程部看板']);
  expect(screen.getAllByRole('link', { name: /查看数据明细/ })[0].getAttribute('href')).toBe('/centers/business/finance');
});
it('overview keeps unavailable and failed departments separate without invented totals', async () => {
  api.apiRequest.mockImplementation((url: string) => {
    if (url.includes('/finance/')) return Promise.reject(new Error('财务读取失败'));
    return Promise.resolve(fixture(url.includes('/presales/') ? 'presales' : 'engineering', url.includes('/presales/')));
  });
  render(<BusinessOverview />);
  expect(await screen.findByText('财务读取失败')).toBeTruthy();
  expect(screen.getByText('暂无已发布台账，未提供的数据以“—”展示。')).toBeTruthy();
  expect(screen.getByRole('article', { name: '工程部看板' }).textContent).toContain('—');
  expect(screen.queryByText('一期项目')).toBeNull();
  expect(screen.getByRole('article', { name: '产品事业部看板' }).textContent).toContain('未发布工作数据');
  expect(within(screen.getByRole('article', { name: '工程部看板' })).queryByRole('meter')).toBeNull();
  expect(within(screen.getByRole('article', { name: '财务部看板' })).queryByText('已发布')).toBeNull();
});
it('overview preview never fetches or displays real business numbers', () => {
  render(<BusinessOverview preview />);
  expect(screen.getAllByText('预览不读取业务数据。')).toHaveLength(3);
  expect(api.apiRequest).not.toHaveBeenCalled();
});
it('overview clears prior values after a refresh fails', async () => {
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(fixture(url.includes('/finance/') ? 'finance' : url.includes('/presales/') ? 'presales' : 'engineering')));
  render(<BusinessOverview />);
  await waitFor(() => expect(screen.getAllByRole('meter', { name: '实施中 1 项' })).toHaveLength(3));
  api.apiRequest.mockRejectedValue(new Error('访问已撤销'));
  await userEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  expect(await screen.findAllByText('访问已撤销')).toHaveLength(3);
  expect(screen.queryAllByText('1')).toHaveLength(0);
});
it('overview refresh keeps existing cards visible until fresh data arrives', async () => {
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(fixture(url.includes('/finance/') ? 'finance' : url.includes('/presales/') ? 'presales' : 'engineering')));
  render(<BusinessOverview />);
  await waitFor(() => expect(screen.getAllByRole('meter', { name: '实施中 1 项' })).toHaveLength(3));
  const pending: { url: string; resolve: (value: unknown) => void }[] = [];
  api.apiRequest.mockImplementation((url: string) => new Promise(resolve => { pending.push({ url, resolve }); }));
  await userEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  expect(screen.getAllByRole('meter', { name: '实施中 1 项' })).toHaveLength(3);
  expect(screen.getAllByRole('link', { name: /查看数据明细/ })).toHaveLength(3);
  await act(async () => pending.forEach(({ url, resolve }) => resolve(fixture(url.includes('/finance/') ? 'finance' : url.includes('/presales/') ? 'presales' : 'engineering'))));
});
it('money display does not round away cents in large aggregates', async () => {
  const data = { ...fixture('finance'), metrics: [{ key: 'contract', label: '合同金额', value: '1999999999999999.99', unit: '元' }] };
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(url.includes('/finance/') ? data : fixture(url.includes('/presales/') ? 'presales' : 'engineering', false)));
  render(<BusinessOverview />);
  expect(await screen.findByText('1,999,999,999,999,999.99')).toBeTruthy();
});
it('home charts source financial metrics without inferred amounts or null-to-zero conversion', async () => {
  const data = { ...fixture('finance'), metrics: [
    { key: 'opening_receivable', label: '期初应收', value: '100.50', unit: '元' },
    { key: 'receivable_balance', label: '应收余额', value: '80.25', unit: '元' },
    { key: 'realized_1', label: '1月实际', value: '0.00', unit: '元' },
    { key: 'planned_8', label: '8月计划', value: null, unit: '元' },
  ] };
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(url.includes('/finance/') ? data : fixture(url.includes('/presales/') ? 'presales' : 'engineering', false)));
  render(<BusinessOverview />);
  const chart = await screen.findByRole('figure', { name: '财务金额对比' });
  expect(within(chart).getAllByRole('img')).toHaveLength(3);
  expect(within(chart).getByRole('img', { name: '期初应收 100.50 元' })).toBeTruthy();
  expect(within(chart).getByRole('img', { name: '1月实际 0.00 元' }).querySelector('.business-chart-fill')?.getAttribute('width')).toBe('0');
  expect(within(chart).getByText('—')).toBeTruthy();
  expect(screen.queryByText('20.25')).toBeNull();
  expect(screen.queryByText(/合同金额－已收金额/)).toBeNull();
});
it('home keeps all API categories including empty stages instead of only the top three', async () => {
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(url.includes('/presales/') ? {
    ...fixture('presales'), distribution: ['已赢单', '阶段二', '阶段三', '未填写'].map((label, index) => ({ label, count: index })),
  } : fixture(url.includes('/finance/') ? 'finance' : 'engineering', false)));
  render(<BusinessOverview />);
  expect(await screen.findByRole('meter', { name: '未填写 3 项' })).toBeTruthy();
  expect(screen.getAllByRole('meter')).toHaveLength(4);
  expect(screen.getByRole('meter', { name: '已赢单 0 项' })).toBeTruthy();
});
it('preview aborts pending home requests and ignores their late responses', async () => {
  const resolvers: ((value: unknown) => void)[] = [];
  api.apiRequest.mockImplementation(() => new Promise(resolve => { resolvers.push(resolve); }));
  const view = render(<BusinessOverview />);
  const requests = [...api.apiRequest.mock.calls];
  view.rerender(<BusinessOverview preview />);
  expect(requests.every(([, options]) => options.signal.aborted)).toBe(true);
  await act(async () => resolvers.forEach((resolve, index) => resolve(fixture(['finance', 'presales', 'engineering'][index]))));
  expect(screen.queryByRole('meter')).toBeNull();
  expect(screen.getAllByText('预览不读取业务数据。')).toHaveLength(3);
});
