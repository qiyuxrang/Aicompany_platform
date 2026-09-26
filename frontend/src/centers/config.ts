import { IconName } from "../Icon";

export const centers = {
  product: {
    name: "产品事业部", icon: "modules" as IconName, description: "一份项目资料贯穿分析、编制、审校与成果复用。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "documents", title: "项目成果流水线" }],
  },
  cost: {
    name: "工程部", icon: "maintenance" as IconName, description: "工程部业务仍在开发，本平台当前只保留隔离入口。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "estimate", title: "成本测算（开发中）" }, { code: "quota", title: "套用定额（开发中）" }],
  },
  hr: {
    name: "人事部门", icon: "people" as IconName, description: "招聘任务、简历处理与历史记录。",
    sections: [{ code: "overview", title: "工作台" }, { code: "job", title: "招聘与 JD" }, { code: "resumes", title: "简历筛选" }, { code: "results", title: "筛选结果" }, { code: "probation", title: "转正工作流" }, { code: "history", title: "历史 JD" }, { code: "profile", title: "岗位需求" }, { code: "channels", title: "招聘平台版本" }],
  },
  business: {
    name: "总经理工作台", icon: "usage" as IconName, description: "聚焦授权项目、业务台账和经营看板，保留原系统口径。",
    sections: [{ code: "overview", title: "经营概览" }, { code: "projects", title: "授权项目" }, { code: "ledgers", title: "台账与看板" }],
  },
} as const;

export type CenterCode = keyof typeof centers;
export function isCenterCode(code: string): code is CenterCode { return Object.hasOwn(centers, code); }
