import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "../api";
import ProductKnowledge from "./ProductKnowledge";
import ProductWorkspace from "../centers/ProductWorkspace";

vi.mock("../api", async original => ({ ...await original<typeof import("../api")>(), apiRequest: vi.fn() }));
const request = vi.mocked(apiRequest);
const root = "/api/product/knowledge/";
const a = "11111111-1111-4111-8111-111111111111";
const b = "22222222-2222-4222-8222-222222222222";
const summary = { id: a, title: "设备资料", version: 1 };
const other = { id: b, title: "另一会话", version: 1 };
const source = { id: "s1", dataset_id: "ds1", document_id: "doc1", title: "设备说明", content: "<script>原文不会执行</script>" };
const turn = { question: "历史问题", answer: "有依据的回答 [1]", sources: [source], outcome: "answered", request_id: "old-request" };
const detail = { ...summary, turns: [turn] };
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function mockApi() {
  request.mockImplementation(async (path, init) => {
    if (path === `${root}status/`) return { available: true, code: "ready", help: "" };
    if (path === `${root}conversations/`) return init?.method === "POST" ? { id: b, title: "新会话", version: 0, turns: [] } : { conversations: [summary, other] };
    if (path === `${root}conversations/${a}/`) return detail;
    if (path === `${root}conversations/${b}/`) return { ...other, turns: [] };
    throw new Error(`Unexpected path ${path}`);
  });
}
async function open() {
  const user = userEvent.setup(); render(<ProductKnowledge/>);
  await user.click(await screen.findByRole("button", { name: /设备资料/ }));
  await screen.findByText(turn.answer);
  return user;
}
function posts() { return request.mock.calls.filter(([, init]) => init?.method === "POST"); }
beforeEach(() => { vi.resetAllMocks(); window.history.replaceState({}, "", "/centers/product/knowledge"); mockApi(); });

