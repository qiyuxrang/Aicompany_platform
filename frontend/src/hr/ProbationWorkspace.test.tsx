import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import ProbationWorkspace from "./ProbationWorkspace";
import { createProbation, listProbations, ProbationCase, transitionProbation } from "./hr-api";

const apiMocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("../api", async importOriginal => ({ ...await importOriginal<typeof import("../api")>(), apiRequest: apiMocks.apiRequest }));

const item = (overrides: Partial<ProbationCase> = {}): ProbationCase => ({
  id: "22222222-2222-2222-2222-222222222222", owner_id: 1, assigned_manager_id: 2, employee_name: "张三", position: "工程师",
  materials: ["试用期总结"], notes: "", manager_opinion: "", hr_conclusion: "", state: "draft", version: 1,
  actions: ["start_collecting"], assistant_enabled: false, assistant_mode: "manual", assistant_reason: "model_not_authorized",
  updated_at: "2026-09-22T00:00:00Z", transitions: [], revisions: [], ...overrides,
});

beforeEach(() => { apiMocks.apiRequest.mockReset(); window.history.replaceState({}, "", "/centers/hr/probation"); });
afterEach(() => { cleanup(); window.history.replaceState({}, "", "/"); });

describe("HR转正API契约", () => {
  it("使用案件列表、创建和合法迁移路径", async () => {
    const current = item();
    apiMocks.apiRequest.mockResolvedValue(current);
    await listProbations();
    await createProbation({ employee_name: "张三", position: "工程师", assigned_manager_id: 2, materials: [], notes: "" });
    await transitionProbation(current, "start_collecting", "");
    expect(apiMocks.apiRequest.mock.calls.map(call => call[0])).toEqual([
      "/api/hr/probations/", "/api/hr/probations/", `/api/hr/probations/${current.id}/transition/`,
    ]);
    expect(JSON.parse(apiMocks.apiRequest.mock.calls[2][1].body)).toEqual({ expected_version: 1, action: "start_collecting", comment: "" });
  });
});

describe("转正工作区", () => {
  it("深链准确打开第二个案例，无效或越权目标不回退第一项", async () => {
    const first = item({ employee_name: "第一员工" });
    const second = item({ id: "33333333-3333-3333-3333-333333333333", employee_name: "第二员工" });
    apiMocks.apiRequest.mockResolvedValue([first, second]);
    window.history.replaceState({}, "", `/centers/hr/probation?case=${second.id}`);
    render(<ProbationWorkspace />);
    expect(await screen.findByRole("heading", { name: /第二员工/ })).toBeTruthy();
    cleanup();
    window.history.replaceState({}, "", "/centers/hr/probation?case=missing");
    render(<ProbationWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toContain("不存在或当前账号无权访问");
    expect(screen.queryByRole("heading", { name: /第一员工/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "创建转正事项" })).toBeNull();
  });

  it("只展示后端actions允许的下一步并提交版本合同", async () => {
    apiMocks.apiRequest.mockImplementation((path?: string) => path?.endsWith("/transition/")
      ? Promise.resolve(item({ state: "collecting", version: 2, actions: ["submit_to_manager"] }))
      : Promise.resolve([item()]));
    render(<ProbationWorkspace />);
    const start = await screen.findByRole("button", { name: "开始收集材料" });
    expect(screen.queryByRole("button", { name: "HR确认并归档" })).toBeNull();
    fireEvent.click(start);
    expect(await screen.findByRole("button", { name: "提交主管审批" })).toBeTruthy();
    const transitionCall = apiMocks.apiRequest.mock.calls.find(call => String(call[0]).endsWith("/transition/"));
    expect(JSON.parse(transitionCall?.[1].body)).toEqual({ expected_version: 1, action: "start_collecting", comment: "" });
  });

  it("非法迁移响应显示错误且不伪造新状态", async () => {
    apiMocks.apiRequest.mockImplementation((path?: string) => path?.endsWith("/transition/")
      ? Promise.reject(new ApiError(409, "当前状态不能执行此操作。", "invalid_transition"))
      : Promise.resolve([item()]));
    render(<ProbationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "开始收集材料" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("当前状态不能执行"));
    expect(screen.getByText(/状态：草稿/)).toBeTruthy();
  });

  it("刷新后展示持久化人工降级原因和修改历史", async () => {
    apiMocks.apiRequest.mockResolvedValue([item({ state: "archived", actions: [], revisions: [{
      before: { notes: "旧" }, after: { notes: "新" }, changed_fields: ["notes", "materials"], actor_id: 7, case_version: 5, created_at: "2026-09-23T08:00:00Z",
    }] })]);
    render(<ProbationWorkspace />);
    expect(await screen.findByText(/原因：model_not_authorized/)).toBeTruthy();
    expect(screen.getByLabelText("转正修改历史").textContent).toContain("案例版本 5");
    expect(screen.getByLabelText("转正修改历史").textContent).toContain("notes、materials");
    expect(screen.getByLabelText("转正修改历史").textContent).toContain("修改人 7");
  });
});
