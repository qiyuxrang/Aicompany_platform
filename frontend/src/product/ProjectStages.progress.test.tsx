import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DocumentTask, ProductWorkflowProgress } from "./product-api";
import ProjectStages from "./ProjectStages";
import { sampleTask } from "./workbench-fixtures";

const props = (task: DocumentTask) => ({
  task,
  outputs: [],
  history: null,
  outputsError: "",
  historyError: "",
  busy: false,
  conflict: false,
  disabled: () => true,
  onAction: vi.fn(),
  onReload: vi.fn(),
  onUpload: vi.fn(),
});

describe("ProjectStages progress", () => {
  it("shows the whole chain without treating source-only preview as retrieval", () => {
    const task = sampleTask({
      state: "RUNNING",
      stage: "BLUEPRINT",
      pending_action: "blueprint",
      checkpoint: { analysis_progress: {
        knowledge: { status: "skipped", detail: "已按当前配置使用项目上传资料，不代表知识库检索命中" },
        documents: { status: "completed", source_count: 2 },
        equipment: { status: "completed", item_count: 3 },
        blueprint: { status: "running" },
      } },
    });

    render(<ProjectStages {...props(task)}/>);

    const chain = screen.getByLabelText("完整处理链路");
    expect(within(chain).getAllByRole("listitem")).toHaveLength(8);
    for (const title of ["知识库检索", "联网降级补充", "组织语言与内容", "生成项目蓝图", "人工审核蓝图", "技术方案", "可行性研究报告", "汇报 PPT"]) {
      expect(within(chain).getByText(title)).toBeTruthy();
    }
    expect(within(chain).getByText("知识库检索").closest("li")?.textContent).toContain("未执行");
    expect(within(chain).getByText("联网降级补充").closest("li")?.textContent).toContain("未配置/未执行");
    expect(within(chain).getByText("知识库检索").closest("li")?.getAttribute("data-status")).toBe("skipped");
    expect(screen.getByText("已结束 3 / 8 · 完成 1")).toBeTruthy();
    expect(screen.getByLabelText("已结束处理节点").getAttribute("value")).toBe("3");
  });

  it("shows web search as skipped when the knowledge base has hits", () => {
    const analysisProgress = {
      knowledge: { status: "completed", source_count: 1 },
      web_search: { status: "skipped", detail: "知识库已命中，无需联网补充" },
      documents: { status: "completed", source_count: 2 },
      equipment: { status: "completed", item_count: 3 },
    } satisfies Record<string, ProductWorkflowProgress>;
    const task = sampleTask({
      knowledge: { mode: "ragflow_required", required: true, status: "ready", ragflow_used: true, source_count: 1, detail: "检索完成" },
      checkpoint: { analysis_progress: analysisProgress as DocumentTask["analysis_progress"] },
    });

    render(<ProjectStages {...props(task)}/>);

    const webStep = within(screen.getByLabelText("完整处理链路")).getByText("联网降级补充").closest("li");
    expect(webStep?.getAttribute("data-status")).toBe("skipped");
    expect(webStep?.textContent).toContain("未执行");
    expect(screen.getByText("知识库已命中，无需联网补充")).toBeTruthy();
  });

  it("pauses generation when completed knowledge retrieval has zero hits and web search is unavailable", () => {
    const detail = "知识库未命中；联网搜索服务尚未接入，已暂停生成。请补充知识库资料后重新检索。";
    const analysisProgress = {
      knowledge: { status: "completed", source_count: 0 },
      web_search: { status: "blocked", detail },
    } satisfies Record<string, ProductWorkflowProgress>;
    const task = sampleTask({
      state: "WAITING_INPUT",
      error_code: "web_search_unconfigured",
      knowledge: { mode: "ragflow_required", required: true, status: "ready", ragflow_used: true, source_count: 0, detail: "知识库检索完成，零命中" },
      checkpoint: { analysis_progress: analysisProgress as DocumentTask["analysis_progress"] },
    });

    render(<ProjectStages {...props(task)}/>);

    const chain = screen.getByLabelText("完整处理链路");
    expect(within(chain).getByText("知识库检索").closest("li")?.getAttribute("data-status")).toBe("completed");
    const webStep = within(chain).getByText("联网降级补充").closest("li");
    expect(webStep?.getAttribute("data-status")).toBe("blocked");
    expect(webStep?.textContent).not.toContain("已完成");
    expect(screen.getByRole("status").textContent).toContain("处理已暂停");
    expect(screen.getByRole("alert").textContent).toContain(detail);
  });

  it.each([
    { family: "technical-solution" as const, title: "技术方案", target: 50000 },
    { family: "feasibility" as const, title: "可行性研究报告", target: 70000 },
  ])("requires the full server target without a profile for $family", ({ family, title, target }) => {
    const task = sampleTask({
      output_targets: { 'technical-solution': 50000, feasibility: 70000 },
      output_generation: { [family]: { target_characters: target, actual_characters: target * 0.9, minimum_characters: target * 0.9, status: "target_met", updated_at: "2026-09-29T10:00:00Z" } },
    });
    const { rerender } = render(<ProjectStages {...props(task)}/>);
    const step = () => within(screen.getByLabelText("完整处理链路")).getByText(title).closest("li");
    expect(step()?.getAttribute("data-status")).toBe("blocked");
    expect(screen.getByText(new RegExp(`当前最低 ${target.toLocaleString()} 字`))).toBeTruthy();
    task.output_generation![family]!.actual_characters = target - 1;
    rerender(<ProjectStages {...props(task)}/>);
    expect(step()?.getAttribute("data-status")).toBe("blocked");
    task.output_generation![family]!.actual_characters = target;
    rerender(<ProjectStages {...props(task)}/>);
    expect(step()?.getAttribute("data-status")).not.toBe("blocked");
  });

  it("marks a short output as unfinished and resumable", () => {
    const task = sampleTask({ state: "WAITING_INPUT", stage: "WRITING", error_code: "output_length_below_target" });

    render(<ProjectStages {...props(task)}/>);

    expect(screen.getByRole("alert").textContent).toContain("正文篇幅未达到当前最低要求，任务未完成");
    expect(screen.getByRole("alert").textContent).toContain("可续写补足篇幅，或从已保存进度重试");
    expect(screen.getByRole("status").textContent).toContain("处理已暂停");
  });

  it("reports exhausted model-call budget instead of completion", () => {
    const task = sampleTask({ state: "WAITING_INPUT", stage: "WRITING", error_code: "model_call_limit" });

    render(<ProjectStages {...props(task)}/>);

    expect(screen.getByRole("alert").textContent).toContain("模型调用预算已耗尽，任务未完成");
    expect(screen.getByRole("alert").textContent).toContain("补充预算后可继续");
    expect(screen.getByRole("status").textContent).toContain("处理已暂停");
  });
});
