import { ReactNode, useEffect, useRef, useState } from "react";
import { isApiError, launchModule, PortalModule } from "../api";
import { EmptyPanel, SectionHeader } from "./shared";

import BusinessBoards, { BusinessOverview } from './BusinessBoards';
import BusinessLedgerWorkspace from './BusinessLedgerWorkspace';

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
  if (section === 'ledgers') return <><BusinessLedgerWorkspace preview={preview} /><section className="center-panel"><h3>原台账系统</h3><p>新录入工作台不影响原系统；过渡期仍可按原有权限进入。</p><div className="center-actions">{launchButton}</div>{error && <p className="notice error" role="alert">{error}</p>}</section></>;

  return <BusinessOverview preview={preview} />;
}
