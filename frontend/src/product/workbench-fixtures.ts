import type { DocumentTask, ProductOverview } from "./product-api";

/** Synthetic fixtures only; never imported by production views. */
export function sampleTask(overrides: Partial<DocumentTask> = {}): DocumentTask {
  return { id: "11111111-1111-4111-8111-111111111111", title: "合成供配电项目", state: "DRAFT", stage: "INTAKE", version: 1, input_version: 1, blueprint_version: 0, blueprint_approved: false,
    input: { project: "合成供配电项目", requirements: "提升供电可靠性", background: "仅用于验收的合成背景", conditions: ["保留原设备数量"], items: [{ row_id: "1", name: "配电柜", quantity: "2", unit: "台" }] }, blueprint: null, chapters: [], reports: [], artifacts: [], sources: [], approvals: [], issues: [], input_issues: [], impact: {}, error_code: "", reviewer_id: 2, owner_id: 1, owner_name: "项目负责人", reviewer_name: "审核人员", created_at: "2026-09-26T10:00:00Z", updated_at: "2026-09-26T10:00:00Z",
    actions: ["edit", "save_blueprint", "add_source", "cancel", "assign_reviewer", "queue_blueprint"], blockers: { queue_blueprint: { code: "model_authorization_required", detail: "尚未取得模型与预算授权。" } }, ...overrides };
}
export function sampleOverview(): ProductOverview {
  const task = sampleTask();
  return { as_of: "2026-09-26T10:00:00Z", scope: "authorized_projects", metrics: { active: 3, review: 2, generation: 1, completed_month: 4 }, projects: [task], pagination: { page: 1, page_size: 12, total: 1, pages: 1 }, recent_projects: [task], todos: [sampleTask({ id: "review-task", title: "合成蓝图待审", state: "WAITING_REVIEW", stage: "BLUEPRINT" })], recent_outputs: [], reviewers: [{ id: 2, name: "审核人员" }], capabilities: { model_generation: false, retrieval: false, formal_release: false, office_preview: false, upload_extensions: [".csv", ".txt"], upload_max_bytes: 1048576 }, templates: [{ id: "frozen-original-v1", name: "产品项目文档母版", version: "frozen-original-v1", families: ["technical-solution", "feasibility", "presentation"], approved: false, description: "合成测试模板" }] };
}
