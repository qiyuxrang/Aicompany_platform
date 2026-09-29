import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import {
  listKnowledgeChunks,
  listKnowledgeDatasets,
  listKnowledgeDocuments,
  type ChunkPage,
  type DocumentPage,
  type KnowledgeDataset,
} from "./knowledge-materials-api";
import KnowledgeMaterials from "./KnowledgeMaterials";

vi.mock("./knowledge-materials-api", async importOriginal => ({
  ...await importOriginal<typeof import("./knowledge-materials-api")>(),
  listKnowledgeDatasets: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  listKnowledgeChunks: vi.fn(),
}));

const readDatasets = vi.mocked(listKnowledgeDatasets);
const readDocuments = vi.mocked(listKnowledgeDocuments);
const readChunks = vi.mocked(listKnowledgeChunks);

const datasets: KnowledgeDataset[] = [
  { id: "engineering", name: "05_产品事业部_P5工程知识库", document_count: 2 },
  { id: "delivery", name: "02_产品事业部_项目案例与交付库", document_count: 1 },
  { id: "solution", name: "01_产品事业部_方案与可研库", document_count: 1 },
];

function documentPage(name = "工程建设规范.pdf", overrides: Partial<DocumentPage> = {}): DocumentPage {
  return {
    documents: [{ id: `doc-${name}`, name, type: "PDF", size: 4096, chunk_count: 11, run: "completed", progress: 1, updated_at: "2026-09-29T08:00:00Z" }],
    total: 1, page: 1, page_size: 20, has_more: false, ...overrides,
  };
}

function chunkPage(content = "配电系统应符合现行工程规范。", overrides: Partial<ChunkPage> = {}): ChunkPage {
  return {
    document: { id: "doc-工程建设规范.pdf", name: "工程建设规范.pdf" },
    chunks: [{ id: "chunk-1", content }], total: 1, page: 1, page_size: 10, has_more: false, ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  readDatasets.mockResolvedValue({ datasets });
  readDocuments.mockImplementation(async datasetId => documentPage(datasetId === "delivery" ? "某市智慧交通交付案例.docx" : "工程建设规范.pdf"));
  readChunks.mockResolvedValue(chunkPage());
});

describe("KnowledgeMaterials", () => {
  it("按后端知识库分类加载真实文档并以纯文本预览片段", async () => {
    const user = userEvent.setup();
    const view = render(<KnowledgeMaterials/>);

    expect(await screen.findByRole("button", { name: "05_产品事业部_P5工程知识库，2 份资料" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "02_产品事业部_项目案例与交付库，1 份资料" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "01_产品事业部_方案与可研库，1 份资料" })).toBeTruthy();
    expect(await screen.findByText("工程建设规范.pdf")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "02_产品事业部_项目案例与交付库，1 份资料" }));
    expect(await screen.findByText("某市智慧交通交付案例.docx")).toBeTruthy();
    expect(readDocuments).toHaveBeenLastCalledWith("delivery", 1, "", expect.any(AbortSignal));

    const unsafeText = '<img src="/private-file">项目交付说明';
    readChunks.mockResolvedValueOnce({ ...chunkPage(unsafeText), document: { id: "doc-某市智慧交通交付案例.docx", name: "某市智慧交通交付案例.docx" } });
    await user.click(screen.getByRole("button", { name: /某市智慧交通交付案例/ }));
    expect(await screen.findByText(unsafeText)).toBeTruthy();
    expect(view.container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "项目资料与任务" }).getAttribute("href")).toBe("/centers/product/projects");
  });

  it("搜索和翻页都只请求当前知识库", async () => {
    const user = userEvent.setup();
    readDocuments.mockResolvedValue(documentPage("第一页.pdf", { total: 21, has_more: true }));
    render(<KnowledgeMaterials/>);
    await screen.findByText("第一页.pdf");

    await user.type(screen.getByLabelText("搜索知识库资料"), "配电 规范");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(readDocuments).toHaveBeenLastCalledWith("engineering", 1, "配电 规范", expect.any(AbortSignal)));

    await user.click(within(screen.getByLabelText("05_产品事业部_P5工程知识库资料列表")).getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(readDocuments).toHaveBeenLastCalledWith("engineering", 2, "配电 规范", expect.any(AbortSignal)));
  });

  it("切换知识库时中止延迟请求且不回填旧文档", async () => {
    const user = userEvent.setup();
    const first = deferred<DocumentPage>();
    readDocuments.mockImplementation(datasetId => datasetId === "engineering" ? first.promise : Promise.resolve(documentPage("交付归档.docx")));
    render(<KnowledgeMaterials/>);

    await user.click(await screen.findByRole("button", { name: "02_产品事业部_项目案例与交付库，1 份资料" }));
    expect(await screen.findByText("交付归档.docx")).toBeTruthy();
    expect(readDocuments.mock.calls[0][3].aborted).toBe(true);

    await act(async () => first.resolve(documentPage("不应出现的旧文档.pdf")));
    expect(screen.queryByText("不应出现的旧文档.pdf")).toBeNull();
  });

  it("权限撤销后清空文档和正文并允许重新检查", async () => {
    const user = userEvent.setup();
    readChunks.mockResolvedValueOnce(chunkPage("只应短暂显示的受保护正文", { total: 11, has_more: true }));
    render(<KnowledgeMaterials/>);

    await user.click(await screen.findByRole("button", { name: /工程建设规范/ }));
    expect(await screen.findByText("只应短暂显示的受保护正文")).toBeTruthy();
    readChunks.mockRejectedValueOnce(new ApiError(403, "grant revoked"));
    await user.click(within(screen.getByLabelText("资料内容预览")).getByRole("button", { name: "下一页" }));

    expect(await screen.findByRole("heading", { name: "无法访问知识库资料" })).toBeTruthy();
    expect(screen.queryByText("只应短暂显示的受保护正文")).toBeNull();
    expect(screen.queryByText("工程建设规范.pdf")).toBeNull();
    expect(screen.getByRole("button", { name: "重新检查权限" })).toBeTruthy();
  });

  it("明确区分已解析但未提取到文字的资料", async () => {
    const user = userEvent.setup();
    readDocuments.mockResolvedValue(documentPage("扫描件.pdf", { documents: [{ id: "scan", name: "扫描件.pdf", type: "PDF", size: 2048, chunk_count: 0, run: "completed", progress: 1, updated_at: "2026-09-29T08:00:00Z" }] }));
    readChunks.mockResolvedValue({ document: { id: "scan", name: "扫描件.pdf" }, chunks: [], total: 0, page: 1, page_size: 10, has_more: false });
    render(<KnowledgeMaterials/>);

    await user.click(await screen.findByRole("button", { name: /扫描件/ }));
    expect(await screen.findByRole("heading", { name: "未提取到文字" })).toBeTruthy();
    expect(screen.getByText("该资料尚未完成文字提取，或原文件没有可识别文本。")).toBeTruthy();
  });

  it("区分服务不可用和空知识库，并在卸载时中止请求", async () => {
    const user = userEvent.setup();
    readDatasets.mockRejectedValueOnce(new Error("知识库连接超时")).mockResolvedValueOnce({ datasets: [] });
    const view = render(<KnowledgeMaterials/>);
    expect(await screen.findByText("知识库连接超时")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("heading", { name: "暂无可浏览的知识库" })).toBeTruthy();

    readDatasets.mockImplementationOnce(() => new Promise(() => undefined));
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(readDatasets).toHaveBeenCalledTimes(3));
    const signal = readDatasets.mock.calls[2][0];
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});
