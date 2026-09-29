import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../api", () => ({ apiRequest: api.apiRequest }));

import { getOpportunityOptions, getRefreshOverview, getTenderSources, listOpportunities, requestRefresh, updateOpportunityState } from "./tender-api";

beforeEach(() => api.apiRequest.mockReset());

it("序列化个人标记及参与状态筛选并通过平台接口保存部分标记", async () => {
  await listOpportunities({ user_state: "favorite", participation: "open" });
  expect(api.apiRequest).toHaveBeenCalledWith("/api/product/opportunities/?user_state=favorite&participation=open", expect.any(Object));
  api.apiRequest.mockResolvedValue({ user_state: { is_read: true, is_favorite: true, is_irrelevant: false } });
  await expect(updateOpportunityState(7, { is_favorite: true })).resolves.toEqual({ is_read: true, is_favorite: true, is_irrelevant: false });
  expect(api.apiRequest).toHaveBeenLastCalledWith("/api/product/opportunities/7/state/", expect.objectContaining({ method: "POST", body: '{"is_favorite":true}' }));
});

it("通过平台 apiRequest 提交无参数刷新", async () => {
  api.apiRequest.mockResolvedValue({ batch_id: "batch-1", stored_state: "QUEUED", display_state: "等待执行", results: {} });
  await expect(requestRefresh()).resolves.toMatchObject({ id: "batch-1", stored_state: "QUEUED" });
  expect(api.apiRequest).toHaveBeenCalledWith("/api/product/refresh/", expect.objectContaining({ method: "POST", body: "{}" }));
});

it("解析全国看板的三个筛选选项", async () => {
  api.apiRequest.mockResolvedValue({ regions: ["陕西省"], industries: [{ value: "coal", label: "煤炭" }], notice_categories: [{ value: "procurement", label: "招标采购" }] });
  await expect(getOpportunityOptions()).resolves.toEqual({
    regions: [{ value: "陕西省", label: "陕西省" }],
    industries: [{ value: "coal", label: "煤炭" }], noticeCategories: [{ value: "procurement", label: "招标采购" }],
  });
});

it("保留来源最近运行的后端 stats 统计", async () => {
  api.apiRequest.mockResolvedValue({ items: [{ code: "ccgp", name: "中国政府采购网", health_state: "degraded",
    last_success_at: null, latest_run: { state: "PARTIAL", stats: { new_notices: 3, new_versions: 1, complete: false } } }] });
  await expect(getTenderSources()).resolves.toMatchObject([{ latest_run: {
    state: "PARTIAL", stats: { new_notices: 3, new_versions: 1, complete: false },
  } }]);
});

it("仅发送全国看板的新筛选字段及分页，忽略旧隐藏条件", async () => {
  const filters = { industry: "coal,medical", notice_category: "procurement", region: "陕西省", page: 2, page_size: 20, q: "legacy", source: "legacy", purchaser: "legacy" };
  await listOpportunities(filters);
  const url = new URL(api.apiRequest.mock.calls[0][0], "https://portal.test");
  expect(Object.fromEntries(url.searchParams)).toEqual({ industry: "coal,medical", notice_category: "procurement", region: "陕西省", page: "2", page_size: "20" });
});

it("读取自动更新安排、最近时间和已完成的自动批次", async () => {
  api.apiRequest.mockResolvedValue({ available: true, consumer_online: true,
    schedule: { enabled: true, interval_minutes: 60, lookback_days: 7, next_run_at: "2026-09-28T02:00:00Z" },
    last_attempt_at: "2026-09-28T01:00:00Z", last_success_at: "2026-09-28T01:05:00Z",
    batch: { batch_id: "scheduled-1", stored_state: "SUCCESS", display_state: "刷新完成", trigger: "scheduled", results: {} },
  });
  await expect(getRefreshOverview()).resolves.toMatchObject({ available: true, consumer_online: true,
    schedule: { enabled: true, interval_minutes: 60, lookback_days: 7, next_run_at: "2026-09-28T02:00:00Z" },
    last_attempt_at: "2026-09-28T01:00:00Z", last_success_at: "2026-09-28T01:05:00Z",
    batch: { id: "scheduled-1", stored_state: "SUCCESS", trigger: "scheduled" },
  });
});

it("更新安排缺失或格式无效时保持未知，不伪造自动运行", async () => {
  api.apiRequest.mockResolvedValue({ available: false, schedule: { enabled: true, interval_minutes: 0, lookback_days: 7 } });
  await expect(getRefreshOverview()).resolves.toMatchObject({ available: false, schedule: null, consumer_online: null,
    last_attempt_at: null, last_success_at: null, batch: null });
});
