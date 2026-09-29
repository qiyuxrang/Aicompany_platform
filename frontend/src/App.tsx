import { type AnchorHTMLAttributes, FormEvent, lazy, MouseEvent, ReactNode, useEffect, useRef, useState } from "react";
import {
  ApiError,
  BusinessSummary,
  CurrentUser,
  PortalModule,
  WorkSummary,
  summaryLabels,
  isModuleCode,
  changePassword,
  clearApiSession,
  getBusinessSummary,
  getWorkSummary,
  getMe,
  getModule,
  getModules,
  isApiError,
  launchModule,
  login,
  logout,
  opsPermissionRevokedEvent,
  passwordChangeRequiredEvent,
  unauthorizedEvent,
} from "./api";
import WorkspaceBoundary from "./WorkspaceBoundary";
import ThemeSwitch from "./ThemeSwitch";
import './password-dialog.css';
import HrHeaderTools from './hr/HrHeaderTools';
import { centers, isCenterCode } from "./centers/config";
import CompanyIdentity from "./CompanyIdentity";
import "./workspace-shell.css";
import Icon from "./Icon";
import "./login-page.css";

const OpsWorkspace = lazy(() => import("./ops/OpsWorkspace"));
const CenterWorkspace = lazy(() => import("./centers/CenterWorkspace"));

const statusMeta = {
  pending: { label: "待接入", tone: "warning" },
  navigation: { label: "导航接入", tone: "info" },
  verified: { label: "已验证集成", tone: "success" },
  disabled: { label: "已停用", tone: "muted" },
} as const;

const departmentHomes: Record<string, string> = {
  product: "/centers/product",
  hr: "/centers/hr",
  engineering: "/centers/cost",
  general_manager: "/centers/business",
};

const departmentChoices: Record<string, string> = {
  ...departmentHomes,
  cost: "/centers/cost",
  business: "/centers/business",
};

function departmentHome(user: CurrentUser): string {
  if (user.is_platform_admin) return "/ops";
  const destinations = [...new Set(user.roles.map(role => departmentHomes[role.code]).filter((home): home is string => Boolean(home)))];
  const provided = (user as CurrentUser & { default_department?: unknown }).default_department;
  const preferred = typeof provided === "string" ? departmentChoices[provided] : undefined;
  return preferred && destinations.includes(preferred) ? preferred : destinations[0] || "/";
}

function internalPath(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return null;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}

function withNext(path: string, returnTo: string | null): string {
  return returnTo && returnTo !== "/" && returnTo !== "/login" && returnTo !== "/password"
    ? `${path}?next=${encodeURIComponent(returnTo)}`
    : path;
}

function nextPath(search: string): string | null {
  return internalPath(new URLSearchParams(search).get("next"));
}

function authorizedReturnPath(user: CurrentUser, search: string): string | null {
  const returnTo = nextPath(search);
  if (!returnTo) return null;
  const target = new URL(returnTo, window.location.origin);
  if (user.is_platform_admin) {
    if (target.pathname === "/workspace" || target.pathname === "/workspace/") return "/ops";
    if (target.pathname === "/ops" || target.pathname.startsWith("/ops/")) return returnTo;
    const preview = target.pathname.match(/^\/preview\/([a-zA-Z0-9_-]+)(?:\/|$)/);
    return preview && isCenterCode(preview[1]) ? returnTo : null;
  }

  const center = target.pathname.match(/^\/centers\/([a-zA-Z0-9_-]+)(?:\/|$)/);
  if (center) {
    const role = { product: "product", cost: "engineering", hr: "hr", business: "general_manager" }[center[1]];
    return role && user.roles.some(item => item.code === role) ? returnTo : null;
  }

  const module = target.pathname.match(/^\/modules\/([^/]+)\/?$/);
  if (module) {
    let code = "";
    try {
      code = decodeURIComponent(module[1]);
    } catch {
      return null;
    }
    const role = { product: "product", cost: "engineering", hr: "hr", business: "general_manager" }[code];
    return role && user.roles.some(item => item.code === role) ? returnTo : null;
  }
  return null;
}

