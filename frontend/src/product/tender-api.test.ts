import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../api", () => ({ apiRequest: api.apiRequest }));

import { getOpportunityOptions, requestRefresh } from "./tender-api";

beforeEach(() => api.apiRequest.mockReset());

it("通过平台 apiRequest 提交无参数刷新", async () => {
  api.apiRequest.mockResolvedValue({ batch_id: "batch-1", stored_state: "QUEUED", display_state: "等待执行", results: {} });
  await expect(requestRefresh()).resolves.toMatchObject({ id: "batch-1", stored_state: "QUEUED" });
  expect(api.apiRequest).toHaveBeenCalledWith("/api/product/refresh/", expect.objectContaining({ method: "POST", body: "{}" }));
});

it("保留后端返回的完整筛选选项", async () => {
  api.apiRequest.mockResolvedValue({ regions: ["陕西省"], sources: [{ code: "ccgp", name: "中国政府采购网" }], statuses: [{ value: "ACTIVE", label: "进行中" }] });
  await expect(getOpportunityOptions()).resolves.toEqual({
    regions: [{ value: "陕西省", label: "陕西省" }],
    sources: [{ value: "ccgp", label: "中国政府采购网" }],
    statuses: [{ value: "ACTIVE", label: "进行中" }], needs: [], noticeTypes: [], publishPeriods: [], deadlinePeriods: [], budgetBands: [],
  });
});
