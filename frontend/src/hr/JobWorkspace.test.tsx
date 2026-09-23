import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import JobWorkspace from "./JobWorkspace";
import { confirmJob, createJob, generateJob, JobInput, JobTask, listJobs, reviseJob, updateJob } from "./hr-api";

const apiMocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../api", async importOriginal => ({ ...await importOriginal<typeof import("../api")>(), apiRequest: apiMocks.apiRequest }));

const input: JobInput = { title: "AI应用工程师", department: "产品部", objective: "交付业务应用", responsibilities: "开发与维护", requirements: "Python基础" };
const task = (overrides: Partial<JobTask> = {}): JobTask => ({
  id: "11111111-1111-1111-1111-111111111111", owner_id: 1, ...input, state: "draft", version: 1, input_version: 1,
  missing_fields: [], current_revision: null, official_revision: null, current_revision_stale: false, revisions: [], updated_at: "2026-09-22T00:00:00Z", ...overrides,
});
const revision = (id = "rev-1", body = "旧正文", version = 1) => ({ id, version, input_version: 1, kind: "manual" as const, body, parent_id: null, created_by_id: 1, confirmed_by_id: null, confirmed_at: null, created_at: "2026-09-22T00:00:00Z" });

beforeEach(() => { apiMocks.apiRequest.mockReset(); window.history.replaceState({}, "", "/centers/hr/job"); });
afterEach(() => { cleanup(); window.history.replaceState({}, "", "/"); });

describe("HR JD API契约", () => {
  it("使用后端既有列表、保存、生成、修订和确认路径", async () => {
    const current = task({ current_revision: { id: "rev-1", version: 1, input_version: 1, kind: "generated", body: "JD", parent_id: null, created_by_id: 1, confirmed_by_id: null, confirmed_at: null, created_at: "2026-09-22T00:00:00Z" } });
    apiMocks.apiRequest.mockResolvedValue(current);
    await listJobs(); await createJob(input); await updateJob(current, input); await generateJob(current); await reviseJob(current, "人工修订"); await confirmJob(current, "rev-1");
    expect(apiMocks.apiRequest.mock.calls.map(call => call[0])).toEqual([
      "/api/hr/jobs/", "/api/hr/jobs/", `/api/hr/jobs/${current.id}/`, `/api/hr/jobs/${current.id}/generate/`, `/api/hr/jobs/${current.id}/revisions/`, `/api/hr/jobs/${current.id}/confirm/`,
    ]);
    expect(JSON.parse(apiMocks.apiRequest.mock.calls[2][1].body)).toMatchObject({ expected_version: 1, title: input.title });
    expect(JSON.parse(apiMocks.apiRequest.mock.calls[5][1].body)).toEqual({ expected_version: 1, revision_id: "rev-1" });
  });
});

