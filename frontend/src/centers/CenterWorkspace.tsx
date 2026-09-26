import { ReactNode, useEffect, useRef, useState } from "react";
import { CurrentUser, getMe, getModule, getModules, isApiError, opsPermissionRevokedEvent, PortalModule } from "../api";
import Icon from "../Icon";
import { centers, CenterCode, isCenterCode } from "./config";
import { CenterLink } from "./shared";
import ProductWorkspace from "./ProductWorkspace";
import EngineeringPendingPage from "./EngineeringPendingPage";
import HrWorkspace from "./HrWorkspace";
import ManagerWorkspace from "./ManagerWorkspace";
import "./centers.css";
import { CompanyMark, ProductIcon, type ProductIconName } from "../product/workbench-shared";
const productNavIcons: Record<string, ProductIconName> = { overview: "home", projects: "folder", sources: "upload", outputs: "file", history: "clock", templates: "layers", new: "plus", documents: "settings" };

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
  const selected = config.sections.find((item) => item.code === section);
  const back = user.is_platform_admin ? "/workspace" : "/";
  const base = preview ? "/preview" : "/centers";

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

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [code, section, access.kind, preview]);
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
  else if (code === "product") content = <ProductWorkspace section={section} />;
  else if (code === "cost") content = <EngineeringPendingPage section={section} />;
  else if (code === "hr") content = <HrWorkspace section={section} user={user} />;
  else content = <ManagerWorkspace section={section} preview={preview} module={module} businessPanel={businessPanel} />;

  return <>{unavailable && failure}<div hidden={unavailable} className={`center-layout center-${code}`} onInputCapture={() => { if (code !== "product" || section === "documents") dirty.current = true; }}>
    <a className="skip-link" href="#center-main">跳到主要内容</a>
    <aside className="center-sidebar">
      <div className="center-brand"><span>{code === "product" ? <CompanyMark/> : <Icon name={config.icon} />}</span><div><strong>{config.name}</strong><small>{preview ? "管理预览 · 不授予业务权限" : "专属工作区 · 按角色授权"}</small></div></div>
      <nav className="center-navigation" aria-label={`${config.name}菜单`}>{config.sections.filter(item => code !== "hr" || (user.roles.some(role => role.code === "hr") ? ['job', 'resumes', 'history'].includes(item.code) : ['overview', 'probation'].includes(item.code))).map((item) => <CenterLink key={item.code} href={`${base}/${code}${item.code === "overview" ? "" : `/${item.code}`}`} current={section === item.code} className={section === item.code ? "active" : ""}>{code === "product" ? <ProductIcon name={productNavIcons[item.code] || "file"}/> : code === "hr" ? <Icon name={item.code === "overview" ? "overview" : item.code === "results" ? "usage" : item.code === "resumes" ? "people" : "modules"}/> : <span className="center-nav-dot" aria-hidden="true"/>}{item.title}</CenterLink>)}</nav>
      <div className="center-switcher"><p>{preview ? "切换预览" : "已授权工作台"}</p>{options.map((item) => <CenterLink key={item} href={`${base}/${item}`} className={item === code ? "selected" : ""}>{centers[item].name}<span aria-hidden="true">↗</span></CenterLink>)}</div>
      <div className="center-sidebar-foot"><span>原系统保持独立</span><small>业务权限与平台管理权限分离</small></div>
    </aside>
    <main className="center-main" id="center-main">
      <nav className="center-breadcrumb" aria-label="当前位置"><CenterLink href={back}>我的工作台</CenterLink><span aria-hidden="true">/</span><span>{config.name}</span><span className="center-mode">{preview ? "前端设计预览" : code === "product" ? "项目与成果协作" : code === "hr" ? "招聘与人才协作" : code === "business" ? "企业台账与分析" : "业务能力待接入"}</span></nav>
      <header className="center-page-head"><div><p className="eyebrow">{config.name}</p><h1 ref={heading} tabIndex={-1}>{selected?.title || "未找到页面"}</h1><p>{config.description}</p></div><CenterLink href={back} className="button secondary">← 返回工作台</CenterLink></header>
      {preview && <div className="center-preview-banner" role="note"><strong>仅预览前端页面</strong><span>不读取部门业务数据，不启动旧系统，不代表已获业务授权。请使用已授权的部门账号执行真实业务操作。</span></div>}
      <div className="center-body">{content}</div>
      <footer className="center-footer"><span>统一入口 · 独立业务 · 明确授权</span>{!preview && <CenterLink href={`/modules/${code}`}>查看模块接入说明</CenterLink>}</footer>
    </main>
  </div></>;
}
