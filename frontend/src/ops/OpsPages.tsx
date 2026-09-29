import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isApiError } from "../api";
import {
  AuditTable,
  auditActions,
  DefinitionList,
  Drawer,
  formatDateTime,
  formatErrorRate,
  formatMetric,
  OpsLink,
  PageHeader,
  Pagination,
  RangeTabs,
  readDays,
  readPage,
  safeAdminHref,
  SearchForm,
  setOpsQuery,
  statusLabel,
  StatePanel,
  StatusText,
  TrendChart,
  useOpsData,
  useOpsSearch,
} from "./components";
import {
  checkOpsModule,
  getOpsIssue,
  getOpsIssues,
  getOpsMaintenance,
  getOpsModules,
  getOpsOverview,
  getOpsUsage,
  getOpsUser,
  getOpsUsers,
  OpsIssue,
  OpsMaintenance,
  OpsModule,
  OpsRangeDays,
  OpsUserDetail,
  updateOpsIssue,
} from "./api";

function MetricCard({ label, value, note, href }: { label: string; value: string; note: string; href: string }) {
  return (
    <OpsLink className="ops-metric-card" href={href}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </OpsLink>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="ops-panel">
      <header className="ops-panel-heading"><h2>{title}</h2>{action}</header>
      {children}
    </section>
  );
}

