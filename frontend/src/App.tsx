import { FormEvent, MouseEvent, ReactNode, useEffect, useRef, useState } from "react";
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
import OpsWorkspace from "./ops/OpsWorkspace";
import Icon from "./Icon";
import ThemeSwitch from "./ThemeSwitch";
import './password-dialog.css';
import CenterWorkspace from "./centers/CenterWorkspace";
import HrHeaderTools from './hr/HrHeaderTools';
import { centers, isCenterCode } from "./centers/config";

const statusMeta = {
  pending: { label: "待接入", tone: "warning", detail: "入口尚在准备中，开放时间以平台通知为准。" },
  navigation: { label: "导航接入", tone: "info", detail: "平台提供统一导航，目标系统保留原有登录。" },
  verified: { label: "已验证集成", tone: "success", detail: "平台已完成入口验证，目标系统仍按自身认证策略运行。" },
  disabled: { label: "已停用", tone: "muted", detail: "该入口当前不可用，请联系平台管理员。" },
} as const;

function departmentHome(user: CurrentUser): string {
  if (user.is_platform_admin) return "/ops";
  const homes: Record<string, string> = { product: "/centers/product", hr: "/centers/hr", engineering: "/centers/cost", general_manager: "/centers/business" };
  const destinations = [...new Set(user.roles.map(role => homes[role.code]).filter(Boolean))];
  return destinations.length === 1 ? destinations[0] : "/";
}

