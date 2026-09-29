import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import BusinessBoards from './BusinessBoards';
import type { BusinessDepartment } from './BusinessProjects';

const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), ...api }));
const project = { id: 'P1', name: '园区项目', record_count: 2, status: '方案已更新', owner: '原表负责人',
  updated_at: '2026-09-29T06:00:00Z', updated_by: { id: 4, name: '实际更新人' }, update_kind: 'record_update' };
const source = { name: '产品工作管控.xls', as_of: '2026-09-29', imported_at: '2026-09-29T06:01:00Z', state: 'published', revision: 3 };
function list(department: BusinessDepartment = 'presales') {
  return { department, title: '项目看板', available: true, source, projects: [project], total: 1, record_total: 2, scope: '只读范围' };
}
function detail(department: BusinessDepartment = 'presales') {
  return { department, project, source, scope: '只读范围', fields: { project_name: '项目名称', owner: '负责人', amount: '原始金额', source_sheet: '来源工作表', follow_up_history: '历史' },
    records: [
      { project_id: 'P1', project_name: '园区项目', owner: '原表负责人', project_progress: '需求调研阶段', amount: '', source_sheet: '商机', source_row: '2-5', follow_up_history: '2024-01-01 调研\n2024-02-01 方案交流' },
      { project_id: 'P2', project_name: '园区项目', owner: '另一个负责人', project_progress: '已签约', amount: '123.45', source_sheet: '售前支撑', source_row: '8', follow_up_history: '' },
    ], updates: [{ id: 'event1', at: '2026-09-29T06:00:00Z', actor: { id: 4, name: '实际更新人' }, action: 'record_update', state: 'published', summary: '项目进展已更新' }] };
}
beforeEach(() => {
  api.apiRequest.mockReset();
  window.history.replaceState({}, '', '/centers/business/presales');
  api.apiRequest.mockImplementation((url: string) => Promise.resolve(url.endsWith('/projects/P1/') ? detail() : list()));
});
afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); });

it.each(['finance', 'presales', 'engineering'] as const)('%s opens only a concise clickable project list with no duplicate heading or charts', async department => {
  window.history.replaceState({}, '', `/centers/business/${department}`);
  api.apiRequest.mockResolvedValue(list(department));
  render(<BusinessBoards initial={department} />);
  const link = await screen.findByRole('link', { name: /园区项目/ });
  expect(link.getAttribute('href')).toBe(`/centers/business/${department}?project=P1`);
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getAllByRole('columnheader').map(header => header.textContent)).toEqual(['项目名称', '当前状态 / 项目进度', '数据记录时间', '记录操作人']);
  expect(screen.getByText('实际更新人')).toBeTruthy();
  expect(screen.queryByText('原表负责人')).toBeNull();
  expect(screen.queryByRole('meter')).toBeNull();
  expect(screen.queryByRole('figure')).toBeNull();
  expect(screen.queryByText('部门数据明细')).toBeNull();
  expect(screen.queryByText('来源工作表')).toBeNull();
  expect(api.apiRequest).toHaveBeenCalledWith(`/api/business/boards/${department}/projects/`, expect.anything());
});

it('opens a project with its own state and updater, switches sources without losing rows and returns to the list', async () => {
  render(<BusinessBoards initial="presales" />);
  await userEvent.click(await screen.findByRole('link', { name: /园区项目/ }));
  expect(await screen.findByRole('heading', { name: '当前状态与项目进度' })).toBeTruthy();
  expect(screen.queryByText('数据操作日志（导入 / 修改记录）')).toBeNull();
  expect(screen.getByRole('heading', { name: '园区项目', level: 1 })).toBeTruthy();
  expect(screen.queryByText('项目进展已更新')).toBeNull();
  expect(screen.getByText('原表负责人／跟进人员')).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByRole('combobox', { name: '来源记录' })).toBeTruthy();
  expect(screen.queryByText('原始金额')).toBeNull();
  expect(screen.getByRole('list', { name: '项目阶段：需求调研' })).toBeTruthy();
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '来源记录' }), 'P2');
  expect(screen.getByRole('list', { name: '项目阶段：已签约' })).toBeTruthy();
  expect(screen.getByText('原始金额')).toBeTruthy();
  expect(screen.getByText('123.45')).toBeTruthy();
  await userEvent.click(screen.getByRole('link', { name: '← 返回项目列表' }));
  expect(await screen.findByRole('link', { name: /园区项目/ })).toBeTruthy();
  expect(window.location.search).toBe('');
  expect(screen.queryByRole('heading', { name: '平台更新记录' })).toBeNull();
});

