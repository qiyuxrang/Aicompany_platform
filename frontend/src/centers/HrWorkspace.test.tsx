import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HrWorkspace from "./HrWorkspace";

const hrUser = { id: 1, username: "hr", display_name: "HR", roles: [{ code: "hr", name: "人事" }], must_change_password: false, is_platform_admin: false };

const hrMocks = vi.hoisted(() => ({
  listJobs: vi.fn(), createJob: vi.fn(), updateJob: vi.fn(), generateJob: vi.fn(), reviseJob: vi.fn(), confirmJob: vi.fn(),
  listProbations: vi.fn(), createProbation: vi.fn(), transitionProbation: vi.fn(),
}));
vi.mock("../hr/hr-api", () => hrMocks);

beforeEach(() => {
  window.history.replaceState({}, "", "/centers/hr");
  Object.values(hrMocks).forEach(mock => mock.mockReset());
  hrMocks.listJobs.mockResolvedValue([]);
  hrMocks.listProbations.mockResolvedValue([]);
});
afterEach(() => { cleanup(); window.history.replaceState({}, "", "/"); });

describe("人事工作台边界", () => {
  it("概览区分持久流程与阻断入口", () => {
    render(<HrWorkspace section="overview" user={hrUser} />);
    expect(screen.getByRole("heading", { name: "人事工作台" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /岗位需求与人才画像/ }).getAttribute("href")).toBe("/centers/hr/profile");
    expect(screen.getByRole("link", { name: /转正工作流/ }).getAttribute("href")).toBe("/centers/hr/probation");
    expect(screen.getByText(/D-01 与 D-04 未批准/)).toBeTruthy();
    expect(hrMocks.listJobs).not.toHaveBeenCalled();
  });

  it("岗位页面读取服务端任务", async () => {
    render(<HrWorkspace section="profile" user={hrUser} />);
    expect(await screen.findByText("暂无岗位任务")).toBeTruthy();
    expect(hrMocks.listJobs).toHaveBeenCalledTimes(1);
  });

  it("简历页只显示 D-01 D-04 阻断且绝不创建文件输入", () => {
    const { container } = render(<HrWorkspace section="resumes" user={hrUser} />);
    expect(screen.getByText("D-01 / D-04 待批准")).toBeTruthy();
    expect(screen.getByText(/不接收、不读取、不上传简历/)).toBeTruthy();
    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(hrMocks.listJobs).not.toHaveBeenCalled();
    expect(hrMocks.listProbations).not.toHaveBeenCalled();
  });

  it("招聘平台版本为只读待接入页", () => {
    render(<HrWorkspace section="channels" user={hrUser} />);
    expect(screen.getByText("外部接入待确认")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it.each(["profile", "job", "probation"])("管理预览 %s 不请求业务 API", section => {
    window.history.replaceState({}, "", `/preview/hr/${section}`);
    render(<HrWorkspace section={section} user={hrUser} />);
    expect(screen.getByText("仅页面预览")).toBeTruthy();
    expect(hrMocks.listJobs).not.toHaveBeenCalled();
    expect(hrMocks.listProbations).not.toHaveBeenCalled();
  });

  it("无 HR 角色的用人经理只可进入转正且不显示创建表单", async () => {
    const manager = { ...hrUser, roles: [{ code: "staff", name: "员工" }] };
    render(<HrWorkspace section="probation" user={manager} />);
    expect(await screen.findByRole("heading", { name: "转正工作流" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "创建转正事项" })).toBeNull();
    expect(hrMocks.listProbations).toHaveBeenCalledOnce();
    cleanup();
    render(<HrWorkspace section="job" user={manager} />);
    expect(screen.getByRole("alert").textContent).toContain("没有 HR 岗位、JD");
    expect(hrMocks.listJobs).not.toHaveBeenCalled();
  });
});