function loginPath(pathname: string, search: string, hash: string): string {
  const returnTo = internalPath(`${pathname}${search}${hash}`);
  return withNext("/login", returnTo);
}

function navigate(path: string, replace = false): void {
  if (`${window.location.pathname}${window.location.search}${window.location.hash}` === path) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export const navigationGuardEvent = "portal:navigation-guard";

function useLocation(): { pathname: string; search: string; hash: string } {
  const [location, setLocation] = useState(() => ({ pathname: window.location.pathname, search: window.location.search, hash: window.location.hash }));
  const accepted = useRef(`${window.location.pathname}${window.location.search}${window.location.hash}`);
  useEffect(() => {
    const update = () => {
      const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (!window.dispatchEvent(new CustomEvent(navigationGuardEvent, { cancelable: true, detail: { from: accepted.current, to: next } }))) {
        window.history.replaceState({}, "", accepted.current);
        return;
      }
      accepted.current = next;
      setLocation({ pathname: window.location.pathname, search: window.location.search, hash: window.location.hash });
    };
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return location;
}

function AppLink({ href, className, children, ...attributes }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children: ReactNode }) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  };
  return (
    <a {...attributes} href={href} className={className} onClick={handleClick}>
      {children}
    </a>
  );
}

function Brand() {
  return <AppLink href="/" className="brand company-brand" aria-label="企业统一门户首页"><CompanyIdentity /></AppLink>;
}

function PageLoading({ label = "正在加载门户" }: { label?: string }) {
  return (
    <main className="state-page" aria-busy="true" aria-live="polite">
      <div className="state-card loading-card">
        <span className="skeleton skeleton-title" />
        <span className="skeleton skeleton-line" />
        <span className="skeleton skeleton-line short" />
        <span className="sr-only">{label}</span>
      </div>
    </main>
  );
}

function ErrorPage({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="state-page">
      <section className="state-card" role="alert">
        <span className="state-symbol" aria-hidden="true">!</span>
        <p className="eyebrow">门户暂不可用</p>
        <h1>无法连接平台服务</h1>
        <p>{message}</p>
        <button className="button primary" type="button" onClick={onRetry}>重新加载</button>
      </section>
    </main>
  );
}

