import { expect, it, vi } from 'vitest';
import { Batch, screeningCounts, selectResumeFiles, uploadSequential } from './recruitment-api';
const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('../api', () => api);
const batch = { id: 'b', version: 2, completed: 4, total: 30, failed: 7 } as Batch;
it('只由持久化状态推导计数，不用总数相减制造待筛选', () => {
  expect(screeningCounts(batch)).toEqual({ screened: 4, pending: null, prescreened: null });
  expect(screeningCounts({ ...batch, status_counts: { pending: 8, queued: 2, running: 1, failed: 7 } })).toEqual({ screened: 4, pending: 3, prescreened: 8 });
  expect(screeningCounts({ ...batch, artifacts: [{ processing_status: 'pending' }, { processing_status: 'queued' }] as Batch['artifacts'] })).toEqual({ screened: 4, pending: 1, prescreened: 1 });
});
it('选择文件时排除无效格式、空文件、超限文件和重复选择', () => {
  const file = new File(['valid'], 'ok.txt', { lastModified: 1 });
  const result = selectResumeFiles([file], [file, new File(['x'], 'x.exe'), new File([], 'empty.pdf'), new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'large.pdf')]);
  expect(result.files).toEqual([file]); expect(result.duplicates).toBe(1); expect(result.rejected).toHaveLength(3);
});
it('每组不超过20份且失败立即停止，不启动后续分组', async () => {
  api.apiRequest.mockReset();
  api.apiRequest.mockResolvedValueOnce({ batch: { ...batch, version: 3 } }).mockRejectedValueOnce(new Error('已达200份上限'));
  const files = Array.from({ length: 45 }, (_, i) => new File(['x'], `${i}.txt`));
  const progress = vi.fn();
  await expect(uploadSequential(batch, files, progress)).rejects.toThrow('200');
  expect(api.apiRequest).toHaveBeenCalledTimes(2);
  expect((api.apiRequest.mock.calls[0][1].body as FormData).getAll('files')).toHaveLength(20);
  expect((api.apiRequest.mock.calls[1][1].body as FormData).get('expected_version')).toBe('3');
  expect(progress).toHaveBeenCalledTimes(1);
});

it('相同文件元数据不能证明内容重复，保留不同字节且不截断超过200份的选择', () => {
  const first = new File(['a'], 'same.txt', { lastModified: 1 });
  const second = new File(['b'], 'same.txt', { lastModified: 1 });
  expect(selectResumeFiles([first], [second]).files).toEqual([first, second]);
  expect(selectResumeFiles([], Array.from({ length: 201 }, () => new File(['a'], 'same.txt', { lastModified: 1 }))).files).toHaveLength(201);
});
