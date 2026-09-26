import { IconName } from "../Icon";

export const centers = {
  product: {
    name: "产品事业部", icon: "modules" as IconName, description: "一份项目资料贯穿分析、编制、审校与成果复用。",
    sections: [{ code: "overview", title: "工作台" }, { code: "projects", title: "项目" }, { code: "sources", title: "资料" }, { code: "outputs", title: "文档成果" }, { code: "history", title: "版本记录" }, { code: "templates", title: "模板" }, { code: "new", title: "新建项目" }, { code: "documents", title: "专业工作台" }],
  },
  cost: {
    name: "工程部", icon: "maintenance" as IconName, description: "工程部业务仍在开发，本平台当前只保留隔离入口。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "estimate", title: "成本测算（开发中）" }, { code: "quota", title: "套用定额（开发中）" }],
  },
  hr: {
    name: "人事部", icon: "people" as IconName, description: "连接岗位需求、招聘内容、简历整理与转正协同。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "profile", title: "人才画像" }, { code: "job", title: "岗位说明" }, { code: "channels", title: "招聘平台版本" }, { code: "resumes", title: "简历整理与筛选" }, { code: "probation", title: "转正工作流" }],
  },
  business: {
    name: "总经理工作台", icon: "usage" as IconName, description: "聚焦授权项目、业务台账和经营看板，保留原系统口径。",
    sections: [{ code: "overview", title: "经营概览" }, { code: "projects", title: "授权项目" }, { code: "ledgers", title: "台账与看板" }],
  },
} as const;

export type CenterCode = keyof typeof centers;
export function isCenterCode(code: string): code is CenterCode { return Object.hasOwn(centers, code); }
