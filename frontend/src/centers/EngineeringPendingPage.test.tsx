import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "../api";
import EngineeringPendingPage from "./EngineeringPendingPage";

vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), apiRequest: vi.fn() }));
const request = vi.mocked(apiRequest);
const root = "/api/engineering/jobs/";
const knowledgeRoot = "/api/engineering/knowledge/";
const quotaRoot = "/api/engineering/quota/";
const capabilities = { cost: { status: "ready", detail: "内部草稿服务可用" }, ragflow: { status: "locked", detail: "工程 RAGFlow 权限尚未解锁/未接入。" } };
const completed = {
  id: "11111111-1111-4111-8111-111111111111", status: "completed", region: "榆林",
  files: [{ name: "电力电缆清单.xlsx", size: 100, sha256: "a".repeat(64) }],
  inspection: { ok: true, files: [{ name: "one.xlsx", passed: true, issues: [] }] },
  result: { download_available: true, summary: { online_allowed: false, files: [{ status: "completed", validation_passed: true, preflight_issues: [], validation_issues: [], source_health: { pricing: "healthy" }, pending_confirmations: [] }] } }, error: null,
};

beforeEach(() => {
  vi.resetAllMocks();
  request.mockImplementation(async (path) => path === knowledgeRoot
    ? { status: "locked", detail: "尚未授权" } : { jobs: [], capabilities });
});

