import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, CurrentUser, PortalModule } from "../api";
import CenterWorkspace from "./CenterWorkspace";

const apiMocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  getModule: vi.fn(),
  getModules: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  ...apiMocks,
}));

const admin: CurrentUser = {
  id: 1, username: "admin", display_name: "平台管理员",
  roles: [{ code: "platform_admin", name: "平台管理员" }],
  must_change_password: false, is_platform_admin: true,
};

const staff: CurrentUser = {
  id: 2, username: "product-user", display_name: "产品人员",
  roles: [{ code: "product", name: "产品人员" }],
  must_change_password: false, is_platform_admin: false,
};

function productModule(overrides: Partial<PortalModule> = {}): PortalModule {
  return { code: "product", name: "产品事业部", description: "产品工作台", status: "verified", enabled: true, ...overrides };
}

beforeEach(() => {
  Object.values(apiMocks).forEach(mock => mock.mockReset());
  apiMocks.getMe.mockResolvedValue(admin);
  apiMocks.getModule.mockResolvedValue(productModule());
  apiMocks.getModules.mockResolvedValue([productModule()]);
});

describe("CenterWorkspace", () => {
  it("产品导航不再包含需求准备稿", async () => {
    render(<CenterWorkspace code="product" section="overview" user={staff} businessPanel={null} />);
    await screen.findByRole("heading", { name: "产品事业部工作台" });
    const navigation = screen.getByRole("navigation", { name: "产品事业部菜单" });
    expect(navigation.textContent).not.toContain("需求准备稿");
    expect(screen.getByRole("link", { name: "专业工作台" })).toBeTruthy();
  });

  it("模块授权失败时不挂载产品内容", async () => {
    apiMocks.getModule.mockRejectedValueOnce(new ApiError(403, "access revoked"));
    const { container } = render(<CenterWorkspace code="product" section="overview" user={staff} businessPanel={null} />);
    await screen.findByRole("alert");
    expect(container.querySelector(".center-layout")).toBeNull();
  });

  it.each([{ enabled: false }, { status: "disabled" as const }])("停用模块不展示工作区 %j", async override => {
    apiMocks.getModule.mockResolvedValueOnce(productModule(override));
    const { container } = render(<CenterWorkspace code="product" section="overview" user={staff} businessPanel={null} />);
    await screen.findByRole("alert");
    expect(container.querySelector(".center-layout")).toBeNull();
  });

  it("非管理员不能使用管理预览", async () => {
    apiMocks.getMe.mockResolvedValueOnce(staff);
    const { container } = render(<CenterWorkspace code="product" section="overview" user={admin} preview businessPanel={null} />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(container.querySelector(".center-layout")).toBeNull();
    expect(apiMocks.getModule).not.toHaveBeenCalled();
  });

});
