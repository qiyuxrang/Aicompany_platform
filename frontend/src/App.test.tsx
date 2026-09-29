import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import userEvent from "@testing-library/user-event";
import { clearApiSession } from "./api";
import { COMPANY_NAME, COMPANY_ENGLISH_NAME } from "./CompanyIdentity";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it.each(['product', 'cost', 'hr', 'business'])("%s 部门共享完整品牌和统一侧栏，预览不读取业务", async code => {
  clearApiSession();
  window.history.replaceState({}, '', `/preview/${code}`);
  const fetchMock = vi.fn((input: RequestInfo | URL) => String(input) === '/api/me/'
    ? Promise.resolve(json({ id: 99, username: 'admin', display_name: '管理员', is_platform_admin: true, roles: [], must_change_password: false }))
    : Promise.resolve(json({ detail: 'unexpected' }, 404)));
  vi.stubGlobal('fetch', fetchMock);
  const { container } = render(<App/>);
  expect(await screen.findByText(COMPANY_NAME)).toBeTruthy();
  expect(screen.getByText(COMPANY_ENGLISH_NAME)).toBeTruthy();
  expect(screen.getByRole('link', { name: '企业统一门户首页' })).toBeTruthy();
  await screen.findByRole('navigation', { name: '切换预览' });
  expect(container.querySelector('.unified-app-shell > .app-content .workspace-sidebar')).not.toBeNull();
  expect(fetchMock.mock.calls.every(([path]) => String(path) === '/api/me/')).toBe(true);
  cleanup(); vi.unstubAllGlobals();
});

const baseUser = {
  id: 1,
  username: "tester",
  display_name: "测试用户",
  roles: [{ code: "staff", name: "业务人员" }],
  must_change_password: false,
  is_platform_admin: false,
};

const validSummary = {
  projects: [{ id: "test-project", name: "<img src=x onerror=alert(1)>" }],
  summary: { project_count: 0, contract_amount: "123456.78", received_amount: 0, receivable_amount: "20.00" },
  source: "测试来源（仅测试夹具）",
  updated_at: "2026-09-20T08:00:00Z",
};

const validWorkSummary = {
  modules: { product: { available: true }, hr: { available: false, reason: "未获授权访问人事模块。" } },
  sections: {
    my_tasks: {
      available: true,
      count: 1,
      items: [{
        id: "12", title: "技术方案", status: "draft", href: "/centers/product/documents?task=12",
        updated_at: "2026-09-22T08:00:00Z", kind: "product_task",
      }],
      reason: "仅汇总已授权模块；未授权模块不计入数量。",
    },
    pending_reviews: { available: true, count: 0, items: [], reason: "仅汇总已授权模块；未授权模块不计入数量。" },
    recent_results: { available: true, count: 0, items: [], reason: "仅汇总已授权模块；未授权模块不计入数量。" },
  },
};

