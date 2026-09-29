import { lazy } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import WorkspaceBoundary from "./WorkspaceBoundary";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps navigation available when a workspace crashes and recovers on another route", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  function Broken(): never { throw new Error("private diagnostic must not be displayed"); }
  const view = render(<><nav>部门菜单</nav><WorkspaceBoundary resetKey="one"><Broken/></WorkspaceBoundary></>);
  expect(screen.getByRole("navigation").textContent).toBe("部门菜单");
  expect(screen.getByRole("alert").textContent).not.toContain("private diagnostic");
  expect(screen.getByRole("button", { name: "重新加载页面" })).toBeTruthy();
  view.rerender(<WorkspaceBoundary resetKey="two"><p>正常工作台</p></WorkspaceBoundary>);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("正常工作台")).toBeTruthy();
});

it("shows loading and an actionable error when a lazy page cannot be downloaded", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  let reject!: (reason: Error) => void;
  const Page = lazy(() => new Promise<{ default: () => null }>((_, fail) => { reject = fail; }));
  render(<WorkspaceBoundary><Page/></WorkspaceBoundary>);
  expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  reject(new Error("chunk unavailable"));
  expect(await screen.findByRole("alert")).toBeTruthy();
});