function LoginPage({
  notice,
  onAuthenticated,
}: {
  notice: string;
  onAuthenticated: (user: CurrentUser) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (!username.trim() || !password) {
      setError("请输入用户名和密码。");
      return;
    }
    setPending(true);
    try {
      onAuthenticated(await login(username.trim(), password));
    } catch (caught) {
      setError(isApiError(caught) ? caught.message : "登录请求失败，请稍后重试。");
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="auth-layout login-page">
      <div className="login-theme"><ThemeSwitch /></div>
      <section className="auth-intro" aria-labelledby="login-title">
        <div className="auth-brand-row"><Brand /></div>
        <div className="login-welcome">
          <p className="login-kicker">企业协同 · 智能工作空间</p>
          <h1 id="login-title">欢迎回来</h1>
          <p className="auth-lead">登录后继续部门工作</p>
          <span className="login-divider" aria-hidden="true" />
          <div className="login-features">
            <div><span><Icon name="portal" /></span><strong>高效协同</strong><small>连接团队与业务</small></div>
            <div><span><Icon name="usage" /></span><strong>智能驱动</strong><small>让工作更高效</small></div>
            <div><span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></svg></span><strong>安全可靠</strong><small>按角色授权访问</small></div>
          </div>
        </div>
        <div className="login-art" aria-hidden="true">
          <div className="login-wave wave-back" /><div className="login-wave wave-front" />
          <div className="login-orbit" />
          <div className="login-platform platform-back" /><div className="login-platform" />
          <div className="login-glass glass-back" /><div className="login-glass glass-middle" />
          <div className="login-glass glass-front"><svg viewBox="0 0 120 100"><defs><linearGradient id="login-cloud" x2="80%" y2="100%"><stop stopColor="#69c9ff"/><stop offset="1" stopColor="#1670ed"/></linearGradient></defs><path d="M31 79C9 79 5 48 25 40 22 6 71 0 80 34c31-3 43 45 9 45Z" fill="url(#login-cloud)"/></svg></div>
          <span className="login-sphere sphere-one" /><span className="login-sphere sphere-two" /><span className="login-sphere sphere-three" />
        </div>
        <p className="login-brand-note">让信息连接价值，让协作创造可能。</p>
      </section>
      <section className="auth-panel" aria-label="登录表单">
        <div className="form-card">
          <div className="form-heading">
            <p className="eyebrow">账号登录</p>
            <h2>进入工作台</h2>
          </div>
          {notice && <div className="notice info" role="status">{notice}</div>}
          {error && <div className="notice error" role="alert">{error}</div>}
          <form onSubmit={handleSubmit} noValidate>
            <label htmlFor="username">用户名</label>
            <div className="login-input">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></svg>
            <input
              id="username"
              name="username"
              autoComplete="username"
              placeholder="请输入用户名"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              autoFocus
            />
            </div>
            <label htmlFor="password">密码</label>
            <div className="login-input">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 7v-3"/></svg>
            <input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              placeholder="请输入密码"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button className="login-password-toggle" type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>{showPassword && <path d="m3 3 18 18"/>}</svg></button>
            </div>
            <button className="button primary full" type="submit" disabled={pending}>
              {pending ? "正在验证…" : "登录"}
            </button>
          </form>
          <p className="form-footnote">如无法登录，请联系平台管理员核对账号状态。</p>
        </div>
        <p className="login-panel-note">企业统一门户 · 授权访问</p>
      </section>
    </main>
  );
}

function PasswordPage({
  forced,
  onChanged,
  onLogout,
}: {
  forced: boolean;
  onChanged: (detail: string) => void;
  onLogout: () => Promise<void>;
}) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const passwordDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!forced || !passwordDialog.current) return;
    const dialog = passwordDialog.current;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    return () => { if (typeof dialog.close === "function") dialog.close(); };
  }, [forced]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if ((!forced && !oldPassword) || !newPassword || !confirmPassword) {
      setError(forced ? "请输入新密码并再次确认。" : "请完整填写三个密码字段。");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setPending(true);
    try {
      const result = await changePassword(forced ? undefined : oldPassword, newPassword, confirmPassword);
      onChanged(result.detail);
    } catch (caught) {
      setError(isApiError(caught) ? caught.message : "密码修改失败，请稍后重试。");
    } finally {
      setPending(false);
    }
  };

  const handleLogout = async () => {
    setError("");
    try {
      await onLogout();
    } catch (caught) {
      setError(isApiError(caught) ? caught.message : "退出失败，请稍后重试。");
    }
  };

  const card = <section className="form-card password-card" aria-labelledby="password-title">
        <div className="form-heading">
          <p className="eyebrow">账号安全</p>
          <h1 id="password-title">{forced ? "首次登录，请先修改密码" : "修改登录密码"}</h1>
          <p>{forced ? "完成修改后，其他功能才会开放。" : "修改成功后，所有会话将失效并返回登录页。"}</p>
        </div>
        {error && <div className="notice error" role="alert">{error}</div>}
        <form onSubmit={handleSubmit} noValidate>
          {!forced && <><label htmlFor="old-password">当前密码</label>
          <input
            id="old-password"
            type="password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
            required
            autoFocus
          /></>}
          <label htmlFor="new-password">新密码</label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            autoFocus={forced}
            minLength={12}
            required
          />
          <label htmlFor="confirm-password">确认新密码</label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            required
          />
          <button className="button primary full" type="submit" disabled={pending}>
            {pending ? "正在提交…" : "确认修改"}
          </button>
        </form>
        {forced && <button className="text-button" type="button" onClick={handleLogout}>退出当前账号</button>}
      </section>;
  return <main className={`password-layout ${forced ? "" : "within-shell"}`}>
    {forced && <div className="password-brand"><Brand /><ThemeSwitch /></div>}
    {forced ? <dialog ref={passwordDialog} className="first-password-dialog" aria-labelledby="password-title" onCancel={event => event.preventDefault()}>{card}</dialog> : card}
  </main>;
}

