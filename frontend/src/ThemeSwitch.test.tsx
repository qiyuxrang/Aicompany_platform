import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ThemeSwitch from "./ThemeSwitch";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  const meta = document.createElement("meta");
  meta.name = "theme-color";
  document.head.append(meta);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.remove());
});

describe("界面主题", () => {
  it("没有偏好时默认日间，不写入账号或业务数据", () => {
    render(<ThemeSwitch />);
    const button = screen.getByRole("button", { name: "切换至夜间模式" });
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(button.textContent).toBe("");
    expect(button.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.length).toBe(0);
  });

  it("可双向切换，仅保存Django兼容的外观偏好", async () => {
    const user = userEvent.setup();
    render(<ThemeSwitch />);
    await user.click(screen.getByRole("button", { name: "切换至夜间模式" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(localStorage.length).toBe(1);
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe("#101927");
    expect(screen.getByRole("button", { name: "切换至日间模式" }).getAttribute("title")).toBe("切换至日间模式");
    await user.click(screen.getByRole("button", { name: "切换至日间模式" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("theme")).toBe("light");
  });

  it("切换不重建旁边正在编辑的表单", async () => {
    render(<><ThemeSwitch /><input aria-label="当前草稿" /></>);
    const input = screen.getByRole("textbox", { name: "当前草稿" }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "保留手工内容" } });
    await userEvent.click(screen.getByRole("button", { name: "切换至夜间模式" }));
    expect(screen.getByRole("textbox", { name: "当前草稿" })).toBe(input);
    expect(input.value).toBe("保留手工内容");
  });

  it("重新挂载保留此前选择", async () => {
    const view = render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("button", { name: "切换至夜间模式" }));
    view.unmount();
    render(<ThemeSwitch />);
    expect(screen.getByRole("button", { name: "切换至日间模式" })).toBeTruthy();
  });

  it.each(["auto", "unexpected"])("旧后台主题值%s安全回退日间", (value) => {
    localStorage.setItem("theme", value);
    render(<ThemeSwitch />);
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("后台已有深色选择可直接复用", () => {
    localStorage.setItem("theme", "dark");
    render(<ThemeSwitch />);
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("存储受限时仍能切换并在同页重挂载保留", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked"); });
    const view = render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("button", { name: "切换至夜间模式" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    view.unmount();
    render(<ThemeSwitch />);
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("同步其他标签页的主题变化，忽略其他存储项", () => {
    render(<ThemeSwitch />);
    fireEvent(window, new StorageEvent("storage", { key: "theme", newValue: "dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent(window, new StorageEvent("storage", { key: "unrelated", newValue: "light" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent(window, new StorageEvent("storage", { key: "theme", newValue: null }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("存储只读时重新聚焦不会撤销本页的手动选择", async () => {
    localStorage.setItem("theme", "light");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("quota"); });
    render(<ThemeSwitch />);
    await userEvent.click(screen.getByRole("button", { name: "切换至夜间模式" }));
    fireEvent(window, new Event("focus"));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("从后台返回和重新聚焦时读取新的主题选择", () => {
    render(<ThemeSwitch />);
    localStorage.setItem("theme", "dark");
    fireEvent(window, new Event("pageshow"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    localStorage.setItem("theme", "light");
    fireEvent(window, new Event("focus"));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("清空偏好时回退日间，卸载后不再监听事件", () => {
    const view = render(<ThemeSwitch />);
    fireEvent(window, new StorageEvent("storage", { key: "theme", newValue: "dark" }));
    fireEvent(window, new StorageEvent("storage", { key: null, newValue: null }));
    expect(document.documentElement.dataset.theme).toBe("light");
    view.unmount();
    fireEvent(window, new StorageEvent("storage", { key: "theme", newValue: "dark" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  const html = readFileSync("index.html", "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  it.each(["dark", "light", "auto", "invalid", null])("首屏脚本在渲染前正确读取%s", (value) => {
    if (value) localStorage.setItem("theme", value);
    expect(script).toBeTruthy();
    new Function(script!)();
    expect(document.documentElement.dataset.theme).toBe(value === "dark" ? "dark" : "light");
    expect(html.indexOf("<script>")).toBeLessThan(html.indexOf("<body>"));
  });

  it("首屏脚本在存储被禁用时不会中断加载", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked"); });
    expect(() => new Function(script!)()).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
