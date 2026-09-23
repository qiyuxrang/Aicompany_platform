import { CurrentUser } from "../api";
import Icon from "../Icon";
import {
  IssuesPage,
  MaintenancePage,
  ModulesPage,
  OverviewPage,
  PeoplePage,
  UsagePage,
} from "./OpsPages";
import { OpsLink } from "./components";
import "./ops.css";

const navigation = [
  { href: "/ops", label: "运维总览", mark: "overview" },
  { href: "/ops/people", label: "人员与权限", mark: "people" },
  { href: "/ops/usage", label: "业务与使用分析", mark: "usage" },
  { href: "/ops/modules", label: "模块与接入管理", mark: "modules" },
  { href: "/ops/issues", label: "问题中心", mark: "issues" },
  { href: "/ops/maintenance", label: "系统维护", mark: "maintenance" },
] as const;

function isActive(pathname: string, href: string): boolean {
  const normalized = pathname.replace(/\/$/, "") || "/";
  return href === "/ops" ? normalized === href : normalized === href;
}

export default function OpsWorkspace({ user, pathname }: { user: CurrentUser; pathname: string }) {
  let page: React.ReactNode;
  const normalized = pathname.replace(/\/$/, "") || "/";
  if (normalized === "/ops") page = <OverviewPage />;
  else if (normalized === "/ops/people") page = <PeoplePage />;
  else if (normalized === "/ops/usage") page = <UsagePage />;
  else if (normalized === "/ops/modules") page = <ModulesPage />;
  else if (normalized === "/ops/issues") page = <IssuesPage />;
  else if (normalized === "/ops/maintenance") page = <MaintenancePage />;
  else page = (
    <section className="ops-state">
      <strong>运维页面不存在</strong>
      <p>请从左侧六项菜单重新选择。</p>
      <OpsLink className="button primary compact" href="/ops">返回运维总览</OpsLink>
    </section>
  );

  return (
    <div className="ops-layout">
      <a className="skip-link" href="#ops-main">跳到主要内容</a>
      <aside className="ops-sidebar">
        <div className="ops-sidebar-heading">
          <span className="ops-sidebar-mark" aria-hidden="true"><Icon name="portal" /></span>
          <div><strong>平台运维</strong><small>统一管理 · 安全运维</small></div>
        </div>
        <nav className="ops-navigation" aria-label="运维主菜单">
          {navigation.map((item) => (
            <OpsLink href={item.href} key={item.href} className={isActive(pathname, item.href) ? "active" : undefined} ariaCurrent={isActive(pathname, item.href) ? "page" : undefined}>
              <span aria-hidden="true"><Icon name={item.mark} /></span>{item.label}
            </OpsLink>
          ))}
        </nav>
        <div className="ops-sidebar-foot">
          <span>{user.display_name || user.username}</span>
          <small>平台管理员 · 写操作仍受后端保护</small>
        </div>
      </aside>
      <main className="ops-main" id="ops-main" tabIndex={-1}>{page}</main>
    </div>
  );
}