it('source import credits the actual importer without fabricating the original business updater', async () => {
  window.history.replaceState({}, '', '/centers/business/presales?project=P1');
  api.apiRequest.mockResolvedValue({ ...detail(), project: { ...project, update_kind: 'source_import', updated_by: { id: 4, name: '台账导入员' } } });
  render(<BusinessBoards initial="presales" />);
  expect(await screen.findByText('台账导入员')).toBeTruthy();
  expect(screen.getByText('导入人')).toBeTruthy();
  expect(screen.getByText(/导入人不等同于原始业务更新人/)).toBeTruthy();
  expect(screen.getByText('最近平台导入')).toBeTruthy();
});

it('search matches names and actors without any data mutation', async () => {
  render(<BusinessBoards initial="presales" />);
  await screen.findByRole('link', { name: /园区项目/ });
  await userEvent.type(screen.getByRole('searchbox', { name: '搜索项目' }), '实际更新人');
  expect(screen.getByRole('link', { name: /园区项目/ })).toBeTruthy();
  await userEvent.clear(screen.getByRole('searchbox', { name: '搜索项目' }));
  await userEvent.type(screen.getByRole('searchbox', { name: '搜索项目' }), '无匹配');
  expect(screen.queryByRole('link', { name: /园区项目/ })).toBeNull();
  expect(screen.getByText('没有符合搜索条件的项目。')).toBeTruthy();
  expect(api.apiRequest).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: /新增|编辑|发布|导入/ })).toBeNull();
});

it('preserves empty engineering instead of showing fake project data', async () => {
  api.apiRequest.mockResolvedValue({ ...list('engineering'), available: false, source: null, projects: [], total: 0, record_total: 0 });
  render(<BusinessBoards initial="engineering" />);
  expect(await screen.findByRole('heading', { name: '暂无工程部项目' })).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
});

it('preview never fetches projects or details', () => {
  window.history.replaceState({}, '', '/preview/business/finance?project=P1');
  render(<BusinessBoards initial="finance" preview />);
  expect(api.apiRequest).not.toHaveBeenCalled();
  expect(screen.getByText(/预览不读取项目数据/)).toBeTruthy();
});

it('refresh failure removes previously visible project details', async () => {
  window.history.replaceState({}, '', '/centers/business/presales?project=P1');
  render(<BusinessBoards initial="presales" />);
  await screen.findByRole('heading', { name: '当前状态与项目进度' });
  api.apiRequest.mockRejectedValue(new Error('权限已撤销'));
  await userEvent.click(screen.getByRole('button', { name: '刷新项目' }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('实际更新人')).toBeNull();
  expect(screen.queryByText('项目进展已更新')).toBeNull();
});

it('a late project-detail response cannot replace the returned project list', async () => {
  let resolveDetail!: (value: ReturnType<typeof detail>) => void;
  api.apiRequest.mockImplementation((url: string) => url.endsWith('/projects/P1/') ? new Promise(resolve => { resolveDetail = resolve; }) : Promise.resolve(list()));
  render(<BusinessBoards initial="presales" />);
  await userEvent.click(await screen.findByRole('link', { name: /园区项目/ }));
  await userEvent.click(screen.getByRole('link', { name: '← 返回项目列表' }));
  await screen.findByRole('link', { name: /园区项目/ });
  await act(async () => resolveDetail(detail()));
  expect(screen.queryByRole('heading', { name: '平台更新记录' })).toBeNull();
  expect(screen.getByRole('link', { name: /园区项目/ })).toBeTruthy();
});

it('rejects mismatched project data and clears stale department lists', async () => {
  api.apiRequest.mockResolvedValue(list());
  const view = render(<BusinessBoards initial="presales" />);
  await screen.findByRole('link', { name: /园区项目/ });
  view.rerender(<BusinessBoards initial="finance" />);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByRole('link', { name: /园区项目/ })).toBeNull();
});

it('supports browser back navigation between project detail and the list', async () => {
  render(<BusinessBoards initial="presales" />);
  await userEvent.click(await screen.findByRole('link', { name: /园区项目/ }));
  await screen.findByRole('heading', { name: '当前状态与项目进度' });
  act(() => { window.history.replaceState({}, '', '/centers/business/presales'); window.dispatchEvent(new PopStateEvent('popstate')); });
  await waitFor(() => expect(within(screen.getByRole('table')).getByRole('link', { name: /园区项目/ })).toBeTruthy());
});
