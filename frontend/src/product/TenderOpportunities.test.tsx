import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import TenderOpportunities, { REFRESH_POLL_MS } from "./TenderOpportunities";

const mocks = vi.hoisted(() => ({
  listOpportunities: vi.fn(),
  getOpportunity: vi.fn(),
  getOpportunityOptions: vi.fn(),
  getTenderSources: vi.fn(),
  getRefreshOverview: vi.fn(),
  requestRefresh: vi.fn(),
  getRefreshBatch: vi.fn(),
}));

vi.mock("./tender-api", () => mocks);

const source = { code: "ccgp", name: "中国政府采购网", health_state: "ok", health_label: "正常", health_detail: "最近采集成功", last_success_at: "2026-09-28T01:00:00Z" };
const opportunity = {
  id: 7,
  project_name: "榆林市智慧交通建设项目",
  project_code: "YL-2026-07",
  region: "陕西省",
  purchaser: "榆林市采购单位",
  budget: { amount_yuan: "1200000.00", cap_yuan: null, raw: "" },
  publish_at: null,
  publish_date: "2026-09-28",
  publish_precision: "date" as const,
  bid_deadline: "2026-10-10T02:00:00Z",
  status: "ACTIVE",
  status_label: "进行中",
  source,
  original_url: "https://www.ccgp.gov.cn/notice/7",
  current_version: 2,
  first_seen_at: "2026-09-28T01:00:00Z",
};
const list = { items: [opportunity], total: 1, page: 1, page_size: 20, has_more: false };
const noRefresh = { available: true, unavailable_reason: "", batch: null, last_success_at: null };
const queued = { id: "batch-1", stored_state: "QUEUED", display_state: "等待执行", results: {} };

beforeEach(() => {
  window.history.replaceState({}, "", "/centers/product/opportunities");
  Object.values(mocks).forEach(mock => mock.mockReset());
  mocks.listOpportunities.mockResolvedValue(list);
  mocks.getOpportunity.mockResolvedValue(opportunity);
  mocks.getOpportunityOptions.mockResolvedValue({ regions: [{ value: "陕西省", label: "陕西省" }], sources: [], statuses: [{ value: "ACTIVE", label: "进行中" }], needs: [], noticeTypes: [], publishPeriods: [], deadlinePeriods: [], budgetBands: [] });
  mocks.getTenderSources.mockResolvedValue([source]);
  mocks.getRefreshOverview.mockResolvedValue(noRefresh);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("TenderOpportunities", () => {
  it("进入页面只读现存公告，并提供可信原文链接", async () => {
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole("button", { name: "榆林市智慧交通建设项目" }));
    const link = await screen.findByRole("link", { name: /查看原始公告/ });
    expect(link.getAttribute("href")).toBe(opportunity.original_url);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(mocks.requestRefresh).not.toHaveBeenCalled();
  });

  it("提交筛选时请求后端并把条件写入当前深链", async () => {
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "智慧交通" } });
    fireEvent.change(screen.getByLabelText("地区"), { target: { value: "陕西省" } });
    fireEvent.click(screen.getByRole("button", { name: "筛选" }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ q: "智慧交通", region: "陕西省", page: 1 }), expect.any(AbortSignal)));
    expect(window.location.search).toContain("q=%E6%99%BA%E6%85%A7%E4%BA%A4%E9%80%9A");
    expect(window.location.search).toContain("region=%E9%99%95%E8%A5%BF%E7%9C%81");
  });

  it("409 时接续后端现有批次而不重复提交", async () => {
    mocks.requestRefresh.mockRejectedValue(new ApiError(409, "已有刷新批次", "refresh_active"));
    mocks.getRefreshOverview.mockResolvedValueOnce(noRefresh).mockResolvedValueOnce({ ...noRefresh, batch: queued });
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    await waitFor(() => expect(mocks.getRefreshOverview).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "刷新公告" }));
    expect(await screen.findByText("等待执行")).toBeTruthy();
    expect(mocks.requestRefresh).toHaveBeenCalledTimes(1);
  });

  it("刷新到终态后停止轮询并重取列表与来源状态", async () => {
    const finished = { id: "batch-1", stored_state: "PARTIAL", display_state: "PARTIAL", results: { ccgp: { state: "FAILED", error_code: "blocked", new_notices: 2 } } };
    mocks.requestRefresh.mockResolvedValue(queued);
    mocks.getRefreshBatch.mockResolvedValue(finished);
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "刷新公告" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS); });
    expect(screen.getByText("部分更新")).toBeTruthy();
    expect(screen.getByText(/刷新失败 · 新增 2 条 · blocked/)).toBeTruthy();
    expect(mocks.getRefreshBatch).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS * 2); });
    expect(mocks.getRefreshBatch).toHaveBeenCalledTimes(1);
    expect(mocks.listOpportunities.mock.calls.length).toBeGreaterThan(1);
    expect(mocks.getTenderSources.mock.calls.length).toBeGreaterThan(1);
  });

  it("轮询网络失败时保留现存公告并停止无限加载", async () => {
    mocks.requestRefresh.mockResolvedValue(queued);
    mocks.getRefreshBatch.mockRejectedValue(new Error("断线"));
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "刷新公告" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS); });
    expect(screen.getByText("榆林市智慧交通建设项目")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("可能过时");
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS * 2); });
    expect(mocks.getRefreshBatch).toHaveBeenCalledTimes(1);
  });

  it("刷新状态返回撤权时立即清空受保护内容", async () => {
    mocks.requestRefresh.mockResolvedValue(queued);
    mocks.getRefreshBatch.mockRejectedValue(new ApiError(403, "无权限", "forbidden"));
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "刷新公告" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS); });
    expect(screen.getByRole("alert").textContent).toContain("访问权限已变化");
    expect(screen.queryByText("榆林市智慧交通建设项目")).toBeNull();
  });
});
