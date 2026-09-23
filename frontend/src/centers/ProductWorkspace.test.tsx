import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProductWorkspace from "./ProductWorkspace";

describe("ProductWorkspace", () => {
  it("只提供项目成果流水线，不拆分三个成果入口", () => {
    render(<ProductWorkspace section="overview" />);
    expect(screen.getByRole("link", { name: "进入流水线" }).getAttribute("href")).toBe("/centers/product/documents");
    expect(screen.getByRole("heading", { name: "项目成果流水线" })).toBeTruthy();
    expect(screen.queryByText("需求准备稿")).toBeNull();
    expect(screen.queryByRole("link", { name: /可行性研究报告|演示文稿/ })).toBeNull();
  });
});
