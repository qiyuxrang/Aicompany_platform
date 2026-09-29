import { lazy, ReactNode, useEffect, useRef, useState } from "react";
import { CurrentUser, getMe, getModule, getModules, isApiError, opsPermissionRevokedEvent, PortalModule } from "../api";
import Icon from "../Icon";
import WorkspaceSidebar from "../WorkspaceSidebar";
import NetworkNotice from "../NetworkNotice";
import { centers, CenterCode, isCenterCode } from "./config";
import { CenterLink } from "./shared";
import WorkspaceBoundary from "../WorkspaceBoundary";

const ProductWorkspace = lazy(() => import("./ProductWorkspace"));
const EngineeringPendingPage = lazy(() => import("./EngineeringPendingPage"));
const HrWorkspace = lazy(() => import("./HrWorkspace"));
const ManagerWorkspace = lazy(() => import("./ManagerWorkspace"));

import "./centers.css";
import { CompanyMark, ProductIcon, type ProductIconName } from "../product/workbench-shared";
const productNavIcons: Record<string, ProductIconName> = { overview: "home", opportunities: "search", projects: "folder", sources: "upload", outputs: "file", history: "clock", templates: "layers", presales: "file", new: "plus", documents: "settings" };

type AccessState = { kind: "loading" } | { kind: "ready"; module: PortalModule; choices: PortalModule[] } | { kind: "error"; message: string };

