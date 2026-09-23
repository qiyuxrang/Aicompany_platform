import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearApiSession, unauthorizedEvent } from "../api";
import DocumentWorkspace from "./DocumentWorkspace";
import type { DocumentTask } from "./product-api";

function task(overrides: Partial<DocumentTask> = {}): DocumentTask {
  return {
    id: "task-1", title: "真实项目", state: "DRAFT", stage: "INTAKE", version: 2, input_version: 1, blueprint_version: 1,
    input: { project: "已有项目", requirements: "真实需求", background: "已有背景", items: [], conditions: [] },
    blueprint: { id: "blueprint-1", version: 1, sha256: "blueprint-sha", payload: { purpose: "已确认目标", audience: "产品人员", chapters: [], conditions: [], missing: [], conflicts: [], template_version: "v1" } },
    chapters: [{ id: "revision-1", version: 1, payload: { chapter_id: "chapter-1", title: "建设目标", paragraphs: ["已核实正文"], source_ids: ["source-1"] } }],
    artifacts: [{ id: "artifact-1", version: 1, sha256: "old-sha" }, { id: "artifact-2", version: 2, sha256: "new-sha", render_evidence: { kind: "candidate", status: "rendered", page_count: 2, pages: [{ page: 1, sha256: "page-1-sha" }, { page: 2, sha256: "page-2-sha" }] } }],
    sources: [{ id: "source-1", original_name: "真实背景.txt" }], approvals: [], issues: [], error_code: "", reviewer_id: 2, owner_id: 1,
    input_issues: [], impact: {},
    actions: ["edit", "add_source", "assign_reviewer", "review_input", "add_statement", "queue_retrieve", "queue_blueprint", "save_blueprint", "review_blueprint", "queue_write", "save_chapter", "queue_render", "queue_candidate", "verify_artifact", "review_artifact", "cancel", "retry"],
    blockers: {},
    ...overrides,
  };
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
let current: DocumentTask;
let override: (path: string, init: RequestInit) => Response | Promise<Response> | undefined;
let requests: { path: string; init: RequestInit }[];
beforeEach(() => {
  clearApiSession();
  window.history.replaceState({}, "", "/centers/product/documents");
  current = task(); requests = []; override = () => undefined;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    requests.push({ path, init });
    const custom = override(path, init);
    if (custom) return custom;
    if (path === "/api/csrf/") return response({ csrfToken: "test-csrf" });
    if (path === "/api/product/tasks/" && !init.method?.match(/POST/)) return response([current, task({ id: "task-2", title: "第二任务" })]);
    if (path === "/api/product/tasks/task-2/") return response(task({ id: "task-2", title: "第二任务", input: { ...current.input!, project: "第二任务项目" } }));
    return response(current);
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const writes = () => requests.filter(request => request.init.method && request.init.method !== "GET");
const bodyOfLastWrite = () => JSON.parse(writes().at(-1)!.init.body as string) as Record<string, unknown>;
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function openTask() {
  render(<DocumentWorkspace />);
  await screen.findByRole("option", { name: /真实项目/ });
  await act(async () => { fill("选择任务", "task-1"); });
  await screen.findByLabelText("草稿标题");
}
async function click(name: string) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
}

it("从服务端输入分离只读来源元数据，普通保存不发送伪造来源字段", async () => {
  current.input = { ...current.input!, sources: [{ id: "source-1" }], issues: [],
    items: [{ row_id: "2", name: "合成设备", quantity: "2", unit: "台", source_id: "source-1", source_row: 2 }] } as unknown as DocumentTask["input"];
  await openTask();
  const editor = screen.getByLabelText("草稿输入 JSON") as HTMLTextAreaElement;
  const value = JSON.parse(editor.value);
  expect(value.sources).toBeUndefined();
  expect(value.issues).toBeUndefined();
  expect(value.items[0]).toEqual({ row_id: "2", name: "合成设备", quantity: "2", unit: "台" });
});

describe("技术方案任务前端交互（mock，不验证模型或 Word 格式）", () => {
  it("深链准确打开第二个任务，并拒绝无效任务与跨任务成果", async () => {
    window.history.replaceState({}, "", "/centers/product/documents?task=task-2");
    render(<DocumentWorkspace />);
    expect((await screen.findByLabelText("草稿标题") as HTMLInputElement).value).toBe("第二任务");
    cleanup();

    window.history.replaceState({}, "", "/centers/product/documents?task=missing");
    render(<DocumentWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toContain("不存在或当前账号无权访问");
    expect(screen.queryByLabelText("草稿标题")).toBeNull();
    expect(screen.queryByText(/真实项目 ·/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "创建任务" })).toBeNull();
    cleanup();

    window.history.replaceState({}, "", "/centers/product/documents?task=task-1&artifact=artifact-2");
    render(<DocumentWorkspace />);
    expect((await screen.findByLabelText("选择审核文档版本") as HTMLSelectElement).value).toBe("artifact-2");
    cleanup();

    window.history.replaceState({}, "", "/centers/product/documents?task=task-2&artifact=artifact-1");
    override = path => path === "/api/product/tasks/task-2/" ? response(task({ id: "task-2", title: "第二任务", artifacts: [{ id: "artifact-x", version: 1, sha256: "x" }] })) : undefined;
    render(<DocumentWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toContain("不属于当前产品任务");
    expect(screen.queryByLabelText("草稿标题")).toBeNull();
    expect(screen.queryByText(/第二任务 ·/)).toBeNull();
  });

  it("预览不请求业务数据，也不给管理员隐含权限", () => {
    window.history.replaceState({}, "", "/preview/product/documents");
    render(<DocumentWorkspace />);
    expect(screen.getByText(/平台管理员不会自动获得业务权限/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("一句话创建任务，可附加多个文件；失败重试复用幂等键", async () => {
    let rejectFirst: (value: Response) => void = () => {};
    let attempts = 0;
    let uploadsCompleted = 0;
    override = (path, init) => {
      if (path === "/api/product/conversations/" && init.method === "POST") {
        if (++attempts === 1) return new Promise(resolve => { rejectFirst = resolve; });
        return response({ task: current, intent: { mode: "deterministic_fallback", model_called: false, requested_outputs: [], detected_paths: [] }, blockers: {} });
      }
      if (path === "/api/product/tasks/task-1/sources/" && init.method === "POST") return response({ source_id: `source-${uploadsCompleted}`, task: task({ version: 3 + uploadsCompleted++ }) });
      return undefined;
    };
    render(<DocumentWorkspace />);
    await screen.findByLabelText("描述任务、文件或资料路径");
    fill("描述任务、文件或资料路径", "使用 D:\\项目资料 生成技术方案");
    const first = new File(["a"], "清单.csv", { type: "text/csv" });
    const second = new File(["b"], "背景.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("＋ 添加文件"), { target: { files: [first, second] } });
    await click("发送并创建任务");
    expect((screen.getByRole("button", { name: "正在创建…" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByLabelText("描述任务、文件或资料路径").closest("form")!);
    expect(writes()).toHaveLength(1);
    await act(async () => { rejectFirst(response({ detail: "暂不可用" }, 503)); });
    expect(screen.getByRole("alert").textContent).toContain("缺少业务授权或服务授权");
    expect((screen.getByLabelText("描述任务、文件或资料路径") as HTMLTextAreaElement).disabled).toBe(true);
    await click("发送并创建任务");
    await screen.findByLabelText("草稿标题");
    const creates = writes().filter(request => request.path === "/api/product/conversations/");
    expect(creates).toHaveLength(2);
    const key = new Headers(creates[0].init.headers).get("Idempotency-Key");
    expect(key).toMatch(/^[\da-f-]{36}$/i);
    expect(new Headers(creates[1].init.headers).get("Idempotency-Key")).toBe(key);
    expect(creates[1].init.body).toBe(creates[0].init.body);
    expect(JSON.parse(creates[1].init.body as string)).toEqual({ message: "使用 D:\\项目资料 生成技术方案" });
    expect(new Headers(creates[1].init.headers).get("X-CSRFToken")).toBe("test-csrf");
    const uploads = writes().filter(request => request.path === "/api/product/tasks/task-1/sources/");
    expect(uploads).toHaveLength(2);
    expect((uploads[0].init.body as FormData).get("file")).toBe(first);
    expect((uploads[1].init.body as FormData).get("file")).toBe(second);
    expect((uploads[0].init.body as FormData).get("expected_version")).toBe("2");
    expect((uploads[1].init.body as FormData).get("expected_version")).toBe("3");
    await click("＋ 新建任务");
    fill("描述任务、文件或资料路径", "另一个任务");
    await click("发送并创建任务");
    expect(new Headers(writes().at(-1)!.init.headers).get("Idempotency-Key")).not.toBe(key);
  });

  it("只添加附件也能自动创建任务", async () => {
    override = (path, init) => {
      if (path === "/api/product/conversations/" && init.method === "POST") {
        return response({ task: current, intent: { mode: "deterministic_fallback", model_called: false, requested_outputs: [], detected_paths: [] }, blockers: {} });
      }
      if (path === "/api/product/tasks/task-1/sources/" && init.method === "POST") return response({ source_id: "source-1", task: task({ version: 3 }) });
      return undefined;
    };
    render(<DocumentWorkspace />);
    await screen.findByLabelText("描述任务、文件或资料路径");
    const file = new File(["背景"], "项目背景.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("＋ 添加文件"), { target: { files: [file] } });
    await click("发送并创建任务");
    const request = writes().find(item => item.path === "/api/product/conversations/")!;
    expect(JSON.parse(request.init.body as string)).toEqual({ message: "请根据已上传资料编制技术方案、可研报告和汇报 PPT：项目背景.txt" });
  });

  it("创建后附件部分失败可续传，不重复任务和已成功附件", async () => {
    let secondAttempts = 0;
    override = (path, init) => {
      if (path === "/api/product/conversations/" && init.method === "POST") return response({ task: current, intent: { mode: "deterministic_fallback", model_called: false, requested_outputs: [], detected_paths: [] }, blockers: {} });
      if (path === "/api/product/tasks/task-1/sources/" && init.method === "POST") {
        const body = init.body as FormData;
        const file = body.get("file") as File;
        if (file.name === "一.csv") return response({ source_id: "one", task: task({ version: 3 }) });
        if (++secondAttempts === 1) return response({ detail: "上传暂时失败" }, 503);
        return response({ source_id: "two", task: task({ version: 4 }) });
      }
      return undefined;
    };
    render(<DocumentWorkspace />);
    await screen.findByLabelText("描述任务、文件或资料路径");
    fill("描述任务、文件或资料路径", "生成技术方案");
    const first = new File(["1"], "一.csv", { type: "text/csv" });
    const second = new File(["2"], "二.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("＋ 添加文件"), { target: { files: [first, second] } });
    await click("发送并创建任务");
    expect(await screen.findByText(/仍有 1 个附件待上传/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "继续上传剩余资料" })).toBeTruthy();
    await click("继续上传剩余资料");
    await screen.findByLabelText("草稿标题");
    const creates = writes().filter(item => item.path === "/api/product/conversations/");
    const uploads = writes().filter(item => item.path.endsWith("/sources/"));
    expect(creates).toHaveLength(1);
    expect(uploads.map(item => ((item.init.body as FormData).get("file") as File).name)).toEqual(["一.csv", "二.txt", "二.txt"]);
    expect((uploads[2].init.body as FormData).get("expected_version")).toBe("3");
  });

  it("明确新建任务会清理尚未发送的旧附件", async () => {
    render(<DocumentWorkspace />);
    await screen.findByLabelText("描述任务、文件或资料路径");
    const old = new File(["old"], "旧资料.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("＋ 添加文件"), { target: { files: [old] } });
    expect(screen.getByRole("list", { name: "待上传附件" }).textContent).toContain("旧资料.txt");
    await click("＋ 新建任务");
    expect(screen.queryByRole("list", { name: "待上传附件" })).toBeNull();
  });

  it("任务内持续对话部分失败可重试，只提交一次要求并续传剩余附件", async () => {
    let secondAttempts = 0;
    override = (path, init) => {
      if (path === "/api/product/tasks/task-1/conversation/" && init.method === "POST") return response({ task: task({ version: 3 }), intent: { mode: "deterministic_fallback", model_called: false, requested_outputs: [], detected_paths: [] }, blockers: {} });
      if (path === "/api/product/tasks/task-1/sources/" && init.method === "POST") {
        const file = (init.body as FormData).get("file") as File;
        if (file.name === "补充.csv") return response({ source_id: "one", task: task({ version: 4 }) });
        if (++secondAttempts === 1) return response({ detail: "上传暂时失败" }, 503);
        return response({ source_id: "two", task: task({ version: 5 }) });
      }
      return undefined;
    };
    await openTask();
    fill("继续补充要求或资料路径", "补充实施边界");
    const first = new File(["1"], "补充.csv", { type: "text/csv" });
    const second = new File(["2"], "说明.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("＋ 添加资料"), { target: { files: [first, second] } });
    await click("发送");
    expect(await screen.findByText(/要求已保存，仍有 1 个附件待上传/)).toBeTruthy();
    await click("继续上传剩余资料");
    await waitFor(() => expect((screen.getByLabelText("继续补充要求或资料路径") as HTMLTextAreaElement).value).toBe(""));
    const continuations = writes().filter(item => item.path.endsWith("/conversation/"));
    const uploads = writes().filter(item => item.path.endsWith("/sources/"));
    expect(continuations).toHaveLength(1);
    expect(JSON.parse(continuations[0].init.body as string)).toEqual({ expected_version: 2, message: "补充实施边界" });
    expect(uploads.map(item => ((item.init.body as FormData).get("file") as File).name)).toEqual(["补充.csv", "说明.txt", "说明.txt"]);
    expect((uploads[2].init.body as FormData).get("expected_version")).toBe("4");
  });

  it("任务对话区显示真实状态和资料，P2 成果保持授权阻断", async () => {
    await openTask();
    expect(screen.getByRole("region", { name: "对话任务工作区" }).textContent).toContain("当前阶段：需求录入");
    expect(screen.getByRole("complementary", { name: "任务概览" }).textContent).toContain("真实背景.txt");
    expect(screen.getByText("可行性研究报告").nextSibling?.textContent).toBe("已纳入任务 · P2 授权后生成");
    expect(screen.getByText("汇报 PPT").nextSibling?.textContent).toBe("已纳入任务 · P2 授权后生成");
  });

  it("保存 draft 带版本；409 保留编辑并要求显式重载", async () => {
    await openTask();
    fill("草稿标题", "未保存标题"); fill("草稿输入 JSON", JSON.stringify({ ...current.input, requirements: "未保存需求" }));
    override = (path, init) => path.endsWith("task-1/") && init.method === "PATCH" ? response({ detail: "冲突" }, 409) : undefined;
    await click("保存服务端草稿");
    expect(bodyOfLastWrite()).toMatchObject({ expected_version: 2, title: "未保存标题", input: { requirements: "未保存需求" } });
    expect(screen.getByRole("alert").textContent).toContain("版本冲突");
    expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("未保存标题");
    expect((screen.getByRole("button", { name: "保存服务端草稿" }) as HTMLButtonElement).disabled).toBe(true);
    current = task({ version: 3, title: "服务端改名" }); override = () => undefined;
    await click("重新加载服务端状态（保留编辑）");
    expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("未保存标题");
    await click("保存服务端草稿");
    expect(bodyOfLastWrite()).toMatchObject({ expected_version: 3 });
  });

  it("无有效 JSON 时不发写请求", async () => {
    await openTask(); fill("蓝图 JSON", "不是 JSON");
    await click("保存蓝图");
    expect(screen.getByRole("alert").textContent).toContain("JSON 格式不正确");
    expect(writes()).toHaveLength(0);
  });

  it("上传 FormData 复用统一 CSRF，不设置 JSON Content-Type", async () => {
    await openTask();
    const file = new File(["真实背景"], "背景.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("选择试用资料"), { target: { files: [file] } });
    await click("上传资料");
    const upload = writes().at(-1)!;
    expect(upload.path).toBe("/api/product/tasks/task-1/sources/");
    expect(upload.init.body).toBeInstanceOf(FormData);
    expect((upload.init.body as FormData).get("file")).toBe(file);
    expect((upload.init.body as FormData).get("expected_version")).toBe("2");
    expect(new Headers(upload.init.headers).has("Content-Type")).toBe(false);
    expect(new Headers(upload.init.headers).get("X-CSRFToken")).toBe("test-csrf");
    expect(upload.init.credentials).toBe("same-origin");
  });

  it("按 actions 提交审核人、逐项输入核对和补充判断合同", async () => {
    current = task({ input_issues: [{ issue_hash: "issue-sha", code: "duplicate_row_id", item_index: 2 }], impact: { blueprint: true } });
    await openTask();
    expect(screen.getByText(/事实为来源直接支持/)).toBeTruthy();
    expect(screen.getAllByText(/"blueprint": true/)).toHaveLength(2);

    fill("指定审核人 ID", "23"); fill("指定或改派原因", "业务审核职责调整"); await click("指定或改派审核人");
    expect(writes().at(-1)!.path).toBe("/api/product/tasks/task-1/reviewer/");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2, reviewer_id: 23, reason: "业务审核职责调整" });

    fill("问题 1 分类", "conflict"); fill("问题 1 核对依据", "来源记录与清单冲突");
    fireEvent.click(screen.getAllByLabelText("真实背景.txt（source-1）")[0]);
    await click("提交全部输入问题核对");
    expect(writes().at(-1)!.path).toBe("/api/product/tasks/task-1/input-review/");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2, resolutions: [{ issue_hash: "issue-sha", category: "conflict", reason: "来源记录与清单冲突", source_ids: ["source-1"] }] });

    fill("补充判断分类", "fact"); fill("补充判断说明", "资料明确记录部署地点");
    fireEvent.click(screen.getAllByLabelText("真实背景.txt（source-1）").at(-1)!);
    await click("提交补充判断");
    expect(writes().at(-1)!.path).toBe("/api/product/tasks/task-1/statements/");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2, category: "fact", text: "资料明确记录部署地点", source_ids: ["source-1"] });
  });

  it("排队、蓝图保存、章节人工修改、取消重试使用版本合同", async () => {
    await openTask();
    for (const [name, action] of [["发起授权检索（默认未授权，可能被拒）", "retrieve"], ["生成蓝图", "blueprint"], ["生成正文", "write"], ["生成 Word 草稿", "render"], ["生成正式候选并后台 Office 渲染（非发布）", "candidate"]]) {
      await click(name);
      expect(writes().at(-1)!.path).toBe("/api/product/tasks/task-1/queue/");
      expect(bodyOfLastWrite()).toEqual({ expected_version: 2, action });
    }
    expect(screen.getByText(/候选生成或渲染成功也不表示已经批准或发布/)).toBeTruthy();
    const editedBlueprint = { ...current.blueprint!.payload, purpose: "人工更正目标" };
    fill("蓝图 JSON", JSON.stringify(editedBlueprint)); await click("保存蓝图");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2, payload: editedBlueprint });
    expect(writes().at(-1)!.init.method).toBe("PATCH");
    await click("编辑 建设目标（版本 1）");
    const editedChapter = { ...current.chapters[0].payload, paragraphs: ["人工更正正文"] };
    fill("章节 JSON", JSON.stringify(editedChapter)); await click("保存人工章节");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2, ...editedChapter });
    expect(writes().at(-1)!.path).toContain("/chapters/");
    await click("取消任务"); expect(writes().at(-1)!.path).toContain("/cancel/");
    await click("重试任务"); expect(writes().at(-1)!.path).toContain("/retry/");
    expect(bodyOfLastWrite()).toEqual({ expected_version: 2 });
  });

  it("正式决策绑定服务端 ID/hash，历史下载仅为 session API", async () => {
    await openTask(); fill("审核意见", "人工核对意见");
    fill("蓝图 JSON", JSON.stringify({ ...current.blueprint!.payload, purpose: "尚未保存的目的" }));
    for (const [name, decision] of [["批准服务端蓝图", "approve"], ["退回蓝图", "revise"]]) {
      await click(name);
      expect(bodyOfLastWrite()).toEqual({ expected_version: 2, target: "blueprint", target_id: "blueprint-1", sha256: "blueprint-sha", decision, comment: "人工核对意见" });
    }
    fill("选择审核文档版本", "artifact-1"); await click("批准所选文档");
    expect(bodyOfLastWrite()).toMatchObject({ target: "artifact", target_id: "artifact-1", sha256: "old-sha", decision: "approve" });
    await click("退回所选文档"); expect(bodyOfLastWrite()).toMatchObject({ decision: "revise" });
    expect(screen.getByRole("link", { name: "下载版本 1" }).getAttribute("href")).toBe("/api/product/artifacts/artifact-1/download/");
    expect(screen.getByRole("link", { name: "下载版本 2" }).getAttribute("href")).toBe("/api/product/artifacts/artifact-2/download/");
  });

  it("正式候选必须逐页手动核对后提交核验，且不替代批准", async () => {
    await openTask(); fill("选择审核文档版本", "artifact-2");
    const verifyButton = screen.getByRole("button", { name: "提交正式候选核验" }) as HTMLButtonElement;
    expect(verifyButton.disabled).toBe(true);
    expect((screen.getByLabelText("第 1 页已人工核对并通过") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole("link", { name: "鉴权预览第 1 页" }).getAttribute("href")).toBe("/api/product/artifacts/artifact-2/preview/?page=1");
    expect(screen.getByAltText("正式候选第 2 页预览").getAttribute("src")).toBe("/api/product/artifacts/artifact-2/preview/?page=2");

    for (const page of [1, 2]) {
      fireEvent.click(screen.getByLabelText(`第 ${page} 页已人工核对并通过`));
      fill(`第 ${page} 页核验意见`, `已逐项核对第 ${page} 页`);
    }
    for (const label of ["事实已核对", "数量已核对", "术语已核对", "来源已核对", "完整性已核对", "条件已核对"]) fireEvent.click(screen.getByLabelText(label));
    expect(verifyButton.disabled).toBe(true);
    fill("审核意见", "逐页与内容核对依据");
    expect(verifyButton.disabled).toBe(false);
    await click("提交正式候选核验");
    expect(writes().at(-1)!.path).toBe("/api/product/artifacts/artifact-2/verification/");
    expect(bodyOfLastWrite()).toEqual({
      expected_version: 2, sha256: "new-sha",
      pages: [{ page: 1, sha256: "page-1-sha", passed: true, comment: "已逐项核对第 1 页" }, { page: 2, sha256: "page-2-sha", passed: true, comment: "已逐项核对第 2 页" }],
      content_checks: { facts: true, quantities: true, terms: true, sources: true, completeness: true, conditions: true },
      comment: "逐页与内容核对依据",
    });

    fill("审核意见", "独立批准意见"); await click("批准所选文档");
    expect(writes().at(-1)!.path).toBe("/api/product/tasks/task-1/decisions/");
    expect(bodyOfLastWrite()).toMatchObject({ target_id: "artifact-2", decision: "approve", comment: "独立批准意见" });
  });

  it("没有后端 actions 不显示可用业务操作", async () => {
    current = task({ actions: [] }); await openTask();
    fill("选择审核文档版本", "artifact-2");
    for (const name of ["保存服务端草稿", "指定或改派审核人", "提交补充判断", "发起授权检索（默认未授权，可能被拒）", "生成蓝图", "批准服务端蓝图", "退回蓝图", "生成正文", "生成 Word 草稿", "生成正式候选并后台 Office 渲染（非发布）", "提交正式候选核验", "取消任务", "重试任务"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
    expect((screen.getByLabelText("第 1 页已人工核对并通过") as HTMLInputElement).disabled).toBe(true);
    expect(writes()).toHaveLength(0);
  });

  it("活动任务每 3 秒刷新，停止后不轮询，且不覆盖编辑", async () => {
    vi.useFakeTimers(); current = task({ state: "QUEUED" });
    await act(async () => { render(<DocumentWorkspace />); });
    await act(async () => { fill("选择任务", "task-1"); });
    fill("草稿标题", "本地草稿"); fill("蓝图 JSON", "本地未保存蓝图");
    const details = () => requests.filter(request => request.path === "/api/product/tasks/task-1/").length;
    expect(details()).toBe(1);
    current = task({ state: "RUNNING", version: 3, title: "服务端标题" });
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); }); expect(details()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); }); expect(details()).toBe(2);
    expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("本地草稿");
    expect((screen.getByLabelText("蓝图 JSON") as HTMLTextAreaElement).value).toBe("本地未保存蓝图");
    current = task({ state: "COMPLETED", version: 4 });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); }); expect(details()).toBe(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); }); expect(details()).toBe(3);
  });

  it("切换任务时 abort，忽略跨任务迟到的 detail", async () => {
    let finish: (value: Response) => void = () => {};
    override = path => path === "/api/product/tasks/task-1/" ? new Promise(resolve => { finish = resolve; }) : undefined;
    render(<DocumentWorkspace />); await screen.findByRole("option", { name: /真实项目/ });
    await act(async () => { fill("选择任务", "task-1"); });
    const firstSignal = requests.at(-1)!.init.signal;
    await act(async () => { fill("选择任务", "task-2"); });
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => { finish(response(task())); });
    expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("第二任务");
    expect((screen.getByLabelText("草稿输入 JSON") as HTMLTextAreaElement).value).toContain("第二任务项目");
  });

  it("轮询失败后仍可刷新，切换任务会中止在途轮询", async () => {
    vi.useFakeTimers(); current = task({ state: "running" });
    await act(async () => { render(<DocumentWorkspace />); });
    await act(async () => { fill("选择任务", "task-1"); });
    override = path => path === "/api/product/tasks/task-1/" ? response({ detail: "暂时不可用" }, 503) : undefined;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByRole("alert").textContent).toContain("缺少业务授权或服务授权");
    let finish: (value: Response) => void = () => {};
    override = path => path === "/api/product/tasks/task-1/" ? new Promise(resolve => { finish = resolve; }) : undefined;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    const signal = requests.at(-1)!.init.signal;
    await act(async () => { fill("选择任务", "task-2"); });
    expect(signal?.aborted).toBe(true);
    await act(async () => { finish(response(task({ title: "迟到的轮询标题" }))); });
    expect((screen.getByLabelText("草稿标题") as HTMLInputElement).value).toBe("第二任务");
    const count = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(requests).toHaveLength(count);
  });

  it("非活动草稿不轮询；卸载活动任务清理定时器", async () => {
    vi.useFakeTimers();
    await act(async () => { render(<DocumentWorkspace />); });
    await act(async () => { fill("选择任务", "task-1"); });
    let count = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(requests).toHaveLength(count);
    current = task({ state: "queued" });
    await click("重新加载服务端状态（保留编辑）");
    count = requests.length; cleanup();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(requests).toHaveLength(count);
  });

  it("卸载清理轮询和在途写操作，迟到结果不再触发详情读取", async () => {
    await openTask();
    let finish: (value: Response) => void = () => {};
    override = (path, init) => path.endsWith("/queue/") && init.method === "POST" ? new Promise(resolve => { finish = resolve; }) : undefined;
    await click("生成蓝图");
    const pendingSignal = writes().at(-1)!.init.signal;
    const count = requests.length;
    cleanup(); expect(pendingSignal?.aborted).toBe(true);
    await act(async () => { finish(response(current)); });
    expect(requests).toHaveLength(count);
  });

  it("503 明确提示授权阻塞，401 交给统一 session 处理", async () => {
    override = path => path === "/api/product/tasks/" ? response({ detail: "产品服务未授权" }, 503) : undefined;
    render(<DocumentWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toContain("缺少业务授权或服务授权");
    const listener = vi.fn(); window.addEventListener(unauthorizedEvent, listener);
    override = path => path === "/api/product/tasks/" ? response({ detail: "请登录" }, 401) : undefined;
    await click("刷新任务列表");
    await waitFor(() => expect(listener).toHaveBeenCalledOnce());
    window.removeEventListener(unauthorizedEvent, listener);
  });

  it("显示后端阻断原因并禁用对应动作", async () => {
    current = task({ blockers: {
      queue_retrieve: { code: "retrieval_authorization_required", detail: "D-01/D-08 尚未批准真实资料检索。" },
      queue_blueprint: { code: "model_authorization_required", detail: "D-01 尚未批准真实模型调用。" },
    } });
    await openTask();
    expect(screen.getByRole("region", { name: "当前操作阻断" }).textContent).toContain("D-01/D-08");
    expect((screen.getByRole("button", { name: "发起授权检索（默认未授权，可能被拒）" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "生成蓝图" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