describe("EngineeringPendingPage", () => {
  it.each([
    ["network failure", new Error("网络中断")],
    ["service failure", new ApiError(503, "工程服务暂不可用")],
    ["invalid response", null],
  ])("does not describe an initial %s as an empty job list, and refresh can recover", async (_, failure) => {
    let failed = true;
    let completeRefresh: (() => void) | undefined;
    request.mockImplementation(async (path) => {
      if (path === knowledgeRoot) return { status: "locked", detail: "尚未授权" };
      if (failed) {
        if (failure) throw failure;
        return { jobs: "invalid" };
      }
      await new Promise<void>(resolve => { completeRefresh = resolve; });
      return { jobs: [], capabilities };
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("任务列表读取失败"));
    expect(screen.queryByText("暂无服务端任务。")).toBeNull();
    expect(screen.queryByText("正在读取任务列表…")).toBeNull();

    failed = false;
    await user.click(screen.getByRole("button", { name: "刷新任务状态" }));
    expect(screen.queryByText("暂无服务端任务。")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("任务列表读取失败");
    await act(async () => { completeRefresh!(); });
    expect(await screen.findByText("暂无服务端任务。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks retained job records as stale when polling fails and clears the warning on recovery", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let failed = false;
      request.mockImplementation(async (path) => {
        if (path === knowledgeRoot) return { status: "locked", detail: "等待解析" };
        if (failed) throw new Error("任务同步中断");
        return { jobs: [completed], capabilities };
      });
      render(<EngineeringPendingPage section="estimate" />);
      expect(await screen.findByText("电力电缆清单 · 成本测算")).toBeTruthy();
      failed = true;
      await act(async () => { vi.advanceTimersByTime(5000); });
      expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("上次成功读取的记录"));
      expect(screen.getByText("电力电缆清单 · 成本测算")).toBeTruthy();
      expect(screen.queryByText("暂无服务端任务。")).toBeNull();

      failed = false;
      await act(async () => { vi.advanceTimersByTime(5000); });
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });

  it("limits uploads to one or two XLSX files", async () => {
    render(<EngineeringPendingPage section="estimate" />);
    await screen.findByText("暂无服务端任务。");
    const input = screen.getByLabelText("清单文件");
    fireEvent.change(input, { target: { files: [new File([""], "a.xlsx"), new File([""], "b.xlsx"), new File([""], "c.xlsx")] } });
    expect(screen.getByRole("alert").textContent).toContain("仅支持 1 至 2 个 .xlsx");
    expect((screen.getByRole("button", { name: "创建内部成本草稿" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { files: [new File([""], "a.csv")] } });
    expect(request.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("posts repeated files then reads nested detail and exposes only the authorized download route", async () => {
    request.mockImplementation(async (path, init) => {
      if (path === root && init?.method === "POST") return { job: { ...completed, status: "queued", result: { ...completed.result, download_available: false } }, capabilities };
      if (path === `${root}${completed.id}/`) return { job: completed, capabilities };
      if (path === root) return { jobs: [], capabilities };
      throw new Error(`Unexpected request ${path}`);
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    await screen.findByText("暂无服务端任务。");
    fireEvent.change(screen.getByLabelText("清单文件"), { target: { files: [new File(["a"], "one.xlsx"), new File(["b"], "two.xlsx")] } });
    await user.type(screen.getByLabelText("地区（可选）"), "榆林");
    await user.click(screen.getByRole("button", { name: "创建内部成本草稿" }));
    await screen.findByText("当前状态：已完成 · 地区：榆林");
    const post = request.mock.calls.find(([path, init]) => path === root && init?.method === "POST");
    const body = post?.[1]?.body as FormData;
    expect(Array.from(body.getAll("files"), (file) => (file as File).name)).toEqual(["one.xlsx", "two.xlsx"]);
    expect(body.get("region")).toBe("榆林");
    expect(screen.getByText("原始 source_health：{\"pricing\":\"healthy\"}")).toBeTruthy();
    const download = screen.getByRole("link", { name: "下载内部成本草稿" });
    expect(download.getAttribute("href")).toBe(`${root}${completed.id}/download/`);
    expect(download.hasAttribute("download")).toBe(true);
  });

  it("submits without a region so the service default applies, and never sends an empty region", async () => {
    // 后端 region 有默认值「陕西」，为可选字段。此前前端把地区设为必填并禁用按钮，
    // 导致不填地区的用户完全无法上传。留空时必须仍可提交，且不发送空 region 键。
    request.mockImplementation(async (path, init) => {
      if (path === root && init?.method === "POST") return { job: { ...completed, status: "queued" }, capabilities };
      if (path === `${root}${completed.id}/`) return { job: completed, capabilities };
      if (path === root) return { jobs: [], capabilities };
      throw new Error(`Unexpected request ${path}`);
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    await screen.findByText("暂无服务端任务。");
    fireEvent.change(screen.getByLabelText("清单文件"), { target: { files: [new File(["a"], "one.xlsx")] } });
    const submit = screen.getByRole("button", { name: "创建内部成本草稿" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    await user.click(submit);
    const post = request.mock.calls.find(([path, init]) => path === root && init?.method === "POST");
    expect(post).toBeTruthy();
    const body = post?.[1]?.body as FormData;
    expect(body.has("region")).toBe(false);
  });

  it("uses backend inspection, result summary, error and capability statuses for review", async () => {
    const failed = {
      id: "22222222-2222-4222-8222-222222222222", status: "failed", region: "榆林",
      files: [{ name: "清单.xlsx", size: 10, sha256: "c".repeat(64) }],
      inspection: { ok: false, files: [{ name: "清单.xlsx", passed: false, issues: ["缺少设备数量"] }] },
      result: { download_available: false, summary: { files: [{ status: "failed", validation_passed: false, preflight_issues: ["单位不完整"], validation_issues: ["数量无效"], source_health: { price_source: "unavailable" }, pending_confirmations: ["人工核对价格来源"] }] } },
      error: { code: "preflight_failed", detail: "工程清单预检未通过，未执行测算。", retryable: false },
    };
    request.mockImplementation(async (path) => path === knowledgeRoot ? { status: "locked", detail: "尚未授权" } : path === root ? { jobs: [failed], capabilities } : { job: failed, capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="overview" />);
    expect(await screen.findByText(/知识库状态：待解锁 · 尚未授权/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^清单 · 成本测算/ }));
    await screen.findByText("任务错误：preflight_failed · 工程清单预检未通过，未执行测算。");
    expect(screen.getAllByText("待复核")).toHaveLength(2);
    expect(screen.getByText("缺少设备数量")).toBeTruthy();
    expect(screen.getByText("单位不完整")).toBeTruthy();
    expect(screen.getByText("数量无效")).toBeTruthy();
    expect(screen.getByText("人工核对价格来源")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "下载内部成本草稿" })).toBeNull();
  });

  it("marks suspicious source audits for review and displays their health statistics", async () => {
    const suspicious = {
      ...completed, id: "33333333-3333-4333-8333-333333333333",
      result: { download_available: false, summary: { files: [{ status: "completed", validation_passed: true, preflight_issues: [], validation_issues: [], source_health: { "结论": "可疑", "警告": ["报价来源待核验"], "零价项": 2, "来源分布": { "历史清单": 3, "询价": 0 } }, pending_confirmations: [] }] } },
    };
    request.mockImplementation(async (path) => path === root ? { jobs: [suspicious], capabilities } : { job: suspicious, capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="overview" />);
    await user.click(await screen.findByRole("button", { name: /^电力电缆清单 · 成本测算/ }));
    expect(screen.getAllByText("待复核")).toHaveLength(2);
    expect(screen.getByText("审计结论：可疑")).toBeTruthy();
    expect(screen.getByText("报价来源待核验")).toBeTruthy();
    expect(screen.getByText("零价项：2")).toBeTruthy();
    expect(screen.getByText("来源分布：历史清单：3；询价：0")).toBeTruthy();
  });

  it("keeps the knowledge base unavailable and quota unimplemented", async () => {
    const view = render(<EngineeringPendingPage section="estimate" />);
    await screen.findByText(/知识库状态：待解锁 · 尚未授权/);
    expect(screen.getByText(/上传清单并预检通过、工程文档解析完成后，知识库才可能解锁；定额推荐和正式报价仍未开放/)).toBeTruthy();
    view.rerender(<EngineeringPendingPage section="quota" />);
    expect(screen.getByText("未实现")).toBeTruthy();
    expect(screen.getByText(/不导入、不推荐，也不宣称任何定额已审定/)).toBeTruthy();
    expect(screen.getByText(/套用定额 D-05 尚未实现/)).toBeTruthy();
  });

  it("locks retrieval and preserves D-05 on the quota route", async () => {
    render(<EngineeringPendingPage section="quota" />);
    expect(await screen.findByText(/知识库状态：待解锁 · 尚未授权/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "检索资料" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/套用定额 D-05 尚未实现/)).toBeTruthy();
    expect(request.mock.calls.some(([path]) => path === root)).toBe(false);
  });

  it("manually queries bounded internal unapproved quota candidates without applying or claiming prices", async () => {
    const candidate = { code: "A-01", major: "安防", name: "防火墙设备", unit: "台", score: 0.98, source: "历史内部清单", unit_compatible: true };
    request.mockImplementation(async (path) => path === `${quotaRoot}?${new URLSearchParams({ name: "防火墙设备", unit: "台" })}`
      ? { status: "candidates", classification: "internal_unapproved", candidates: [candidate] }
      : path === knowledgeRoot ? { status: "locked", detail: "尚未授权" } : { jobs: [], capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    const name = screen.getByLabelText("名称") as HTMLInputElement;
    const unit = screen.getByLabelText("单位") as HTMLInputElement;
    expect(name.maxLength).toBe(100);
    expect(unit.maxLength).toBe(20);
    await user.type(name, "  防火墙设备  ");
    await user.type(unit, " 台 ");
    await user.click(screen.getByRole("button", { name: "查询内部候选" }));
    expect(await screen.findByText("A-01 · 防火墙设备")).toBeTruthy();
    expect(screen.getByText("专业：安防 · 单位：台 · 匹配分：0.98")).toBeTruthy();
    expect(screen.getByText("原始来源：历史内部清单")).toBeTruthy();
    expect(screen.getByText("单位兼容：兼容")).toBeTruthy();
    expect(request.mock.calls.some(([path]) => path === `${quotaRoot}?${new URLSearchParams({ name: "防火墙设备", unit: "台" })}`)).toBe(true);
    expect(screen.queryByRole("button", { name: /套用|应用|生成价格/ })).toBeNull();
  });

  it("shows an empty internal unapproved quota candidate search", async () => {
    request.mockImplementation(async (path) => path.startsWith(quotaRoot)
      ? { status: "candidates", classification: "internal_unapproved", candidates: [] }
      : path === knowledgeRoot ? { status: "locked", detail: "尚未授权" } : { jobs: [], capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    await user.type(screen.getByLabelText("名称"), "门禁");
    await user.type(screen.getByLabelText("单位"), "套");
    await user.click(screen.getByRole("button", { name: "查询内部候选" }));
    expect(await screen.findByText("未找到符合条件的内部未审批定额候选。")).toBeTruthy();
  });

  it("shows quota candidate search errors without displaying untrusted results", async () => {
    request.mockImplementation(async (path) => {
      if (path.startsWith(quotaRoot)) throw new Error("候选服务暂不可用");
      return path === knowledgeRoot ? { status: "locked", detail: "尚未授权" } : { jobs: [], capabilities };
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    await user.type(screen.getByLabelText("名称"), "摄像机");
    await user.type(screen.getByLabelText("单位"), "台");
    await user.click(screen.getByRole("button", { name: "查询内部候选" }));
    expect(await screen.findByText("候选查询失败：候选服务暂不可用")).toBeTruthy();
    expect(screen.queryByLabelText("内部未审批定额候选结果")).toBeNull();
  });

  it("polls the job list so a task completed by the worker shows up without a manual reload", async () => {
    // 任务由后台 Worker 异步处理：此前页面只在进入时拉取一次，
    // 导致服务端已完成、页面仍停在「排队中/已阻止」，必须手动刷新浏览器。
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let status = "queued";
      request.mockImplementation(async (path) => path === knowledgeRoot
        ? { status: "locked", detail: "等待解析" }
        : { jobs: [{ ...completed, status, result: { ...completed.result, download_available: status === "completed" } }], capabilities });
      render(<EngineeringPendingPage section="estimate" />);
      expect(await screen.findByText(/排队中/)).toBeTruthy();

      status = "completed";
      await act(async () => { vi.advanceTimersByTime(5000); });

      expect(await screen.findByText("已完成")).toBeTruthy();
      expect(screen.getByText(/电力电缆清单 · 成本测算/)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("manual refresh updates the open task detail instead of leaving it stale", async () => {
    let status = "queued";
    const record = () => ({ ...completed, status, region: "榆林", error: null,
      result: { ...completed.result, download_available: status === "completed" } });
    request.mockImplementation(async (path) => {
      if (path === knowledgeRoot) return { status: "locked", detail: "等待解析" };
      if (path === root) return { jobs: [record()], capabilities };
      if (path === `${root}${completed.id}/`) return { job: record(), capabilities };
      throw new Error(`Unexpected request ${path}`);
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    await user.click(await screen.findByRole("button", { name: /排队中/ }));
    expect(await screen.findByText(/当前状态：排队中/)).toBeTruthy();

    status = "completed";
    await user.click(screen.getByRole("button", { name: "刷新任务状态" }));

    expect(await screen.findByText(/当前状态：已完成 · 地区：榆林/)).toBeTruthy();
    expect(await screen.findByRole("link", { name: "下载内部成本草稿" })).toBeTruthy();
  });

  it("labels each job with the uploaded list name and 成本测算 instead of a bare id", async () => {
    // 用户此前只能看到 UUID，无法辨认是哪份清单。
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "locked", detail: "等待解析" }
      : { jobs: [completed], capabilities });
    render(<EngineeringPendingPage section="estimate" />);
    expect(await screen.findByText("电力电缆清单 · 成本测算")).toBeTruthy();
    // UUID 不再作为主标识露出，仅保留在按钮 title 中供排查。
    expect(screen.queryByText(new RegExp(completed.id))).toBeNull();
  });

  it("summarises multiple uploaded lists in the job label", async () => {
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "locked", detail: "等待解析" }
      : { jobs: [{ ...completed, files: [
          { name: "弱电清单.xlsx", size: 1, sha256: "a".repeat(64) },
          { name: "给排水清单.xlsx", size: 1, sha256: "b".repeat(64) },
        ] }], capabilities });
    render(<EngineeringPendingPage section="estimate" />);
    expect(await screen.findByText("弱电清单 等 2 份清单 · 成本测算")).toBeTruthy();
  });

  it("deletes a finished job after confirmation and drops it from the list", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    request.mockImplementation(async (path, init) => {
      if (path === knowledgeRoot) return { status: "locked", detail: "等待解析" };
      if (path === `${root}${completed.id}/` && init?.method === "DELETE") return undefined;
      if (path === root) return { jobs: [completed], capabilities };
      throw new Error(`Unexpected request ${path}`);
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    await user.click(await screen.findByRole("button", { name: "删除：电力电缆清单 · 成本测算" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("不可恢复"));
    await waitFor(() => expect(screen.queryByText(/电力电缆清单/)).toBeNull());
    const call = request.mock.calls.find(([path, init]) => path === `${root}${completed.id}/` && init?.method === "DELETE");
    expect(call?.[0]).toBe(`${root}${completed.id}/`);
    confirm.mockRestore();
  });

  it("does not delete when the confirmation is dismissed", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "locked", detail: "等待解析" }
      : { jobs: [completed], capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    await user.click(await screen.findByRole("button", { name: "删除：电力电缆清单 · 成本测算" }));
    expect(request.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
    expect(screen.getByText(/电力电缆清单 · 成本测算/)).toBeTruthy();
    confirm.mockRestore();
  });

  it("disables the delete control while a job is still being processed", async () => {
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "locked", detail: "等待解析" }
      : { jobs: [{ ...completed, status: "running", result: { ...completed.result, download_available: false } }], capabilities });
    render(<EngineeringPendingPage section="estimate" />);
    const remove = await screen.findByRole("button", { name: "删除：电力电缆清单 · 成本测算" }) as HTMLButtonElement;
    expect(remove.disabled).toBe(true);
  });

  it("refreshes from locked to ready after the worker completes without reloading jobs", async () => {
    let ready = false;
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? ready ? { status: "ready", detail: "已解析", dataset_document_count: 1 } : { status: "locked", detail: "等待解析", dataset_document_count: 0 }
      : { jobs: [], capabilities });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="estimate" />);
    expect(await screen.findByText(/知识库状态：待解锁 · 等待解析 · 文档数：0/)).toBeTruthy();
    expect((screen.getByLabelText("检索问题") as HTMLInputElement).disabled).toBe(true);
    ready = true;
    await user.click(screen.getByRole("button", { name: "刷新知识库状态" }));
    expect(await screen.findByText(/知识库状态：已就绪 · 已解析 · 文档数：1/)).toBeTruthy();
    expect((screen.getByLabelText("检索问题") as HTMLInputElement).disabled).toBe(false);
    expect(request.mock.calls.filter(([path]) => path === knowledgeRoot)).toHaveLength(2);
    expect(request.mock.calls.filter(([path]) => path === root)).toHaveLength(1);
  });

  it("fails closed when refreshing a previously ready status fails", async () => {
    let failed = false;
    request.mockImplementation(async (path) => {
      if (path !== knowledgeRoot) return { jobs: [], capabilities };
      if (failed) throw new Error("状态暂不可用");
      return { status: "ready", detail: "已解析", dataset_document_count: 1 };
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    await screen.findByText(/知识库状态：已就绪 · 已解析/);
    failed = true;
    await user.click(screen.getByRole("button", { name: "刷新知识库状态" }));
    expect(await screen.findByText(/知识库状态读取失败：状态暂不可用/)).toBeTruthy();
    expect(screen.queryByText(/知识库状态：已就绪/)).toBeNull();
    expect((screen.getByLabelText("检索问题") as HTMLInputElement).disabled).toBe(true);
  });

  it("shows an empty ready dataset without implying retrieval or quota is available", async () => {
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "ready", detail: "已连接", dataset_document_count: 0 } : { jobs: [], capabilities });
    render(<EngineeringPendingPage section="quota" />);
    expect(await screen.findByText(/知识库状态：已就绪 · 已连接 · 文档数：0/)).toBeTruthy();
    expect(screen.getByText("知识库暂无文档，暂不可检索。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "检索资料" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/RAGFlow 工程知识库：未接入/)).toBeNull();
  });

  it("uses the knowledge endpoint rather than static job capabilities on the estimate route", async () => {
    request.mockImplementation(async (path) => path === knowledgeRoot
      ? { status: "ready", detail: "可检索", dataset_document_count: 1 } : { jobs: [], capabilities });
    render(<EngineeringPendingPage section="estimate" />);
    await screen.findByText(/知识库状态：已就绪 · 可检索/);
    await screen.findByText("暂无服务端任务。");
    expect(screen.queryByText(/RAGFlow 工程知识库：未接入/)).toBeNull();
    expect((screen.getByRole("button", { name: "检索资料" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("检索问题") as HTMLInputElement).disabled).toBe(false);
  });

  it("posts only a bounded question and renders structured evidence", async () => {
    request.mockImplementation(async (path, init) => {
      if (path === knowledgeRoot) return { status: "ready", detail: "可检索", dataset_document_count: 2 };
      if (path === `${knowledgeRoot}retrieve/` && init?.method === "POST") return { status: "completed", sources: [{ document_id: "doc-1", title: "工程工法", content: "施工工序说明", chunk_id: "chunk-1" }] };
      return { jobs: [], capabilities };
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    await screen.findByText(/知识库状态：已就绪 · 可检索/);
    await user.type(screen.getByLabelText("检索问题"), "  如何施工？  ");
    await user.click(screen.getByRole("button", { name: "检索资料" }));
    expect(await screen.findByText("施工工序说明")).toBeTruthy();
    expect(screen.getByText("工程工法")).toBeTruthy();
    expect(screen.getByText("文档 ID：doc-1 · 片段 ID：chunk-1")).toBeTruthy();
    const post = request.mock.calls.find(([path]) => path === `${knowledgeRoot}retrieve/`);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ question: "如何施工？" });
    expect(screen.queryByText(/推荐价格|审定价格/)).toBeNull();
  });

  it("fails closed on status errors and locks again on retrieval conflict", async () => {
    request.mockImplementation(async (path) => {
      if (path === knowledgeRoot) throw new Error("网络中断");
      return { jobs: [], capabilities };
    });
    const view = render(<EngineeringPendingPage section="quota" />);
    expect(await screen.findByText(/知识库状态读取失败：网络中断/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "检索资料" }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount();

    request.mockImplementation(async (path) => {
      if (path === knowledgeRoot) return { status: "ready", detail: "可检索", dataset_document_count: 1 };
      throw new ApiError(409, "权限尚未解锁");
    });
    const user = userEvent.setup();
    render(<EngineeringPendingPage section="quota" />);
    await screen.findByText(/知识库状态：已就绪/);
    await user.type(screen.getByLabelText("检索问题"), "施工依据");
    await user.click(screen.getByRole("button", { name: "检索资料" }));
    expect(await screen.findByText(/知识库状态：待解锁 · 权限尚未解锁/)).toBeTruthy();
    expect(screen.getByText(/检索失败：权限尚未解锁/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "检索资料" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