export default function CenterWorkspace({ code, section, user, preview = false, businessPanel }: {
  code: CenterCode; section: string; user: CurrentUser; preview?: boolean; businessPanel: ReactNode;
}) {
  const [access, setAccess] = useState<AccessState>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [previewAccess, setPreviewAccess] = useState("checking");
  const previousAccess = useRef<Extract<AccessState, { kind: "ready" }> | null>(null);
  const previewWasAllowed = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const dirty = useRef(false);
  const config = centers[code];
  const isBusinessManager = user.roles.some(role => role.code === "general_manager");
  const requestedSection = section === "assistant" ? "overview" : section;
  const activeSection = code === "business" && !preview
    ? isBusinessManager ? requestedSection === "ledgers" ? "overview" : requestedSection : "ledgers"
    : code === "hr" && !preview && !user.roles.some(role => role.code === "hr") && requestedSection === "overview" ? "probation"
    : requestedSection;
  const selected = config.sections.find((item) => item.code === activeSection);
  const pageOwnsHeading = (code === "product" && !preview && ["overview", "projects", "knowledge", "opportunities", "outputs", "new", "documents", "history"].includes(activeSection))
    || (code === "business" && ["finance", "presales", "engineering"].includes(activeSection));
  const back = user.is_platform_admin ? "/ops" : "/";
  const base = preview ? "/preview" : "/centers";

  useEffect(() => {
    if (section !== "assistant" || window.location.pathname !== `${base}/${code}/assistant`) return;
    window.history.replaceState({}, "", `${base}/${code}${window.location.search}${window.location.hash}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, [base, code, section]);

  useEffect(() => {
    if (!preview || !user.is_platform_admin) return;
    let active = true;
    let inFlight = false;
    const check = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const identity = await getMe();
        if (!active) return;
        previewWasAllowed.current = identity.is_platform_admin && !identity.must_change_password;
        setPreviewAccess(previewWasAllowed.current ? "allowed" : "denied");
        if (!identity.is_platform_admin) window.dispatchEvent(new Event(opsPermissionRevokedEvent));
      } catch (error) {
        if (active) {
          if (isApiError(error) && [401, 403, 404].includes(error.status)) previewWasAllowed.current = false;
          setPreviewAccess("denied");
        }
      } finally {
        inFlight = false;
      }
    };
    void check();
    const refresh = () => { if (!document.hidden) void check(); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [preview, user.is_platform_admin]);

  useEffect(() => {
    if (preview) return;
    let active = true;
    let inFlight = false;
    setAccess({ kind: "loading" });
    const fail = (error: unknown) => {
      if (!active) return;
      if (isApiError(error) && [401, 403, 404].includes(error.status)) previousAccess.current = null;
      setAccess({ kind: "error", message: isApiError(error) && [403, 404].includes(error.status)
        ? "未获得此工作台授权，或授权已撤销。请返回工作台选择已授权入口。"
        : "暂时无法确认工作台授权，内容已隐藏。请检查连接后重试。" });
    };
    const rejected = (error: unknown): never => { fail(error); throw error; };
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const results = await Promise.allSettled([getModule(code).catch(rejected), getModules().catch(rejected)]);
        for (const result of results) {
          if (result.status === "rejected" && isApiError(result.reason) && [401, 403, 404].includes(result.reason.status)) throw result.reason;
        }
        const [moduleResult, choicesResult] = results;
        if (moduleResult.status === "rejected") throw moduleResult.reason;
        if (choicesResult.status === "rejected") throw choicesResult.reason;
        const module = moduleResult.value;
        const choices = choicesResult.value;
        if (!active) return;
        if (!module.enabled || module.status === "disabled") {
          previousAccess.current = null;
          setAccess({ kind: "error", message: "此工作台已停用，请联系平台管理员。" });
        } else {
          previousAccess.current = { kind: "ready", module, choices };
          setAccess(previousAccess.current);
        }
      } catch (error) {
        fail(error);
      } finally {
        inFlight = false;
      }
    };
    void load();
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [code, preview, reload]);

  useEffect(() => {
    const target = pageOwnsHeading ? document.getElementById("center-main") : heading.current;
    target?.focus({ preventScroll: true });
  }, [code, activeSection, access.kind, preview, pageOwnsHeading]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  if (preview && !user.is_platform_admin) return <main className="workspace compact"><div className="notice error" role="alert">仅平台管理员可使用前端设计预览；这不会授予业务权限。</div><CenterLink href={back}>返回工作台</CenterLink></main>;
  const unavailable = preview ? previewAccess !== "allowed" : access.kind !== "ready";
  const availableState = access.kind === "ready" ? access : previousAccess.current;
  const failure = preview ? <main className="workspace compact"><CenterLink href={back}>返回工作台</CenterLink><p role={previewAccess === "checking" ? "status" : "alert"}>{previewAccess === "checking" ? "正在确认管理预览权限…" : "无法确认管理预览权限，页面内容已隐藏。请检查账号授权与连接后重新进入。"}</p></main>
    : <main className="workspace compact"><CenterLink href={back} className="back-link">← 返回工作台</CenterLink>{access.kind === "loading"
    ? <div className="center-empty" role="status">正在确认工作台授权…</div>
    : <div className="center-empty" role="alert"><h1>工作台暂不可用</h1><p>{access.kind === "error" ? access.message : ""}</p><button className="button secondary" onClick={() => setReload((value) => value + 1)}>重新检查授权</button></div>}</main>;
  if (unavailable && !(preview ? previewWasAllowed.current : availableState)) return failure;

  const module = availableState?.module;
  const options = preview ? Object.keys(centers).filter(isCenterCode) : availableState
    ? availableState.choices.filter((item) => item.enabled && item.status !== "disabled").map((item) => item.code).filter(isCenterCode) : [];
  let content: ReactNode;
  if (!selected) content = <div className="center-empty"><h2>页面不存在</h2><p>请从工作台菜单选择页面。</p><CenterLink href={`${base}/${code}`} className="button secondary">返回工作概览</CenterLink></div>;

  else if (code === "product") content = <ProductWorkspace section={activeSection} />;
  else if (code === "cost") content = preview
    ? <section className="center-panel"><h2>工程部工作台预览</h2><p>仅预览页面结构，不读取工程任务、清单或知识库；平台管理权限不授予工程业务权限。</p></section>
    : <EngineeringPendingPage section={activeSection} />;
  else if (code === "hr") content = <HrWorkspace section={activeSection} user={user} />;
  else content = <ManagerWorkspace section={activeSection} preview={preview} module={module} businessPanel={businessPanel} />;

  const mainClass = code === "product" && !preview
    ? activeSection === "knowledge" ? "center-main center-main-knowledge" : activeSection === "sources" ? "center-main center-main-materials" : "center-main"
    : "center-main";

  return <>{unavailable && failure}<div hidden={unavailable} className={`center-layout center-${code}${code === "product" && activeSection === "sources" && !preview ? " center-product-materials" : ""}`} onInputCapture={() => { if (code !== "product" && (code !== "business" || activeSection === "ledgers")) dirty.current = true; }}>
    <a className="skip-link" href="#center-main">跳到主要内容</a>
    <WorkspaceSidebar title={config.name} subtitle={preview ? "管理预览 · 不授予业务权限" : "专属工作区 · 按角色授权"}
      mark={code === "product" ? <CompanyMark/> : <Icon name={config.icon}/>}
      workspaceLabel={preview ? "切换预览" : "已授权工作台"}
      workspaces={<>{options.map(item => <CenterLink key={item} href={`${base}/${item}`} current={item === code} className={item === code ? "selected" : ""}>{centers[item].name}<span aria-hidden="true">↗</span></CenterLink>)}{user.is_platform_admin && <CenterLink href="/ops">平台运维<span aria-hidden="true">↗</span></CenterLink>}</>}
      footer={<><span>业务权限与平台管理权限分离</span><CenterLink href={back}>返回我的工作台</CenterLink></>}>
      <nav className="center-navigation" aria-label={`${config.name}菜单`}>{config.sections.filter(item => code !== "product" || item.code !== "templates").filter(item => code !== "hr" || (user.roles.some(role => role.code === "hr") ? ['overview', 'job', 'resumes', 'history'].includes(item.code) : ['probation'].includes(item.code))).filter(item => code !== "business" || preview || (isBusinessManager ? item.code !== "ledgers" : item.code === "ledgers")).map((item) => <CenterLink key={item.code} href={`${base}/${code}${item.code === "overview" ? "" : `/${item.code}`}`} current={activeSection === item.code} className={activeSection === item.code ? "active" : ""}>{code === "product" ? <ProductIcon name={productNavIcons[item.code] || "file"}/> : code === "hr" ? <Icon name={item.code === "overview" ? "overview" : item.code === "results" ? "usage" : item.code === "resumes" ? "people" : "modules"}/> : <span className="center-nav-dot" aria-hidden="true"/>}{item.title}</CenterLink>)}</nav>
    </WorkspaceSidebar>
    <main className={mainClass} id="center-main" tabIndex={-1}>
      <NetworkNotice/>
      {!(code === "product" && activeSection === "opportunities" && !preview) && <nav className="center-breadcrumb" aria-label="当前位置"><CenterLink href={`${base}/${code}`}>{config.name}</CenterLink><span aria-hidden="true">/</span><span>{selected?.title || "未找到页面"}</span></nav>}
      {!pageOwnsHeading && <header className="center-page-head"><div><h1 ref={heading} tabIndex={-1}>{selected?.title || "未找到页面"}</h1></div></header>}
      {preview && <div className="center-preview-banner" role="note"><strong>仅预览前端页面</strong><span>不读取部门业务数据，不启动旧系统，不代表已获业务授权。请使用已授权的部门账号执行真实业务操作。</span></div>}
      <div className="center-body"><WorkspaceBoundary resetKey={activeSection}>{content}</WorkspaceBoundary></div>
    </main>
  </div></>;
}
