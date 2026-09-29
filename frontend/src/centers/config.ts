import { IconName } from "../Icon";

export const centers = {
  product: {
    name: "产品事业部", icon: "modules" as IconName, description: "一份项目资料贯穿分析、编制、审校与成果复用。",
    sections: [{ code: "overview", title: "工作台" }, { code: "opportunities", title: "全国商机看板" }, { code: "projects", title: "历史项目" }, { code: "sources", title: "资料" }, { code: "outputs", title: "文档成果" }, { code: "history", title: "版本记录" }, { code: "templates", title: "模板" }, { code: "knowledge", title: "知识库问答" }, { code: "presales", title: "售前数据录入" }, { code: "new", title: "新建项目" }, { code: "documents", title: "生成文档" }],
  },
  cost: {
    name: "工程部", icon: "maintenance" as IconName, description: "清单核验、内部成本草稿与定额查询，具体能力以授权和服务状态为准。",
    sections: [{ code: "overview", title: "工作概览" }, { code: "estimate", title: "成本测算" }, { code: "quota", title: "套用定额" }],
  },
  hr: {
    name: "人事部门", icon: "people" as IconName, description: "招聘任务、简历处理与历史记录。",
    sections: [{ code: "overview", title: "工作台" }, { code: "job", title: "JD 生成" }, { code: "resumes", title: "简历筛选" }, { code: "results", title: "筛选结果" }, { code: "probation", title: "转正工作流" }, { code: "history", title: "历史筛选记录" }, { code: "profile", title: "岗位需求" }, { code: "channels", title: "招聘平台版本" }],
  },
  business: {
    name: "总经理工作台", icon: "usage" as IconName, description: "首页查看经营分析，分部门核对台账明细与更新状态。",
    sections: [{ code: "overview", title: "数据分析首页" }, { code: "finance", title: "财务部看板" }, { code: "presales", title: "产品事业部看板" }, { code: "engineering", title: "工程部看板" }, { code: "ledgers", title: "台账录入" }, { code: "projects", title: "授权项目" }],
  },
} as const;

export type CenterCode = keyof typeof centers;
export function isCenterCode(code: string): code is CenterCode { return Object.hasOwn(centers, code); }
