import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import EngineeringPendingPage from "./EngineeringPendingPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("工程部隔离页", () => {
  it.each(["overview", "estimate", "quota"])("%s 只显示开发中隔离状态且不执行工程业务", (section) => {
    const fetchMock = vi.fn();
    const xhr = vi.spyOn(XMLHttpRequest.prototype, "open");
    const storageRead = vi.spyOn(Storage.prototype, "getItem");
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<EngineeringPendingPage section={section} />);

    expect(screen.getByText("开发中 · 已隔离")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "暂不提供业务操作" })).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /录入|测算|定额推荐|保存|生成/ })).toBeNull();
    expect(screen.getByText(/不创建内存记录，不调用工程服务/)).toBeTruthy();

    view.rerender(<EngineeringPendingPage section={section === "overview" ? "estimate" : "overview"} />);
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(xhr).not.toHaveBeenCalled();
    expect(storageRead).not.toHaveBeenCalled();
    expect(storageWrite).not.toHaveBeenCalled();
  });
});