export function OverviewPage() {
  const search = useOpsSearch();
  const days = readDays(search.get("days"));
  const [state, refresh] = useOpsData(() => getOpsOverview(days), [days]);
  const data = state.kind === "ready" ? state.data : null;
  return (
    <>
      <PageHeader
        eyebrow="平台运行"
        title="运维总览"
        description="仅呈现平台真实采集、检查与审计结果；未采集的数据保持明确空缺。"
        updatedAt={data?.updated_at}
        onRefresh={refresh}
      >
        <RangeTabs value={days} />
      </PageHeader>
      <StatePanel state={state} onRetry={refresh}>
        {(overview) => (
          <div className="ops-page-stack">
            <section className="ops-metric-grid" aria-label="运维核心指标">
              <MetricCard label="平台健康" value={statusLabel(overview.health.state)} note={overview.health.message || "当前连接检查"} href="/ops/maintenance?tab=diagnostics" />
              <MetricCard label="启用账号" value={`${overview.accounts.enabled} / ${overview.accounts.total}`} note="当前快照，不是在线人数" href="/ops/people?status=active" />
              <MetricCard label="登录活跃人数" value={formatMetric(overview.usage.login_users)} note={`${days}天内成功登录去重`} href={`/ops/usage?days=${days}`} />
              <MetricCard label="成功登录次数" value={formatMetric(overview.usage.login_count)} note="仅成功登录审计事件" href={`/ops/usage?days=${days}`} />
              <MetricCard label="模块启动次数" value={formatMetric(overview.usage.module_launches)} note="不是业务产出或绩效" href={`/ops/usage?days=${days}`} />
              <MetricCard label="当前问题" value={formatMetric(overview.issue_count)} note="人工关闭不代表恢复" href={`/ops/issues?days=${days}`} />
              <MetricCard
                label="平均响应耗时"
                value={formatMetric(overview.performance.average_ms, " 毫秒")}
                note={overview.performance.state === "not_collected"
                  ? `最近 ${Math.round(overview.performance.window_seconds / 60)} 分钟未采集`
                  : `错误率 ${formatErrorRate(overview.performance.error_rate)}（错误请求数 ÷ 有效接口请求数）`}
                href="/ops/maintenance?tab=diagnostics"
              />
            </section>

            <div className="ops-range-note">统计范围：{formatDateTime(overview.range.start)} 至 {formatDateTime(overview.range.end)}（按平台时区自然日统计）</div>

            <Section title="登录与模块启动趋势">
              <TrendChart points={overview.trend} days={days} />
            </Section>

            <div className="ops-two-column">
              <Section title="模块状态" action={<OpsLink className="ops-inline-link" href="/ops/modules">查看全部</OpsLink>}>
                {overview.modules.length === 0 ? <p className="ops-inline-empty">接口未返回模块信息。</p> : (
                  <ul className="ops-summary-list">
                    {overview.modules.map((module) => (
                      <li key={module.code}>
                        <span><strong>{module.name}</strong><small>业务模块</small></span>
                        <StatusText value={module.check?.state ?? module.status} />
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
              <Section title="最近问题" action={<OpsLink className="ops-inline-link" href="/ops/issues">进入问题中心</OpsLink>}>
                {overview.recent_issues.length === 0 ? <p className="ops-inline-empty">暂无最近问题。</p> : (
                  <ul className="ops-summary-list">
                    {overview.recent_issues.map((issue) => (
                      <li key={issue.id}>
                        <OpsLink href={`/ops/issues?issue=${issue.id}`}><strong>{issue.title}</strong><small>{issue.module_name} · {formatDateTime(issue.last_seen)}</small></OpsLink>
                        <StatusText value={issue.status} />
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            </div>

            <div className="ops-two-column">
              <Section title="备份与版本">
                <dl className="ops-compact-definitions">
                  <div><dt>备份演练</dt><dd><StatusText value={overview.backup.state} /></dd></div>
                  <div><dt>备份说明</dt><dd>{overview.backup.message || "未接入"}</dd></div>
                  <div><dt>版本</dt><dd>{overview.version || "未接入"}</dd></div>
                </dl>
              </Section>
              <Section title="本人业务授权">
                {overview.my_modules.length === 0 ? <p className="ops-inline-empty">管理员不自动获得业务内容；当前无本人业务授权。</p> : (
                  <ul className="ops-summary-list">
                    {overview.my_modules.map((module) => <li key={module.code}><strong>{module.name}</strong><StatusText value={module.enabled ? module.status : "disabled"} /></li>)}
                  </ul>
                )}
              </Section>
            </div>

            <Section title="最近审计" action={<OpsLink className="ops-inline-link" href="/ops/maintenance?tab=audit">查看完整审计</OpsLink>}>
              <AuditTable rows={overview.recent_audit} />
            </Section>
          </div>
        )}
      </StatePanel>
    </>
  );
}

function UserDrawer({ id, opener, onClose }: { id: string; opener: HTMLElement | null; onClose: () => void }) {
  const [state, refresh] = useOpsData<OpsUserDetail>(() => getOpsUser(id), [id]);
  return (
    <Drawer title="人员详情" open opener={opener} onClose={onClose}>
      <StatePanel state={state} onRetry={refresh}>
        {(user) => (
          <div className="ops-drawer-stack">
            <div className="ops-person-heading">
              <span>{(user.display_name || user.username).slice(0, 1)}</span>
              <div><h3>{user.display_name || user.username}</h3><p>@{user.username}</p></div>
            </div>
            <dl className="ops-compact-definitions">
              <div><dt>账号状态</dt><dd><StatusText value={user.is_active ? "active" : "inactive"} /></dd></div>
              <div><dt>首次改密</dt><dd>{user.must_change_password ? "待完成" : "已完成"}</dd></div>
              <div><dt>最近登录</dt><dd>{formatDateTime(user.last_login)}</dd></div>
              <div><dt>角色</dt><dd>{user.roles.map((role) => role.name).join("、") || "未分配"}</dd></div>
            </dl>
            <div className="ops-admin-actions">
              {safeAdminHref(user.admin_url) && <a className="button secondary compact" href={user.admin_url}>编辑账号与权限</a>}
              {safeAdminHref(user.password_url) && <a className="button secondary compact" href={user.password_url}>重置密码</a>}
            </div>
            <div className="ops-callout">编辑与重置将在管理后台完成。重置密码后，用户下次登录需修改密码。</div>
            <h3>最近审计</h3>
            <AuditTable rows={user.recent_audit} />
          </div>
        )}
      </StatePanel>
    </Drawer>
  );
}

export function PeoplePage() {
  const search = useOpsSearch();
  const q = search.get("q") ?? "";
  const status = search.get("status") ?? "all";
  const role = search.get("role") ?? "";
  const page = readPage(search.get("page"));
  const selectedId = search.get("user") ?? "";
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const closeDrawer = useCallback(() => setOpsQuery({ user: null }), []);
  const [state, refresh] = useOpsData(() => getOpsUsers({ q, status, role, page }), [q, status, role, page]);
  const data = state.kind === "ready" ? state.data : null;
  return (
    <>
      <PageHeader
        eyebrow="账号管理"
        title="人员与权限"
        description="搜索人员后，可直接编辑账号、调整角色或启停；点击详情查看授权和审计。"
        onRefresh={refresh}
      >
        <a className="button secondary compact" href="/admin/portal/role/">管理角色</a>
        <a className="button primary compact" href="/admin/portal/user/add/">新增账号</a>
      </PageHeader>
      <div className="ops-filterbar">
        <SearchForm initialValue={q} label="搜索用户名或显示名称" onSubmit={(value) => setOpsQuery({ q: value || null }, { resetPage: true })} />
        <label>状态<select value={status} onChange={(event) => setOpsQuery({ status: event.target.value }, { resetPage: true })}>
          <option value="all">全部状态</option><option value="active">启用</option><option value="inactive">停用</option>
        </select></label>
        <label>角色<select value={role} onChange={(event) => setOpsQuery({ role: event.target.value || null }, { resetPage: true })}>
          <option value="">全部角色</option>
          {data?.roles.map((item) => <option value={item.code} key={item.code}>{item.name}</option>)}
        </select></label>
        {(q || status !== "all" || role) && <button className="button secondary compact" type="button" onClick={() => setOpsQuery({ q: null, status: null, role: null, page: null, user: null })}>清空筛选</button>}
      </div>
      <StatePanel state={state} onRetry={refresh} empty={(result) => result.items.length === 0}>
        {(result) => (
          <section className="ops-panel">
            <div className="ops-table-wrap" role="region" aria-label="人员表格，可横向滚动" tabIndex={0}>
              <table className="ops-table interactive">
                <thead><tr><th>人员</th><th>角色</th><th>状态</th><th>首次改密</th><th>最近登录</th><th><span className="sr-only">操作</span></th></tr></thead>
                <tbody>
                  {result.items.map((user) => (
                    <tr key={user.id}>
                      <td><strong>{user.display_name || user.username}</strong><small>@{user.username}</small></td>
                      <td>{user.roles.map((item) => item.name).join("、") || "未分配"}</td>
                      <td><StatusText value={user.is_active ? "active" : "inactive"} /></td>
                      <td>{user.must_change_password ? "待完成" : "已完成"}</td>
                      <td>{formatDateTime(user.last_login)}</td>
                      <td><div className="ops-row-actions">
                        <button type="button" className="ops-table-button" onClick={(event) => { setOpener(event.currentTarget); setOpsQuery({ user: user.id }); }}>查看详情</button>
                        {safeAdminHref(user.admin_url) && <a className="ops-table-button" href={user.admin_url}>编辑账号</a>}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={result} />
          </section>
        )}
      </StatePanel>
      {selectedId && <UserDrawer id={selectedId} opener={opener} onClose={closeDrawer} />}
    </>
  );
}

export function UsagePage() {
  const search = useOpsSearch();
  const days = readDays(search.get("days"));
  const module = search.get("module") ?? "";
  const selectedDate = search.get("date") ?? "";
  const closeDrawer = useCallback(() => setOpsQuery({ date: null }), []);
  const [state, refresh] = useOpsData(async () => {
    const [usage, modules] = await Promise.all([getOpsUsage(days, module), getOpsModules()]);
    return { usage, modules };
  }, [days, module]);
  const data = state.kind === "ready" ? state.data : null;
  const day = data?.usage.trend.find((point) => point.date === selectedDate);
  return (
    <>
      <PageHeader
        eyebrow="使用统计"
        title="业务与使用分析"
        description="登录与业务模型指标始终按全平台统计；模块筛选只影响模块启动次数，不代表业务使用、产出或绩效。"
        updatedAt={data?.usage.updated_at}
        onRefresh={refresh}
      >
        <RangeTabs value={days} />
      </PageHeader>
      <div className="ops-filterbar compact-bar">
        <label>模块<select value={module} onChange={(event) => setOpsQuery({ module: event.target.value || null, date: null })}>
          <option value="">全部模块</option>
          {data?.modules.items.map((item) => <option value={item.code} key={item.code}>{item.name}</option>)}
        </select></label>
        <span className="ops-filter-note">当前筛选只作用于“模块启动次数”，不影响模型统计。</span>
      </div>
      <StatePanel state={state} onRetry={refresh}>
        {({ usage }) => {
          const employees = usage.employees ?? [];
          const modelUsage = usage.model_usage;
          return (
            <div className="ops-page-stack ops-usage-dashboard">
              <section className="ops-metric-grid four">
                <MetricCard label="启用账号" value={formatMetric(usage.summary.enabled_accounts)} note="当前账号快照" href="/ops/people?status=active" />
                <MetricCard label="登录活跃人数" value={formatMetric(usage.summary.login_users)} note="全平台成功登录去重" href={`/ops/usage?days=${days}`} />
                <MetricCard label="成功登录次数" value={formatMetric(usage.summary.login_count)} note="全平台登录指标" href={`/ops/usage?days=${days}`} />
                <MetricCard label="模块启动次数" value={formatMetric(usage.summary.module_launches)} note={module ? `仅 ${module}` : "全部模块"} href={`/ops/usage?days=${days}${module ? `&module=${module}` : ""}`} />
              </section>
              <Section title="每日趋势"><TrendChart points={usage.trend} days={days} module={module} /></Section>
              <div className="ops-usage-details">
              <Section title="员工活动榜">
                <div className="ops-callout compact">仅统计非平台管理员的成功登录与模块启动事件，最多展示20人；模块筛选只影响启动次数。</div>
                {employees.length === 0 ? <p className="ops-inline-empty" role="status">当前范围无员工成功活动记录。</p> : (
                  <div className="ops-table-wrap" role="region" aria-label="员工活动表格，可横向滚动" tabIndex={0}>
                    <table className="ops-table">
                      <thead><tr><th scope="col">排名</th><th scope="col">员工</th><th scope="col">成功登录</th><th scope="col">模块启动</th></tr></thead>
                      <tbody>
                        {employees.map((employee, index) => (
                          <tr key={employee.id}>
                            <td>{index + 1}</td>
                            <td><strong>{employee.display_name || employee.username}</strong><small>@{employee.username}</small></td>
                            <td>{formatMetric(employee.login_count, " 次")}</td>
                            <td>{formatMetric(employee.module_launches, " 次")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
              <Section
                title="业务模型调用统计"
                action={<span><a className="ops-inline-link" href="/admin/portal/modelroute/">模型后台维护</a>{" · "}<a className="ops-inline-link" href="/admin/portal/modelcalllog/">调用日志审查</a></span>}
              >
                <div className="ops-callout compact">仅统计业务调用，处理中调用不计为失败；全部数据均为全平台口径，不受模块筛选影响。</div>
                {!modelUsage ? <p className="ops-inline-empty" role="status">当前范围暂无业务模型调用统计。</p> : <>
                  <div className="ops-metric-grid flat ops-model-metrics" aria-label="业务模型调用汇总">
                    <div><span>启用路由</span><strong>{formatMetric(modelUsage.enabled_routes)}</strong></div>
                    <div><span>调用次数</span><strong>{formatMetric(modelUsage.calls)}</strong></div>
                    <div><span>成功次数</span><strong>{formatMetric(modelUsage.successes)}</strong></div>
                    <div><span>失败次数</span><strong>{formatMetric(modelUsage.failures)}</strong></div>
                    <div><span>输入 Token</span><strong>{formatMetric(modelUsage.prompt_tokens)}</strong></div>
                    <div><span>输出 Token</span><strong>{formatMetric(modelUsage.completion_tokens)}</strong></div>
                  </div>
                  {modelUsage.routes.length === 0 ? <p className="ops-inline-empty" role="status">当前范围无业务模型调用记录。</p> : (
                    <div className="ops-table-wrap" role="region" aria-label="业务模型路由调用表格，可横向滚动" tabIndex={0}>
                      <table className="ops-table">
                        <thead><tr><th scope="col">路由</th><th scope="col">调用</th><th scope="col">成功</th><th scope="col">失败</th></tr></thead>
                        <tbody>
                          {modelUsage.routes.map((route) => (
                            <tr key={route.code}>
                              <td><strong>{route.name || route.code}</strong><small>{route.code}</small></td>
                              <td>{formatMetric(route.calls)}</td>
                              <td>{formatMetric(route.successes)}</td>
                              <td>{formatMetric(route.failures)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>}
              </Section>
              </div>
              <div className="ops-two-column">
                <Section title="模块启动排行">
                  {usage.ranking.length === 0 ? <p className="ops-inline-empty">当前范围无模块启动记录。</p> : (
                    <ol className="ops-ranking">
                      {usage.ranking.map((item, index) => <li key={item.code}><span><b>{index + 1}</b>{item.name}</span><strong>{item.launches} 次</strong></li>)}
                    </ol>
                  )}
                </Section>
                <Section title="统计口径"><details className="ops-usage-definitions"><summary>查看指标定义与统计范围</summary><DefinitionList values={usage.definitions} /></details></Section>
              </div>
            </div>
          );
        }}
      </StatePanel>
      <Drawer title={`${selectedDate || "当日"} 使用明细`} open={Boolean(selectedDate && day)} opener={null} onClose={closeDrawer}>
        {day && <div className="ops-drawer-stack">
          <div className="ops-callout">模块筛选只影响启动次数；登录人数和次数仍为全平台口径。</div>
          <dl className="ops-compact-definitions">
            <div><dt>登录活跃人数</dt><dd>{day.login_users}</dd></div>
            <div><dt>成功登录次数</dt><dd>{day.login_count}</dd></div>
            <div><dt>模块启动次数</dt><dd>{day.module_launches}</dd></div>
          </dl>
          <DefinitionList values={data?.usage.definitions ?? {}} />
        </div>}
      </Drawer>
    </>
  );
}

export function ModulesPage() {
  const [state, refresh] = useOpsData(getOpsModules, []);
  const [overrides, setOverrides] = useState<Record<string, OpsModule>>({});
  const [checking, setChecking] = useState<Set<string>>(new Set());
  const checkingRef = useRef(new Set<string>());
  const [messages, setMessages] = useState<Record<string, { tone: "success" | "error"; text: string; issueId?: number | string }>>({});
  const data = state.kind === "ready" ? state.data : null;

  const runCheck = async (code: string) => {
    if (checkingRef.current.has(code)) return;
    checkingRef.current.add(code);
    setChecking((current) => new Set(current).add(code));
    setMessages((current) => ({ ...current, [code]: { tone: "success", text: "正在执行固定目标检查…" } }));
    try {
      const result = await checkOpsModule(code);
      setOverrides((current) => ({ ...current, [code]: result.module }));
      setMessages((current) => ({
        ...current,
        [code]: {
          tone: result.issue ? "error" : "success",
          text: result.issue ? "检查完成，已关联运维问题。" : "检查完成，当前未产生问题。",
          issueId: result.issue?.id,
        },
      }));
    } catch (error) {
      setMessages((current) => ({
        ...current,
        [code]: { tone: "error", text: isApiError(error) ? error.message : "检查请求失败，请稍后重试。" },
      }));
    } finally {
      checkingRef.current.delete(code);
      setChecking((current) => {
        const next = new Set(current);
        next.delete(code);
        return next;
      });
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="业务接入"
        title="模块与接入管理"
        description="展示后端返回的四个固定模块与真实检查结果；浏览器不接受自定义 URL，也不执行业务重放。"
        updatedAt={data?.updated_at}
        onRefresh={refresh}
      />
      <StatePanel state={state} onRetry={refresh} empty={(result) => result.items.length === 0}>
        {(result) => (
          <div className="ops-page-stack">
            {result.items.length !== 4 && <div className="ops-callout warning">后端当前返回 {result.items.length} 个固定模块；前端未补造缺失模块。</div>}
            <section className="ops-module-grid">
              {result.items.map((source) => {
                const module = overrides[source.code] ?? source;
                const adminUrl = safeAdminHref(module.admin_url);
                const pending = checking.has(module.code);
                const coolingDown = Boolean(module.check?.next_check_at && Date.parse(module.check.next_check_at) > Date.now());
                const message = messages[module.code];
                return (
                  <article className="ops-module-card" key={module.code}>
                    <header>
                      <div><span className="ops-code">业务模块</span><h2>{module.name}</h2></div>
                      <StatusText value={module.enabled ? module.status : "disabled"} />
                    </header>
                    <p>{module.description || "接口未提供接入说明。"}</p>
                    <dl className="ops-compact-definitions">
                      <div><dt>入口</dt><dd>{module.url || "未配置"}</dd></div>
                      <div><dt>最近检查</dt><dd>{module.check ? formatDateTime(module.check.checked_at) : "未检查"}</dd></div>
                      <div><dt>检查状态</dt><dd>{module.check ? <StatusText value={module.check.state} /> : "未检查"}</dd></div>
                      <div><dt>耗时</dt><dd>{formatMetric(module.check?.duration_ms, " 毫秒")}</dd></div>
                      <div><dt>下次可检查</dt><dd>{module.check?.next_check_at ? formatDateTime(module.check.next_check_at) : "当前可检查"}</dd></div>
                    </dl>
                    {module.check && <p className="ops-module-message">{module.check.message}{module.check.stale ? "（配置已变化，结果已过期）" : ""}</p>}
                    <div className="ops-callout compact">依赖链：接口未提供已配置依赖，因此不绘制推测关系。</div>
                    {message && <div className={`ops-inline-message ${message.tone}`} role="status">
                      {message.text} {message.issueId && <OpsLink href={`/ops/issues?issue=${message.issueId}`}>查看问题</OpsLink>}
                    </div>}
                    <footer>
                      <button className="button primary compact" type="button" disabled={pending || coolingDown} onClick={() => void runCheck(module.code)}>
                        {pending ? "检查中…" : coolingDown ? "检查冷却中" : "执行固定检查"}
                      </button>
                      {adminUrl && <a className="button secondary compact" href={adminUrl}>配置模块</a>}
                    </footer>
                    <small className="ops-boundary-note">停用入口仅阻止门户访问，不等于停止目标服务。</small>
                  </article>
                );
              })}
            </section>
          </div>
        )}
      </StatePanel>
    </>
  );
}

function IssueDrawer({ id, opener, onClose, onUpdated }: {
  id: string;
  opener: HTMLElement | null;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [state, refresh] = useOpsData<OpsIssue>(() => getOpsIssue(id), [id]);
  const [status, setStatus] = useState<"" | "open" | "investigating" | "closed">("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const issue = state.kind === "ready" ? state.data : null;

  useEffect(() => {
    if (!issue) return;
    setStatus(issue.status === "recovered" ? "" : issue.status);
    setNote("");
  }, [issue?.id, issue?.status]);

  useEffect(() => setMessage(null), [issue?.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!issue || pendingRef.current) return;
    const trimmedNote = note.trim();
    const nextStatus = status && status !== issue.status ? status : undefined;
    if (!nextStatus && !trimmedNote) {
      setMessage({ tone: "error", text: "请选择新的处理状态或填写备注。" });
      return;
    }
    if (nextStatus === "closed" && !window.confirm("人工关闭只表示停止跟踪，不表示服务已恢复。确认关闭此问题？")) return;
    pendingRef.current = true;
    setPending(true);
    setMessage(null);
    try {
      await updateOpsIssue(issue.id, { status: nextStatus, note: trimmedNote || undefined });
      setMessage({ tone: "success", text: "问题记录已更新。" });
      setNote("");
      refresh();
      onUpdated();
    } catch (error) {
      setMessage({ tone: "error", text: isApiError(error) ? error.message : "更新失败，请稍后重试。" });
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return (
    <Drawer title="问题详情" open opener={opener} onClose={onClose}>
      <StatePanel state={state} onRetry={refresh}>
        {(detail) => (
          <div className="ops-drawer-stack">
            <div><span className="ops-code">{detail.module_code}</span><h3>{detail.title}</h3><p>{detail.module_name}</p></div>
            <div className="ops-status-row"><StatusText value={detail.severity} /><StatusText value={detail.status} /><StatusText value={detail.health_state} /></div>
            <dl className="ops-compact-definitions">
              <div><dt>首次发生</dt><dd>{formatDateTime(detail.first_seen)}</dd></div>
              <div><dt>最近发生</dt><dd>{formatDateTime(detail.last_seen)}</dd></div>
              <div><dt>发生次数</dt><dd>{detail.occurrences}</dd></div>
              <div><dt>最近检查</dt><dd>{formatDateTime(detail.checked_at)}</dd></div>
              <div className="wide"><dt>安全证据</dt><dd>{detail.evidence || "未提供"}</dd></div>
            </dl>
            {safeAdminHref(detail.admin_url) && <a className="button secondary compact" href={detail.admin_url}>在 管理后台 查看模块配置</a>}
            <section><h3>处理备注</h3>{detail.notes.length === 0 ? <p className="ops-inline-empty">暂无处理备注。</p> : (
              <ol className="ops-notes">{detail.notes.map((item, index) => <li key={`${item.at}-${index}`}><p>{item.text}</p><small>{item.actor} · {formatDateTime(item.at)}</small></li>)}</ol>
            )}</section>
            <form className="ops-issue-form" onSubmit={submit}>
              <h3>更新处理记录</h3>
              <label>处理状态<select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
                {detail.status === "recovered" && <option value="">保持已恢复</option>}
                <option value="open">待处理</option><option value="investigating">处理中</option><option value="closed">人工关闭</option>
              </select></label>
              <label>备注<textarea value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} placeholder="最多500字；请勿填写凭据、票据或敏感原文" /></label>
              <p className="ops-form-hint">人工关闭不改变探测健康结果，也不等于服务恢复。</p>
              {message && <div className={`ops-inline-message ${message.tone}`} role={message.tone === "error" ? "alert" : "status"}>{message.text}</div>}
              <button className="button primary compact" type="submit" disabled={pending}>{pending ? "保存中…" : "保存更新"}</button>
            </form>
          </div>
        )}
      </StatePanel>
    </Drawer>
  );
}

export function IssuesPage() {
  const search = useOpsSearch();
  const severity = search.get("severity") ?? "all";
  const status = search.get("status") ?? "all";
  const module = search.get("module") ?? "";
  const days = readDays(search.get("days"), 30);
  const page = readPage(search.get("page"));
  const selectedId = search.get("issue") ?? "";
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const closeDrawer = useCallback(() => setOpsQuery({ issue: null }), []);
  const [state, refresh] = useOpsData(async () => {
    const [issues, modules] = await Promise.all([
      getOpsIssues({ severity, status, module, days, page }),
      getOpsModules(),
    ]);
    return { issues, modules };
  }, [severity, status, module, days, page]);
  const data = state.kind === "ready" ? state.data : null;
  const severityOptions = useMemo(() => {
    const values = new Set(data?.issues.items.map((item) => item.severity) ?? []);
    if (severity !== "all") values.add(severity);
    return [...values].filter(Boolean).sort();
  }, [data, severity]);
  return (
    <>
      <PageHeader
        eyebrow="异常处理"
        title="问题中心"
        description="按真实探测结果聚合问题、证据与备注；人工关闭和检查恢复保留为不同状态。"
        onRefresh={refresh}
      >
        <RangeTabs value={days} />
      </PageHeader>
      <div className="ops-filterbar issue-filters">
        <label>严重级别<select value={severity} onChange={(event) => setOpsQuery({ severity: event.target.value }, { resetPage: true })}>
          <option value="all">全部级别</option>{severityOptions.map((value) => <option value={value} key={value}>{statusLabel(value)}</option>)}
        </select></label>
        <label>处理状态<select value={status} onChange={(event) => setOpsQuery({ status: event.target.value }, { resetPage: true })}>
          <option value="all">全部状态</option><option value="open">待处理</option><option value="investigating">处理中</option><option value="closed">人工关闭</option><option value="recovered">已恢复</option>
        </select></label>
        <label>模块<select value={module} onChange={(event) => setOpsQuery({ module: event.target.value || null }, { resetPage: true })}>
          <option value="">全部模块</option>{data?.modules.items.map((item) => <option value={item.code} key={item.code}>{item.name}</option>)}
        </select></label>
      </div>
      <StatePanel state={state} onRetry={refresh} empty={(result) => result.issues.items.length === 0}>
        {(result) => (
          <section className="ops-panel">
            <div className="ops-table-wrap" role="region" aria-label="问题表格，可横向滚动" tabIndex={0}>
              <table className="ops-table interactive">
                <thead><tr><th>问题</th><th>模块</th><th>级别</th><th>处理状态</th><th>健康状态</th><th>最近发生</th><th>次数</th><th><span className="sr-only">操作</span></th></tr></thead>
                <tbody>{result.issues.items.map((issue) => (
                  <tr key={issue.id}>
                    <td><strong>{issue.title}</strong><small>{issue.evidence || "未提供证据"}</small></td>
                    <td>{issue.module_name}</td><td><StatusText value={issue.severity} /></td><td><StatusText value={issue.status} /></td><td><StatusText value={issue.health_state} /></td>
                    <td>{formatDateTime(issue.last_seen)}</td><td>{issue.occurrences}</td>
                    <td><button className="ops-table-button" type="button" onClick={(event) => { setOpener(event.currentTarget); setOpsQuery({ issue: issue.id }); }}>查看详情</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <Pagination page={result.issues} />
          </section>
        )}
      </StatePanel>
      {selectedId && <IssueDrawer id={selectedId} opener={opener} onClose={closeDrawer} onUpdated={refresh} />}
    </>
  );
}

function PerformancePanel({ data }: { data: OpsMaintenance["performance"] }) {
  const scope = data.scope === "single_process_api" ? "单应用进程有效接口请求" : data.scope;
  return (
    <div className="ops-page-stack compact-stack">
      <div className="ops-status-row"><StatusText value={data.state} /><span>{scope}</span><span>窗口 {data.window_seconds} 秒</span><span>采集始于 {formatDateTime(data.collected_since)}</span></div>
      <dl className="ops-metric-grid four flat">
        <div><span>请求</span><strong>{data.requests}</strong></div><div><span>错误</span><strong>{data.errors}</strong></div>
        <div><span>错误率</span><strong>{formatErrorRate(data.error_rate)}</strong></div><div><span>平均耗时</span><strong>{formatMetric(data.average_ms, " 毫秒")}</strong></div>
        <div><span>最大耗时</span><strong>{formatMetric(data.max_ms, " 毫秒")}</strong></div>
      </dl>
      <div className="ops-callout compact">错误率 = 错误请求数 ÷ 最近 {Math.round(data.window_seconds / 60)} 分钟有效接口请求数。</div>
      {data.routes.length === 0 ? <p className="ops-inline-empty">最近15分钟未采集到有效接口请求。</p> : (
        <div className="ops-table-wrap" role="region" aria-label="诊断路由表格，可横向滚动" tabIndex={0}><table className="ops-table"><thead><tr><th>方法</th><th>路由模板</th><th>请求</th><th>错误</th><th>平均耗时</th></tr></thead><tbody>
          {data.routes.map((row, index) => <tr key={index}><td>{String(row.method ?? "—")}</td><td>{String(row.route ?? row.path ?? "—")}</td><td>{String(row.requests ?? row.count ?? "—")}</td><td>{String(row.errors ?? "—")}</td><td>{row.average_ms === null || row.average_ms === undefined ? "未采集" : `${String(row.average_ms)} 毫秒`}</td></tr>)}
        </tbody></table></div>
      )}
    </div>
  );
}

function EnvironmentPanel({ data }: { data: OpsMaintenance["environment"] }) {
  const mode = data.mode === "production" ? "生产环境" : data.mode === "debug" ? "调试环境" : data.mode;
  const timezone = data.timezone === "Asia/Shanghai" ? "中国标准时间" : data.timezone;
  return (
    <div className="ops-page-stack compact-stack">
      <dl className="ops-definition-grid">
        <div><dt>运行模式</dt><dd>{mode}</dd></div>
        <div><dt>加密访问</dt><dd>{data.https ? "已启用" : "未启用"}</dd></div>
        <div><dt>数据库引擎</dt><dd>{data.database_engine}</dd></div>
        <div><dt>时区</dt><dd>{timezone}</dd></div>
      </dl>
      <section className="ops-host-metrics">
        <h2>主机指标</h2>
        <div>
          {["处理器", "整机内存", "磁盘"].map((label) => <p key={label}><span>{label}</span><StatusText value={data.host_metrics.state} /></p>)}
        </div>
        <small>{data.host_metrics.message.replaceAll("CPU", "处理器")}</small>
      </section>
    </div>
  );
}

export function MaintenancePage() {
  const search = useOpsSearch();
  const days = readDays(search.get("days"));
  const page = readPage(search.get("page"));
  const action = search.get("action") ?? "";
  const requestedTab = search.get("tab") ?? "environment";
  const tab = ["environment", "history", "audit", "diagnostics"].includes(requestedTab) ? requestedTab : "environment";
  const [state, refresh] = useOpsData(() => getOpsMaintenance({ days, page, action }), [days, page, action]);
  const data = state.kind === "ready" ? state.data : null;
  return (
    <>
      <PageHeader
        eyebrow="运行保障"
        title="系统维护"
        description="查看安全环境摘要、历史状态、审计与诊断；不返回环境全集、路径、凭据或连接串。"
        updatedAt={data?.updated_at}
        onRefresh={refresh}
      >
        <RangeTabs value={days} />
      </PageHeader>
      <nav className="ops-tabs" aria-label="维护分类">
        {[{ key: "environment", label: "安全环境" }, { key: "history", label: "历史状态" }, { key: "audit", label: "审计" }, { key: "diagnostics", label: "诊断" }].map((item) => (
          <button type="button" key={item.key} aria-current={tab === item.key ? "page" : undefined} onClick={() => setOpsQuery({ tab: item.key, page: null })}>{item.label}</button>
        ))}
      </nav>
      <StatePanel state={state} onRetry={refresh}>
        {(maintenance) => (
          <section className="ops-panel maintenance-panel">
            {tab === "environment" && <EnvironmentPanel data={maintenance.environment} />}
            {tab === "history" && <div className="ops-page-stack compact-stack">
              <div><h2>部署历史</h2><div className="ops-history-card"><StatusText value={maintenance.deployment_history.state} /><p>{maintenance.deployment_history.message || "未接入"}</p></div></div>
              <div><h2>备份恢复演练</h2><div className="ops-history-card"><StatusText value={maintenance.backup.state} /><p>{maintenance.backup.message || "未接入"}</p>{maintenance.backup.record && <DefinitionList values={maintenance.backup.record} />}</div></div>
              <div><h2>应用版本</h2><p>{maintenance.version || "未接入"}</p></div>
            </div>}
            {tab === "audit" && <div className="ops-page-stack compact-stack">
              <label className="ops-audit-filter">按动作筛选
                <select value={action} onChange={(event) => setOpsQuery({ action: event.target.value || null }, { resetPage: true })}>
                  <option value="">全部操作</option>
                  {action && !auditActions[action] && <option value={action}>其他操作（当前筛选）</option>}
                  {Object.entries(auditActions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <AuditTable rows={maintenance.audit.items} />
              <Pagination page={maintenance.audit} />
            </div>}
            {tab === "diagnostics" && <PerformancePanel data={maintenance.performance} />}
          </section>
        )}
      </StatePanel>
    </>
  );
}