describe("JD工作区", () => {
  it("深链准确打开第二个岗位，且无效目标不回退第一项", async () => {
    const first = task({ title: "第一岗位" });
    const second = task({ id: "22222222-2222-2222-2222-222222222222", title: "第二岗位" });
    apiMocks.apiRequest.mockResolvedValue([first, second]);
    window.history.replaceState({}, "", `/centers/hr/job?task=${second.id}`);
    render(<JobWorkspace mode="job" />);
    expect(await screen.findByDisplayValue("第二岗位")).toBeTruthy();
    cleanup();
    window.history.replaceState({}, "", "/centers/hr/job?task=missing");
    render(<JobWorkspace mode="job" />);
    expect((await screen.findByRole("alert")).textContent).toContain("不存在或当前账号无权访问");
    expect(screen.queryByDisplayValue("第一岗位")).toBeNull();
    expect(screen.queryByRole("heading", { name: "创建岗位需求" })).toBeNull();
  });

  it("展示服务端缺项并阻止生成", async () => {
    apiMocks.apiRequest.mockResolvedValue([task({ title: "", missing_fields: ["title", "responsibilities"] })]);
    render(<JobWorkspace mode="job" />);
    expect(await screen.findByText("缺项：岗位名称、岗位职责")).toBeTruthy();
    expect((screen.getByRole("button", { name: "生成确定性 JD 草稿" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("后端拒绝合法按钮时显示真实错误", async () => {
    apiMocks.apiRequest.mockImplementation((path?: string) => !path || path === "/api/hr/jobs/" ? Promise.resolve([task()]) : Promise.reject(new ApiError(409, "数据已更新，请刷新后重试。", "version_conflict")));
    render(<JobWorkspace mode="job" />);
    const button = await screen.findByRole("button", { name: "生成确定性 JD 草稿" });
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("数据已更新"));
  });

  it("未保存正文阻止确认、保存需求与切换；保存修订后确认精确新版本", async () => {
    const first = task({ current_revision: revision() });
    const second = task({ id: "22222222-2222-2222-2222-222222222222", title: "第二岗位", current_revision: revision("rev-other", "第二正文") });
    const revised = task({ version: 2, current_revision: revision("rev-2", "新正文", 2), revisions: [revision("rev-2", "新正文", 2)] });
    apiMocks.apiRequest.mockImplementation((path?: string, init?: RequestInit) => {
      if (path === "/api/hr/jobs/") return Promise.resolve([first, second]);
      if (path?.endsWith("/revisions/")) return Promise.resolve(revised);
      if (path?.endsWith("/confirm/")) return Promise.resolve({ ...revised, state: "confirmed" });
      return Promise.resolve(first);
    });
    render(<JobWorkspace mode="job" />);
    const body = await screen.findByLabelText("JD正文");
    fireEvent.change(body, { target: { value: "新正文" } });
    expect((screen.getByRole("button", { name: "确认当前版本为正式 JD" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "保存岗位需求" }));
    expect(screen.getByRole("alert").textContent).toContain("未保存修改");
    expect(apiMocks.apiRequest.mock.calls.some(call => call[1]?.method === "PATCH")).toBe(false);
    fireEvent.change(screen.getByLabelText("选择岗位任务"), { target: { value: second.id } });
    expect((screen.getByLabelText("选择岗位任务") as HTMLSelectElement).value).toBe(first.id);
    fireEvent.click(screen.getByRole("button", { name: "保存人工修订" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "确认当前版本为正式 JD" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "确认当前版本为正式 JD" }));
    await waitFor(() => expect(apiMocks.apiRequest.mock.calls.some(call => call[0]?.endsWith("/confirm/"))).toBe(true));
    const confirmCall = apiMocks.apiRequest.mock.calls.find(call => call[0]?.endsWith("/confirm/"));
    expect(JSON.parse(confirmCall?.[1].body)).toEqual({ expected_version: 2, revision_id: "rev-2" });
  });

  it("已有任务时仍可进入新建并创建第二个岗位任务", async () => {
    const existing = task();
    const created = task({ id: "33333333-3333-3333-3333-333333333333", title: "第二岗位" });
    apiMocks.apiRequest.mockImplementation((path?: string, init?: RequestInit) => path === "/api/hr/jobs/" && init?.method === "POST" ? Promise.resolve(created) : Promise.resolve([existing]));
    render(<JobWorkspace mode="job" />);
    await screen.findByDisplayValue(existing.title);
    fireEvent.click(screen.getByRole("button", { name: "新建岗位任务" }));
    fireEvent.change(screen.getByLabelText("岗位名称"), { target: { value: "第二岗位" } });
    fireEvent.click(screen.getByRole("button", { name: "创建岗位需求" }));
    await waitFor(() => expect(apiMocks.apiRequest.mock.calls.some(call => call[0] === "/api/hr/jobs/" && call[1]?.method === "POST")).toBe(true));
    expect(await screen.findByDisplayValue("第二岗位")).toBeTruthy();
  });
});
