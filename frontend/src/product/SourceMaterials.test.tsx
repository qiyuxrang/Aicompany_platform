import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { getSourceDetail, type SourceDetail } from "./product-api";
import SourceMaterials from "./SourceMaterials";
import { sampleTask } from "./workbench-fixtures";

vi.mock("./product-api", async original => ({ ...await original<typeof import("./product-api")>(), getSourceDetail: vi.fn() }));
const read = vi.mocked(getSourceDetail);
function detail(): SourceDetail {
  return { id: "source-one", task_id: sampleTask().id, task_version: 1, name: "设计说明.docx", sha256: "a".repeat(64), size: 3000, media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    summary: { status: "completed", method: "native", extraction_hash: "b".repeat(64), parser_version: "product-intake-1", block_count: 1, item_count: 0, character_count: 8, truncated: false, metadata: {} },
    blocks: [{ id: "b1", kind: "paragraph", text: "本项目设备数量为2台", location: { paragraph: 1 }, location_label: "段落 1" }], warnings: [], issues: [], revisions: [{ version: 1, sha256: "c".repeat(64), created_at: "2026-09-26T01:00:00Z", reason: "external_source_upload" }], pagination: { page: 1, page_size: 40, total: 1 }, can_reparse: true, can_correct: true, can_preview: false };
}
function props() {
  return { task: sampleTask({ sources: [{ id: "source-one", original_name: "设计说明.docx", size: 3000 }] }), busy: false, disabled: () => false, onSourceAction: vi.fn().mockResolvedValue(true), onAction: vi.fn().mockResolvedValue(true) };
}
beforeEach(() => { vi.clearAllMocks(); window.history.replaceState({}, "", "/centers/product/projects?tab=sources"); read.mockResolvedValue(detail()); });

