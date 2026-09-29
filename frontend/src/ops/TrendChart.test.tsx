import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TrendChart } from "./components";

afterEach(cleanup);

describe("compact usage trend", () => {
  it("稀疏展示30天日期但保留所有数据点和筛选明细", () => {
    const points = Array.from({ length: 30 }, (_, index) => ({
      date: `2026-09-${String(index + 1).padStart(2, "0")}`,
      login_users: 1,
      login_count: index,
      module_launches: 0,
    }));
    const { container } = render(<TrendChart points={points} days={30} module="product" />);
    expect(screen.getAllByRole("link")).toHaveLength(30);
    expect(container.querySelectorAll("svg text").length).toBeLessThan(20);
    expect(screen.getByText("09-01")).toBeTruthy();
    expect(screen.getByText("09-30")).toBeTruthy();
    const point = screen.getByRole("link", { name: "2026-09-30：登录 29 次，模块启动 0 次" });
    fireEvent.keyDown(point, { key: "Enter" });
    expect(window.location.search).toBe("?days=30&date=2026-09-30&module=product");
  });

  it("空状态切换到单日全零数据时保持有效坐标", () => {
    const { container, rerender } = render(<TrendChart points={[]} days={1} />);
    expect(screen.getByText("当前范围暂无趋势数据。")).toBeTruthy();
    rerender(<TrendChart points={[{ date: "2026-09-29", login_users: 0, login_count: 0, module_launches: 0 }]} days={1} />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("09-29")).toBeTruthy();
    for (const line of container.querySelectorAll("polyline")) {
      expect(line.getAttribute("points")).not.toMatch(/NaN|Infinity/);
    }
    expect(container.querySelectorAll(".axis")).toHaveLength(4);
  });
});
