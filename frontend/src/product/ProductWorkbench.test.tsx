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
  it("hides the unused legacy retrieval gate without hiding knowledge service failures", () => {
    const task = sampleTask({ state: "FAILED", error_code: "ragflow_unavailable", actions: ["retry"], blockers: {
      queue_retrieve: { code: "retrieval_authorization_required", detail: "D-01/D-08 尚未批准真实资料检索与身份映射，当前不能发起检索。" },
      queue_blueprint: { code: "ragflow_required", detail: "正式模式必须先完成 RAGFlow 授权检索；服务不可用时不会绕过该步骤。" },
    } });
    render(<ProjectStages {...stages(task)}/>);
    expect(screen.queryByText(/D-01\/D-08/)).toBeNull();
    expect(screen.getByText(/正式模式必须先完成/)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("知识库服务暂时无法连接");
    expect(screen.getByRole("button", { name: "从已保存进度重试" })).toBeTruthy();
  });
  it("shows chapter titles, content and generation budgets inline and preserves review actions", () => {
    render(<ProjectStages {...stages({ ...blueprintTask(), output_targets: { 'technical-solution': 3000, feasibility: 5000 } })}/>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("region", { name: "项目蓝图 v1" })).toBeTruthy();
    expect(screen.getByText('第 1 章 · 建设范围')).toBeTruthy();
    expect(screen.getByText('合成系统边界')).toBeTruthy();
    expect(screen.getByText('技术方案：至少 3,000 字')).toBeTruthy();
    expect(screen.getByText('可行性研究报告：至少 5,000 字')).toBeTruthy();
    const runtime = screen.getByText('运行细节').closest('details');
    expect(runtime?.open).toBe(false);
    expect(screen.getByLabelText('完整处理链路').querySelectorAll('li')).toHaveLength(8);
    expect(screen.queryByText(/确认绑定蓝图/)).toBeNull();
    expect(screen.queryByText(/修改意见会随本版蓝图/)).toBeNull();
    expect(screen.getByLabelText('蓝图确认依据')).toBeTruthy();
    expect(screen.getByLabelText('项目处理进度').textContent).toContain('已结束 2 / 8 · 完成 1');
  });
  it.each(["formal", undefined] as const)("shows formal blueprint targets as minimums with profile %s", (profile) => {
    render(<ProjectStages {...stages({ ...blueprintTask(), output_profile: profile, output_targets: { 'technical-solution': 50000, feasibility: 70000 } })}/>);
    expect(screen.getByText('技术方案：至少 50,000 字')).toBeTruthy();
    expect(screen.getByText('可行性研究报告：至少 70,000 字')).toBeTruthy();
  });
  it("does not open review while a waiting task has no persisted blueprint", () => {
    render(<ProjectStages {...stages(sampleTask({ state: "WAITING_REVIEW", stage: "BLUEPRINT", actions: ["confirm_blueprint"] }))}/>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByLabelText("蓝图确认依据")).toBeNull();
    expect(screen.getByText("等待形成项目蓝图")).toBeTruthy();
  });
  it("keeps failures visible in Chinese and stops the running indicator", () => {
    render(<ProjectStages {...stages(sampleTask({ state: 'FAILED', error_code: 'execution_failed' }))}/>);
    expect(screen.getByRole('alert').textContent).toContain('生成未完成');
    expect(screen.getByRole('alert').textContent).not.toContain('execution_failed');
    expect(screen.getByLabelText('项目处理进度').getAttribute('data-running')).toBe('false');
  });
  it("keeps file inputs mounted when returning from the file picker", async () => {
    render(<NewProductProject/>);
    const equipmentInput = await screen.findByLabelText("设备清单文件");
    const backgroundInput = screen.getByLabelText("选择项目资料文件");
    let finishRefresh!: (value: api.ProductOverview) => void;
    overview.mockImplementationOnce(() => new Promise(resolve => { finishRefresh = resolve; }));
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(overview).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("设备清单文件")).toBe(equipmentInput);
    expect(screen.getByLabelText("选择项目资料文件")).toBe(backgroundInput);
    fireEvent.change(equipmentInput, { target: { files: [new File(["name,quantity"], "设备.csv")] } });
    fireEvent.change(backgroundInput, { target: { files: [new File(["项目背景"], "调研.txt")] } });
    expect(screen.getByText(/设备.csv/)).toBeTruthy();
    expect(screen.getByText("调研.txt")).toBeTruthy();
    await act(async () => finishRefresh(sampleOverview()));
    expect(screen.getByText(/设备.csv/)).toBeTruthy();
    expect(screen.getByText("调研.txt")).toBeTruthy();
    expect(upload).not.toHaveBeenCalled();
  });
  it("hides the upload form if focus refresh reports revoked access", async () => {
    render(<NewProductProject/>);
    await screen.findByLabelText("设备清单文件");
    overview.mockRejectedValueOnce(new ApiError(403, "权限已撤销"));
    fireEvent(window, new Event("focus"));
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("设备清单文件")).toBeNull();
  });
  it("shows authorized projects with their status and next action", async () => {
    render(<ProductDashboard/>);
    expect((await screen.findAllByText("合成供配电项目")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { level: 1, name: "产品事业部工作台" })).toBeTruthy();
    expect(screen.getByLabelText("进行中项目数量").textContent).toBe("3");
    expect(screen.getByRole("link", { name: /本月完成/ }).getAttribute("href")).toContain("filter=completed_month");
    expect(screen.getByRole("link", { name: "继续处理" }).getAttribute("href")).toContain(sampleTask().id);
    expect(screen.getAllByText("查看资料缺口").length).toBeGreaterThan(0);
    expect(screen.getByText("查看待确认项目蓝图")).toBeTruthy();
  });
  it("does not render invented project counts when overview loading fails", async () => {
    overview.mockRejectedValueOnce(new ApiError(503, "服务暂不可用"));
    render(<ProductDashboard/>); await screen.findByRole("alert");
    expect(screen.queryByLabelText("进行中项目数量")).toBeNull();
    expect(screen.queryByText("合成供配电项目")).toBeNull();
  });
  it("prioritizes a review state over a retained generation action", async () => {
    const task = sampleTask({ state: "WAITING_REVIEW", stage: "BLUEPRINT", pending_action: "blueprint" });
    overview.mockResolvedValueOnce({ ...sampleOverview(), recent_projects: [task], todos: [] });
    render(<ProductDashboard/>);
    expect((await screen.findAllByText("查看待确认项目蓝图")).length).toBeGreaterThan(0);
    expect(screen.queryByText("查看蓝图生成进度")).toBeNull();
  });
  it("translates a returned workflow action instead of exposing its internal code", async () => {
    const task = sampleTask({ state: "RUNNING", stage: "WRITING", pending_action: "generate_outputs" });
    overview.mockResolvedValueOnce({ ...sampleOverview(), recent_projects: [task] });
    render(<ProductDashboard/>);
    expect((await screen.findAllByText("查看成果生成进度")).length).toBeGreaterThan(0);
    expect(screen.queryByText("generate_outputs")).toBeNull();
  });
  it("preview never reads project data", () => {
    render(<ProductDashboard preview/>); expect(overview).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 1, name: "产品事业部工作台" })).toBeTruthy();
    expect(screen.queryByText("合成供配电项目")).toBeNull();
  });
  it("aborts outdated list searches instead of letting them replace the next page", async () => {
    const view = render(<ProductProjects/>); await screen.findByText("合成供配电项目");
    const signal = overview.mock.calls[0][1]; view.unmount(); expect(signal.aborted).toBe(true);
    window.history.replaceState({}, "", "/centers/product/projects?filter=review&q=%E4%BE%9B%E7%94%B5&page=2");
    render(<ProductProjects/>); await waitFor(() => expect(overview).toHaveBeenLastCalledWith("filter=review&q=%E4%BE%9B%E7%94%B5&page=2&page_size=12", expect.anything()));
  });
  it("opens the blueprint workbench and preserves list filters for return", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?filter=review&q=%E4%BE%9B%E7%94%B5&page=2");
    render(<ProductProjects/>);
    const project = await screen.findByRole("link", { name: "合成供配电项目" });
    const target = new URL(project.getAttribute("href") || "", window.location.origin);
    expect(target.searchParams.get("tab")).toBe("blueprint");
    expect(target.searchParams.get("returnTo")).toBe("/centers/product/projects?filter=review&q=%E4%BE%9B%E7%94%B5&page=2");
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
  it("retains failed approval feedback and clears it after successful inline approval", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const props = stages(blueprintTask(), { onAction: vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true) }); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    const approve = screen.getByRole("button", { name: "批准蓝图并生成成果" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    await user.type(screen.getByLabelText("蓝图确认依据"), "已根据项目资料逐项核对");
    expect(approve.disabled).toBe(false);
    expect(screen.queryByRole("checkbox")).toBeNull();
    await user.click(approve);
    expect((screen.getByLabelText("蓝图确认依据") as HTMLTextAreaElement).value).toBe("已根据项目资料逐项核对");
    await user.click(approve);
    await waitFor(() => expect((screen.getByLabelText("蓝图确认依据") as HTMLTextAreaElement).value).toBe(""));
    expect(screen.getByRole("region", { name: "项目蓝图 v1" })).toBeTruthy();
    expect(props.onAction).toHaveBeenCalledTimes(2);
    expect(props.onAction).toHaveBeenLastCalledWith("decisions/", expect.objectContaining({ target: "blueprint", target_id: "blueprint-id", sha256: "a".repeat(64), decision: "approve" }));
  });
  it("keeps versions inline and clears approval comments when the blueprint changes", async () => {
    const props = stages(blueprintTask());
    const user = userEvent.setup();
    const view = render(<ProjectStages {...props}/>);
    await user.type(screen.getByLabelText("蓝图确认依据"), "旧版本意见");
    expect(screen.queryByRole("dialog")).toBeNull();
    view.rerender(<ProjectStages {...props}/>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("region", { name: "项目蓝图 v1" })).toBeTruthy();
    const nextTask = blueprintTask();
    nextTask.version = 2;
    nextTask.blueprint_version = 2;
    nextTask.blueprint = { ...nextTask.blueprint!, id: "blueprint-id-2", version: 2, sha256: "b".repeat(64) };
    view.rerender(<ProjectStages {...stages(nextTask)}/>);
    expect(screen.getByRole("region", { name: "项目蓝图 v2" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((screen.getByLabelText("蓝图确认依据") as HTMLTextAreaElement).value).toBe("");
  });
  it("keeps approved blueprints read-only without a leftover confirmation field", async () => {
    const task = { ...blueprintTask(), state: "COMPLETED" as const, stage: "FINAL_REVIEW" as const, blueprint_approved: true, actions: [] };
    const user = userEvent.setup();
    render(<ProjectStages {...stages(task)}/>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByLabelText("蓝图确认依据")).toBeNull();
    expect(screen.getByText("该蓝图已批准，审核内容只读。")).toBeTruthy();
    expect(screen.queryByLabelText("蓝图确认依据")).toBeNull();
  });
  it("uses manual Radix tabs, URL state, and the preserved list return", async () => {
    const returnTo = "/centers/product/projects?filter=review&q=供电&page=2";
    window.history.replaceState({}, "", `/centers/product/projects?task=${sampleTask().id}&tab=blueprint&returnTo=${encodeURIComponent(returnTo)}`);
    const user = userEvent.setup();
    render(<ProjectStages {...stages(blueprintTask())}/>);
    const blueprint = screen.getByRole("tab", { name: "蓝图审批" });
    const technical = screen.getByRole("tab", { name: "技术方案" });
    expect(blueprint.getAttribute("aria-selected")).toBe("true");
    blueprint.focus();
    await act(async () => { await user.keyboard("{ArrowRight}"); });
    expect(document.activeElement).toBe(technical);
    expect(technical.getAttribute("aria-selected")).toBe("false");
    await act(async () => { await user.keyboard("{Enter}"); });
    await waitFor(() => expect(technical.getAttribute("aria-selected")).toBe("true"));
    expect(new URLSearchParams(window.location.search).get("tab")).toBe("technical-solution");
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(technical.id);
    const back = new URL(screen.getByRole("link", { name: "← 返回项目清单" }).getAttribute("href") || "", window.location.origin);
    expect([back.pathname, back.searchParams.get("filter"), back.searchParams.get("q"), back.searchParams.get("page")]).toEqual(["/centers/product/projects", "review", "供电", "2"]);
  });
  it("shows source-only provenance and disables a fourth automatic blueprint revision", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const task = blueprintTask();
    task.blueprint_review = { revision_count: 3, revision_limit: 3, revisions_remaining: 0 };
    const props = stages(task); const user = userEvent.setup(); render(<ProjectStages {...props}/>);
    expect(screen.getByText("仅使用项目资料")).toBeTruthy();
    expect(screen.getByText("本版本未执行知识库检索，未产生知识库命中记录。")).toBeTruthy();
    expect(screen.queryByText(/测试预览|伪造知识库命中/)).toBeNull();
    await user.type(screen.getByLabelText("蓝图确认依据"), "仍需继续修改");
    expect((screen.getByRole("button", { name: "退回修改（剩余 0/3 次）" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/自动修改次数已用完/)).toBeTruthy();
  });
  it("does not expose legacy report approval steps or let legacy reviewers confirm a blueprint", () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=blueprint");
    const view = render(<ProjectStages {...stages({ ...blueprintTask(), actions: ["review_input"] })}/>);
    expect(screen.queryByRole("button", { name: "批准蓝图并生成成果" })).toBeNull();
    expect(screen.queryByLabelText("蓝图确认依据")).toBeNull();
    expect(screen.getByText("当前用户无蓝图审批权限，内容仅供查看。")).toBeTruthy();
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
    const flow = screen.getByLabelText('项目处理进度');
    expect(flow.textContent).toContain('已结束 2 / 8 · 完成 1');
    expect(flow.textContent).toContain('生成项目蓝图 · 处理中');
    expect(screen.getByLabelText('已结束处理节点').getAttribute('value')).toBe('2');
    expect(screen.queryByLabelText('项目流程')).toBeNull();
  });
  it('shows the complete eight-node workflow and prioritizes checkpoint progress', () => {
    const task = sampleTask({ state: 'RUNNING', stage: 'BLUEPRINT', pending_action: 'blueprint',
      analysis_progress: { documents: { status: 'failed', detail: '旧状态' } },
      checkpoint: { analysis_progress: {
        documents: { status: 'completed', source_count: 4 },
        equipment: { status: 'completed', item_count: 18 },
        knowledge: { status: 'waiting', detail: 'RAGFlow 连接待配置' },
        blueprint: { status: 'running', detail: '正在编排章节' },
      } } });
    render(<ProjectStages {...stages(task)}/>);
    const flow = screen.getByLabelText('项目处理进度');
    expect(flow.textContent).toContain('已结束 2 / 8 · 完成 1');
    expect(flow.textContent).toContain('生成项目蓝图 · 处理中');
    expect(flow.textContent).not.toContain('RAGFlow');
    expect(screen.queryByText('旧状态')).toBeNull();
  });
  it('queues the required knowledge preparation action before a formal blueprint', async () => {
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
    await user.click(screen.getByRole('button', { name: '先检索知识库' }));
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
  it.each(["formal", undefined] as const)("rechecks legacy generation progress against the current formal minimum with profile %s", (profile) => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=technical-solution");
    const output: api.DraftOutput = { id: "technical-current", family: "technical-solution", version: 1, sha256: "1".repeat(64), current: true, stale: false, draft: true, approved: false, review_status: "pending_review", engine: "test", content_version: 1, content_sha256: null, content_approved: false, source_versions: [] };
    const task = sampleTask({ output_profile: profile, output_targets: { 'technical-solution': 50000, feasibility: 70000 }, output_generation: { 'technical-solution': { target_characters: 3000, actual_characters: 3000, minimum_characters: 2700, status: "target_met", updated_at: "2026-09-29T10:00:00Z" } } });
    render(<ProjectStages {...stages(task, { outputs: [output] })}/>);
    expect(screen.getByText("篇幅结果").closest("div")?.textContent).toContain("3,000 / 至少 50,000 字 · 未达当前最低要求，可续写或重试");
    expect(screen.queryByText("已达目标")).toBeNull();
    expect(screen.getByLabelText("完整处理链路").querySelector('li[data-status="blocked"]')?.textContent).toContain("技术方案");
  });
  it("never offers stale artifacts as current outputs in any artifact tab", async () => {
    window.history.replaceState({}, "", "/centers/product/projects?tab=technical-solution");
    const staleOutputs = (["technical-solution", "feasibility", "presentation"] as const).map((family, index): api.DraftOutput => ({ id: `old-artifact-${index}`, family, version: 1, sha256: String(index + 1).repeat(64), current: false, stale: true, draft: true, approved: false, review_status: "stale", engine: "test", content_version: 1, content_sha256: null, content_approved: false, source_versions: [] }));
    const user = userEvent.setup();
    render(<ProjectStages {...stages(sampleTask(), { outputs: staleOutputs })}/>);
    for (const name of ["技术方案", "可研报告", "汇报 PPT"]) {
      const tab = screen.getByRole("tab", { name });
      await user.click(tab);
      await waitFor(() => expect(tab.getAttribute("aria-selected")).toBe("true"));
      expect(screen.queryByRole("link", { name: "下载文件" })).toBeNull();
      expect(screen.getByRole("tabpanel").textContent).toContain("尚未生成");
    }
  });
  it('renders successful knowledge retrieval as success instead of an orange warning', () => {
    const task = blueprintTask();
    task.knowledge = { mode: 'ragflow_required', required: true, status: 'ready', ragflow_used: true, source_count: 3, detail: '检索完成' };
    render(<ProjectStages {...stages(task)}/>);
    const status = screen.getByText('知识库检索已完成').closest('.pd-feedback');
    expect(status?.classList.contains('success')).toBe(true);
    expect(status?.classList.contains('pd-knowledge-preview')).toBe(false);
  });
  it('offers an explicit retry from saved progress after an approved blueprint fails', async () => {
    const task = { ...blueprintTask(), state: 'FAILED' as const, stage: 'WRITING' as const, blueprint_approved: true, error_code: 'execution_failed', actions: ['retry'] };
    const props = stages(task);
    const user = userEvent.setup();
    render(<ProjectStages {...props}/>);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByLabelText('蓝图确认依据')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('已保存项目资料和已完成章节');
    await user.click(screen.getByRole('button', { name: '从已保存进度重试' }));
    expect(props.onAction).toHaveBeenCalledWith('retry/', {});
    expect(props.onAction).not.toHaveBeenCalledWith('decisions/', expect.anything());
  });
});
