import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProductWorkspace from "./ProductWorkspace";

describe("ProductWorkspace", () => {
  it("按项目组织工作台，不拆成三个互不关联的生成入口", () => {
    render(<ProductWorkspace section="overview" />);
    expect(screen.getByRole("link", { name: "新建项目" }).getAttribute("href")).toBe("/centers/product/new");
    expect(screen.getByRole("heading", { name: "产品事业部工作台" })).toBeTruthy();
    expect(screen.queryByText("需求准备稿")).toBeNull();
    expect(screen.queryByRole("link", { name: /可行性研究报告|演示文稿/ })).toBeNull();
  });
});
