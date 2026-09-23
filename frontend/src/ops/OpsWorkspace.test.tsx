import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { clearApiSession } from "../api";
import { AuditTable, DefinitionList, formatErrorRate, StatusText } from "./components";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const admin = {
  id: 9,
  username: "ops-admin",
  display_name: "运维管理员",
  roles: [{ code: "platform_admin", name: "平台管理员" }],
  must_change_password: false,
  is_platform_admin: true,
};

const staff = {
  id: 2,
  username: "staff",
  display_name: "普通用户",
  roles: [{ code: "product", name: "产品人员" }],
  must_change_password: false,
  is_platform_admin: false,
};

const moduleRow = {
  id: 1,
  code: "product",
  name: "产品方案中心",
  description: "产品入口",
  url: null,
  status: "pending",
  enabled: true,
  admin_url: "/admin/portal/module/1/change/",
  check: null,
};

const issue = {
  id: 1,
  module_code: "product",
  module_name: "产品方案中心",
  severity: "high",
  title: "模块不可用",
  status: "open",
  health_state: "unresolved",
  first_seen: "2026-09-20T08:00:00Z",
  last_seen: "2026-09-21T08:00:00Z",
  occurrences: 2,
  evidence: "固定目标连接失败",
  checked_at: "2026-09-21T08:00:00Z",
  notes: [],
  admin_url: "/admin/portal/module/1/change/",
};

function overview(days: 7 | 30) {
  return {
    updated_at: "2026-09-21T08:00:00Z",
    range: { days, start: "2026-09-15", end: "2026-09-21", timezone: "Asia/Shanghai" },
    health: { state: "healthy", checked_at: "2026-09-21T08:00:00Z", database_ms: 4, message: "数据库可达" },
    accounts: { enabled: 3, total: 4 },
    usage: { login_users: 2, login_count: 5, module_launches: 3 },
    performance: { state: "collected", scope: "应用进程", window_seconds: 900, collected_since: "2026-09-21T07:45:00Z", requests: 3, errors: 0, error_rate: 0, average_ms: 12, max_ms: 20, routes: [] },
    issue_count: 1,
    trend: [{ date: "2026-09-21", login_users: 2, login_count: 5, module_launches: 3 }],
    modules: [moduleRow],
    recent_issues: [issue],
    recent_audit: [],
    backup: { state: "missing", message: "未接入", checked_at: null, record: null },
    version: "portal-test",
    my_modules: [],
  };
}

