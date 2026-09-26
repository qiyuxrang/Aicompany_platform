import { ReactNode, useEffect, useRef, useState } from "react";
import { isApiError, launchModule, PortalModule } from "../api";
import { CenterLink, EmptyPanel, SectionHeader } from "./shared";

import BusinessBoards from './BusinessBoards';

const legacyAreas = [
  { title: "经营总览", description: "在原系统查看项目台账与商机管理，沿用现有经营口径。", items: "项目台账 · 商机管理" },
  { title: "销售台账", description: "查看原销售台账，不在新门户复制或重新计算销售数据。", items: "原销售业务视图" },
  { title: "在建台账", description: "从原在建项目视图查看实施情况，详情仍由旧系统提供。", items: "原在建项目视图" },
  { title: "应收台账", description: "在原系统切换应收盘点、确认、回款计划与到账记录。", items: "应收盘点 · 应收确认 · 回款计划 · 到账记录 · 金额调整" },
];

export default function ManagerWorkspace({ section, preview, module, businessPanel }: {
  section: string; preview: boolean; module?: PortalModule; businessPanel: ReactNode;
}) {
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const launchRequest = useRef(0);
  const navigationReady = !preview && module?.enabled && module.status !== "pending" && module.status !== "disabled";

  useEffect(() => {
    setLaunching(false);
    setError("");
    return () => { launchRequest.current += 1; };
  }, [section, preview, navigationReady]);

  const enterLegacy = async () => {
    if (!navigationReady || launching) return;
    const request = ++launchRequest.current;
    setLaunching(true);
    setError("");
    try {
      const target = await launchModule("business");
      if (request !== launchRequest.current) return;
      window.location.assign(target);
    } catch (caught) {
      if (request !== launchRequest.current) return;
      setError(isApiError(caught) ? caught.message : "暂时无法进入原经营系统，请稍后重试。");
      setLaunching(false);
    }
  };
  const launchButton = <button type="button" className="button primary" disabled={!navigationReady || launching} onClick={() => void enterLegacy()}>{launching ? "正在核验入口…" : preview ? "管理预览不启动旧系统" : navigationReady ? "进入原台账看板" : "台账入口待配置"}</button>;

  if (section === "projects") return <section className="center-panel">
    <SectionHeader title="按当前身份查看授权项目" description="只读接口当前仅提供项目标识、名称与数量，不提供完整财务台账。权限仍由原系统决定。" />
    {preview ? <EmptyPanel title="管理预览不读取业务数据">需要单独获得项目经营模块授权后，才能按本人映射查询。此处不放置演示项目或经营数字。</EmptyPanel>
      : !navigationReady ? <EmptyPanel title="只读查询尚未开放">模块仍待接入，不会尝试读取旧系统数据。请由管理员确认接入配置。</EmptyPanel>
        : <><div className="center-actions"><button type="button" className="button secondary" onClick={() => setRefresh((value) => value + 1)}>刷新授权项目</button></div><div key={refresh}>{businessPanel}</div></>}
    <p className="center-note">可信数据调用不等于浏览器单点登录。已经返回的内容无法远程收回；刷新时按当前权限重新查询。</p>
  </section>;

  if (['engineering', 'finance', 'presales'].includes(section)) return <BusinessBoards initial={section as 'engineering' | 'finance' | 'presales'} preview={preview} />;

  return <>
    {section === 'overview' && <BusinessBoards preview={preview} />}
    {section === "overview" && <section className="center-manager-intro"><div><p className="eyebrow">经营入口 · 原有口径</p><h2>台账看板，回到同一工作入口</h2><p>原经营系统继续管理项目和业务权限。上方看板分析当前账号导入的台账快照；也可进入原系统或查询已授权项目。</p><div className="center-actions">{launchButton}<CenterLink href="/centers/business/projects" className="button secondary">查看授权项目</CenterLink></div></div><div className="center-boundaries"><div><strong>台账业务归属</strong><span>原监控看板保持独立运行</span></div><div><strong>浏览器登录</strong><span>保留原系统登录，未实现单点登录</span></div><div><strong>访问边界</strong><span>只按本人已获授权的范围访问</span></div></div></section>}
    <section className="center-panel">
      <SectionHeader title="台账与经营视图" description="以下是现有监控看板已具备的菜单。原系统使用内部切换，没有独立菜单网址；进入后再选择相应栏目。" />
      <div className="center-feature-grid">{legacyAreas.map((area) => <article className="center-feature-card" key={area.title}><span className="eyebrow">原系统菜单</span><h3>{area.title}</h3><p>{area.description}</p><span>{area.items}</span></article>)}</div>
      {section === "ledgers" && <div className="center-actions">{launchButton}</div>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <p className="center-note">{preview ? "当前为页面预览，不会跳转旧系统或读取台账。平台管理员不会默认获得总经理业务权限。" : "入口先由门户后端核验。平台撤权只影响后续门户请求，不会默认停用旧系统原生账号或注销旧会话。"}</p>
    </section>
    {section === "overview" && <section className="center-panel"><SectionHeader title="业务边界清晰，数据口径不变" description="本期只连接已经存在的经营能力，不把导航接入宣传为系统重建。" /><div className="center-grid"><div className="center-note"><strong>授权项目</strong><p>已配置可信桥接时，可查询本人授权的项目名称、标识和数量；未配置时明确提示，不以缓存或样例数据替代。</p><CenterLink href="/centers/business/projects">进入只读项目页 →</CenterLink></div><div className="center-note"><strong>完整台账</strong><p>销售、在建与应收的查看和业务规则仍留在原监控看板。门户没有新增财务计算或修改入口。</p><CenterLink href="/centers/business/ledgers">查看台账入口说明 →</CenterLink></div></div></section>}
  </>;
}
