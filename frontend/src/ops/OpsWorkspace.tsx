import { useEffect, useState } from "react";
import { CurrentUser, getModules, type PortalModule } from "../api";
import WorkspaceSidebar from "../WorkspaceSidebar";
import { centers, isCenterCode } from "../centers/config";
import Icon from "../Icon";
import NetworkNotice from "../NetworkNotice";
import WorkspaceBoundary from "../WorkspaceBoundary";
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
  const [authorizedModules, setAuthorizedModules] = useState<PortalModule[]>([]);
  useEffect(() => {
    let active = true;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try { const modules = await getModules(); if (active) setAuthorizedModules(modules); }
      catch { if (active) setAuthorizedModules([]); }
      finally { pending = false; }
    };
    void load();
    const check = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(check, 15000);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", check); document.removeEventListener("visibilitychange", check); };
  }, [user.id]);
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
      <WorkspaceSidebar title="平台运维" subtitle="平台管理员 · 按权限管理" mark={<Icon name="portal"/>}
        workspaces={<><OpsLink href="/ops" className="selected" ariaCurrent="page">平台运维</OpsLink>{authorizedModules.filter(module => module.enabled && module.status !== "disabled" && isCenterCode(module.code)).map(module => <OpsLink key={module.code} href={`/centers/${module.code}`}>{isCenterCode(module.code) ? centers[module.code].name : module.name}<span aria-hidden="true">↗</span></OpsLink>)}</>}
        footer={<><span>{user.display_name || user.username}</span><small>平台管理权限不授予业务数据权限</small></>}>
        <nav className="ops-navigation" aria-label="运维主菜单">
          {navigation.map((item) => (
            <OpsLink href={item.href} key={item.href} className={isActive(pathname, item.href) ? "active" : undefined} ariaCurrent={isActive(pathname, item.href) ? "page" : undefined}>
              <span aria-hidden="true"><Icon name={item.mark} /></span>{item.label}
            </OpsLink>
          ))}
        </nav>
      </WorkspaceSidebar>
      <main className="ops-main" id="ops-main" tabIndex={-1}><NetworkNotice/><WorkspaceBoundary resetKey={pathname}>{page}</WorkspaceBoundary></main>
    </div>
  );
}