function AppShell({ user, onLogout, children }: { user: CurrentUser; onLogout: () => Promise<void>; children: ReactNode }) {
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const hrShell = window.location.pathname.startsWith('/centers/hr');

  const handleLogout = async () => {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await onLogout();
    } catch (caught) {
      setLogoutError(isApiError(caught) ? caught.message : "退出失败，请稍后重试。");
      setLoggingOut(false);
    }
  };

  return (
    <div className="app-shell unified-app-shell">
      <header className="topbar">
        <Brand />
        {hrShell && user.roles.some(role => role.code === 'hr') && <HrHeaderTools />}
        <nav className="account-nav" aria-label="账户导航">
          <span className="account-name">{user.display_name || user.username}</span>
          {user.is_platform_admin && <AppLink href="/ops">运维工作台</AppLink>}
          {user.is_platform_admin && <AppLink href="/preview/product">业务页面预览</AppLink>}
          <AppLink href="/password">修改密码</AppLink>
          {user.is_platform_admin && <a href="/admin/">管理后台</a>}
          <button type="button" className="text-button" onClick={handleLogout} disabled={loggingOut}>
            {loggingOut ? "退出中…" : "退出登录"}
          </button>
          <ThemeSwitch />
        </nav>
      </header>
      {logoutError && <div className="global-alert" role="alert">{logoutError}</div>}
      <div className="app-content">{children}</div>
    </div>
  );
}

function StatusBadge({ module }: { module: PortalModule }) {
  const meta = statusMeta[module.status];
  return <span className={`status ${meta.tone}`}>{meta.label}</span>;
}

function ModuleGrid({ onBusinessAccess }: { onBusinessAccess: (allowed: boolean) => void }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; modules: PortalModule[] }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  const load = async () => {
    setState({ kind: "loading" });
    onBusinessAccess(false);
    try {
      const modules = await getModules();
      setState({ kind: "ready", modules: Array.isArray(modules) ? modules : [] });
      onBusinessAccess(Array.isArray(modules) && modules.some((module) =>
        module.code === "business" && module.enabled && module.status !== "disabled" && module.status !== "pending"));
    } catch (caught) {
      setState({ kind: "error", message: isApiError(caught) ? caught.message : "模块列表加载失败。" });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="module-grid" aria-busy="true" aria-label="正在加载业务模块">
        {[0, 1, 2, 3].map((item) => <div className="module-card skeleton-card" key={item} />)}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="inline-state" role="alert">
        <strong>业务模块加载失败</strong>
        <p>{state.message}</p>
        <button className="button secondary" type="button" onClick={load}>重试</button>
      </div>
    );
  }

  if (state.modules.length === 0) {
    return (
      <div className="inline-state empty">
        <span className="state-symbol" aria-hidden="true">0</span>
        <strong>暂无已授权模块</strong>
        <p>当前账号尚未分配业务入口，请联系平台管理员。</p>
      </div>
    );
  }

  return (
    <div className="module-grid">
      {state.modules.map((module) => (
        <article className={`module-card ${!module.enabled || module.status === "disabled" ? "is-disabled" : ""}`} key={module.code}>
          <div className="module-topline">
            <span className="module-code">业务入口</span>
            <StatusBadge module={module} />
          </div>
          <div>
            <h3>{isCenterCode(module.code) ? centers[module.code].name : module.name}</h3>
            <p>{isCenterCode(module.code) ? centers[module.code].description : module.description || "暂无说明。"}</p>
          </div>
          {isCenterCode(module.code) && module.enabled && module.status !== "disabled" && <AppLink href={`/centers/${module.code}`} className="button secondary">打开工作台</AppLink>}
          <AppLink href={`/modules/${encodeURIComponent(module.code)}`} className="module-link">
            查看接入详情 <span aria-hidden="true">→</span>
          </AppLink>
        </article>
      ))}
    </div>
  );
}

