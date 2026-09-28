import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import BusinessLedgerWorkspace from './BusinessLedgerWorkspace';

const ledgerApi = vi.hoisted(() => ({
  listLedgerPermissions: vi.fn(), getLedger: vi.fn(), listLedgerVersions: vi.fn(),
  addLedgerRecord: vi.fn(), updateLedgerRecord: vi.fn(), deleteLedgerRecord: vi.fn(), importLedger: vi.fn(),
  transitionLedger: vi.fn(), returnLedger: vi.fn(), updateLedgerMetadata: vi.fn(),
}));
vi.mock('./business-ledger-api', async original => ({ ...await original<typeof import('./business-ledger-api')>(), ...ledgerApi }));

const fixture = (state: 'draft' | 'submitted' | 'published' = 'draft', revision = 7) => ({
  department: 'engineering' as const, title: '工程部看板', state, revision, as_of: '2026-09-27', source_name: '工程台账',
  records: [{ project_id: 'P-1', project_name: '一期项目', status: '实施中' }],
  fields: { project_id: '项目编号', project_name: '项目名称', status: '状态' }, statuses: ['实施中', '已验收'],
  permissions: { can_edit: true, can_submit: true, can_publish: true }, updated_at: '2026-09-27T10:00:00Z', last_return_reason: '',
});

beforeEach(() => {
  Object.values(ledgerApi).forEach(mock => mock.mockReset());
  ledgerApi.listLedgerPermissions.mockResolvedValue({ departments: [{ department: 'engineering', title: '工程部台账', can_edit: true, can_submit: true, can_publish: true }] });
  ledgerApi.getLedger.mockResolvedValue(fixture());
  ledgerApi.listLedgerVersions.mockResolvedValue([]);
});
afterEach(cleanup);

it('loads only granted ledgers and adds a record against the displayed revision', async () => {
  ledgerApi.addLedgerRecord.mockResolvedValue(fixture('draft', 8));
  render(<BusinessLedgerWorkspace />);
  expect(await screen.findByRole('tabpanel', { name: '工程部看板' })).toBeTruthy();
  await userEvent.clear(screen.getByLabelText('项目编号'));
  await userEvent.type(screen.getByLabelText('项目编号'), 'P-2');
  await userEvent.clear(screen.getByLabelText('项目名称'));
  await userEvent.type(screen.getByLabelText('项目名称'), '二期项目');
  await userEvent.selectOptions(screen.getByLabelText('状态'), '已验收');
  await userEvent.click(screen.getByRole('button', { name: '新增记录' }));
  await waitFor(() => expect(ledgerApi.addLedgerRecord).toHaveBeenCalledWith(expect.objectContaining({ revision: 7 }), expect.objectContaining({ project_id: 'P-2', project_name: '二期项目', status: '已验收' })));
  expect(await screen.findByText('记录已加入草稿。')).toBeTruthy();
});

it('publishes submitted data and exposes an explicit return reason', async () => {
  ledgerApi.getLedger.mockResolvedValue(fixture('submitted'));
  ledgerApi.transitionLedger.mockResolvedValue(fixture('published', 8));
  render(<BusinessLedgerWorkspace />);
  await userEvent.click(await screen.findByRole('button', { name: '发布到看板' }));
  expect(ledgerApi.transitionLedger).toHaveBeenCalledWith(expect.objectContaining({ revision: 7 }), 'publish');
  expect(await screen.findByText(/总经理看板已使用本版本/)).toBeTruthy();
});

it('on a revision conflict reloads the latest ledger before another edit', async () => {
  ledgerApi.updateLedgerMetadata.mockRejectedValue(new ApiError(409, '冲突'));
  ledgerApi.getLedger.mockResolvedValueOnce(fixture()).mockResolvedValueOnce(fixture('draft', 9));
  render(<BusinessLedgerWorkspace />);
  await userEvent.click(await screen.findByRole('button', { name: '保存台账信息' }));
  expect((await screen.findByRole('alert')).textContent).toContain('数据已被其他人员更新');
  await waitFor(() => expect(ledgerApi.getLedger).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('版本 9')).toBeTruthy();
});

it('preview and accounts without grants cannot mutate business data', async () => {
  const preview = render(<BusinessLedgerWorkspace preview />);
  expect(screen.getByText(/预览不会读取/)).toBeTruthy();
  expect(ledgerApi.listLedgerPermissions).not.toHaveBeenCalled();
  preview.unmount();
  ledgerApi.listLedgerPermissions.mockResolvedValue({ departments: [] });
  render(<BusinessLedgerWorkspace />);
  expect(await screen.findByRole('heading', { name: '没有台账录入权限' })).toBeTruthy();
  expect(ledgerApi.getLedger).not.toHaveBeenCalled();
});

it('product workspace limits record entry to presales even when other ledgers are granted', async () => {
  ledgerApi.listLedgerPermissions.mockResolvedValue({ departments: [
    { department: 'engineering', title: '工程部看板', can_edit: true },
    { department: 'presales', title: '售前部门看板', can_edit: true },
  ] });
  ledgerApi.getLedger.mockResolvedValue({ ...fixture(), department: 'presales', title: '售前部门看板',
    fields: { project_id: '项目编号', project_name: '项目名称', status: '状态', owner: '负责人', amount: '预计金额',
      follow_up_date: '跟进时间', project_progress: '项目进展', description: '项目概况' },
    records: [], statuses: ['方案编制'] });
  render(<BusinessLedgerWorkspace onlyDepartment="presales" />);
  expect(await screen.findByRole('heading', { name: '售前项目跟进' })).toBeTruthy();
  expect(screen.queryByRole('tab', { name: '工程部看板' })).toBeNull();
  await waitFor(() => expect(ledgerApi.getLedger).toHaveBeenCalledWith('presales', expect.anything()));
  expect(await screen.findByLabelText('跟进时间')).toBeTruthy();
  expect(screen.getByLabelText('项目概况').tagName).toBe('TEXTAREA');
});
