import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import WorkspaceSidebar from "./WorkspaceSidebar";

it("共享侧栏只呈现调用方授权菜单，窄屏展开状态可操作且导航后收起", () => {
  render(<WorkspaceSidebar title="工程部" subtitle="按角色授权" mark={<span>工</span>}
    workspaces={<a href="/centers/cost">工程部</a>} footer={<span>权限独立</span>}>
    <nav aria-label="工程部菜单"><a href="#estimate">成本测算</a></nav>
  </WorkspaceSidebar>);
  const toggle = screen.getByRole('button', { name: '展开菜单' });
  const body = document.getElementById(toggle.getAttribute('aria-controls')!);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(body?.getAttribute('data-expanded')).toBe('false');
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(body?.getAttribute('data-expanded')).toBe('true');
  expect(within(screen.getByRole('navigation', { name: '已授权工作台' })).getAllByRole('link')).toHaveLength(1);
  expect(screen.queryByRole('link', { name: '人事部门' })).toBeNull();
  fireEvent.click(screen.getByRole('link', { name: '成本测算' }));
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
});
