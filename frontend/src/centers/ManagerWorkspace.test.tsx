import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, PortalModule } from "../api";
import ManagerWorkspace from "./ManagerWorkspace";

const apiMocks = vi.hoisted(() => ({
  launchModule: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  ...apiMocks,
}));

function businessModule(status: PortalModule["status"] = "verified"): PortalModule {
  return {
    code: "business",
    name: "总经理工作台",
    description: "经营入口",
    status,
    enabled: true,
  };
}

function pendingLaunch() {
  let resolve!: (target: string) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<string>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  apiMocks.launchModule.mockReturnValueOnce(promise);
  return { resolve, reject };
}

const assignLocation = vi.fn();

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  apiMocks.launchModule.mockReset();
  assignLocation.mockReset();
  const location = { ...window.location, assign: assignLocation };
  vi.stubGlobal("window", new Proxy(window, {
    get(target, property) {
      return property === "location" ? location : Reflect.get(target, property, target);
    },
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("ManagerWorkspace boundaries", () => {
  it.each(["overview", "projects", "ledgers"])("%s 预览不挂载业务面板或调用旧系统入口", async (section) => {
    const BusinessPanel = vi.fn(() => <div>受保护的经营数据</div>);

    render(<ManagerWorkspace section={section} preview module={businessModule()} businessPanel={<BusinessPanel />} />);

    expect(BusinessPanel).not.toHaveBeenCalled();
    expect(screen.queryByText("受保护的经营数据")).toBeNull();
    for (const button of screen.queryAllByRole("button")) await userEvent.click(button);
    expect(apiMocks.launchModule).not.toHaveBeenCalled();
  });

  it("pending 项目页不挂载查询面板或刷新动作", () => {
    const BusinessPanel = vi.fn(() => <div>查询结果</div>);

    render(<ManagerWorkspace section="projects" preview={false} module={businessModule("pending")} businessPanel={<BusinessPanel />} />);

    expect(screen.getByRole("heading", { name: "只读查询尚未开放" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "刷新授权项目" })).toBeNull();
    expect(BusinessPanel).not.toHaveBeenCalled();
    expect(screen.queryByText("查询结果")).toBeNull();
  });

  it("旧系统导航失败时展示后端错误反馈", async () => {
    apiMocks.launchModule.mockRejectedValue(new ApiError(502, "经营系统入口暂不可用"));

    render(<ManagerWorkspace section="ledgers" preview={false} module={businessModule("navigation")} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));

    expect((await screen.findByRole("alert")).textContent).toContain("经营系统入口暂不可用");
    expect(apiMocks.launchModule).toHaveBeenCalledTimes(1);
    expect(apiMocks.launchModule).toHaveBeenCalledWith("business");
  });

  it("刷新授权项目通过重新挂载业务面板发起新查询", async () => {
    const BusinessPanel = vi.fn(() => <div>授权项目查询</div>);

    render(<ManagerWorkspace section="projects" preview={false} module={businessModule()} businessPanel={<BusinessPanel />} />);

    expect(screen.getByText("授权项目查询")).toBeTruthy();
    expect(BusinessPanel).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "刷新授权项目" }));
    expect(BusinessPanel).toHaveBeenCalledTimes(2);
  });

  it("当前页面的有效启动响应仍正常跳转", async () => {
    const request = pendingLaunch();
    render(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));
    expect((screen.getByRole("button", { name: "正在核验入口…" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => request.resolve("https://legacy.example.test/ledger"));

    expect(assignLocation).toHaveBeenCalledExactlyOnceWith("https://legacy.example.test/ledger");
  });

  it.each(["resolve", "reject"] as const)("卸载后忽略迟到的 %s 结果", async (outcome) => {
    const request = pendingLaunch();
    const view = render(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));
    view.unmount();

    await act(async () => {
      if (outcome === "resolve") request.resolve("https://legacy.example.test/stale");
      else request.reject(new ApiError(502, "旧请求错误"));
    });

    expect(assignLocation).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(["resolve", "reject"] as const)("section变化使旧 %s 结果失效且不干扰新启动请求", async (outcome) => {
    const previous = pendingLaunch();
    const view = render(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));
    view.rerender(<ManagerWorkspace section="ledgers" preview={false} module={businessModule()} businessPanel={null} />);
    expect((screen.getByRole("button", { name: "进入原台账看板" }) as HTMLButtonElement).disabled).toBe(false);
    const current = pendingLaunch();
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));

    await act(async () => {
      if (outcome === "resolve") previous.resolve("https://legacy.example.test/stale");
      else previous.reject(new ApiError(502, "旧页面错误"));
    });

    expect(assignLocation).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("button", { name: "正在核验入口…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => current.resolve("https://legacy.example.test/current"));
    expect(assignLocation).toHaveBeenCalledExactlyOnceWith("https://legacy.example.test/current");
  });

  it("离开section再返回也不会重新接受旧响应", async () => {
    const request = pendingLaunch();
    const view = render(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));
    view.rerender(<ManagerWorkspace section="projects" preview={false} module={businessModule()} businessPanel={null} />);
    view.rerender(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);

    await act(async () => request.resolve("https://legacy.example.test/stale"));

    expect(assignLocation).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "进入原台账看板" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(["preview", "disabled"] as const)("进入%s状态使正在等待的启动响应失效", async (mode) => {
    const request = pendingLaunch();
    const view = render(<ManagerWorkspace section="overview" preview={false} module={businessModule()} businessPanel={null} />);
    await userEvent.click(screen.getByRole("button", { name: "进入原台账看板" }));
    view.rerender(<ManagerWorkspace section="overview" preview={mode === "preview"} module={businessModule(mode === "disabled" ? "disabled" : "verified")} businessPanel={null} />);

    await act(async () => request.resolve("https://legacy.example.test/stale"));

    expect(assignLocation).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
