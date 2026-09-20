import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import userEvent from "@testing-library/user-event";
import { clearApiSession } from "./api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

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

function mockSummary(body: unknown, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    if (String(input) === "/api/me/") return Promise.resolve(json(baseUser));
    if (String(input) === "/api/modules/") return Promise.resolve(json([
      { code: "business", name: "项目经营中心", description: "测试入口", status: "navigation", enabled: true },
    ]));
    if (String(input) === "/api/business/summary/") return Promise.resolve(json(body, status));
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
    expect(screen.getByText(/浏览器 SSO：未实现/)).toBeTruthy();
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
