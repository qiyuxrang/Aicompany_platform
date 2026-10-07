import { beforeEach, expect, it, vi } from 'vitest';
import { apiRequest } from './api';
import { agentReferencePath, changeAgentRequirement, commandAgentWork, createAgentProject, getAgentEvents, getAgentWork, listAgentConversations, listAgentMessages, nextAgentPage, mergeAgentEvents, removeAgentInstallation, saveAgentInstallation, sendAgentMessage, uploadAgentAttachment } from './agent-api';

vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), apiRequest: vi.fn() }));
const request = vi.mocked(apiRequest);
beforeEach(() => request.mockReset());

it('sends only a project name and normalizes server page numbers', async () => {
  request.mockResolvedValueOnce({ id: 'project', name: '新项目' }).mockResolvedValueOnce({ items: [], total: 43 });
  await createAgentProject('新项目');
  expect(request).toHaveBeenNthCalledWith(1, '/api/agent/projects/', expect.objectContaining({ body: JSON.stringify({ name: '新项目' }) }));
  expect(nextAgentPage(await listAgentConversations(2))).toBe(3);
  expect(request).toHaveBeenNthCalledWith(2, '/api/agent/conversations/?page=2&page_size=20', expect.anything());
});

it('retains the server message identity and serializes correction fields exactly', async () => {
  const attachment = { type: 'agent_attachment' as const, id: 'file1', sha256: 'a'.repeat(64) };
  request.mockResolvedValueOnce({ id: 'm1', content: '补充', role: 'user', attachment_references: [attachment], created_at: '2026-09-30', work_id: 'w1' });
  const message = await sendAgentMessage('c1', '补充', [attachment], 'stable-key', 'w1');
  expect(message.content).toBe('补充');
  expect(request).toHaveBeenLastCalledWith('/api/agent/conversations/c1/messages/', expect.objectContaining({ body: JSON.stringify({ text: '补充', attachment_references: [attachment], client_request_id: 'stable-key', work_id: 'w1' }) }));
  await changeAgentRequirement('w1', 3, message.id, '更正');
  expect(request).toHaveBeenLastCalledWith('/api/agent/work/w1/requirements/', expect.objectContaining({ body: JSON.stringify({ expected_version: 3, message_id: 'm1', content: '更正' }) }));
});

it('uploads only the file and requires a server verified attachment digest', async () => {
  const file = new File(['data'], '资料.txt', { type: 'text/plain' });
  request.mockResolvedValueOnce({ id: 'a1', type: 'agent_attachment', name: file.name, sha256: 'a'.repeat(64) });
  expect((await uploadAgentAttachment(file)).id).toBe('a1');
  const body = request.mock.calls[0][1]?.body as FormData;
  expect([...body.keys()]).toEqual(['file']);
  request.mockResolvedValueOnce({ id: 'a2', name: file.name });
  await expect(uploadAgentAttachment(file)).rejects.toThrow('附件返回的授权引用不完整');
});

it('normalizes received requirements without inventing an applied timestamp', async () => {
  request.mockResolvedValue({ id: 'w1', state: 'running', result_references: [], requirements: [{ version: 2, content: '新要求', received_at: '2026-09-30', applied_at: null }] });
  const work = await getAgentWork('w1');
  expect(work.requirements?.[0]).toMatchObject({ content: '新要求', applied_at: null });
  expect(work.state).toBe('running');
});

it('reads authoritative work after cancel acknowledgement rather than fabricating cancelled', async () => {
  request.mockResolvedValueOnce({ state: 'stopping', event_seq: 2 }).mockResolvedValueOnce({ id: 'w1', state: 'stopping', result_references: [] });
  expect((await commandAgentWork('w1', 'cancel', 4, 'key')).state).toBe('stopping');
  expect(request).toHaveBeenLastCalledWith('/api/agent/work/w1/', expect.anything());
});

it('deduplicates and orders root-scoped event sequences, including gaps from other work', async () => {
  const event = (seq: number) => ({ seq, event_key: String(seq), type: 'status', payload: { summary: '公开状态' }, created_at: '' });
  expect(mergeAgentEvents([event(2)], [event(7), event(2), event(4)]).map(item => item.seq)).toEqual([2, 4, 7]);
  expect(() => mergeAgentEvents([event(4)], [event(2)])).toThrow();
  expect(() => mergeAgentEvents([event(2)], [{ ...event(4), event_key: '2' }])).toThrow();
  request.mockResolvedValue({ items: [{ id: 'e7', seq: 7, type: 'status', payload: {}, created_at: '' }], total: 2 });
  expect(await getAgentEvents('work', 4)).toMatchObject({ has_more: true, items: [{ event_key: 'e7' }] });
  expect(request).toHaveBeenLastCalledWith('/api/agent/work/work/events/?after_seq=4&page_size=50', expect.anything());
});