function formatUpdatedAt(value?: string): string {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function BusinessSummaryPanel() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; summary: BusinessSummary }
    | { kind: "disabled"; detail: string }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  const load = async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", summary: await getBusinessSummary() });
    } catch (caught) {
      if (isApiError(caught) && caught.status === 503 && caught.code === "integration_not_configured") {
        setState({ kind: "disabled", detail: caught.message });
      } else {
        setState({ kind: "error", message: isApiError(caught) ? caught.message : "经营摘要加载失败。" });
      }
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (state.kind === "loading") {
    return <div className="summary-loading skeleton" aria-label="正在加载经营摘要" />;
  }

  if (state.kind === "disabled") {
    return (
      <div className="summary-state">
        <span className="status muted">未接入 · 未验证</span>
        <strong>经营摘要暂未接入</strong>
        <p>{state.detail}</p>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="summary-state" role="alert">
        <strong>经营摘要加载失败</strong>
        <p>{state.message}</p>
        <button className="button secondary" type="button" onClick={load}>重试</button>
      </div>
    );
  }

  const { projects, summary } = state.summary;
  const metricKeys = (Object.keys(summaryLabels) as (keyof typeof summaryLabels)[])
    .filter((key) => summary[key] !== undefined);
  return (
    <div className="summary-content">
      <dl className="summary-meta">
        <div><dt>数据来源</dt><dd>{state.summary.source === "legacy-ledger:authorized-projects" ? "项目台账" : state.summary.source || "暂无"}</dd></div>
        <div><dt>更新时间</dt><dd>{formatUpdatedAt(state.summary.updated_at)}</dd></div>
      </dl>
      {metricKeys.length > 0 ? (
        <dl className="metric-grid">
          {metricKeys.map((key) => (
            <div key={key}>
              <dt>{summaryLabels[key]}</dt>
              <dd>{summary[key] === "" ? "暂无数据" : summary[key]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="summary-empty">暂无摘要数据。</p>
      )}
      <section className="project-section" aria-label="经营项目">
        <h3>项目列表</h3>
        {projects.length > 0 ? (
          <ul className="project-list">
            {projects.map((project, index) => (
              <li key={`${project.id}-${index}`}>
                <strong>{project.name || "项目名称为空"}</strong>
                <span>项目编号：{project.id}</span>
              </li>
            ))}
          </ul>
        ) : <p className="summary-empty">暂无项目数据。</p>}
      </section>
    </div>
  );
}

const workSectionMeta = {
  my_tasks: { title: "我的任务", empty: "当前没有进行中的个人任务。" },
  pending_reviews: { title: "待我审批", empty: "当前没有待处理审批。" },
  recent_results: { title: "最近成果", empty: "当前没有已确认成果。" },
} as const;

function WorkSummaryPanel() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; summary: WorkSummary }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  const load = async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", summary: await getWorkSummary() });
    } catch (caught) {
      setState({ kind: "error", message: isApiError(caught) ? caught.message : "工作摘要加载失败。" });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (state.kind === "loading") {
    return <div className="work-summary-grid" aria-busy="true" aria-label="正在加载工作摘要">
      {[0, 1, 2].map((item) => <div className="work-summary-card skeleton-card" key={item} />)}
    </div>;
  }
  if (state.kind === "error") {
    return <div className="inline-state" role="alert">
      <strong>工作摘要加载失败</strong><p>{state.message}</p>
      <button className="button secondary" type="button" onClick={load}>重试</button>
    </div>;
  }

  return <div className="work-summary-grid">
    {(Object.keys(workSectionMeta) as (keyof typeof workSectionMeta)[]).map((code) => {
      const section = state.summary.sections[code];
      const meta = workSectionMeta[code];
      return <article className="work-summary-card" key={code}>
        <div className="work-summary-heading">
          <h3>{meta.title}</h3>
          <strong>{section.available ? section.count : "—"}</strong>
        </div>
        {section.reason && <p className="work-summary-reason">{section.reason}</p>}
        {!section.available ? <p className="summary-empty">当前权限下暂不可汇总。</p>
          : section.items.length === 0 ? <p className="summary-empty">{meta.empty}</p>
            : <ul className="work-summary-list">{section.items.map((item) => <li key={`${item.kind}-${item.id}`}>
              <AppLink href={item.href}><strong>{item.title}</strong><span>{item.status} · {formatUpdatedAt(item.updated_at)}</span></AppLink>
            </li>)}</ul>}
      </article>;
    })}
  </div>;
}

function Workbench({ user }: { user: CurrentUser }) {
  const [hasBusinessAccess, setBusinessAccess] = useState(false);
  return (
    <main className="workspace">
      <section className="welcome-panel">
        <div>
          <p className="eyebrow">个人工作台</p>
          <h1>{user.display_name || user.username}，欢迎回来</h1>
          <p>平台仅展示当前账号已授权的业务模块，入口状态以服务端返回为准。</p>
        </div>
        <div className="identity-card" aria-label="当前用户信息">
          <span className="avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1)}</span>
          <div>
            <strong>{user.display_name || user.username}</strong>
            <span>@{user.username}</span>
          </div>
          <div className="role-list" aria-label="当前角色">
            {user.roles.length > 0 ? user.roles.map((role) => <span key={role.code}>{role.name}</span>) : <span>未分配角色</span>}
          </div>
        </div>
      </section>

      <section className="workspace-section" aria-labelledby="work-summary-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">个人待办</p>
            <h2 id="work-summary-title">工作摘要</h2>
          </div>
          <p>仅汇总当前账号在已授权模块中可见的真实任务、审批与成果。</p>
        </div>
        <WorkSummaryPanel />
      </section>

      <section className="workspace-section" aria-labelledby="modules-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">部门与经营工作台</p>
            <h2 id="modules-title">已授权业务入口</h2>
          </div>
        </div>
        <ModuleGrid onBusinessAccess={setBusinessAccess} />
      </section>

      {hasBusinessAccess && <section className="workspace-section summary-section" aria-labelledby="summary-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">只读信息</p>
            <h2 id="summary-title">经营摘要</h2>
          </div>
        </div>
        <BusinessSummaryPanel />
      </section>}
    </main>
  );
}

function ModuleDetailPage({ code, backHref }: { code: string; backHref: string }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; module: PortalModule }
    | { kind: "error"; message: string }
  >({ kind: "loading" });
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState("");

  const load = async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", module: await getModule(code) });
    } catch (caught) {
      setState({ kind: "error", message: isApiError(caught) ? caught.message : "模块详情加载失败。" });
    }
  };

  useEffect(() => {
    void load();
  }, [code]);

  if (state.kind === "loading") return <PageLoading label="正在加载模块详情" />;
  if (state.kind === "error") {
    return (
      <main className="workspace compact">
        <AppLink href={backHref} className="back-link">← 返回工作台</AppLink>
        <div className="inline-state" role="alert">
          <strong>模块详情加载失败</strong>
          <p>{state.message}</p>
          <button className="button secondary" type="button" onClick={load}>重试</button>
        </div>
      </main>
    );
  }

  const module = state.module;
  const meta = statusMeta[module.status];
  const unavailable = !module.enabled || module.status === "disabled" || module.status === "pending";

  const handleLaunch = async () => {
    if (unavailable || launching) return;
    setLaunching(true);
    setLaunchError("");
    try {
      window.location.assign(await launchModule(module.code));
    } catch (caught) {
      if (caught instanceof ApiError) {
        const fallback = caught.status === 503
          ? "目标系统当前离线。"
          : caught.status === 403
            ? "当前账号无权访问该模块。"
            : caught.status === 409
              ? "该模块仍在接入中。"
              : "入口请求失败，请稍后重试。";
        setLaunchError(caught.message || fallback);
      } else {
        setLaunchError("入口请求失败，请稍后重试。");
      }
      setLaunching(false);
    }
  };

  return (
    <main className="workspace compact">
      <AppLink href={backHref} className="back-link">← 返回工作台</AppLink>
      <article className="detail-card">
        <div className="detail-header">
          <div>
            <span className="module-code">业务入口</span>
            <h1>{module.name}</h1>
          </div>
          <StatusBadge module={module} />
        </div>
        <p className="detail-description">{module.description || "暂无说明。"}</p>
        <section className="access-panel" aria-labelledby="access-title">
          <div>
            <p className="eyebrow">接入说明</p>
            <h2 id="access-title">{meta.label}</h2>
           </div>
          <button className="button primary" type="button" onClick={handleLaunch} disabled={unavailable || launching}>
            {launching ? "正在确认入口…" : module.status === "pending" ? "待接入，暂不可进入" : unavailable ? "入口不可用" : "进入业务系统"}
          </button>
        </section>
        {launchError && <div className="notice error" role="alert">{launchError}</div>}
      </article>
    </main>
  );
}