function mockSummary(body: unknown, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    if (String(input) === "/api/me/") return Promise.resolve(json(baseUser));
    if (String(input) === "/api/modules/") return Promise.resolve(json([
      { code: "business", name: "项目经营中心", description: "测试入口", status: "navigation", enabled: true },
    ]));
    if (String(input) === "/api/business/summary/") return Promise.resolve(json(body, status));
    if (String(input) === "/api/work/summary/") return Promise.resolve(json(validWorkSummary));
    return Promise.resolve(json({ detail: "未找到" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("portal routing", () => {
  beforeEach(() => {
    clearApiSession();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("总经理从个人工作台入口直接进入企业台账首页", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/me/") return Promise.resolve(json({ ...baseUser, roles: [{ code: "general_manager", name: "总经理" }] }));
      if (url === "/api/modules/business/") return Promise.resolve(json({ code: "business", name: "总经理工作台", description: "", status: "verified", enabled: true }));
      if (url === "/api/modules/") return Promise.resolve(json([{ code: "business", name: "总经理工作台", description: "", status: "verified", enabled: true }]));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/centers/business"));
    expect(await screen.findByRole("link", { name: /企业台账/ })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "工作摘要" })).toBeNull();
  });

  it.each([
    { role: "engineering", name: "工程人员", home: "/centers/cost" },
    { role: "product", name: "产品人员", home: "/centers/product" },
    { role: "hr", name: "人事人员", home: "/centers/hr" },
  ])("单部门账号 $role 访问根地址时直接进入对应部门，不经过个人工作台", async ({ role, name, home }) => {
    // README 约定：单部门账号登录后直接进入对应部门。此前仅企业台账实现了该跳转，
    // 工程部等账号会停在个人工作台，需要手动点一次入口才能进部门。
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/me/") return Promise.resolve(json({ ...baseUser, roles: [{ code: role, name }] }));
      if (url === "/api/modules/") return Promise.resolve(json([{ code: role === "engineering" ? "cost" : role, name, description: "", status: "verified", enabled: true }]));
      if (url === `/api/modules/${role === "engineering" ? "cost" : role}/`) return Promise.resolve(json({ code: role === "engineering" ? "cost" : role, name, description: "", status: "verified", enabled: true }));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe(home));
    expect(screen.queryByRole("heading", { name: /欢迎回来/ })).toBeNull();
  });

  it("多部门账号访问根地址时仍留在统一工作台选择入口", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/me/") return Promise.resolve(json({ ...baseUser, roles: [{ code: "product", name: "产品人员" }, { code: "hr", name: "人事人员" }] }));
      if (url === "/api/modules/") return Promise.resolve(json([]));
      if (url === "/api/work/summary/") return Promise.resolve(json(validWorkSummary));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    expect(await screen.findByRole("heading", { name: /欢迎回来/ })).toBeTruthy();
    expect(window.location.pathname).toBe("/");
  });

  it("待接入模块禁止启动并保留返回工作台", async () => {
    window.history.replaceState({}, "", "/modules/product");
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/me/") return Promise.resolve(json(baseUser));
      if (String(input) === "/api/modules/product/") return Promise.resolve(json({
        code: "product", name: "产品方案中心", description: "待接入", status: "pending", enabled: true,
      }));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const launch = await screen.findByRole("button", { name: "待接入，暂不可进入" });
    expect((launch as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(launch);
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes("launch"))).toBe(false);
    expect(screen.getByRole("link", { name: "← 返回工作台" }).getAttribute("href")).toBe("/");
    expect(screen.queryByText("Enterprise Workspace")).toBeNull();
    expect(screen.getByText("浏览器单点登录")).toBeTruthy();
  });

  it("强制首次改密用户不能进入工作台", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...baseUser, must_change_password: true })));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "首次登录，请先修改密码" })).toBeTruthy();
    expect(window.location.pathname).toBe("/password");
  });

  it("业务请求返回 401 时回到登录页并提示会话过期", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/me/") return Promise.resolve(json(baseUser));
      if (url === "/api/modules/") return Promise.resolve(json({ detail: "会话已失效" }, 401));
      if (url === "/api/business/summary/") return Promise.resolve(json({ detail: "未启用" }, 503));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    expect(await screen.findByText("登录状态已过期，请重新登录。")).toBeTruthy();
    expect(window.location.pathname).toBe("/login");
  });

  it("显示真实契约的项目、四项摘要、来源时间且安全渲染文本", async () => {
    mockSummary(validSummary);
    const { container } = render(<App />);
    expect(await screen.findByText(validSummary.source)).toBeTruthy();
    for (const label of ["项目数量", "合同金额", "已收金额", "应收金额"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("123456.78")).toBeTruthy();
    expect(screen.getByText("20.00")).toBeTruthy();
    expect(container.querySelectorAll(".metric-grid dd")[0].textContent).toBe("0");
    expect(container.querySelectorAll(".metric-grid dd")[2].textContent).toBe("0");
    expect(screen.getByText(validSummary.projects[0].name)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/2026\/09\/20/)).toBeTruthy();
    expect(screen.getByText(/接口未提供币种或计量单位/)).toBeTruthy();
  });

  it("工作摘要展示真实数量、对象链接及部分授权说明", async () => {
    mockSummary(validSummary);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "工作摘要" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeTruthy();
    const task = await screen.findByRole("link", { name: /技术方案/ });
    expect(task.getAttribute("href")).toBe("/centers/product/documents?task=12");
    expect(screen.getAllByText("仅汇总已授权模块；未授权模块不计入数量。")).toHaveLength(3);
    expect(screen.getByText("当前没有待处理审批。")).toBeTruthy();
  });

  it("仅查询参数变化及前后退会重新定位准确产品任务", async () => {
    const details = (id: string, title: string) => ({
      id, title, state: "DRAFT", stage: "INTAKE", version: 1, input_version: 1, blueprint_version: 0,
      input: { project: title, requirements: "需求", background: "", items: [], conditions: [] }, blueprint: null,
      chapters: [], artifacts: [], sources: [], approvals: [], issues: [], error_code: "", actions: [], blockers: {}, reviewer_id: null, owner_id: 1, input_issues: [], impact: {},
    });
    window.history.replaceState({}, "", "/centers/product/documents?task=one");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(baseUser));
      if (path === "/api/modules/product/") return Promise.resolve(json({ code: "product", name: "产品", description: "", status: "verified", enabled: true }));
      if (path === "/api/modules/") return Promise.resolve(json([{ code: "product", name: "产品", description: "", status: "verified", enabled: true }]));
      if (path === "/api/product/tasks/") return Promise.resolve(json([{ id: "one", title: "第一任务", state: "DRAFT", stage: "INTAKE", version: 1 }, { id: "two", title: "第二任务", state: "DRAFT", stage: "INTAKE", version: 1 }]));
      if (path === "/api/product/tasks/one/") return Promise.resolve(json(details("one", "第一任务")));
      if (path === "/api/product/tasks/two/") return Promise.resolve(json(details("two", "第二任务")));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));
    render(<App />);
    expect((await screen.findByLabelText("草稿标题") as HTMLInputElement).value).toBe("第一任务");
    window.history.pushState({}, "", "/centers/product/documents?task=two");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("第二任务"));
    window.history.replaceState({}, "", "/centers/product/documents?task=one");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("第一任务"));
  });

  it("历史 JD 入口只读，保留原版本正文", async () => {
    const hrUser = { ...baseUser, roles: [{ code: "hr", name: "人事" }] };
    const job = (id: string, title: string, body: string) => ({
      id, owner_id: 1, title, department: "人事部", objective: "目标", responsibilities: "职责", requirements: "要求",
      state: "generated", version: 1, input_version: 1, missing_fields: [], current_revision: { id: `rev-${id}`, version: 1, input_version: 1, kind: "generated", body, parent_id: null, created_by_id: 1, confirmed_by_id: null, confirmed_at: null, created_at: "2026-09-23T00:00:00Z" },
      official_revision: null, current_revision_stale: false, revisions: [], updated_at: "2026-09-23T00:00:00Z",
    });
    const jobs = [job("job-1", "第一岗位", "第一正文"), job("job-2", "第二岗位", "第二正文")];
    window.history.replaceState({}, "", "/centers/hr/history");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(hrUser));
      if (path === "/api/modules/hr/") return Promise.resolve(json({ code: "hr", name: "人事", description: "", status: "verified", enabled: true }));
      if (path === "/api/modules/") return Promise.resolve(json([{ code: "hr", name: "人事", description: "", status: "verified", enabled: true }]));
      if (path === "/api/hr/jobs/") return Promise.resolve(json(jobs));
      if (path === "/api/hr/recruitment/requests/") return Promise.resolve(json([]));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));
    render(<App />);
    expect(await screen.findByText('第一岗位')).toBeTruthy();
    expect(screen.getByText('第二岗位')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '生成确定性 JD 草稿' })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'JD 正文' })).toBeNull();
  });

  it("同一 App 实例响应转正 case query 变化，普通经理仅获得本人审批链", async () => {
    const probation = (id: string, name: string) => ({ id, owner_id: 9, assigned_manager_id: 1, employee_name: name, position: "工程师", materials: [], notes: "", manager_opinion: "", hr_conclusion: "", state: "manager_pending", version: 3, actions: ["manager_approve"], assistant_enabled: false, assistant_mode: "manual", assistant_reason: "model_not_authorized", updated_at: "2026-09-23T00:00:00Z", transitions: [], revisions: [] });
    const cases = [probation("case-1", "第一员工"), probation("case-2", "第二员工")];
    window.history.replaceState({}, "", "/centers/hr/probation?case=case-1");
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(baseUser));
      if (path === "/api/modules/hr/") return Promise.resolve(json({ code: "hr", name: "人事", description: "", status: "verified", enabled: true }));
      if (path === "/api/modules/") return Promise.resolve(json([{ code: "hr", name: "人事", description: "", status: "verified", enabled: true }]));
      if (path === "/api/hr/probations/") return Promise.resolve(json(cases));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByRole("heading", { name: /第一员工/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "主管批准" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "创建转正事项" })).toBeNull();
    expect(screen.queryByRole("link", { name: "岗位说明" })).toBeNull();
    expect(fetchMock.mock.calls.some(([path]) => String(path) === "/api/hr/jobs/")).toBe(false);
    window.history.pushState({}, "", "/centers/hr/probation?case=case-2");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByRole("heading", { name: /第二员工/ })).toBeTruthy();
  });

  it("无经营授权不渲染摘要也不发送经营请求", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/me/") return Promise.resolve(json(baseUser));
      if (String(input) === "/api/modules/") return Promise.resolve(json([]));
      return Promise.resolve(json({ detail: "不应请求" }, 403));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByText("暂无已授权模块")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "经营摘要" })).toBeNull();
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/business/summary/")).toBe(false);
  });

  it("空项目与空摘要显示各自空态，不推算或填零", async () => {
    mockSummary({ ...validSummary, projects: [], summary: {} });
    const { container } = render(<App />);
    expect(await screen.findByText("暂无项目数据。")).toBeTruthy();
    expect(screen.getByText("接口已启用，但暂无可展示的摘要项。")).toBeTruthy();
    expect(container.querySelector(".metric-grid")).toBeNull();
  });

  it("未配置是正常未接入状态，并明确未验证、无 SSO", async () => {
    mockSummary({ detail: "集成尚未配置", code: "integration_not_configured" }, 503);
    render(<App />);
    expect(await screen.findByText("未接入 · 未验证")).toBeTruthy();
    expect(screen.getByText(/浏览器单点登录：未实现/)).toBeTruthy();
    expect(screen.queryByText("经营摘要加载失败")).toBeNull();
  });

  it.each([403, 503])("其他 %i 是失败而非正常未接入，支持重试", async (status) => {
    const fetchMock = mockSummary({ detail: "服务拒绝或离线" }, status);
    render(<App />);
    expect(await screen.findByText("经营摘要加载失败")).toBeTruthy();
    expect(screen.queryByText("未接入 · 未验证")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(fetchMock.mock.calls.filter(([path]) => path === "/api/business/summary/")).toHaveLength(2);
  });

  it.each([null, { items: [] }, { ...validSummary, projects: [{ id: {}, name: "bad" }] },
    { ...validSummary, summary: { contract_amount: {} } }])("无效摘要响应显示错误而不是空态 %#", async (body) => {
    mockSummary(body);
    render(<App />);
    expect(await screen.findByText("经营摘要返回格式无效，未展示不可信数据。")).toBeTruthy();
    expect(screen.queryByText("暂无项目数据。")).toBeNull();
  });

  it("摘要请求 401 清除门户视图并回登录", async () => {
    mockSummary({ detail: "会话过期" }, 401);
    render(<App />);
    expect(await screen.findByText("登录状态已过期，请重新登录。")).toBeTruthy();
    expect(screen.queryByText("经营摘要")).toBeNull();
  });

  it.each(["%E0%A4%A", "%2F", "%2e%2e%2Fadmin"])("错误模块参数 %s 不发送业务请求", async (code) => {
    window.history.replaceState({}, "", `/modules/${code}`);
    const fetchMock = mockSummary(validSummary);
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "模块参数无效，请从工作台重新选择入口。");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
