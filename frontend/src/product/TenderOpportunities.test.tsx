import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import TenderOpportunities, { OVERVIEW_POLL_MS, REFRESH_POLL_MS } from "./TenderOpportunities";

const mocks = vi.hoisted(() => ({
  listOpportunities: vi.fn(),
  getOpportunity: vi.fn(),
  updateOpportunityState: vi.fn(),
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
  industry_code: "transport", industry_label: "交通运输", digital_tags: ["智能化", "智慧交通"],
  classification_status: "matched", notice_category: "procurement",
};
const list = { items: [opportunity], total: 1, page: 1, page_size: 20, has_more: false,
  stats: { total: 1, today_new: 1, closing_soon: 0 }, last_updated_at: "2026-09-28T01:00:00Z" };
const noRefresh = { available: true, unavailable_reason: "", batch: null, last_success_at: null, last_attempt_at: null,
  consumer_online: false, schedule: { enabled: false, interval_minutes: 60, lookback_days: 7, next_run_at: null } };
const queued = { id: "batch-1", stored_state: "QUEUED", display_state: "等待执行", results: {} };

beforeEach(() => {
  window.history.replaceState({}, "", "/centers/product/opportunities");
  Object.values(mocks).forEach(mock => mock.mockReset());
  mocks.listOpportunities.mockResolvedValue(list);
  mocks.getOpportunity.mockResolvedValue(opportunity);
  mocks.updateOpportunityState.mockResolvedValue({ is_read: true, is_favorite: false, is_irrelevant: false });
  mocks.getOpportunityOptions.mockResolvedValue({ regions: [{ value: "陕西省", label: "陕西省" }], industries: [], noticeCategories: [] });
  mocks.getTenderSources.mockResolvedValue([source]);
  mocks.getRefreshOverview.mockResolvedValue(noRefresh);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("TenderOpportunities", () => {
  it("跨部门popstate发生在卸载前不会把商机默认参数写入新页面", async () => {
    render(<TenderOpportunities/>);
    await screen.findByRole('button', { name: opportunity.project_name });
    window.history.pushState({}, '', '/centers/hr?task=existing-task');
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));
    expect(window.location.pathname).toBe('/centers/hr');
    expect(window.location.search).toBe('?task=existing-task');
    expect(window.location.search).not.toContain('notice_category');
  });

  it("详情待核实字段显示中文，公告列表按实际时间精度与主列表保持一致", async () => {
    const timestamp = '2026-09-28T10:24:00Z';
    const precise = { ...opportunity, publish_at: timestamp, publish_precision: 'second' as const };
    mocks.listOpportunities.mockResolvedValue({ ...list, items: [precise] });
    mocks.getOpportunity.mockResolvedValue({ ...precise,
      unknown_fields: ['bid_deadline', 'signup_time', 'budget_cap', 'contact_phone', 'custom_internal_field'],
      notices: [{ id: 1, title: '精确时间公告', notice_type: '采购', source, original_url: opportunity.original_url,
        publish_date: '2026-09-28', publish_at: timestamp, publish_precision: 'second' },
      { id: 2, title: '仅日期公告', notice_type: '更正', source, original_url: opportunity.original_url,
        publish_date: '2026-09-28', publish_at: timestamp, publish_precision: 'date' }] });
    render(<TenderOpportunities/>);
    const title = await screen.findByRole('button', { name: opportunity.project_name });
    const cells = within(title.closest('tr')!).getAllByRole('cell');
    const listPublished = cells[4].textContent;
    fireEvent.click(title);
    await screen.findByRole('heading', { name: opportunity.project_name });
    const note = screen.getByText(/公告未明确：/);
    expect(note.textContent).toContain('投标截止、报名时间、预算上限、联系电话、其他信息');
    expect(note.textContent).not.toContain('bid_deadline');
    expect(note.textContent).not.toContain('custom_internal_field');
    expect(screen.getByText('精确时间公告').closest('li')?.querySelector('small')?.textContent).toContain(listPublished);
    expect(screen.getByText('精确时间公告').closest('li')?.querySelector('small')?.textContent).toContain(':24');
    expect(screen.getByText('仅日期公告').closest('li')?.querySelector('small')?.textContent).toBe('中国政府采购网 · 更正 · 2026-09-28');
  });

  it("标记和参与状态筛选保留到地址并随恢复默认清除", async () => {
    window.history.replaceState({}, "", "/centers/product/opportunities?industry=coal&user_state=unread&participation=unknown");
    render(<TenderOpportunities/>);
    await screen.findByText(opportunity.project_name);
    expect(mocks.listOpportunities).toHaveBeenCalledWith(expect.objectContaining({ industry: 'coal', user_state: 'unread', participation: 'unknown' }), expect.any(AbortSignal));
    fireEvent.change(screen.getByLabelText('我的标记'), { target: { value: 'favorite' } });
    fireEvent.change(screen.getByLabelText('参与状态'), { target: { value: 'expired' } });
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ user_state: 'favorite', participation: 'expired' }), expect.any(AbortSignal)));
    expect(new URLSearchParams(window.location.search).get('user_state')).toBe('favorite');
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ notice_category: 'procurement', page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(window.location.search).not.toContain('user_state');
    expect(window.location.search).not.toContain('participation');
  });

  it("呈现核心层级、本轮新增、全部公告数量及真实截止状态", async () => {
    mocks.listOpportunities.mockResolvedValue({ ...list, stats: { ...list.stats, latest_batch_new: 3 }, items: [{ ...opportunity,
      relevance_tier: 'core', relevance_label: '核心商机', participation_status: 'unknown', participation_label: '截止待核实',
      latest_batch_new: true, notice_count: 4, user_state: { is_read: false, is_favorite: false, is_irrelevant: false } }] });
    render(<TenderOpportunities/>);
    await screen.findByText('核心商机');
    expect(screen.getByText('4 份公告')).toBeTruthy();
    expect(screen.getByRole('cell', { name: '截止待核实' })).toBeTruthy();
    expect(within(screen.getByRole('region', { name: '当前筛选统计' })).getByText('3')).toBeTruthy();
    expect(screen.getAllByText('本轮新增')).toHaveLength(2);
  });

  it("关注保存期间禁止重复提交，失败保留状态并可重试", async () => {
    let fail: (error: Error) => void = () => {};
    mocks.updateOpportunityState.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
    render(<TenderOpportunities/>);
    const button = await screen.findByRole('button', { name: '重点关注' });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1));
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await act(async () => fail(new Error('保存失败，请重试')));
    expect((await screen.findByRole('alert')).textContent).toContain('保存失败');
    mocks.updateOpportunityState.mockResolvedValue({ is_read: false, is_favorite: true, is_irrelevant: false });
    mocks.listOpportunities.mockResolvedValue({ ...list, items: [{ ...opportunity, user_state: { is_read: false, is_favorite: true, is_irrelevant: false } }] });
    fireEvent.click(screen.getByRole('button', { name: '重点关注' }));
    expect((await screen.findByRole('button', { name: '取消关注' })).getAttribute('aria-pressed')).toBe('true');
  });

  it("不相关从默认列表隐藏且可在不相关筛选中恢复", async () => {
    let irrelevant = false;
    mocks.listOpportunities.mockImplementation(({ user_state }: { user_state?: string }) => Promise.resolve({ ...list,
      items: irrelevant === (user_state === 'irrelevant') ? [{ ...opportunity, user_state: { is_read: false, is_favorite: false, is_irrelevant: irrelevant } }] : [],
      total: irrelevant === (user_state === 'irrelevant') ? 1 : 0 }));
    mocks.updateOpportunityState.mockImplementation((_id: number, patch: { is_irrelevant: boolean }) => {
      irrelevant = patch.is_irrelevant;
      return Promise.resolve({ is_read: false, is_favorite: false, is_irrelevant: irrelevant });
    });
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole('button', { name: '不相关' }));
    await screen.findByText('暂无符合条件的商机');
    fireEvent.change(screen.getByLabelText('我的标记'), { target: { value: 'irrelevant' } });
    fireEvent.click(await screen.findByRole('button', { name: '恢复相关' }));
    await screen.findByText('暂无符合条件的商机');
    expect(mocks.updateOpportunityState).toHaveBeenLastCalledWith(7, { is_irrelevant: false });
    fireEvent.change(screen.getByLabelText('我的标记'), { target: { value: '' } });
    expect(await screen.findByRole('button', { name: opportunity.project_name })).toBeTruthy();
  });

  it("保存个人标记时授权失效会立即隐藏商机内容", async () => {
    mocks.updateOpportunityState.mockRejectedValueOnce(new ApiError(403, 'denied'));
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole('button', { name: '重点关注' }));
    expect(await screen.findByText('商机访问权限已变化')).toBeTruthy();
    expect(screen.queryByRole('button', { name: opportunity.project_name })).toBeNull();
  });

  it("关注操作不打开详情，随后打开详情的已看写入按同一项目顺序执行", async () => {
    let finish: (value: { is_read: boolean; is_favorite: boolean; is_irrelevant: boolean }) => void = () => {};
    mocks.updateOpportunityState.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    mocks.updateOpportunityState.mockResolvedValue({ is_read: true, is_favorite: true, is_irrelevant: false });
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole('button', { name: '重点关注' }));
    await waitFor(() => expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1));
    expect(mocks.getOpportunity).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: opportunity.project_name }));
    await screen.findByRole('heading', { name: opportunity.project_name });
    expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1);
    await act(async () => finish({ is_read: false, is_favorite: true, is_irrelevant: false }));
    await waitFor(() => expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(2));
    expect(mocks.updateOpportunityState).toHaveBeenLastCalledWith(7, { is_read: true });
    expect(await screen.findByRole('button', { name: '取消关注' })).toBeTruthy();
    expect(screen.getByText('已看')).toBeTruthy();
  });

  it("快速返回再打开同一详情会复用尚未完成的已看请求", async () => {
    let finish: (value: { is_read: boolean; is_favorite: boolean; is_irrelevant: boolean }) => void = () => {};
    mocks.updateOpportunityState.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole('button', { name: opportunity.project_name }));
    await waitFor(() => expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '← 返回商机列表' }));
    fireEvent.click(await screen.findByRole('button', { name: opportunity.project_name }));
    await waitFor(() => expect(mocks.getOpportunity).toHaveBeenCalledTimes(2));
    expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1);
    await act(async () => finish({ is_read: true, is_favorite: false, is_irrelevant: false }));
    expect(await screen.findByText('已看')).toBeTruthy();
    expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1);
  });

  it("关闭详情后延迟完成的自动已看仍同步并重取列表", async () => {
    let finish: (value: { is_read: boolean; is_favorite: boolean; is_irrelevant: boolean }) => void = () => {};
    let isRead = false;
    mocks.listOpportunities.mockImplementation(() => Promise.resolve({ ...list, items: [{ ...opportunity,
      user_state: { is_read: isRead, is_favorite: false, is_irrelevant: false } }] }));
    mocks.getOpportunity.mockResolvedValue({ ...opportunity, user_state: { is_read: false, is_favorite: false, is_irrelevant: false } });
    mocks.updateOpportunityState.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole("button", { name: opportunity.project_name }));
    await waitFor(() => expect(mocks.updateOpportunityState).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "← 返回商机列表" }));
    await screen.findByRole("button", { name: opportunity.project_name });
    isRead = true;
    await act(async () => finish({ is_read: true, is_favorite: false, is_irrelevant: false }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenCalledTimes(2));
    expect(within(screen.getByRole("button", { name: opportunity.project_name }).closest("tr")!).getByText("已看")).toBeTruthy();
  });

  it("仅在详情展示可收起采购范围、报名时间和待核实原文而不暴露技术规则", async () => {
    mocks.getOpportunity.mockResolvedValue({ ...opportunity, procurement_scope: '建设矿区设备远程监测系统，包含传感器及平台。',
      signup_time_text: '2026年9月29日至10月9日', extraction_warnings: ['截止时间存在冲突，请核实更正公告'],
      field_evidence: { bid_deadline: { status: 'unverified', raw: '响应截止时间：2026年10月10日10时', rule: 'internal_rule_v3',
        source_url: 'https://www.ccgp.gov.cn/notice/7', conflict: true } },
      attachments: [{ name: '未核实正文.pdf', url: 'https://www.ccgp.gov.cn/a.pdf', text_status: 'unverified' }] });
    render(<TenderOpportunities/>);
    expect(screen.queryByText('采购范围与报名信息')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: opportunity.project_name }));
    const scope = await screen.findByText('采购范围与报名信息');
    expect(screen.getByText('建设矿区设备远程监测系统，包含传感器及平台。')).toBeTruthy();
    expect(screen.getByText('2026年9月29日至10月9日')).toBeTruthy();
    expect(scope.closest('details')?.open).toBe(true);
    fireEvent.click(scope);
    expect(scope.closest('details')?.open).toBe(false);
    fireEvent.click(screen.getByText('查看字段原文与待核实说明'));
    expect(screen.getByText('投标截止 · 待核实')).toBeTruthy();
    expect(screen.getByText('响应截止时间：2026年10月10日10时')).toBeTruthy();
    expect(screen.getByRole('link', { name: '核对原始公告 ↗' }).getAttribute('href')).toBe('https://www.ccgp.gov.cn/notice/7');
    expect(screen.getByText('附件正文待核实')).toBeTruthy();
    expect(screen.queryByText('internal_rule_v3')).toBeNull();
  });

  it("详情自动保存已看，失败不阻断公告及附件并支持重试", async () => {
    mocks.getOpportunity.mockResolvedValue({ ...opportunity, user_state: { is_read: false, is_favorite: false, is_irrelevant: false },
      notices: [1, 2].map(id => ({ id, title: `项目公告${id}`, notice_type: id === 1 ? '采购' : '更正', source, original_url: `https://www.ccgp.gov.cn/${id}`, publish_date: '2026-09-28', publish_precision: 'date' })),
      attachments: [{ url: 'https://www.ccgp.gov.cn/test.pdf', name: '采购需求.pdf', text_status: 'available' }, { url: 'javascript:alert(1)', name: '不可用附件', text_status: 'unknown' }] });
    mocks.updateOpportunityState.mockRejectedValueOnce(new Error('read save unavailable'));
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole('button', { name: opportunity.project_name }));
    await screen.findByRole('heading', { name: '项目全部公告' });
    await screen.findByText('已看标记保存失败，可点击“标为已看”重试。');
    expect(mocks.updateOpportunityState).toHaveBeenCalledWith(7, { is_read: true });
    expect(screen.getByText('项目公告2')).toBeTruthy();
    expect(screen.getByRole('link', { name: '查看官方附件 ↗' }).getAttribute('href')).toBe('https://www.ccgp.gov.cn/test.pdf');
    expect(screen.getByText('附件可打开，正文未提取')).toBeTruthy();
    expect(screen.queryByText('正文已提取')).toBeNull();
    expect(screen.getByText('附件链接不可用')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '标为已看' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '标为已看' })).toBeNull());
    expect(screen.getByText('已看')).toBeTruthy();
  });

  it("长采购单位采用两行样式并保留完整名称及完整项目标题", async () => {
    const purchaser = '榆林市某智能化建设项目采购管理单位'.repeat(8);
    mocks.listOpportunities.mockResolvedValue({ ...list, items: [{ ...opportunity, purchaser }] });
    render(<TenderOpportunities/>);
    const name = await screen.findByText(purchaser);
    expect(name.className).toBe('tender-purchaser');
    expect(name.getAttribute('title')).toBe(purchaser);
    const title = screen.getByRole('button', { name: opportunity.project_name });
    expect(title.textContent).toBe(opportunity.project_name);
    expect(title.className).toBe('tender-title');
  });

  it("进入页面只读现存公告，并提供可信原文链接", async () => {
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole("button", { name: "榆林市智慧交通建设项目" }));
    const link = await screen.findByRole("link", { name: /查看原始公告/ });
    expect(link.getAttribute("href")).toBe(opportunity.original_url);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(mocks.requestRefresh).not.toHaveBeenCalled();
  });

  it("默认只提供行业、公告类型和地区，展示后端统计与建设方向", async () => {
    mocks.listOpportunities.mockResolvedValue({ ...list, total: 51, stats: { total: 51, today_new: 8, closing_soon: 3 } });
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    expect(screen.getByRole("heading", { name: "全国商机看板" })).toBeTruthy();
    expect(screen.getByText("信息化 · 数字化 · 智能化公开商机")).toBeTruthy();
    expect(screen.getByText("榆林优先 · 优先可参与与核心商机")).toBeTruthy();
    expect(screen.getByLabelText("公告类型")).toHaveProperty("value", "procurement");
    expect(screen.getByLabelText("地区")).toHaveProperty("value", "");
    expect(screen.getByRole("button", { name: "行业（可多选） 全部行业" })).toBeTruthy();
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "筛选" })).toBeNull();
    const stats = screen.getByRole("region", { name: "当前筛选统计" });
    expect(within(stats).getByText("51")).toBeTruthy();
    expect(within(stats).getByText("8")).toBeTruthy();
    expect(within(stats).getByText("3")).toBeTruthy();
    expect(screen.getByText("智能化")).toBeTruthy();
    expect(screen.getByText(/最近更新：/).textContent).not.toContain("最近更新：—");
    expect(mocks.listOpportunities).toHaveBeenCalledWith({ notice_category: "procurement", page: 1, page_size: 20 }, expect.any(AbortSignal));
  });

  it("行业多选和下拉变更自动请求，恢复默认清除旧深链隐藏条件", async () => {
    window.history.replaceState({}, "", "/centers/product/opportunities?q=old&purchaser=old&source=ccgp&status=CLOSED&need=other&notice_type=other&publish_period=week&deadline_period=week&budget_band=large&page=3&classification_status=excluded&ordering=budget&publish_from=2020-01-01&publish_to=2020-02-01&deadline_from=2020-01-01&deadline_to=2020-02-01&budget_min=1&budget_max=2");
    const user = userEvent.setup();
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ notice_category: "procurement", page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({ notice_category: "procurement" });
    await user.click(screen.getByRole("button", { name: "行业（可多选） 全部行业" }));
    await user.click(screen.getByRole("checkbox", { name: "煤炭" }));
    await user.click(screen.getByRole("checkbox", { name: "医疗卫生" }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ industry: "coal,medical", notice_category: "procurement", page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(new URLSearchParams(window.location.search).get("industry")).toBe("coal,medical");
    fireEvent.change(screen.getByLabelText("公告类型"), { target: { value: "result" } });
    fireEvent.change(screen.getByLabelText("地区"), { target: { value: "陕西省" } });
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ industry: "coal,medical", notice_category: "result", region: "陕西省", page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(new URLSearchParams(window.location.search).has("q")).toBe(false);
    expect(new URLSearchParams(window.location.search).has("purchaser")).toBe(false);
    await user.click(screen.getByRole("button", { name: "恢复默认" }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ notice_category: "procurement", page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(window.location.search).toBe("?notice_category=procurement");
    expect(screen.getByLabelText("公告类型")).toHaveProperty("value", "procurement");
    expect(screen.getByLabelText("地区")).toHaveProperty("value", "");
  });

  it("行业下拉可用键盘打开、选择及关闭", async () => {
    const user = userEvent.setup();
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    const summary = screen.getByRole("button", { name: "行业（可多选） 全部行业" });
    summary.focus();
    await user.keyboard("{Enter}");
    const coal = screen.getByRole("checkbox", { name: "煤炭" });
    coal.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ industry: "coal" }), expect.any(AbortSignal)));
    await user.keyboard("{Escape}");
    expect(summary.closest("details")?.open).toBe(false);
    expect(document.activeElement).toBe(summary);
  });

  it("前进后退同步筛选、页码和详情，分页保留三个条件", async () => {
    mocks.listOpportunities.mockImplementation(({ page }: { page: number }) => Promise.resolve({ ...list, page, total: 80, has_more: true }));
    window.history.replaceState({}, "", "/centers/product/opportunities?industry=coal&notice_category=change&region=陕西省&page=2");
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ industry: "coal", notice_category: "change", region: "陕西省", page: 3 }), expect.any(AbortSignal)));
    act(() => {
      window.history.replaceState({}, "", "/centers/product/opportunities?industry=water&notice_category=result&page=2&notice=7");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("link", { name: /查看原始公告/ });
    act(() => {
      window.history.replaceState({}, "", "/centers/product/opportunities?industry=water&notice_category=result&page=2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({ industry: "water", notice_category: "result", page: 2, page_size: 20 }, expect.any(AbortSignal)));
    expect(screen.getByLabelText("公告类型")).toHaveProperty("value", "result");
    expect(screen.getByRole("button", { name: "行业（可多选） 水利水电" })).toBeTruthy();
  });

  it("越界深链采用后端返回的第一页并保留三个筛选", async () => {
    window.history.replaceState({}, "", "/centers/product/opportunities?industry=coal&notice_category=procurement&region=陕西省&page=99");
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    await waitFor(() => expect(mocks.listOpportunities).toHaveBeenLastCalledWith({
      industry: "coal", notice_category: "procurement", region: "陕西省", page: 1, page_size: 20,
    }, expect.any(AbortSignal)));
    expect(new URLSearchParams(window.location.search).has("page")).toBe(false);
    expect(new URLSearchParams(window.location.search).get("region")).toBe("陕西省");
    expect(screen.queryByText("暂无符合条件的商机")).toBeNull();
    expect(screen.getByRole("button", { name: "上一页" })).toHaveProperty("disabled", true);
  });

  it("旧服务返回空的越界页时重新请求第一页而不扩大筛选", async () => {
    window.history.replaceState({}, "", "/centers/product/opportunities?industry=transport&notice_category=procurement&page=2");
    mocks.listOpportunities.mockResolvedValueOnce({ ...list, items: [], page: 2 });
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    expect(mocks.listOpportunities).toHaveBeenLastCalledWith({
      industry: "transport", notice_category: "procurement", page: 1, page_size: 20,
    }, expect.any(AbortSignal));
    expect(new URLSearchParams(window.location.search).get("industry")).toBe("transport");
    expect(new URLSearchParams(window.location.search).has("page")).toBe(false);
  });

  it("空数据保留真实零统计与来源异常，未返回统计时不编造数字", async () => {
    mocks.listOpportunities.mockResolvedValue({ ...list, items: [], total: 0, stats: { total: 0, today_new: 0, closing_soon: 0 }, last_updated_at: null });
    mocks.getTenderSources.mockResolvedValue([{ ...source, health_state: "blocked", health_label: "异常", health_detail: "来源暂时不可访问" }]);
    render(<TenderOpportunities/>);
    await screen.findByText("暂无符合条件的商机");
    expect(within(screen.getByRole("region", { name: "当前筛选统计" })).getAllByText("0")).toHaveLength(3);
    expect(screen.getByText("来源暂时不可访问")).toBeTruthy();
    expect(screen.queryByText("榆林市智慧交通建设项目")).toBeNull();
    expect(screen.getByText(/最近更新：尚未成功更新/)).toBeTruthy();
    mocks.listOpportunities.mockResolvedValue({ ...list, items: [], total: 0, stats: undefined });
    fireEvent.change(screen.getByLabelText("公告类型"), { target: { value: "all" } });
    await waitFor(() => expect(within(screen.getByRole("region", { name: "当前筛选统计" })).getAllByText("—")).toHaveLength(4));
  });

  it("筛选请求进行时标明旧结果，迟到的旧请求不能覆盖当前条件", async () => {
    let finishOld!: (value: typeof list) => void;
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    mocks.listOpportunities.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    fireEvent.change(screen.getByLabelText("公告类型"), { target: { value: "change" } });
    expect(screen.getByText(/正在更新筛选结果/)).toBeTruthy();
    const oldSignal = mocks.listOpportunities.mock.calls.at(-1)![1] as AbortSignal;
    const current = { ...list, items: [{ ...opportunity, id: 8, project_name: "当前结果公告" }] };
    mocks.listOpportunities.mockResolvedValueOnce(current);
    fireEvent.change(screen.getByLabelText("公告类型"), { target: { value: "result" } });
    await screen.findByText("当前结果公告");
    expect(oldSignal.aborted).toBe(true);
    await act(async () => { finishOld(list); });
    expect(screen.queryByText("榆林市智慧交通建设项目")).toBeNull();
    expect(screen.queryByText(/正在更新筛选结果/)).toBeNull();
    expect(screen.getByText("当前结果公告")).toBeTruthy();
  });

  it("详情前进后退时取消旧请求，迟到结果不会覆盖新详情", async () => {
    let finishOld!: (value: typeof opportunity) => void;
    mocks.getOpportunity.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    render(<TenderOpportunities/>);
    fireEvent.click(await screen.findByRole("button", { name: "榆林市智慧交通建设项目" }));
    const oldSignal = mocks.getOpportunity.mock.calls.at(-1)![1] as AbortSignal;
    mocks.getOpportunity.mockResolvedValueOnce({ ...opportunity, id: 8, project_name: "新的公告详情" });
    act(() => {
      window.history.replaceState({}, "", "/centers/product/opportunities?notice=8");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("heading", { name: "新的公告详情" });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => { finishOld(opportunity); });
    expect(screen.queryByRole("heading", { name: opportunity.project_name })).toBeNull();
    expect(screen.getByRole("heading", { name: "新的公告详情" })).toBeTruthy();
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
    expect(screen.getByText(/刷新失败 · 新增 2 条 · 来源暂时无法访问/)).toBeTruthy();
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

  it("紧凑布局移除自动更新横栏，列表保留筛选统计", async () => {
    mocks.getRefreshOverview.mockResolvedValue({ ...noRefresh, consumer_online: true,
      schedule: { ...noRefresh.schedule, enabled: true },
      batch: { ...queued, trigger: "scheduled", stored_state: "PARTIAL", display_state: "PARTIAL" } });
    render(<TenderOpportunities/>);
    await screen.findByText("榆林市智慧交通建设项目");
    expect(screen.queryByRole("region", { name: "公告更新安排" })).toBeNull();
    expect(screen.queryByText("自动每小时更新")).toBeNull();
    expect(screen.queryByText("自动更新状态")).toBeNull();
    const list = screen.getByRole("region", { name: "项目商机列表" });
    expect(within(list).getByRole("region", { name: "当前筛选统计" })).toBeTruthy();
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    expect(mocks.requestRefresh).not.toHaveBeenCalled();
  });

  it("每30秒发现已完成的自动批次，同一终态只更新列表一次", async () => {
    vi.useFakeTimers();
    const finished = { id: "auto-1", stored_state: "SUCCESS", display_state: "更新完成", trigger: "scheduled", results: {} };
    render(<TenderOpportunities/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(mocks.listOpportunities).toHaveBeenCalledTimes(1);
    mocks.getRefreshOverview.mockResolvedValue({ ...noRefresh, batch: finished });
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS); });
    expect(screen.queryByText("自动更新状态")).toBeNull();
    expect(mocks.listOpportunities).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS * 2); });
    expect(mocks.listOpportunities).toHaveBeenCalledTimes(2);
    expect(mocks.getTenderSources).toHaveBeenCalledTimes(4);
    expect(mocks.getOpportunityOptions).toHaveBeenCalledTimes(1);
    expect(mocks.getRefreshBatch).not.toHaveBeenCalled();
    expect(mocks.requestRefresh).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1);
  });

  it("筛选选项失败不会被成功的来源状态轮询清除", async () => {
    let finishOverview: (value: typeof noRefresh) => void = () => {};
    mocks.getOpportunityOptions.mockRejectedValue(new Error("options unavailable"));
    mocks.getRefreshOverview.mockImplementationOnce(() => new Promise(resolve => { finishOverview = resolve; }));
    render(<TenderOpportunities/>);
    expect(await screen.findByText("部分筛选选项暂不可用。")).toBeTruthy();
    await act(async () => finishOverview({ ...noRefresh, available: false, unavailable_reason: "暂时关闭手动刷新。" }));
    expect(await screen.findByText(/刷新当前不可用：暂时关闭手动刷新/)).toBeTruthy();
    expect(screen.getByText("部分筛选选项暂不可用。")).toBeTruthy();
  });

  it("隐藏页面暂停所有状态轮询，恢复可见立即发现新批次且只保留一个计时器", async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const { unmount } = render(<TenderOpportunities/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS * 2); });
    expect(mocks.getRefreshOverview).not.toHaveBeenCalled();
    expect(mocks.getTenderSources).not.toHaveBeenCalled();
    mocks.getRefreshOverview.mockResolvedValue({ ...noRefresh, batch: queued });
    mocks.getRefreshBatch.mockResolvedValue(queued);
    visibility.mockReturnValue("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(mocks.getRefreshOverview).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_POLL_MS); });
    expect(mocks.getRefreshBatch).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS * 3); });
    expect(mocks.getRefreshBatch).toHaveBeenCalledTimes(1);
    expect(mocks.getRefreshOverview).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(mocks.getRefreshOverview).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("定时状态查询撤权会停止轮询并隐藏列表", async () => {
    vi.useFakeTimers();
    render(<TenderOpportunities/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    mocks.getRefreshOverview.mockRejectedValue(new ApiError(403, "无权限", "forbidden"));
    await act(async () => { await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS); });
    expect(screen.getByRole("alert").textContent).toContain("访问权限已变化");
    expect(screen.queryByText(opportunity.project_name)).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("显示来源安全统计与中文原因，未知错误不暴露原始代码", async () => {
    mocks.getTenderSources.mockResolvedValue([{ ...source, health_state: "degraded", latest_run: {
      state: "PARTIAL", stats: { new_notices: 3, new_versions: 1 }, error_code: "private_internal_exception",
      error_detail: "服务限制访问，请稍后重试。", started_at: "2026-09-28T00:00:00Z",
    } }]);
    mocks.getRefreshOverview.mockResolvedValue({ ...noRefresh, consumer_online: false,
      schedule: { ...noRefresh.schedule, enabled: true }, batch: { ...queued, stored_state: "FAILED", display_state: "FAILED",
        results: { ccgp: { state: "FAILED", error_code: "raw_unexpected_exception", error_detail: "private file /tmp/secret" } } } });
    render(<TenderOpportunities/>);
    await screen.findByText("服务限制访问，请稍后重试。");
    expect(screen.getByText("部分更新")).toBeTruthy();
    expect(screen.getByText(/最近一轮：部分更新 · 新增 3 条 · 更新 1 版/)).toBeTruthy();
    expect(screen.getByText("服务限制访问，请稍后重试。")).toBeTruthy();
    expect(screen.getByText(/来源更新失败，请稍后重试或查看来源状态/)).toBeTruthy();
    expect(screen.queryByText(/raw_unexpected_exception|private_internal_exception|private file/)).toBeNull();
  });

  it("有限扫描标注部分更新，批次和来源优先显示安全detail字段", async () => {
    mocks.getTenderSources.mockResolvedValue([{ ...source, latest_run: {
      state: "PARTIAL", error_code: "coverage_partial", detail: "本轮已更新可访问范围，其余公告待后续采集。",
      error_detail: "不应展示的旧错误详情", stats: { new_notices: 2 },
    } }]);
    mocks.getRefreshOverview.mockResolvedValue({ ...noRefresh, batch: {
      ...queued, stored_state: "PARTIAL", display_state: "PARTIAL", results: {
        ccgp: { state: "PARTIAL", error_code: "coverage_partial", detail: "采集页数已达本轮上限，未覆盖全部公告。", error_detail: "不应展示的旧错误详情" },
        protected: { state: "BLOCKED", error_code: "source_blocked" },
      },
    } });
    render(<TenderOpportunities/>);
    await screen.findByText("本轮已更新可访问范围，其余公告待后续采集。");
    expect(screen.getAllByText("部分更新")).toHaveLength(2);
    expect(screen.getByText(/采集页数已达本轮上限，未覆盖全部公告/)).toBeTruthy();
    expect(screen.getByText(/来源限制访问，本轮更新已停止/)).toBeTruthy();
    expect(screen.queryByText(/不应展示的旧错误详情|coverage_partial|source_blocked/)).toBeNull();
  });
});