it('uses reviewed catalog identity and the existing personal uninstall route', async () => {
  await saveAgentInstallation('skill-1', true);
  expect(request).toHaveBeenLastCalledWith('/api/agent/installations/', expect.objectContaining({ body: JSON.stringify({ skill_id: 'skill-1', enabled: true }) }));
  await removeAgentInstallation('skill-1');
  expect(request).toHaveBeenLastCalledWith('/api/agent/installations/skill-1/', { method: 'DELETE' });
});

it('maps typed immutable artifact IDs and never follows model supplied URLs', () => {
  const finance = { domain_type: 'business_revision', object_id: '00000000-0000-4000-8000-000000000002', revision: 2, public_summary: '本人精确版本已发布' };
  expect(agentReferencePath(finance, 'view')).toBe('/api/business/ledgers/finance/versions/00000000-0000-4000-8000-000000000002/');
  expect(agentReferencePath(finance, 'download')).toBeNull();
  expect(agentReferencePath({ ...finance, object_id: 'https://untrusted.test' }, 'view')).toBeNull();
  expect(agentReferencePath({ domain_type: 'product_artifact', object_id: 'artifact1', revision: 2, public_summary: '方案' }, 'download')).toBe('/api/product/artifacts/artifact1/download/');
  expect(agentReferencePath({ domain_type: 'https://untrusted.test', object_id: 'x', revision: 1, public_summary: '伪造链接' }, 'view')).toBeNull();
  expect(agentReferencePath({ domain_type: 'product_artifact', object_id: '', revision: 1, public_summary: '缺少标识' }, 'view')).toBeNull();
});

it('loads conversation history as public content rather than raw runtime messages', async () => {
  request.mockResolvedValue({ items: [{ id: 'm1', role: 'assistant', content: '结果摘要', attachment_references: [], created_at: '2026-09-30', private_reasoning: 'never render' }], total: 1 });
  expect((await listAgentMessages('c1')).items[0]).toMatchObject({ id: 'm1', role: 'assistant', content: '结果摘要', attachment_references: [], created_at: '2026-09-30' });
});

it('uses only typed registered references for GM, not employee APIs or supplied URLs', () => {
  const reference = { domain_type: 'document_artifact', object_id: 'artifact1', reference_id: '00000000-0000-4000-8000-000000000001', revision: 2, public_summary: '方案' };
  expect(agentReferencePath(reference, 'download', true)).toBe('/api/agent/management/references/00000000-0000-4000-8000-000000000001/download/');
  expect(agentReferencePath({ ...reference, domain_type: 'finance_record' }, 'view', true)).toBeNull();
  expect(agentReferencePath({ ...reference, reference_id: 'https://untrusted.test' }, 'view', true)).toBeNull();
  expect(agentReferencePath({ ...reference, domain_type: 'document_task' }, 'download', true)).toBeNull();
  expect(agentReferencePath({ ...reference, reference_id: undefined }, 'view', true)).toBeNull();
});

it('keeps employee access on the business API with exact registered source digests', () => {
  expect(agentReferencePath({ domain_type: 'document_source', object_id: 'source1', revision: 'a'.repeat(64), public_summary: '原件' }, 'view')).toBe('/api/product/sources/source1/preview/?page=1');
  expect(agentReferencePath({ domain_type: 'document_source', object_id: 'source1', revision: 'not-a-digest', public_summary: '原件' }, 'view')).toBeNull();
});

it('archives corrections without dispatch before the exact requirement version is persisted', async () => {
  request.mockResolvedValue({ id: 'm2', content: '更正' });
  await sendAgentMessage('c1', '更正', [], 'correction-key', 'w1', true);
  expect(request).toHaveBeenLastCalledWith('/api/agent/conversations/c1/messages/', expect.objectContaining({ body: JSON.stringify({ text: '更正', attachment_references: [], client_request_id: 'correction-key', work_id: 'w1', defer_dispatch: true }) }));
});