function NotFoundPage() {
  return (
    <main className="state-page">
      <section className="state-card">
        <span className="state-symbol" aria-hidden="true">404</span>
        <h1>页面不存在</h1>
        <p>请返回工作台继续访问已授权模块。</p>
        <AppLink href="/" className="button primary">返回工作台</AppLink>
      </section>
    </main>
  );
}

function ForbiddenPage() {
  return (
    <main className="state-page">
      <section className="state-card" role="alert">
        <span className="state-symbol" aria-hidden="true">403</span>
        <h1>无运维访问权限</h1>
        <p>运维页面仅向平台管理员开放，后端仍会独立校验每次请求。</p>
        <AppLink href="/" className="button primary">返回工作台</AppLink>
      </section>
    </main>
  );
}

export default function App() {
  const { pathname, search, hash } = useLocation();
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [bootstrapError, setBootstrapError] = useState("");
  const [loginNotice, setLoginNotice] = useState("");
  const identityRefreshInFlight = useRef(false);

  const bootstrap = async (clearIdentity = false) => {
    if (clearIdentity && identityRefreshInFlight.current) return;
    if (clearIdentity) {
      identityRefreshInFlight.current = true;
      setUser(null);
    }
    setPhase("loading");
    setBootstrapError("");
    try {
      setUser(await getMe());
      setPhase("ready");
    } catch (caught) {
      if (isApiError(caught) && caught.status === 401) {
        setUser(null);
        setPhase("ready");
      } else {
        setBootstrapError(isApiError(caught) ? caught.message : "无法确认当前登录状态。请检查网络后重试。");
        setPhase("error");
      }
    } finally {
      if (clearIdentity) identityRefreshInFlight.current = false;
    }
  };

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    const handleUnauthorized = () => {
      clearApiSession();
      setUser(null);
      setLoginNotice("登录状态已过期，请重新登录。");
      navigate(loginPath(window.location.pathname, window.location.search, window.location.hash), true);
    };
    const handlePasswordRequired = () => {
      setUser((current) => current ? { ...current, must_change_password: true } : current);
      const returnTo = internalPath(`${window.location.pathname}${window.location.search}${window.location.hash}`);
      navigate(withNext("/password", returnTo), true);
    };
    const handleOpsPermissionRevoked = () => {
      void bootstrap(true);
    };
    window.addEventListener(unauthorizedEvent, handleUnauthorized);
    window.addEventListener(passwordChangeRequiredEvent, handlePasswordRequired);
    window.addEventListener(opsPermissionRevokedEvent, handleOpsPermissionRevoked);
    return () => {
      window.removeEventListener(unauthorizedEvent, handleUnauthorized);
      window.removeEventListener(passwordChangeRequiredEvent, handlePasswordRequired);
      window.removeEventListener(opsPermissionRevokedEvent, handleOpsPermissionRevoked);
    };
  }, []);

  let requiredPath = "";
  if (phase === "ready") {
    if (!user && pathname !== "/login") requiredPath = loginPath(pathname, search, hash);
    if (user?.must_change_password && pathname !== "/password") requiredPath = "/password";
    if (user && !user.must_change_password && pathname === "/login") requiredPath = authorizedReturnPath(user, search) || departmentHome(user);
    if (user?.is_platform_admin && !user.must_change_password && ["/", "/workspace", "/workspace/"].includes(pathname)) requiredPath = "/ops";
    if (user && !user.must_change_password && pathname === "/" && !user.is_platform_admin && departmentHome(user) !== "/") requiredPath = departmentHome(user);
    if (user && ["/centers/product/solution", "/centers/product/feasibility", "/centers/product/slides"].includes(pathname)) requiredPath = "/centers/product/documents";
    if (user?.is_platform_admin && ["/preview/product/solution", "/preview/product/feasibility", "/preview/product/slides"].includes(pathname)) requiredPath = "/preview/product/documents";
  }

  useEffect(() => {
    if (requiredPath) navigate(requiredPath, true);
  }, [requiredPath]);

  useEffect(() => {
    const match = pathname.match(/^\/(?:centers|preview)\/([^/]+)(?:\/([^/]+))?/);
    const config = match && isCenterCode(match[1]) ? centers[match[1]] : null;
    const section = config?.sections.find(item => item.code === (match?.[2] || "overview"));
    const title = config ? `${section?.title || "工作台"} · ${config.name}`
      : pathname === "/login" ? "登录" : pathname === "/password" ? "修改密码"
      : pathname.startsWith("/ops") ? "平台运维" : "我的工作台";
    document.title = `${title} · 企业统一门户`;
  }, [pathname]);

  if (phase === "loading" || requiredPath) return <PageLoading />;
  if (phase === "error") return <ErrorPage message={bootstrapError} onRetry={() => void bootstrap()} />;

  const handleAuthenticated = (currentUser: CurrentUser) => {
    setUser(currentUser);
    setLoginNotice("");
    navigate(currentUser.must_change_password ? withNext("/password", nextPath(search)) : authorizedReturnPath(currentUser, search) || departmentHome(currentUser), true);
  };

  const handleLogout = async () => {
    await logout();
    clearApiSession();
    setUser(null);
    setLoginNotice("");
    navigate("/login", true);
  };

  const handlePasswordChanged = (detail: string) => {
    clearApiSession();
    setUser(null);
    setLoginNotice(detail || "密码已修改，请重新登录。");
    navigate(withNext("/login", nextPath(search)), true);
  };

  if (!user) return <LoginPage notice={loginNotice} onAuthenticated={handleAuthenticated} />;

  if (pathname === "/password") {
    const page = <PasswordPage forced={user.must_change_password} onChanged={handlePasswordChanged} onLogout={handleLogout} />;
    return user.must_change_password ? page : <AppShell user={user} onLogout={handleLogout}>{page}</AppShell>;
  }

  let content: ReactNode;
  const moduleMatch = pathname.match(/^\/modules\/([^/]+)\/?$/);
  const centerMatch = pathname.match(/^\/(centers|preview)\/([a-zA-Z0-9_-]+)(?:\/([a-zA-Z0-9_-]+))?\/?$/);
  if (pathname === "/") content = <Workbench user={user} />;
  else if (pathname === "/ops" || pathname.startsWith("/ops/")) {
    content = user.is_platform_admin ? <OpsWorkspace user={user} pathname={pathname} /> : <ForbiddenPage />;
  }
  else if (centerMatch && isCenterCode(centerMatch[2])) {
    content = <CenterWorkspace key={`${centerMatch[1]}-${centerMatch[2]}-${centerMatch[3] || "overview"}`} code={centerMatch[2]} section={centerMatch[3] || "overview"} user={user} preview={centerMatch[1] === "preview"} businessPanel={<BusinessSummaryPanel />} />;
  }
  else if (moduleMatch) {
    let code = "";
    try {
      code = decodeURIComponent(moduleMatch[1]);
    } catch {
      code = "";
    }
    content = isModuleCode(code) ? <ModuleDetailPage key={code} code={code} backHref={user.is_platform_admin ? "/ops" : "/"} /> : (
      <main className="workspace compact">
        <div className="notice error" role="alert">模块参数无效，请从工作台重新选择入口。</div>
        <AppLink href="/" className="back-link">返回工作台</AppLink>
      </main>
    );
  }
  else content = <NotFoundPage />;

  return <AppShell user={user} onLogout={handleLogout}><WorkspaceBoundary resetKey={pathname}>{content}</WorkspaceBoundary></AppShell>;
}
