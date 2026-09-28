import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiEventStream,
  apiRequest,
  clearApiSession,
  changePassword,
  getModule,
  launchModule,
  unauthorizedEvent,
  getModules,
  login,
  logout,
  opsPermissionRevokedEvent,
  passwordChangeRequiredEvent,
} from "./api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API client", () => {
  it("incrementally parses fragmented answer frames and requires a done event", async () => {
    const encoder = new TextEncoder();
    const chunks = ['data: {"delta":"网', '络"}\n\ndata: {"delta":"安全"}\n\n', 'data: {"done":{"id":"verified"}}\n\n'];
    const stream = new ReadableStream({ start(controller) {
      for (const part of chunks) controller.enqueue(encoder.encode(part));
      controller.close();
    } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ csrfToken: "token" }))
      .mockResolvedValueOnce(new Response(stream, { headers: { "Content-Type": "text/event-stream" } })));
    const deltas: string[] = [];
    const result = await apiEventStream<{ id: string }>("/api/product/knowledge/conversations/id/", { stream: true },
      new AbortController().signal, delta => deltas.push(delta));
    expect(deltas).toEqual(["网络", "安全"]);
    expect(result).toEqual({ id: "verified" });
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get("X-CSRFToken")).toBe("token");
  });

  it("rejects truncated streaming responses instead of treating drafts as saved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ csrfToken: "token" }))
      .mockResolvedValueOnce(new Response('data: {"delta":"未完成"}\n\n', { headers: { "Content-Type": "text/event-stream" } })));
    await expect(apiEventStream("/api/product/knowledge/conversations/id/", { stream: true },
      new AbortController().signal, () => undefined)).rejects.toMatchObject({ code: "incomplete_stream" });
  });
  it("上传资料使用浏览器的 multipart 边界并保留 CSRF", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrfToken: "upload-token" }))
      .mockResolvedValueOnce(json({ saved: true }));
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.append("file", new File(["合成资料"], "test.txt", { type: "text/plain" }));
    await apiRequest("/product/tasks/test/sources/", { method: "POST", body });
    const options = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(options.headers).has("Content-Type")).toBe(false);
    expect(new Headers(options.headers).get("X-CSRFToken")).toBe("upload-token");
    expect(options.body).toBe(body);
  });

  beforeEach(() => {
    clearApiSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("为不安全请求携带 CSRF，并在登录后刷新令牌", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrfToken: "before-login" }))
      .mockResolvedValueOnce(json({
        id: 1,
        username: "tester",
        display_name: "测试用户",
        roles: [],
        must_change_password: false,
        is_platform_admin: false,
      }))
      .mockResolvedValueOnce(json({ csrfToken: "after-login" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await login("tester", "secret");
    await logout();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    const loginInit = fetchMock.mock.calls[1][1] as RequestInit;
    const logoutInit = fetchMock.mock.calls[3][1] as RequestInit;
    expect(new Headers(loginInit.headers).get("X-CSRFToken")).toBe("before-login");
    expect(new Headers(logoutInit.headers).get("X-CSRFToken")).toBe("after-login");
    expect(loginInit.credentials).toBe("same-origin");
  });

  it("将首次改密限制广播给路由层", async () => {
    const listener = vi.fn();
    window.addEventListener(passwordChangeRequiredEvent, listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({
      detail: "请先修改密码",
      code: "password_change_required",
    }, 403)));

    await expect(getModules()).rejects.toMatchObject({ status: 403, code: "password_change_required" });
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(passwordChangeRequiredEvent, listener);
  });

  it.each(["password", "logout", "csrf"])("%s 的 401 会通知会话过期", async (endpoint) => {
    const listener = vi.fn();
    window.addEventListener(unauthorizedEvent, listener);
    const fetchMock = vi.fn();
    if (endpoint !== "csrf") fetchMock.mockResolvedValueOnce(json({ csrfToken: "test-token" }));
    fetchMock.mockResolvedValueOnce(json({ detail: "会话已失效" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    try {
      await expect(endpoint === "password" ? changePassword("old", "new") : logout())
        .rejects.toMatchObject({ status: 401 });
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(unauthorizedEvent, listener);
    }
  });

  it("仅运维接口的 ops_forbidden 广播撤权事件", async () => {
    const listener = vi.fn();
    window.addEventListener(opsPermissionRevokedEvent, listener);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(json({ detail: "运维权限已撤销", code: "ops_forbidden" }, 403))
      .mockResolvedValueOnce(json({ detail: "普通业务拒绝", code: "ops_forbidden" }, 403)));
    try {
      await expect(apiRequest("/api/ops/modules/")).rejects.toMatchObject({ status: 403, code: "ops_forbidden" });
      await expect(apiRequest("/api/modules/")).rejects.toMatchObject({ status: 403, code: "ops_forbidden" });
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(opsPermissionRevokedEvent, listener);
    }
  });

  it("普通 CSRF 403 不广播运维撤权事件", async () => {
    const listener = vi.fn();
    window.addEventListener(opsPermissionRevokedEvent, listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "CSRF 校验失败", code: "csrf_failed" }, 403)));
    try {
      await expect(logout()).rejects.toMatchObject({ status: 403, code: "csrf_failed" });
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(opsPermissionRevokedEvent, listener);
    }
  });

  it("拒绝错误模块参数，不请求后端或旧站", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getModule("../admin")).rejects.toMatchObject({ status: 400 });
    await expect(launchModule("/")).rejects.toMatchObject({ status: 400 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("未知模块状态报格式错误而不是渲染崩溃", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json([
      { code: "product", name: "产品", description: "", status: "unexpected", enabled: true },
    ])));
    await expect(getModules()).rejects.toMatchObject({ status: 502 });
  });

  it.each(["javascript:alert(1)", "https://user:password@example.test", "http://["])("拒绝不安全或无效跳转 %s", async (url) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrfToken: "test-token" }))
      .mockResolvedValueOnce(json({ url }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(launchModule("business")).rejects.toMatchObject({ status: 502 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([403, 409, 503])("保留 launch 的 %i 错误且不探测旧系统", async (status) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrfToken: "test-token" }))
      .mockResolvedValueOnce(json({ detail: "入口不可用" }, status));
    vi.stubGlobal("fetch", fetchMock);
    await expect(launchModule("product")).rejects.toMatchObject({ status, message: "入口不可用" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/modules/product/launch/");
  });
});
