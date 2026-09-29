const legacyTranslations: Record<string, string> = {
  blueprint: "项目蓝图（尚未明确具体建设目标）",
  project_stakeholders: "项目相关人员",
  "Define project background, scope, and constraints based on provided equipment list and background material.": "根据设备清单和背景材料，明确项目背景、建设范围及约束条件。",
  "Detail the power distribution system design including high/low voltage switchgear, transformers, and cabling.": "详细说明供配电系统设计，包括高低压开关柜、变压器及电缆配置。",
  "Describe PLC systems, instrumentation, and control strategies for coal preparation processes.": "说明选煤工艺中的可编程逻辑控制系统、仪器仪表及控制策略。",
  "Cover video surveillance, communication, fire alarm, and information management systems.": "涵盖视频监控、通信、火灾报警及信息管理系统。",
  "Outline implementation phases and list items requiring further confirmation or clarification.": "说明实施阶段，并列出需要进一步确认或澄清的事项。",
};

export function blueprintDisplayText(text: string): string {
  return legacyTranslations[text.trim()] ?? text;
}