describe("资料解析与来源核对", () => {
  it("displays native text and paragraph location without rendering unsafe document HTML", async () => {
    const value = detail(); value.blocks[0].text = '<script>alert("not executable")</script>';
    read.mockResolvedValueOnce(value);
    const view = render(<SourceMaterials {...props()}/>);
    await screen.findByText(value.blocks[0].text);
    expect(screen.getByText("段落 1")).toBeTruthy();
    expect(view.container.querySelector("script")).toBeNull();
    expect(screen.getByText("解析完成")).toBeTruthy();
  });
  it("shows partial extraction and warnings instead of a successful completion claim", async () => {
    const value = detail(); value.summary.status = "partial"; value.warnings = [{ code: "ocr_limit", detail: "剩余扫描页未识别", severity: "partial" }];
    read.mockResolvedValueOnce(value); render(<SourceMaterials {...props()}/>);
    await screen.findByText("剩余扫描页未识别");
    expect(screen.getByText("部分解析")).toBeTruthy();
    expect(screen.queryByText("解析完成")).toBeNull();
  });
  it("binds manual correction to source hash, extraction hash and task version", async () => {
    const value = props(); const user = userEvent.setup(); render(<SourceMaterials {...value}/>);
    await user.click(await screen.findByRole("button", { name: "校正内容" }));
    await user.clear(screen.getByLabelText("校正后的文字")); await user.type(screen.getByLabelText("校正后的文字"), "实际设备数量为3台");
    expect((screen.getByRole("button", { name: "保存解析校正" }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("校正依据"), "对照原件核对");
    await user.click(screen.getByRole("button", { name: "保存解析校正" }));
    expect(value.onSourceAction).toHaveBeenCalledWith("source-one", "correction/", { expected_version: 1, source_sha256: "a".repeat(64), extraction_hash: "b".repeat(64), block_id: "b1", text: "实际设备数量为3台", reason: "对照原件核对" });
  });
  it("edits table cells separately and submits cells rather than guessing column splits", async () => {
    const data = detail(); data.blocks[0] = { ...data.blocks[0], kind: "table_row", cells: ["001", "配电柜", "2", "台"], text: "001\t配电柜\t2\t台" };
    read.mockResolvedValue(data); const value = props(); const user = userEvent.setup(); render(<SourceMaterials {...value}/>);
    await user.click(await screen.findByRole("button", { name: "校正内容" }));
    await user.clear(screen.getByLabelText("第 3 列")); await user.type(screen.getByLabelText("第 3 列"), "5");
    await user.type(screen.getByLabelText("校正依据"), "单元格核对"); await user.click(screen.getByRole("button", { name: "保存解析校正" }));
    expect(value.onSourceAction.mock.calls[0][2].cells).toEqual(["001", "配电柜", "5", "台"]);
  });
  it("preserves pending correction when the project version changes", async () => {
    const value = props(); const user = userEvent.setup(); const view = render(<SourceMaterials {...value}/>);
    await user.click(await screen.findByRole("button", { name: "校正内容" }));
    await user.type(screen.getByLabelText("校正后的文字"), "尚未保存");
    read.mockResolvedValue({ ...detail(), task_version: 2 });
    view.rerender(<SourceMaterials {...value} task={{ ...value.task, version: 2 }}/>);
    await screen.findByRole("alert");
    expect((screen.getByLabelText("校正后的文字") as HTMLTextAreaElement).value).toContain("尚未保存");
    expect((screen.getByRole("button", { name: "保存解析校正" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("requests the selected historical snapshot and does not offer writes", async () => {
    const user = userEvent.setup(); render(<SourceMaterials {...props()}/>);
    await screen.findByLabelText("选择解析版本");
    read.mockResolvedValue({ ...detail(), can_correct: false, can_reparse: false });
    await user.selectOptions(screen.getByLabelText("选择解析版本"), "1");
    await waitFor(() => expect(read.mock.calls.at(-1)?.[1]).toContain("revision=1"));
    await screen.findByText(/正在查看历史解析快照/);
    expect(screen.queryByRole("button", { name: "校正内容" })).toBeNull();
    expect((screen.getByRole("button", { name: "重新解析" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("loads a raster preview only after explicit user action", async () => {
    const data = detail(); data.can_preview = true; data.media_type = "application/pdf"; data.summary.metadata.pages = 3;
    read.mockResolvedValue(data); const user = userEvent.setup(); render(<SourceMaterials {...props()}/>);
    await screen.findByText("本项目设备数量为2台"); expect(screen.queryByRole("img")).toBeNull();
    await user.click(screen.getByRole("button", { name: "预览原件" }));
    expect(screen.getByRole("img").getAttribute("src")).toBe("/api/product/sources/source-one/preview/?page=1");
    fireEvent.error(screen.getByRole("img")); expect(screen.getByRole("alert").textContent).toContain("下载原文件核对");
  });
  it("requires reviewer classification and reason before resolving an issue", async () => {
    const data = detail(); data.issues = [{ code: "low_ocr_confidence", detail: "识别不确定", issue_hash: "issue-hash" }];
    read.mockResolvedValue(data); const value = props(); const user = userEvent.setup(); render(<SourceMaterials {...value}/>);
    await screen.findByText("识别不确定");
    await user.click(screen.getByText("人工核对待处理项（1）"));
    const button = screen.getByRole("button", { name: "保存核对" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await user.selectOptions(screen.getByLabelText("核对分类 low_ocr_confidence"), "missing");
    await user.type(screen.getByLabelText("核对依据 low_ocr_confidence"), "需要补充清晰原图");
    await user.click(button);
    expect(value.onAction).toHaveBeenCalledWith("input-review/", { resolutions: [{ issue_hash: "issue-hash", category: "missing", reason: "需要补充清晰原图", source_ids: ["source-one"] }] });
  });
  it("rejects a response for another project and clears protected contents", async () => {
    read.mockResolvedValue({ ...detail(), task_id: "another-task" });
    render(<SourceMaterials {...props()}/>); await screen.findByRole("alert");
    expect(screen.queryByText("本项目设备数量为2台")).toBeNull();
  });
  it("aborts source reads when the view unmounts", async () => {
    const view = render(<SourceMaterials {...props()}/>); await screen.findByText("本项目设备数量为2台");
    const signal = read.mock.calls[0][2]; view.unmount(); expect(signal.aborted).toBe(true);
  });
});
