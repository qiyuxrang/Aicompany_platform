import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import DepartmentModelPanel, { type DepartmentModelCode } from "./DepartmentModelPanel";

const api = vi.hoisted(() => ({ request: vi.fn() }));
const models = vi.hoisted(() => ({ getOptions: vi.fn() }));

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  apiRequest: api.request,
}));

vi.mock("../model-selection-api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../model-selection-api")>(),
  getModelRouteOptions: models.getOptions,
}));

function routeOptions(route: string) {
  return {
    route: { code: route, name: "部门助手", module: "department" },
    default_model_id: "model-public",
    models: [
      { id: "model-public", name: "企业通用模型", capabilities: { text: true, vision: false }, max_output_tokens: 4096, is_default: true, config_version: "config-v1" },
      { id: "model-writing", name: "长文模型", capabilities: { text: true, vision: false }, max_output_tokens: 8192, is_default: false, config_version: "config-v2" },
    ],
  };
}

beforeEach(() => {
  api.request.mockReset();
  models.getOptions.mockReset();
  models.getOptions.mockImplementation((route: string) => Promise.resolve(routeOptions(route)));
});

afterEach(cleanup);

describe("DepartmentModelPanel", () => {
  it.each<[DepartmentModelCode, string]>([
    ["product", "product_assistant"],
    ["hr", "hr_assistant"],
    ["cost", "engineering_assistant"],
  ])("%s 部门固定使用 %s 路由", async (moduleCode, route) => {
    render(<DepartmentModelPanel moduleCode={moduleCode} />);
    await waitFor(() => expect(models.getOptions).toHaveBeenCalled());
    expect(models.getOptions.mock.calls[0][0]).toBe(route);
  });

  it("无模型或路由未配置时禁止提交，并正确处理选择器 onChange", async () => {
    models.getOptions.mockResolvedValue({ route: { code: "product_assistant", name: "部门助手", module: "product" }, default_model_id: null, models: [] });
    render(<DepartmentModelPanel moduleCode="product" />);
    expect(await screen.findByRole("option", { name: "暂无可用模型" })).toBeTruthy();
    expect(screen.getByLabelText("问题")).toHaveProperty("maxLength", 4000);
    const submit = screen.getByRole("button", { name: "提交问题" }) as HTMLButtonElement;
    await userEvent.type(screen.getByLabelText("问题"), "整理项目风险");
    expect(submit.disabled).toBe(true);
    fireEvent.submit(screen.getByRole("form", { name: "部门模型助手提问" }));
    expect(api.request).not.toHaveBeenCalled();
  });

  it("提交固定请求体、禁用选择器并阻止重复提交", async () => {
    let resolveRequest!: (value: unknown) => void;
    api.request.mockImplementation(() => new Promise(resolve => { resolveRequest = resolve; }));
    render(<DepartmentModelPanel moduleCode="hr" />);
    await screen.findByRole("option", { name: "企业通用模型（默认）" });
    await userEvent.selectOptions(screen.getByLabelText("使用模型"), "model-writing");
    await userEvent.type(screen.getByLabelText("问题"), "  起草面试问题  ");
    const form = screen.getByRole("form", { name: "部门模型助手提问" });
    fireEvent.submit(form); fireEvent.submit(form);

    expect(api.request).toHaveBeenCalledTimes(1);
    expect(api.request).toHaveBeenCalledWith("/api/models/departments/hr/ask/", {
      method: "POST",
      body: JSON.stringify({ prompt: "起草面试问题", model_selection: { model_id: "model-writing", config_version: "config-v2" } }),
    });
    await waitFor(() => expect((screen.getByLabelText("使用模型") as HTMLSelectElement).disabled).toBe(true));
    resolveRequest({ content: "建议先核对岗位要求。" });
    expect(await screen.findByText("建议先核对岗位要求。")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("不得用于工程报价、招聘录用等自动决定");
  });

  it("失败时不回显后端敏感错误", async () => {
    api.request.mockRejectedValue(new ApiError(500, "provider api_key=secret"));
    render(<DepartmentModelPanel moduleCode="cost" />);
    await screen.findByRole("option", { name: "企业通用模型（默认）" });
    await userEvent.type(screen.getByLabelText("问题"), "分析工程事项");
    await userEvent.click(screen.getByRole("button", { name: "提交问题" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "模型助手暂时无法完成请求，请稍后重试。");
    expect(document.body.textContent).not.toContain("api_key");
    expect(document.body.textContent).not.toContain("secret");
  });

  it("管理预览只显示静态占位，不加载选项或部门请求", () => {
    render(<DepartmentModelPanel moduleCode="product" preview />);
    expect(screen.getByText("模型助手（静态预览）")).toBeTruthy();
    expect(screen.queryByLabelText("使用模型")).toBeNull();
    expect(models.getOptions).not.toHaveBeenCalled();
    expect(api.request).not.toHaveBeenCalled();
  });
});
