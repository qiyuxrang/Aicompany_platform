import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import * as api from "./product-api";
import ProductDashboard from "./ProductDashboard";
import NewProductProject from "./NewProductProject";
import ProductProjects from "./ProductProjects";
import ProjectStages from "./ProjectStages";
import ProductProjectDetail from "./ProductProjectDetail";
import { sampleOverview, sampleTask } from "./workbench-fixtures";

vi.mock("./product-api", async importOriginal => ({ ...await importOriginal<typeof import("./product-api")>(), getProductOverview: vi.fn(), createTask: vi.fn(), getTask: vi.fn(), uploadSource: vi.fn(), updateTask: vi.fn(), getDraftOutputs: vi.fn(), getTaskHistory: vi.fn() }));
const overview = vi.mocked(api.getProductOverview);
const create = vi.mocked(api.createTask);
const get = vi.mocked(api.getTask);
const upload = vi.mocked(api.uploadSource);
const update = vi.mocked(api.updateTask);
beforeEach(() => {
  vi.clearAllMocks(); window.history.replaceState({}, "", "/centers/product");
  overview.mockResolvedValue(sampleOverview()); create.mockResolvedValue(sampleTask()); get.mockResolvedValue(sampleTask()); update.mockResolvedValue(sampleTask());
  vi.mocked(api.getDraftOutputs).mockResolvedValue({ task_version: 1, outputs: [] });
  vi.mocked(api.getTaskHistory).mockResolvedValue({ task_id: sampleTask().id, task_version: 1, history_immutable: true, tamper_claim: "application_read_only", timeline: [] });
});
function stages(task = sampleTask(), overrides: Partial<React.ComponentProps<typeof ProjectStages>> = {}) {
  return { task, outputs: [], history: null, outputsError: "", historyError: "", busy: false, conflict: false, disabled: (action: string) => !(task.actions as string[]).includes(action) || !!task.blockers[action], onAction: vi.fn().mockResolvedValue(true), onReload: vi.fn(), onUpload: vi.fn(), ...overrides };
}
function blueprintTask() {
  return sampleTask({ state: "WAITING_REVIEW", stage: "BLUEPRINT", blueprint_version: 1, actions: ["confirm_blueprint"], blueprint: { id: "blueprint-id", version: 1, sha256: "a".repeat(64), payload: { purpose: "提升供电可靠性", audience: "项目评审人员", chapters: [{ id: "c1", title: "建设范围", scope: "合成系统边界", source_ids: [] }], conditions: [{ text: "保留原设备数量", type: "human" }], missing: ["投资估算依据"], conflicts: [], template_version: "frozen-original-v1" } } });
}