function navigate(path: string, replace = false): void {
  if (`${window.location.pathname}${window.location.search}${window.location.hash}` === path) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export const navigationGuardEvent = "portal:navigation-guard";

function useLocation(): { pathname: string; search: string } {
  const [location, setLocation] = useState(() => ({ pathname: window.location.pathname, search: window.location.search }));
  const accepted = useRef(`${window.location.pathname}${window.location.search}${window.location.hash}`);
  useEffect(() => {
    const update = () => {
      const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (!window.dispatchEvent(new CustomEvent(navigationGuardEvent, { cancelable: true, detail: { from: accepted.current, to: next } }))) {
        window.history.replaceState({}, "", accepted.current);
        return;
      }
      accepted.current = next;
      setLocation({ pathname: window.location.pathname, search: window.location.search });
    };
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return location;
}

function AppLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  };
  return (
    <a href={href} className={className} onClick={handleClick}>
      {children}
    </a>
  );
}

function Brand() {
  if (/^\/(centers|preview)\/product(?:\/|$)/.test(window.location.pathname)) return <AppLink href="/" className="brand product-company-brand"><svg width="43" height="35" viewBox="0 0 74 52" aria-hidden="true"><path d="M12 40V13c0-10 14-10 18-2s12 8 18 1" stroke="#00a0e9" strokeWidth="13" strokeLinecap="round" fill="none"/><circle cx="12" cy="40" r="9" fill="#00a0e9"/><circle cx="46" cy="10" r="9" fill="#00a0e9"/><path d="M41 42c7-12 14 12 23 0" stroke="#ffbe00" strokeWidth="13" strokeLinecap="round" fill="none"/><circle cx="65" cy="10" r="8" fill="#ee1729"/></svg><span><strong>陕西一二三数字信息技术有限公司</strong><small>SHAANXI 123 DIGITAL INFORMATION TECHNOLOGY</small></span></AppLink>;
  return (
    <AppLink href="/" className="brand" aria-label="企业统一门户首页">
      <span className="brand-mark" aria-hidden="true"><Icon name="portal" /></span>
      <span>
        <strong>企业统一门户</strong>
        <small>统一入口 · 安全协同</small>
      </span>
    </AppLink>
  );
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
    <main className="auth-layout">
      <section className="auth-intro" aria-labelledby="login-title">
        <div className="auth-brand-row"><Brand /><ThemeSwitch /></div>
        <div>
          <p className="eyebrow">统一身份 · 授权访问</p>
          <h1 id="login-title">欢迎回来</h1>
          <p className="auth-lead">从一个入口访问已授权的业务系统，权限与可用状态均由平台后端确认。</p>
          <div className="auth-capabilities" aria-label="平台能力">
            <span><b>01</b>统一入口</span><span><b>02</b>按角色授权</span><span><b>03</b>独立运行</span>
          </div>
        </div>
        <p className="security-note">身份信息仅用于当前会话，不在浏览器本地保存。</p>
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
            <input
              id="username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              autoFocus
            />
            <label htmlFor="password">密码</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button className="button primary full" type="submit" disabled={pending}>
              {pending ? "正在验证…" : "登录"}
            </button>
          </form>
          <p className="form-footnote">如无法登录，请联系平台管理员核对账号状态。</p>
        </div>
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
    <div className={`app-shell${hrShell ? ' hr-app-shell' : ''}`}>
      <header className="topbar">
        <Brand />
        {hrShell && <nav className="hr-global-nav" aria-label="业务导航">
          <AppLink href="/">工作台</AppLink><AppLink href="/centers/product">产品事业部</AppLink>
          <span aria-disabled="true">招投标商机</span><AppLink href="/centers/hr" className="selected">人事部门</AppLink>
          <AppLink href="/centers/cost">工程管理</AppLink><AppLink href="/centers/business">经营管理</AppLink>
          {user.is_platform_admin && <AppLink href="/ops">系统管理</AppLink>}
        </nav>}
        {hrShell && user.roles.some(role => role.code === 'hr') && <HrHeaderTools />}
        <nav className="account-nav" aria-label="账户导航">
          <span className="account-name">{user.display_name || user.username}</span>
          {user.is_platform_admin && <AppLink href="/ops">运维工作台</AppLink>}
          {user.is_platform_admin && <AppLink href="/workspace">员工视图</AppLink>}
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
      {children}
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
            <p>{isCenterCode(module.code) ? centers[module.code].description : module.description || "暂无接入说明。"}</p>
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
  if (!value) return "接口未提供";
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
        <p>浏览器单点登录：未实现。旧系统保留原生账号登录与会话。</p>
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
        <div><dt>数据来源</dt><dd>{state.summary.source === "legacy-ledger:authorized-projects" ? "原经营系统 · 授权项目查询" : state.summary.source || "接口未提供"}</dd></div>
        <div><dt>更新时间</dt><dd>{formatUpdatedAt(state.summary.updated_at)}</dd></div>
      </dl>
      {metricKeys.length > 0 ? (
        <dl className="metric-grid">
          {metricKeys.map((key) => (
            <div key={key}>
              <dt>{summaryLabels[key]}</dt>
              <dd>{summary[key] === "" ? "接口返回空值" : summary[key]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="summary-empty">接口已启用，但暂无可展示的摘要项。</p>
      )}
      {metricKeys.some((key) => key !== "project_count") && (
        <p className="integration-note">金额按接口原值展示；接口未提供币种或计量单位，不作换算。</p>
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
      <p className="integration-note">浏览器单点登录：未实现。此处仅展示后端只读数据，不代表浏览器已登录旧业务系统。</p>
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
          <p>前端准备页可先使用；业务接入状态独立展示，不代表生成或审批已可用。</p>
        </div>
        <ModuleGrid onBusinessAccess={setBusinessAccess} />
      </section>

      {hasBusinessAccess && <section className="workspace-section summary-section" aria-labelledby="summary-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">只读信息</p>
            <h2 id="summary-title">经营摘要</h2>
          </div>
          <p>仅呈现接口真实返回，不补齐、不推断缺失数据。</p>
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
        <p className="detail-description">{module.description || "暂无接入说明。"}</p>
        <section className="access-panel" aria-labelledby="access-title">
          <div>
            <p className="eyebrow">接入说明</p>
            <h2 id="access-title">{meta.label}</h2>
            <p>{meta.detail}</p>
          </div>
          <button className="button primary" type="button" onClick={handleLaunch} disabled={unavailable || launching}>
            {launching ? "正在确认入口…" : module.status === "pending" ? "待接入，暂不可进入" : unavailable ? "入口不可用" : "进入业务系统"}
          </button>
        </section>
        {launchError && <div className="notice error" role="alert">{launchError}</div>}
        <div className="guardrail-note">
          <strong>集成边界</strong>
          <p>点击后平台会先向后端请求入口，不会由浏览器探测旧站。</p>
          <dl className="boundary-list">
            <div>
              <dt>浏览器单点登录</dt>
              <dd><span className="status muted">未实现</span>旧系统保留自身登录流程。</dd>
            </div>
            <div>
              <dt>撤权生效范围</dt>
              <dd>撤权仅影响门户及平台发起调用的下一次请求，不会注销旧系统原生账号会话。</dd>
            </div>
          </dl>
        </div>
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
  const { pathname } = useLocation();
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
      navigate("/login", true);
    };
    const handlePasswordRequired = () => {
      setUser((current) => current ? { ...current, must_change_password: true } : current);
      navigate("/password", true);
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
    if (!user && pathname !== "/login") requiredPath = "/login";
    if (user?.must_change_password && pathname !== "/password") requiredPath = "/password";
    if (user && !user.must_change_password && pathname === "/login") requiredPath = departmentHome(user);
    if (user?.is_platform_admin && !user.must_change_password && pathname === "/") requiredPath = "/ops";
    if (user && ["/centers/product/solution", "/centers/product/feasibility", "/centers/product/slides"].includes(pathname)) requiredPath = "/centers/product/documents";
    if (user?.is_platform_admin && ["/preview/product/solution", "/preview/product/feasibility", "/preview/product/slides"].includes(pathname)) requiredPath = "/preview/product/documents";
  }

  useEffect(() => {
    if (requiredPath) navigate(requiredPath, true);
  }, [requiredPath]);

  if (phase === "loading" || requiredPath) return <PageLoading />;
  if (phase === "error") return <ErrorPage message={bootstrapError} onRetry={() => void bootstrap()} />;

  const handleAuthenticated = (currentUser: CurrentUser) => {
    setUser(currentUser);
    setLoginNotice("");
    navigate(currentUser.must_change_password ? "/password" : departmentHome(currentUser), true);
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
    navigate("/login", true);
  };

  if (!user) return <LoginPage notice={loginNotice} onAuthenticated={handleAuthenticated} />;

  if (pathname === "/password") {
    const page = <PasswordPage forced={user.must_change_password} onChanged={handlePasswordChanged} onLogout={handleLogout} />;
    return user.must_change_password ? page : <AppShell user={user} onLogout={handleLogout}>{page}</AppShell>;
  }

  let content: ReactNode;
  const moduleMatch = pathname.match(/^\/modules\/([^/]+)\/?$/);
  const centerMatch = pathname.match(/^\/(centers|preview)\/([a-zA-Z0-9_-]+)(?:\/([a-zA-Z0-9_-]+))?\/?$/);
  if (pathname === "/" || (user.is_platform_admin && pathname === "/workspace")) content = <Workbench user={user} />;
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
    content = isModuleCode(code) ? <ModuleDetailPage key={code} code={code} backHref={user.is_platform_admin ? "/workspace" : "/"} /> : (
      <main className="workspace compact">
        <div className="notice error" role="alert">模块参数无效，请从工作台重新选择入口。</div>
        <AppLink href="/" className="back-link">返回工作台</AppLink>
      </main>
    );
  }
  else content = <NotFoundPage />;

  return <AppShell user={user} onLogout={handleLogout}>{content}</AppShell>;
}
