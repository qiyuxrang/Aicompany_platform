import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProductWorkspace from "./ProductWorkspace";

describe("ProductWorkspace", () => {
  beforeEach(() => window.history.replaceState({}, "", "/centers/product"));

  it("按项目组织工作台，不拆成三个互不关联的生成入口", () => {
    render(<ProductWorkspace section="overview" />);
    expect(screen.getByRole("link", { name: "上传资料生成成果" }).getAttribute("href")).toBe("/centers/product/new");
    expect(screen.getByRole("heading", { name: "产品事业部工作台" })).toBeTruthy();
    expect(screen.queryByText("需求准备稿")).toBeNull();
    expect(screen.queryByRole("link", { name: /可行性研究报告|演示文稿/ })).toBeNull();
  });

  it.each(["opportunities", "sources", "documents"])("管理预览的 %s 页不读取业务接口", section => {
    window.history.replaceState({}, "", `/preview/product/${section}`);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<ProductWorkspace section={section} />);
    expect(screen.getByRole("heading", { name: "产品事业部页面预览" })).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