describe("ProductKnowledge", () => {
  it("mounts the dedicated workspace branch and renders cited excerpts as text", async () => {
    const user = userEvent.setup(); const view = render(<ProductWorkspace section="knowledge"/>);
    await user.click(await screen.findByRole("button", { name: /设备资料/ }));
    await screen.findByText(turn.answer);
    await user.click(screen.getByText("[s1] 设备说明"));
    expect(screen.getByText(source.content)).toBeTruthy();
    expect(screen.getByText(/来源 s1 · 文档 doc1 · 数据集 ds1/)).toBeTruthy();
    expect(view.container.querySelector("script")).toBeNull();
    expect(request.mock.calls[0][0]).toBe(`${root}status/`);
  });
  it("uses the backend citation ID for an arbitrary source subset", async () => {
    request.mockResolvedValueOnce({ available: true }).mockResolvedValueOnce({ conversations: [summary] }).mockResolvedValueOnce({ available: true }).mockResolvedValueOnce({ ...summary, turns: [{ ...turn, answer: "参见 [S3]", sources: [{ ...source, id: "S3" }] }] });
    const user = userEvent.setup(); render(<ProductKnowledge/>);
    await user.click(await screen.findByRole("button", { name: /设备资料/ }));
    await screen.findByText("参见 [S3]");
    const citation = screen.getByText("[S3] 设备说明");
    expect(citation.tagName).toBe("SUMMARY");
    expect(screen.queryByText("[1] 设备说明")).toBeNull();
    await user.click(citation); expect(screen.getByText(source.content)).toBeTruthy();
  });
  it("caps question input and pasted content at 2000 characters", async () => {
    const user = await open(); const input = screen.getByLabelText("你的问题") as HTMLTextAreaElement;
    expect(input.maxLength).toBe(2000);
    await user.click(input); await user.paste("问".repeat(2001));
    expect(input.value).toBe("问".repeat(2000));
    await user.type(input, "多"); expect(input.value).toHaveLength(2000);
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0][1]?.body as string).question).toHaveLength(2000);
  });
  it("makes a rejected question editable after 400 and assigns a new ID on resubmission", async () => {
    const user = await open(); const input = screen.getByLabelText("你的问题") as HTMLTextAreaElement;
    await user.type(input, "无效问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(400, "请修改问题", "invalid_question"));
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText(/请修改问题/);
    expect(input.disabled).toBe(false); expect(input.value).toBe("无效问题");
    expect(screen.queryByRole("button", { name: "重试本次问题" })).toBeNull();
    expect(screen.queryByText(/待重试/)).toBeNull(); expect(screen.getByText(turn.answer)).toBeTruthy();
    const first = JSON.parse(posts()[0][1]?.body as string);
    await user.clear(input); await user.type(input, "修改后的问题");
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    const second = JSON.parse(posts()[1][1]?.body as string);
    expect(second.question).toBe("修改后的问题"); expect(second.version).toBe(first.version);
    expect(second.request_id).not.toBe(first.request_id);
  });
  it("preserves the exact request payload when retrying a network failure", async () => {
    const user = await open(); const input = screen.getByLabelText("你的问题") as HTMLTextAreaElement;
    await user.type(input, "网络问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("Failed to fetch"); expect(input.disabled).toBe(true);
    const body = posts()[0][1]?.body;
    await user.click(screen.getByRole("button", { name: "重试本次问题" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1][1]?.body).toBe(body);
  });
  it("does not read business data in the preview branch", () => {
    window.history.replaceState({}, "", "/preview/centers/product/knowledge");
    render(<ProductWorkspace section="knowledge"/>);
    expect(request).not.toHaveBeenCalled();
  });
  it("blocks all conversation calls when unavailable", async () => {
    request.mockResolvedValue({ available: false, code: "not_configured", help: "请配置知识库" });
    render(<ProductKnowledge/>); await screen.findByText("请配置知识库");
    expect(request).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "新建会话" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发送问题" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("creates an empty session using POST {} and clears old answers", async () => {
    const user = await open(); await user.click(screen.getByRole("button", { name: "新建会话" }));
    await screen.findByText("这个会话还没有问题");
    expect(screen.queryByText(turn.answer)).toBeNull();
    expect(posts()[0]).toEqual([`${root}conversations/`, expect.objectContaining({ body: "{}" })]);
  });
  it("supports newline, Enter, IME, pending feedback and a stable retry payload", async () => {
    const user = await open(); const pending = deferred<unknown>();
    const input = screen.getByLabelText("你的问题");
    await user.type(input, "第一行{Shift>}{Enter}{/Shift}第二行");
    expect((input as HTMLTextAreaElement).value).toBe("第一行\n第二行");
    expect(posts()).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(posts()).toHaveLength(0);
    request.mockImplementationOnce(async () => ({ available: true, code: "ready", help: "" })).mockImplementationOnce(() => pending.promise);
    await user.keyboard("{Enter}");
    await screen.findByText("正在检索资料并生成回答…");
    await waitFor(() => expect(posts()).toHaveLength(1));
    fireEvent.submit(input.closest("form")!); expect(posts()).toHaveLength(1);
    await act(async () => { pending.reject(new ApiError(502, "检索失败", "retrieval_failed")); });
    const originalBody = posts()[0][1]?.body as string;
    expect(JSON.parse(originalBody)).toMatchObject({ question: "第一行\n第二行", version: 1, request_id: expect.stringMatching(/^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i) });
    await user.click(screen.getByRole("button", { name: "重试本次问题" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1][1]?.body).toBe(originalBody);
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });
  it("renders retrieval-empty as empty rather than an invented answer", async () => {
    request.mockImplementationOnce(async () => ({ available: true })).mockResolvedValueOnce({ conversations: [summary] }).mockResolvedValueOnce({ available: true }).mockResolvedValueOnce({ ...summary, turns: [{ ...turn, outcome: "empty", answer: "ignored", sources: [] }] });
    const user = userEvent.setup(); render(<ProductKnowledge/>);
    await user.click(await screen.findByRole("button", { name: /设备资料/ }));
    await screen.findByText(/未找到足够的相关资料/); expect(screen.queryByText("ignored")).toBeNull();
  });
  it("clears old answers and citations after a 403, disabling further calls", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "新问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(403, "授权已撤销", "scope_revoked"));
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText(/访问权限已变化/);
    expect(screen.queryByText(turn.answer)).toBeNull(); expect(screen.queryByText(source.content)).toBeNull();
    expect(screen.queryByRole("button", { name: /设备资料/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试本次问题" })).toBeNull();
  });
  it("reloads history after 409 before allowing a new request with a new version and ID", async () => {
    const user = await open(); const reload = deferred<unknown>();
    await user.type(screen.getByLabelText("你的问题"), "并发问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(409, "版本冲突")).mockImplementationOnce(() => reload.promise);
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("会话已变化，正在重新读取历史…");
    expect(screen.queryByText(turn.answer)).toBeNull();
    expect((screen.getByRole("button", { name: "发送问题" }) as HTMLButtonElement).disabled).toBe(true);
    expect(request.mock.calls.at(-1)?.[0]).toBe(`${root}conversations/${a}/`);
    await act(async () => { reload.resolve({ ...detail, version: 3 }); });
    await screen.findByText(/历史已更新，请核对后重新发送/);
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    const first = JSON.parse(posts()[0][1]?.body as string); const second = JSON.parse(posts()[1][1]?.body as string);
    expect(second.version).toBe(3); expect(second.request_id).not.toBe(first.request_id);
  });
  it("retains the original request ID after 409 when history has not advanced", async () => {
    const user = await open(); const input = screen.getByLabelText("你的问题") as HTMLTextAreaElement;
    await user.type(input, "仍在处理的问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(409, "处理中", "conflict")).mockResolvedValueOnce(detail);
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("对话仍在处理或上次请求尚未确认，请稍后重试本次问题");
    expect(posts()).toHaveLength(1);
    expect(input.disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发送问题" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/这将创建新的请求/)).toBeNull();
    const originalBody = posts()[0][1]?.body;
    const payload = JSON.parse(originalBody as string);
    request.mockResolvedValueOnce({ available: true }).mockResolvedValueOnce({ ...detail, version: 2, turns: [...detail.turns, { ...turn, question: payload.question, request_id: payload.request_id }] });
    await user.click(screen.getByRole("button", { name: "重试本次问题" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1][1]?.body).toBe(originalBody);
    await waitFor(() => expect(input.value).toBe(""));
    expect(input.disabled).toBe(false);
    expect(screen.queryByRole("button", { name: "重试本次问题" })).toBeNull();
  });
  it("advises a new conversation on history_limit without automatically retrying", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "超过轮次的问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(409, "此对话已达40轮", "history_limit"));
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("此对话已达到历史上限，请新建会话继续提问。");
    expect(screen.getByRole("alert").textContent).toContain("history_limit");
    expect(posts()).toHaveLength(1);
    expect(request.mock.calls.at(-1)?.[1]?.method).toBe("POST");
    expect(screen.queryByRole("button", { name: "重试本次问题" })).toBeNull();
    expect(screen.getByText(turn.answer)).toBeTruthy();
    expect((screen.getByRole("button", { name: "新建会话" }) as HTMLButtonElement).disabled).toBe(false);
  });
  it("keeps submission blocked if conflict history reload fails", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(409, "冲突")).mockRejectedValueOnce(new Error("读取失败"));
    await user.click(screen.getByRole("button", { name: "发送问题" })); await screen.findByText("读取失败");
    expect((screen.getByRole("button", { name: "发送问题" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "重新加载历史" })); await screen.findByText(turn.answer);
  });
  it("ignores late responses after switching and clears session errors", async () => {
    const user = await open(); const pending = deferred<unknown>();
    await user.type(screen.getByLabelText("你的问题"), "慢问题");
    request.mockResolvedValueOnce({ available: true }).mockImplementationOnce(() => pending.promise);
    await user.click(screen.getByRole("button", { name: "发送问题" })); await waitFor(() => expect(posts()).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: /另一会话/ })); await screen.findByText("这个会话还没有问题");
    await act(async () => { pending.resolve(detail); });
    expect(screen.queryByText(turn.answer)).toBeNull();
    expect((posts()[0][1]?.signal as AbortSignal).aborted).toBe(true);
    await user.type(screen.getByLabelText("你的问题"), "失败问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new Error("旧会话错误"));
    await user.click(screen.getByRole("button", { name: "发送问题" })); await screen.findByText("旧会话错误");
    await user.click(screen.getByRole("button", { name: /设备资料/ })); await screen.findByText(turn.answer);
    expect(screen.queryByText("旧会话错误")).toBeNull(); expect(screen.queryByText(/待重试/)).toBeNull();
  });
  it("rechecks availability before POST and does not send when the service turns off", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "问题");
    request.mockResolvedValueOnce({ available: false, code: "offline", help: "服务离线" });
    await user.click(screen.getByRole("button", { name: "发送问题" })); await screen.findByText("服务离线");
    expect(posts()).toHaveLength(0); expect(screen.queryByText(turn.answer)).toBeNull();
  });
  it("clears an error and pending question when starting a new session", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "失败问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new Error("旧错误"));
    await user.click(screen.getByRole("button", { name: "发送问题" })); await screen.findByText("旧错误");
    await user.click(screen.getByRole("button", { name: "新建会话" })); await screen.findByText("这个会话还没有问题");
    expect(screen.queryByText("旧错误")).toBeNull(); expect(screen.queryByText(turn.answer)).toBeNull();
    expect(screen.queryByText(/待重试/)).toBeNull();
    expect((screen.getByLabelText("你的问题") as HTMLTextAreaElement).value).toBe("");
  });
  it("recognizes an already committed request in conflict history without sending it again", async () => {
    const user = await open(); await user.type(screen.getByLabelText("你的问题"), "已处理问题");
    request.mockResolvedValueOnce({ available: true }).mockRejectedValueOnce(new ApiError(409, "冲突")).mockImplementationOnce(async () => {
      const payload = JSON.parse(posts()[0][1]?.body as string);
      return { ...detail, version: 2, turns: [...detail.turns, { ...turn, question: payload.question, request_id: payload.request_id }] };
    });
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("历史已更新，本次问题已处理，请查看回答。");
    expect(posts()).toHaveLength(1);
    expect((screen.getByLabelText("你的问题") as HTMLTextAreaElement).value).toBe("");
  });
  it("clears content immediately on history switch and handles scope revocation on read", async () => {
    const user = await open(); const pending = deferred<unknown>();
    request.mockResolvedValueOnce({ available: true }).mockImplementationOnce(() => pending.promise);
    await user.click(screen.getByRole("button", { name: /另一会话/ }));
    expect(screen.queryByText(turn.answer)).toBeNull(); expect(screen.queryByText(source.content)).toBeNull();
    await act(async () => { pending.reject(new ApiError(403, "范围已撤销", "scope_revoked")); });
    await screen.findByText(/访问权限已变化/);
    expect((screen.getByRole("button", { name: "新建会话" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("shows bootstrap failures with a recovery action", async () => {
    request.mockRejectedValueOnce(new Error("网络中断")); render(<ProductKnowledge/>);
    await screen.findByText("网络中断");
    await userEvent.click(screen.getByRole("button", { name: "重新检查服务" }));
    await screen.findByRole("button", { name: /设备资料/ });
  });
});
