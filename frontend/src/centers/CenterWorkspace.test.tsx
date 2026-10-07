import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, CurrentUser, PortalModule } from "../api";
import CenterWorkspace from "./CenterWorkspace";
import type { CenterCode } from "./config";

vi.mock("./ManagerWorkspace", () => ({ default: ({ section }: { section: string }) => <div>经营页面：{section}</div> }));
vi.mock("./AgentWorkspace", () => ({ default: ({ department }: { department: string }) => <h2>{department} 智能助手内容</h2> }));
vi.mock("./BusinessLedgerWorkspace", () => ({ default: ({ onlyDepartment }: { onlyDepartment: string }) => <h2>{onlyDepartment} 部门限定台账</h2> }));
vi.mock("./ProductWorkspace", async importOriginal => {
  const { default: ProductWorkspace } = await importOriginal<typeof import("./ProductWorkspace")>();
  return { default: ({ section }: { section: string }) => section === "opportunities"
    ? <section><h2>全国商机看板</h2></section> : <ProductWorkspace section={section}/> };
});
vi.mock("../ModelSelector", () => ({ default: ({ route }: { route: string }) => <div data-testid="department-model-selector">{route}</div> }));

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
  it("共享侧栏只列后端授权且启用的工作台", async () => {
    apiMocks.getModules.mockResolvedValue([productModule(), { code: 'cost', name: '工程部', enabled: false, status: 'disabled' }]);
    render(<CenterWorkspace code="product" section="overview" user={staff} businessPanel={null}/>);
    const switcher = await screen.findByRole('navigation', { name: '已授权工作台' });
    expect(switcher.textContent).toContain('产品事业部');
    expect(switcher.textContent).not.toContain('工程部');
    expect(switcher.textContent).not.toContain('平台运维');
    expect(screen.getByRole('complementary').className).toBe('workspace-sidebar');
  });

  it("只有转正授权的用人经理默认进入转正并不展示招聘菜单", async () => {
    apiMocks.getModule.mockResolvedValue({ code: 'hr', name: '人事部门', enabled: true, status: 'verified' });
    apiMocks.getModules.mockResolvedValue([{ code: 'hr', name: '人事部门', enabled: true, status: 'verified' }]);
    render(<CenterWorkspace code="hr" section="overview" user={staff} businessPanel={null}/>);
    const nav = await screen.findByRole('navigation', { name: '人事部门菜单' });
    expect(nav.textContent).toBe('转正工作流');
    expect(screen.queryByText('当前账号没有 HR 岗位、JD、招聘渠道或简历权限。')).toBeNull();
    expect(screen.getByRole('heading', { name: '转正工作流', level: 1 })).toBeTruthy();
  });

  it("商机页只保留内部紧凑标题且不重复显示父级介绍", async () => {
    const { container } = render(<CenterWorkspace code="product" section="opportunities" user={staff} businessPanel={null} />);
    expect(await screen.findByRole("heading", { name: "全国商机看板" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { name: "全国商机看板" })).toHaveLength(1);
    expect(container.querySelector(".center-page-head")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "当前位置" })).toBeNull();
  });

  it("只在产品知识库页面启用铺满布局", async () => {
    const { container } = render(<CenterWorkspace code="product" section="knowledge" user={staff} businessPanel={null} />);
    await waitFor(() => expect(container.querySelector(".center-main-knowledge")).not.toBeNull());
  });

  it("资料页保留满高布局且各部门不再显示接入说明页脚", async () => {
    const product = render(<CenterWorkspace code="product" section="sources" user={staff} businessPanel={null} />);
    await waitFor(() => expect(product.container.querySelector(".center-main-materials")).not.toBeNull());
    expect(product.container.querySelector(".center-footer")).toBeNull();
    product.unmount();

    const cost = { code: "cost", name: "工程部", description: "成本草稿", status: "verified", enabled: true } as PortalModule;
    apiMocks.getModule.mockResolvedValue(cost);
    apiMocks.getModules.mockResolvedValue([cost]);
    const engineeringUser: CurrentUser = { ...staff, roles: [{ code: "engineering", name: "工程人员" }] };
    const engineering = render(<CenterWorkspace code="cost" section="overview" user={engineeringUser} businessPanel={null} />);
    await screen.findByRole("heading", { name: "工作概览", level: 1 });
    expect(engineering.container.querySelector(".center-footer")).toBeNull();
    expect(screen.queryByText("统一入口 · 独立业务 · 明确授权")).toBeNull();
  });

  it("产品导航不显示模板和需求准备稿", async () => {
    const { container } = render(<CenterWorkspace code="product" section="overview" user={staff} businessPanel={null} />);
    await screen.findByRole("heading", { name: "产品事业部工作台" });
    expect(container.querySelector(".center-main-knowledge")).toBeNull();
    const navigation = screen.getByRole("navigation", { name: "产品事业部菜单" });
    expect(navigation.textContent).not.toContain("模板");
    expect(navigation.textContent).not.toContain("需求准备稿");
    expect(screen.getByRole("link", { name: "全国商机看板" }).getAttribute("href")).toBe("/centers/product/opportunities");
    expect(screen.getByRole("link", { name: "售前数据录入" }).getAttribute("href")).toBe("/centers/product/presales");
    expect(screen.getByRole("link", { name: "生成文档" })).toBeTruthy();
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

  it("非总经理的台账授权人员进入经营模块时只展示录入工作台", async () => {
    const ledgerStaff: CurrentUser = { ...staff, id: 3, username: "ledger-user", roles: [{ code: "engineering", name: "工程人员" }] };
    const business = { code: "business", name: "经营工作台", description: "台账录入", status: "verified", enabled: true } as PortalModule;
    apiMocks.getModule.mockResolvedValue(business);
    apiMocks.getModules.mockResolvedValue([business]);
    render(<CenterWorkspace code="business" section="overview" user={ledgerStaff} businessPanel={null} />);
    expect(await screen.findByText("经营页面：ledgers")).toBeTruthy();
    const navigation = screen.getByRole("navigation", { name: "总经理工作台菜单" });
    expect(navigation.textContent).toContain("台账录入");
    expect(navigation.textContent).not.toContain("财务部看板");
  });

  it("总经理不显示台账录入入口且直接访问录入地址回到只读总览", async () => {
    const manager: CurrentUser = { ...staff, id: 4, username: "manager", roles: [{ code: "general_manager", name: "总经理" }] };
    const business = { code: "business", name: "总经理工作台", description: "只读看板", status: "verified", enabled: true } as PortalModule;
    apiMocks.getModule.mockResolvedValue(business);
    apiMocks.getModules.mockResolvedValue([business]);
    render(<CenterWorkspace code="business" section="ledgers" user={manager} businessPanel={null} />);
    expect(await screen.findByText("经营页面：overview")).toBeTruthy();
    const navigation = screen.getByRole("navigation", { name: "总经理工作台菜单" });
    expect(navigation.textContent).not.toContain("台账录入");
  });

  it.each<{ code: Exclude<CenterCode, "business">; role: string; menu: string; overview: string }>([
    { code: "product", role: "product", menu: "产品事业部菜单", overview: "产品事业部工作台" },
    { code: "hr", role: "hr", menu: "人事部门菜单", overview: "工作台" },
    { code: "cost", role: "engineering", menu: "工程部菜单", overview: "工作概览" },
  ])("$code 助手使用现有壳，工程保持原兼容入口", async ({ code, role, menu, overview }) => {
    const module = { code, name: code, description: "部门工作台", status: "verified", enabled: true } as PortalModule;
    apiMocks.getModule.mockResolvedValue(module);
    apiMocks.getModules.mockResolvedValue([module]);
    const departmentUser: CurrentUser = { ...staff, roles: [{ code: role, name: role }] };
    window.history.replaceState({}, "", `/centers/${code}/assistant`);
    render(<CenterWorkspace code={code} section="assistant" user={departmentUser} businessPanel={null} />);

    if (code === 'cost') {
      expect(await screen.findByRole("heading", { name: overview, level: 1 })).toBeTruthy();
      expect(window.location.pathname).toBe(`/centers/${code}`);
      expect(screen.getByRole("navigation", { name: menu }).textContent).not.toContain("智能助手");
    } else {
      expect(await screen.findByRole('heading', { name: `${code} 智能助手内容` })).toBeTruthy();
      expect(window.location.pathname).toBe(`/centers/${code}/assistant`);
      expect(screen.getByRole('link', { name: '智能助手' }).getAttribute('aria-current')).toBe('page');
    }
    expect(screen.queryByTestId("department-model-selector")).toBeNull();
    window.history.replaceState({}, "", "/");
  });

  it('财务入口复用经营模块授权并限定财务台账', async () => {
    const financialUser = { ...staff, roles: [{ code: 'finance', name: '财务' }] };
    apiMocks.getModule.mockResolvedValue({ code: 'business', enabled: true, status: 'verified' });
    apiMocks.getModules.mockResolvedValue([{ code: 'business', enabled: true, status: 'verified' }]);
    render(<CenterWorkspace code="finance" section="overview" user={financialUser} businessPanel={null}/>);
    expect(await screen.findByText('finance 部门限定台账')).toBeTruthy();
    expect(apiMocks.getModule).toHaveBeenCalledWith('business');
    expect(screen.getByRole('link', { name: '智能助手' }).getAttribute('href')).toBe('/centers/finance/assistant');
    expect(screen.queryByText('财务部看板')).toBeNull();
  });

  it('GM 直接打开财务填写人地址仍被拒绝', async () => {
    const manager = { ...staff, roles: [{ code: 'general_manager', name: '总经理' }] };
    render(<CenterWorkspace code="finance" section="overview" user={manager} businessPanel={null}/>);
    expect((await screen.findByRole('alert')).textContent).toContain('当前账号未获得财务填写人入口权限');
    expect(screen.queryByText('finance 部门限定台账')).toBeNull();
  });

  it("旧模型助手管理预览回到概览且不读取模块或模型选项", async () => {
    window.history.replaceState({}, "", "/preview/cost/assistant");
    render(<CenterWorkspace code="cost" section="assistant" user={admin} preview businessPanel={null} />);
    expect(await screen.findByRole("heading", { name: "工作概览", level: 1 })).toBeTruthy();
    expect(window.location.pathname).toBe("/preview/cost");
    expect(screen.getByRole("navigation", { name: "工程部菜单" }).textContent).not.toContain("模型助手");
    expect(screen.queryByTestId("department-model-selector")).toBeNull();
    expect(apiMocks.getModule).not.toHaveBeenCalled();
    expect(apiMocks.getModules).not.toHaveBeenCalled();
    window.history.replaceState({}, "", "/");
  });
});
