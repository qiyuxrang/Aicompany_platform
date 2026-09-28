import { IconName } from "../Icon";

export const centers = {
  product: {
    name: "产品事业部", icon: "modules" as IconName, description: "一份项目资料贯穿分析、编制、审校与成果复用。",
    sections: [{ code: "overview", title: "工作台" }, { code: "opportunities", title: "商机获取" }, { code: "projects", title: "项目" }, { code: "sources", title: "资料" }, { code: "outputs", title: "文档成果" }, { code: "history", title: "版本记录" }, { code: "templates", title: "模板" }, { code: "knowledge", title: "知识库问答" }, { code: "new", title: "新建项目" }, { code: "documents", title: "专业工作台" }],
  },
  cost: {
    name: "工程部", icon: "maintenance" as IconName, description: "工程部业务仍在开发，本平台当前只保留隔离入口。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "estimate", title: "成本测算（开发中）" }, { code: "quota", title: "套用定额（开发中）" }],
  },
  hr: {
    name: "人事部门", icon: "people" as IconName, description: "招聘任务、简历处理与历史记录。",
    sections: [{ code: "overview", title: "工作台" }, { code: "job", title: "JD 生成" }, { code: "resumes", title: "简历筛选" }, { code: "results", title: "筛选结果" }, { code: "probation", title: "转正工作流" }, { code: "history", title: "历史筛选记录" }, { code: "profile", title: "岗位需求" }, { code: "channels", title: "招聘平台版本" }],
  },
  business: {
    name: "总经理工作台", icon: "usage" as IconName, description: "工程、财务与售前台账分部门呈现，来源和统计口径清晰可查。",
    sections: [{ code: "overview", title: "企业台账" }, { code: "engineering", title: "工程部看板" }, { code: "finance", title: "财务部看板" }, { code: "presales", title: "售前部门看板" }, { code: "ledgers", title: "台账录入" }, { code: "projects", title: "授权项目" }],
  },
} as const;

export type CenterCode = keyof typeof centers;
export function isCenterCode(code: string): code is CenterCode { return Object.hasOwn(centers, code); }