describe("product business workbench", () => {
  it("shows authorized live metrics and links monthly completion to the same scope", async () => {
    render(<ProductDashboard/>);
    await screen.findByText("合成供配电项目");
    expect(screen.getByLabelText("进行中项目数量").textContent).toBe("3个");
    expect(screen.getByRole("link", { name: /本月完成/ }).getAttribute("href")).toContain("filter=completed_month");
    expect(screen.getByRole("link", { name: "继续最近项目" }).getAttribute("href")).toContain(sampleTask().id);
  });
  it("does not replace an unavailable metric with zero", async () => {
    overview.mockRejectedValueOnce(new ApiError(503, "服务暂不可用"));
    render(<ProductDashboard/>); await screen.findByRole("alert");
    expect(screen.getByLabelText("进行中项目数量").textContent).toBe("—");
    expect(screen.queryByText("从第一个项目开始")).toBeNull();
  });
  it("preview never reads project data", () => {
    render(<ProductDashboard preview/>); expect(overview).not.toHaveBeenCalled();
    expect(screen.queryByText("合成供配电项目")).toBeNull();
  });
  it("aborts outdated list searches instead of letting them replace the next page", async () => {
    const view = render(<ProductProjects/>); await screen.findByText("合成供配电项目");
    const signal = overview.mock.calls[0][1]; view.unmount(); expect(signal.aborted).toBe(true);
    window.history.replaceState({}, "", "/centers/product/projects?filter=review&q=%E4%BE%9B%E7%94%B5&page=2");
    render(<ProductProjects/>); await waitFor(() => expect(overview).toHaveBeenLastCalledWith("filter=review&q=%E4%BE%9B%E7%94%B5&page=2&page_size=12", expect.anything()));
  });
  it("creates a single project with an ordinary business form while model calls are gated", async () => {
    const user = userEvent.setup(); render(<NewProductProject/>);
    await screen.findByLabelText("项目名称 *");
    await user.type(screen.getByLabelText("项目名称 *"), "合成新项目");
    await user.type(screen.getByLabelText("建设目标 *"), "可靠供电");
    expect(screen.queryByLabelText("蓝图与成果审核人")).toBeNull();
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    await waitFor(() => expect(window.location.search).toContain(sampleTask().id));
    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0][0]).toMatchObject({ title: "合成新项目", input: { requirements: "可靠供电" } });
    expect(create.mock.calls[0][0]).not.toHaveProperty("reviewer_id");
    expect(update).not.toHaveBeenCalled();
  });
  it("freezes and reuses the idempotency key after an uncertain create response", async () => {
    create.mockRejectedValueOnce(new Error("连接中断"));
    const user = userEvent.setup(); render(<NewProductProject/>);
    await screen.findByLabelText("项目名称 *");
    await user.type(screen.getByLabelText("项目名称 *"), "合成重试项目");
    await user.type(screen.getByLabelText("建设目标 *"), "可靠供电");
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("项目名称 *") as HTMLInputElement).disabled || screen.getByLabelText("项目名称 *").closest("fieldset")?.disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "重试并继续" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[0].slice(0, 2)).toEqual(create.mock.calls[1].slice(0, 2));
  });
  it("rejects unsupported or empty files without submitting them", async () => {
    render(<NewProductProject/>); await screen.findByLabelText("项目名称 *");
    fireEvent.change(screen.getByLabelText("选择项目资料文件"), { target: { files: [new File(["not-pdf"], "不支持.pdf", { type: "application/pdf" })] } });
    await screen.findByRole("alert"); expect(upload).not.toHaveBeenCalled(); expect(create).not.toHaveBeenCalled();
  });
  it("edits a human blueprint without JSON and preserves original conditions", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const props = stages(); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    await user.click(screen.getByRole("button", { name: "人工编制蓝图" }));
    await user.type(screen.getByLabelText("第 1 章范围"), "项目现状和设计范围");
    await user.click(screen.getByRole("button", { name: "保存蓝图新版本" }));
    expect(props.onAction).toHaveBeenCalledWith("blueprint/", expect.objectContaining({ payload: expect.objectContaining({ conditions: [{ text: "保留原设备数量", type: "human" }], chapters: [expect.objectContaining({ scope: "项目现状和设计范围" })] }) }));
  });
  it("binds approval to the viewed blueprint hash and requires explicit confirmation", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const props = stages(blueprintTask()); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    const approve = screen.getByRole("button", { name: "确认蓝图并生成三件套" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    await user.type(screen.getByLabelText("蓝图确认依据"), "已根据项目资料逐项核对");
    expect(approve.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox")); await user.click(approve);
    expect(props.onAction).toHaveBeenCalledWith("decisions/", expect.objectContaining({ target: "blueprint", target_id: "blueprint-id", sha256: "a".repeat(64), decision: "approve" }));
  });
  it("shows preview provenance and disables a fourth automatic blueprint revision", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const task = blueprintTask();
    task.blueprint_review = { revision_count: 3, revision_limit: 3, revisions_remaining: 0 };
    const props = stages(task); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    expect(screen.getByText("测试预览 · RAGFlow 等待接入")).toBeTruthy();
    expect(screen.getByText(/仅使用本项目上传资料/)).toBeTruthy();
    expect(screen.getByText(/不会显示或伪造知识库命中/)).toBeTruthy();
    await user.type(screen.getByLabelText("蓝图确认依据"), "仍需继续修改");
    expect((screen.getByRole("button", { name: "退回修改（剩余 0/3 次）" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/自动修改次数已用完/)).toBeTruthy();
  });
  it("does not expose legacy report approval steps or let legacy reviewers confirm a blueprint", () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const view = render(<ProjectStages {...stages({ ...blueprintTask(), actions: ["review_input"] })}/>);
    expect((screen.getByRole("button", { name: "确认蓝图并生成三件套" }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount();
    window.history.replaceState({}, "", "/centers/product/projects?tab=outputs");
    render(<ProjectStages {...stages(sampleTask({ state: "COMPLETED", stage: "FINAL_REVIEW", actions: [] }))}/>);
    expect(screen.queryByRole("button", { name: "从已审内容生成 PPT" })).toBeNull();
    expect(screen.queryByRole("button", { name: "生成技术方案与可研 Word" })).toBeNull();
  });
  it("preserves local blueprint edits when the server version changes", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const props = stages(); const user = userEvent.setup(); const view = render(<ProjectStages {...props}/>);
    await user.click(screen.getByRole("button", { name: "人工编制蓝图" }));
    await user.type(screen.getByLabelText("第 1 章范围"), "尚未保存的本地编辑");
    view.rerender(<ProjectStages {...props} task={{ ...props.task, version: 2 }}/>);
    expect((screen.getByLabelText("第 1 章范围") as HTMLTextAreaElement).value).toBe("尚未保存的本地编辑");
    expect((screen.getByRole("button", { name: "保存蓝图新版本" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("uses indeterminate progress instead of inventing a percentage", () => {
    render(<ProjectStages {...stages(sampleTask({ state: "RUNNING", stage: "WRITING", actions: ["cancel"] }))}/>);
    const progress = screen.getByRole("progressbar"); expect(progress.hasAttribute("aria-valuenow")).toBe(false);
    expect(screen.queryByText(/60%|70%|预计.*分钟/)).toBeNull();
  });
  it("provides a normal input editor while retaining raw evidence rows", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=sources");
    const props = stages(); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    await user.click(screen.getByRole("button", { name: "编辑项目底稿" }));
    await user.clear(screen.getByLabelText("建设目标")); await user.type(screen.getByLabelText("建设目标"), "修订供电目标");
    await user.click(screen.getByRole("button", { name: "保存项目底稿" }));
    expect(props.onAction).toHaveBeenCalledWith("", expect.objectContaining({ input: expect.objectContaining({ requirements: "修订供电目标", items: sampleTask().input!.items }) }));
  });
  it("hides task details after revoked access on refresh", async () => {
    const user = userEvent.setup(); render(<ProductProjectDetail id={sampleTask().id}/>);
    await screen.findByRole("heading", { name: /合成供配电项目/ });
    get.mockRejectedValueOnce(new ApiError(404, "对象不存在"));
    await user.click(screen.getByRole("button", { name: "刷新状态" }));
    await screen.findByRole("heading", { name: "无法打开项目" }); expect(screen.queryByText("提升供电可靠性")).toBeNull();
  });
  it("revalidates idle object access when the window regains focus", async () => {
    render(<ProductProjectDetail id={sampleTask().id}/>);
    await screen.findByRole("heading", { name: /合成供配电项目/ });
    get.mockRejectedValueOnce(new ApiError(404, "对象不存在"));
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await screen.findByRole("heading", { name: "无法打开项目" });
    expect(screen.queryByText("提升供电可靠性")).toBeNull();
  });
  it("retains the created task and uploads only the remaining file after a partial failure", async () => {
    const first = new File(["first"], "第一份.txt", { type: "text/plain" });
    const second = new File(["second"], "第二份.txt", { type: "text/plain" });
    const saved = sampleTask({ version: 2, sources: [{ id: "source-one", original_name: first.name, size: first.size }] });
    upload.mockResolvedValueOnce(saved).mockRejectedValueOnce(new Error("上传中断")).mockResolvedValueOnce(sampleTask({ version: 3 }));
    get.mockResolvedValue(saved);
    const user = userEvent.setup(); render(<NewProductProject/>);
    await screen.findByLabelText("项目名称 *");
    await user.type(screen.getByLabelText("项目名称 *"), "附件续传项目");
    await user.type(screen.getByLabelText("建设目标 *"), "保持来源和版本连续");
    fireEvent.change(screen.getByLabelText("选择项目资料文件"), { target: { files: [first, second] } });
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "重试并继续" }));
    await waitFor(() => expect(window.location.search).toContain(sampleTask().id));
    expect(create).toHaveBeenCalledTimes(1);
    expect(upload.mock.calls.map(call => [call[1].name, call[2]])).toEqual([[first.name, 1], [second.name, 2], [second.name, 2]]);
  });
  it('requires separate equipment and background files before starting generation', async () => {
    overview.mockResolvedValue({ ...sampleOverview(), capabilities: { ...sampleOverview().capabilities, model_generation: true } });
    const user = userEvent.setup(); render(<NewProductProject/>);
    await screen.findByLabelText('项目名称 *');
    await user.type(screen.getByLabelText('项目名称 *'), '分类型资料项目');
    await user.type(screen.getByLabelText('建设目标 *'), '保留设备与背景来源');
    await user.click(screen.getByRole('button', { name: '开始生成' }));
    expect((await screen.findByRole('alert')).textContent).toContain('一份设备清单');
    expect(create).not.toHaveBeenCalled();
    const equipment = new File(['equipment data'], '设备.csv', { type: 'text/csv' });
    const background = new File(['项目背景'], '调研.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByLabelText('设备清单文件'), { target: { files: [equipment] } });
    fireEvent.change(screen.getByLabelText('选择项目资料文件'), { target: { files: [background] } });
    upload.mockReset();
    upload.mockResolvedValueOnce(sampleTask({ version: 2 })).mockResolvedValueOnce(sampleTask({ version: 3, actions: ['queue_blueprint'], blockers: {} }));
    await user.click(screen.getByRole('button', { name: '开始生成' }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(create.mock.calls[0][0].intake_mode).toBe('equipment_background');
    expect(upload.mock.calls.map(call => [call[1].name, call[4]])).toEqual([['设备.csv', 'equipment'], ['调研.txt', 'background']]);
  });
  it('renders the real persisted three-phase states without a fabricated percentage', () => {
    const task = sampleTask({ state: 'RUNNING', stage: 'BLUEPRINT', pending_action: 'blueprint',
      analysis_progress: { documents: { status: 'completed', updated_at: '2026-09-26T00:00:00Z', source_count: 2 },
        equipment: { status: 'completed', updated_at: '2026-09-26T00:00:01Z', item_count: 3 },
        blueprint: { status: 'running', updated_at: '2026-09-26T00:00:02Z' } } });
    render(<ProjectStages {...stages(task)}/>);
    expect(screen.getByLabelText('智能体工作状态').querySelectorAll('[data-status=completed]')).toHaveLength(2);
    expect(screen.getByText('生成蓝图中').closest('li')?.getAttribute('data-status')).toBe('running');
    expect(screen.getByText('3 行设备事实')).toBeTruthy();
  });
  it('shows the complete six-stage workflow and prioritizes checkpoint progress', () => {
    const task = sampleTask({ state: 'RUNNING', stage: 'BLUEPRINT', pending_action: 'blueprint',
      analysis_progress: { documents: { status: 'failed', detail: '旧状态' } },
      checkpoint: { analysis_progress: {
        documents: { status: 'completed', source_count: 4 },
        equipment: { status: 'completed', item_count: 18 },
        knowledge: { status: 'waiting', detail: 'RAGFlow 连接待配置' },
        blueprint: { status: 'running', detail: '正在编排章节' },
      } } });
    render(<ProjectStages {...stages(task)}/>);
    const flow = screen.getByLabelText('智能体工作状态');
    expect(flow.querySelectorAll(':scope > li')).toHaveLength(6);
    expect(screen.getByText('解析项目背景').closest('li')?.getAttribute('data-status')).toBe('completed');
    expect(screen.getByText('RAGFlow 知识检索').closest('li')?.getAttribute('data-status')).toBe('waiting');
    expect(screen.getByText('生成蓝图中').closest('li')?.getAttribute('data-status')).toBe('running');
    expect(screen.getByText('RAGFlow 连接待配置')).toBeTruthy();
    expect(screen.queryByText('旧状态')).toBeNull();
  });
  it('queues the real RAGFlow preparation action before a formal blueprint', async () => {
    const task = sampleTask({
      actions: ['queue_blueprint_knowledge', 'queue_blueprint'],
      blockers: { queue_blueprint: { code: 'ragflow_required', detail: '正式蓝图必须先检索 RAGFlow。' } },
      blueprint_knowledge: {
        mode: 'ragflow_required', required: true, status: 'waiting_for_ragflow',
        ragflow_used: false, source_count: 0, detail: '等待真实 RAGFlow 检索。',
      },
    });
    const props = stages(task);
    const user = userEvent.setup();
    render(<ProjectStages {...props}/>);
    await user.click(screen.getByRole('button', { name: '先检索 RAGFlow' }));
    expect(props.onAction).toHaveBeenCalledWith('queue/', { action: 'knowledge' });
    expect((screen.getByRole('button', { name: '生成项目蓝图' }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('shows explicit blueprint review rounds and output generation readiness', () => {
    window.history.replaceState({}, '', '/centers/product/projects?tab=blueprint');
    const task = blueprintTask();
    task.blueprint_review = { revision_count: 2, revision_limit: 3, revisions_remaining: 1 };
    render(<ProjectStages {...stages(task)}/>);
    expect(screen.getByText('已修改 2 次 · 最多 3 次 · 剩余 1 次')).toBeTruthy();
    expect(screen.getByText('等待第 3/3 轮人工审核')).toBeTruthy();
  });
  it('binds modification opinions to the exact displayed blueprint', async () => {
    window.history.replaceState({}, '', '/centers/product/projects?tab=blueprint');
    const props = stages(blueprintTask()); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    await user.type(screen.getByLabelText('蓝图确认依据'), '增加分阶段实施说明');
    await user.click(screen.getByRole('button', { name: /退回修改/ }));
    expect(props.onAction).toHaveBeenCalledWith('decisions/', expect.objectContaining({
      decision: 'revise', target_id: 'blueprint-id', sha256: 'a'.repeat(64), comment: '增加分阶段实施说明' }));
  });
  it("never offers a stale artifact as the current project output", () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=outputs");
    const stale: api.DraftOutput = { id: "old-artifact", family: "technical-solution", version: 1, sha256: "b".repeat(64), current: false, stale: true, draft: true, approved: false, review_status: "stale", engine: "test", content_version: 1, content_sha256: null, content_approved: false, source_versions: [] };
    render(<ProjectStages {...stages(sampleTask(), { outputs: [stale] })}/>);
    expect(screen.queryByRole("link", { name: "下载文件" })).toBeNull();
    expect(screen.getAllByText("尚未生成")).toHaveLength(3);
  });
});