describe("ops workspace", () => {
  beforeEach(() => {
    clearApiSession();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("管理员默认进入运维，并在同一路径切换 query 后重新请求", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/overview/?days=7") return Promise.resolve(json(overview(7)));
      if (path === "/api/ops/overview/?days=30") return Promise.resolve(json(overview(30)));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "运维总览" })).toBeTruthy();
    expect(await screen.findByText("错误率 0%（错误请求数 ÷ 有效接口请求数）")).toBeTruthy();
    expect(window.location.pathname).toBe("/ops");
    expect(within(screen.getByRole("navigation", { name: "运维主菜单" })).getByRole("link", { name: "运维总览" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "跳到主要内容" }).getAttribute("href")).toBe("#ops-main");
    expect(screen.getByRole("main").getAttribute("tabindex")).toBe("-1");
    await userEvent.click(screen.getByRole("button", { name: "30天" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path === "/api/ops/overview/?days=30")).toBe(true));
    expect(window.location.search).toBe("?days=30");
  });

  it("普通用户直达运维页显示403且不请求运维接口", async () => {
    window.history.replaceState({}, "", "/ops");
    const fetchMock = vi.fn().mockResolvedValue(json(staff));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "无运维访问权限" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/me/", expect.anything());
  });

  it("运维读取返回 ops_forbidden 后立即清空旧数据并刷新身份", async () => {
    window.history.replaceState({}, "", "/ops");
    let meCalls = 0;
    let overviewCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(meCalls++ === 0 ? admin : staff));
      if (path === "/api/ops/overview/?days=7") {
        overviewCalls += 1;
        return Promise.resolve(overviewCalls === 1
          ? json(overview(7))
          : json({ detail: "运维权限已撤销", code: "ops_forbidden" }, 403));
      }
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    expect(await screen.findByText("3 / 4")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "手动刷新" }));

    expect(await screen.findByRole("heading", { name: "无运维访问权限" })).toBeTruthy();
    expect(screen.queryByText("3 / 4")).toBeNull();
    expect(meCalls).toBe(2);
  });

  it("模块检查撤权后卸载模块数据而不是保留 POST 错误态", async () => {
    window.history.replaceState({}, "", "/ops/modules");
    let meCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(meCalls++ === 0 ? admin : staff));
      if (path === "/api/ops/modules/" && (init?.method ?? "GET") === "GET") {
        return Promise.resolve(json({ updated_at: "2026-09-21T08:00:00Z", items: [moduleRow] }));
      }
      if (path === "/api/csrf/") return Promise.resolve(json({ csrfToken: "test-token" }));
      if (path === "/api/ops/modules/product/check/" && init?.method === "POST") {
        return Promise.resolve(json({ detail: "运维权限已撤销", code: "ops_forbidden" }, 403));
      }
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    expect(await screen.findByText("产品方案中心")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "执行固定检查" }));

    expect(await screen.findByRole("heading", { name: "无运维访问权限" })).toBeTruthy();
    expect(screen.queryByText("产品方案中心")).toBeNull();
    expect(screen.queryByText("运维权限已撤销")).toBeNull();
    expect(meCalls).toBe(2);
  });

  it("问题更新撤权后卸载旧证据与备注", async () => {
    window.history.replaceState({}, "", "/ops/issues");
    let meCalls = 0;
    const issueWithNote = { ...issue, notes: [{ at: "2026-09-21T08:00:00Z", actor: "ops-admin", text: "旧管理备注" }] };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(meCalls++ === 0 ? admin : staff));
      if (path === "/api/ops/issues/?severity=all&status=all&days=30&page=1") {
        return Promise.resolve(json({ items: [issueWithNote], total: 1, page: 1, page_size: 20, pages: 1 }));
      }
      if (path === "/api/ops/modules/") return Promise.resolve(json({ updated_at: "2026-09-21T08:00:00Z", items: [moduleRow] }));
      if (path === "/api/ops/issues/1/" && (init?.method ?? "GET") === "GET") return Promise.resolve(json(issueWithNote));
      if (path === "/api/csrf/") return Promise.resolve(json({ csrfToken: "test-token" }));
      if (path === "/api/ops/issues/1/" && init?.method === "POST") {
        return Promise.resolve(json({ detail: "运维权限已撤销", code: "ops_forbidden" }, 403));
      }
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "问题详情" });
    expect(within(dialog).getByText("旧管理备注")).toBeTruthy();
    await userEvent.selectOptions(within(dialog).getByLabelText("处理状态"), "investigating");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存更新" }));

    expect(await screen.findByRole("heading", { name: "无运维访问权限" })).toBeTruthy();
    expect(screen.queryByText("旧管理备注")).toBeNull();
    expect(screen.queryByText("固定目标连接失败")).toBeNull();
    expect(meCalls).toBe(2);
  });

  it.each(["click", "enter"])("使用分析保留独立模块目录并支持趋势点 %s 导航", async (activation) => {
    window.history.replaceState({}, "", "/ops/usage?days=1&module=business");
    const businessModule = { ...moduleRow, id: 4, code: "business", name: "项目经营中心" };
    const usage = {
      updated_at: "2026-09-21T08:00:00Z",
      range: { days: 1, start: "2026-09-21", end: "2026-09-21", timezone: "Asia/Shanghai" },
      summary: { enabled_accounts: 4, login_users: 2, login_count: 3, module_launches: 1 },
      trend: [{ date: "2026-09-21", login_users: 2, login_count: 3, module_launches: 1 }],
      ranking: [{ code: "business", name: "项目经营中心", launches: 1 }],
      definitions: {
        login_users: "期间成功登录事件的去重账号数。",
        module_filter: "模块筛选仅作用于启动数。",
      },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/usage/?days=1&module=business") return Promise.resolve(json(usage));
      if (path === "/api/ops/modules/") return Promise.resolve(json({ updated_at: usage.updated_at, items: [moduleRow, businessModule] }));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    const moduleSelect = await screen.findByLabelText("模块");
    expect(await within(moduleSelect).findByRole("option", { name: "产品方案中心" })).toBeTruthy();
    expect(within(moduleSelect).getByRole("option", { name: "项目经营中心" })).toBeTruthy();
    expect(screen.getAllByText("登录活跃人数")).toHaveLength(2);

    const point = screen.getByRole("link", { name: /2026-09-21/ });
    const hitTarget = point.querySelector(".trend-hit-target");
    const circleY = Array.from(point.querySelectorAll("circle"), (circle) => Number(circle.getAttribute("cy")));
    const hitTop = Number(hitTarget?.getAttribute("y"));
    const hitBottom = hitTop + Number(hitTarget?.getAttribute("height"));
    expect(hitTarget).toBeTruthy();
    expect(Number(hitTarget?.getAttribute("width"))).toBeGreaterThanOrEqual(24);
    expect(hitTop).toBeLessThanOrEqual(Math.min(...circleY));
    expect(hitBottom).toBeGreaterThanOrEqual(Math.max(...circleY));
    if (activation === "click") await userEvent.click(point);
    else {
      point.focus();
      await userEvent.keyboard("{Enter}");
    }

    await waitFor(() => expect(new URLSearchParams(window.location.search).get("date")).toBe("2026-09-21"));
    expect(new URLSearchParams(window.location.search).get("module")).toBe("business");
    expect(await screen.findByRole("dialog", { name: "2026-09-21 使用明细" })).toBeTruthy();
  });

  it("将错误率、历史记录、失败与统计字段显示为简明中文", () => {
    expect(formatErrorRate(0)).toBe("0%");
    expect(formatErrorRate(0.125)).toBe("12.5%");
    render(<>
      <StatusText value="historical_record" />
      <StatusText value="failure" />
      <DefinitionList values={{ login_users: "期间成功登录事件的去重账号数。" }} />
    </>);
    expect(screen.getByText("历史演练记录")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("登录活跃人数")).toBeTruthy();
  });

  it("审计动作与字段使用中文，未知标识仍可展开追溯", () => {
    render(<AuditTable rows={[
      { id: 1, created_at: "2026-09-21T08:00:00Z", actor: "tester", action: "user_change", target: "1", result: "success", changes: ["roles", "is_active"] },
      { id: 2, created_at: "2026-09-21T08:00:00Z", actor: "tester", action: "future_action", target: "1", result: "success", changes: ["future_field"] },
    ]} />);
    expect(screen.getByText("修改账号")).toBeTruthy();
    expect(screen.getByText("角色、账号状态")).toBeTruthy();
    expect(screen.getByText("原始标识").closest("details")?.open).toBe(false);
    expect(screen.getByText("原始字段").closest("details")?.open).toBe(false);
    expect(screen.getByText("future_action")).toBeTruthy();
    expect(screen.getByText("future_field")).toBeTruthy();
  });

  it("维护页展开已知环境字段并翻译诊断范围", async () => {
    window.history.replaceState({}, "", "/ops/maintenance?tab=environment");
    const maintenance = {
      updated_at: "2026-09-21T08:00:00Z",
      environment: {
        mode: "production",
        https: true,
        database_engine: "PostgreSQL",
        timezone: "Asia/Shanghai",
        host_metrics: { state: "not_collected", message: "CPU、整机内存和磁盘未采集。" },
      },
      performance: {
        state: "collected",
        scope: "single_process_api",
        window_seconds: 900,
        collected_since: "2026-09-21T07:45:00Z",
        requests: 4,
        errors: 0,
        error_rate: 0,
        average_ms: 12,
        max_ms: 20,
        routes: [],
      },
      backup: { state: "historical_record", message: "仅为历史记录。", checked_at: null, record: null },
      version: "portal-test",
      deployment_history: { state: "not_configured", message: "未接入可靠部署历史。" },
      audit: { items: [], total: 0, page: 1, page_size: 20, pages: 0 },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/maintenance/?days=7&page=1") return Promise.resolve(json(maintenance));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);

    expect(await screen.findByText("生产环境")).toBeTruthy();
    expect(screen.getByText("已启用")).toBeTruthy();
    expect(screen.getByText("PostgreSQL")).toBeTruthy();
    expect(screen.getByText("中国标准时间")).toBeTruthy();
    for (const label of ["处理器", "整机内存", "磁盘"]) expect(screen.getByText(label)).toBeTruthy();
    expect(screen.getAllByText("未采集")).toHaveLength(3);

    await userEvent.click(screen.getByRole("button", { name: "诊断" }));
    expect(screen.getByText("已采集")).toBeTruthy();
    expect(screen.getByText("单应用进程有效接口请求")).toBeTruthy();
    expect(screen.getByText("错误率 = 错误请求数 ÷ 最近 15 分钟有效接口请求数。")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "审计" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "按动作筛选" }), "login");
    await waitFor(() => expect(window.location.search).toContain("action=login"));
    expect(window.location.search).not.toContain("page=");
    expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes("action=login"))).toBe(true);
  });

  it("人员侧栏支持 Escape 关闭并将焦点归还触发按钮", async () => {
    window.history.replaceState({}, "", "/ops/people");
    const userRow = {
      id: 2,
      username: "staff",
      display_name: "普通用户",
      is_active: true,
      must_change_password: false,
      roles: [{ code: "product", name: "产品人员" }],
      last_login: "2026-09-21T07:00:00Z",
      admin_url: "/admin/portal/user/2/change/",
      password_url: "/admin/portal/user/2/password/",
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/users/?status=all&page=1") return Promise.resolve(json({ items: [userRow], total: 1, page: 1, page_size: 20, pages: 1, roles: userRow.roles }));
      if (path === "/api/ops/users/2/") return Promise.resolve(json({
        ...userRow,
        recent_audit: [{ id: 1, created_at: "2026-09-21T07:00:00Z", actor: "ops-admin", action: "user_change", target: "2", result: "success", changes: ["roles"] }],
      }));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);
    expect(await screen.findByRole("region", { name: "人员表格，可横向滚动" })).toHaveProperty("className", "ops-table-wrap");
    expect(screen.getByLabelText<HTMLInputElement>("搜索用户名或显示名称").labels?.[0].className).toBe("ops-search-label");
    const detailButton = await screen.findByRole("button", { name: "查看详情" });
    expect(screen.getByRole("link", { name: "编辑账号" }).getAttribute("href")).toBe("/admin/portal/user/2/change/");
    await userEvent.click(detailButton);
    const dialog = await screen.findByRole("dialog", { name: "人员详情" });
    expect(within(dialog).getByRole("region", { name: "审计表格，可横向滚动" })).toHaveProperty("className", "ops-table-wrap");
    const closeButton = within(dialog).getByRole("button", { name: "关闭详情" });
    const focusable = dialog.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
    const lastFocusable = focusable[focusable.length - 1];
    closeButton.focus();
    fireEvent.keyDown(closeButton, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(lastFocusable);
    fireEvent.keyDown(lastFocusable, { key: "Tab" });
    expect(document.activeElement).toBe(closeButton);
    expect(window.location.search).toContain("user=2");

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "人员详情" })).toBeNull());
    expect(window.location.search).not.toContain("user=");
    expect(document.activeElement).toBe(detailButton);
  });

  it("人工关闭先明确确认，取消时不发送写请求", async () => {
    window.history.replaceState({}, "", "/ops/issues");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/issues/?severity=all&status=all&days=30&page=1") return Promise.resolve(json({ items: [issue], total: 1, page: 1, page_size: 20, pages: 1 }));
      if (path === "/api/ops/modules/") return Promise.resolve(json({ updated_at: "2026-09-21T08:00:00Z", items: [moduleRow] }));
      if (path === "/api/ops/issues/1/" && (init?.method ?? "GET") === "GET") return Promise.resolve(json(issue));
      if (path === "/api/csrf/") return Promise.resolve(json({ csrfToken: "test-token" }));
      if (path === "/api/ops/issues/1/" && init?.method === "POST") return Promise.resolve(json({ ...issue, status: "closed" }));
      return Promise.resolve(json({ detail: "未找到" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<App />);
    expect(await screen.findByRole("region", { name: "问题表格，可横向滚动" })).toHaveProperty("className", "ops-table-wrap");
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "问题详情" });
    await userEvent.selectOptions(within(dialog).getByLabelText("处理状态"), "closed");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存更新" }));

    expect(confirmMock).toHaveBeenCalledWith(expect.stringContaining("不表示服务已恢复"));
    expect(fetchMock.mock.calls.some(([path, init]) => path === "/api/ops/issues/1/" && (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("问题状态保存并刷新详情后仍保留成功反馈", async () => {
    window.history.replaceState({}, "", "/ops/issues");
    let detailGets = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/me/") return Promise.resolve(json(admin));
      if (path === "/api/ops/issues/?severity=all&status=all&days=30&page=1") {
        return Promise.resolve(json({ items: [issue], total: 1, page: 1, page_size: 20, pages: 1 }));
      }
      if (path === "/api/ops/modules/") return Promise.resolve(json({ updated_at: "2026-09-21T08:00:00Z", items: [moduleRow] }));
      if (path === "/api/ops/issues/1/" && (init?.method ?? "GET") === "GET") {
        detailGets += 1;
        return Promise.resolve(json(detailGets === 1 ? issue : { ...issue, status: "investigating" }));
      }
      if (path === "/api/csrf/") return Promise.resolve(json({ csrfToken: "test-token" }));
      if (path === "/api/ops/issues/1/" && init?.method === "POST") {
        return Promise.resolve(json({ ...issue, status: "investigating" }));
      }
      return Promise.resolve(json({ detail: "未找到" }, 404));
    }));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "问题详情" });
    await userEvent.selectOptions(within(dialog).getByLabelText("处理状态"), "investigating");
    await userEvent.click(within(dialog).getByRole("button", { name: "保存更新" }));

    await waitFor(() => expect(detailGets).toBe(2));
    expect(within(dialog).getByText("问题记录已更新。")).toBeTruthy();
  });
});
