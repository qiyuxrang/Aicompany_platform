import { FormEvent, useRef, useState } from "react";
import ModelSelector from "../ModelSelector";
import { apiRequest, isApiError } from "../api";
import type { ModelSelection } from "../model-selection-api";
import { EmptyPanel, SectionHeader } from "./shared";

export type DepartmentModelCode = "product" | "hr" | "cost";

const routes: Record<DepartmentModelCode, string> = {
  product: "product_assistant",
  hr: "hr_assistant",
  cost: "engineering_assistant",
};

function readContent(value: unknown): string {
  if (!value || typeof value !== "object" || typeof (value as { content?: unknown }).content !== "string") {
    throw new Error("模型助手返回格式无效。");
  }
  return (value as { content: string }).content;
}

export default function DepartmentModelPanel({ moduleCode, preview = false }: {
  moduleCode: DepartmentModelCode;
  preview?: boolean;
}) {
  const [prompt, setPrompt] = useState("");
  const [selection, setSelection] = useState<ModelSelection | null>(null);
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  if (preview) return <section className="center-panel" aria-label="模型助手管理预览">
    <EmptyPanel title="模型助手（静态预览）">管理预览不读取模型选项或部门数据，也不能提交问题。</EmptyPanel>
  </section>;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const boundedPrompt = prompt.trim();
    if (submittingRef.current || !selection || !boundedPrompt) return;
    submittingRef.current = true;
    setSubmitting(true); setError(""); setContent("");
    try {
      const response = await apiRequest<unknown>(`/api/models/departments/${moduleCode}/ask/`, {
        method: "POST",
        body: JSON.stringify({ prompt: boundedPrompt, model_selection: selection }),
      });
      setContent(readContent(response));
    } catch (caught) {
      setError(isApiError(caught) && [401, 403].includes(caught.status)
        ? "当前账号未获此模型助手授权。"
        : "模型助手暂时无法完成请求，请稍后重试。");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return <section className="center-panel" aria-label="部门模型助手">
    <SectionHeader title="模型助手" description="使用当前部门已授权的模型处理通用文本问题。" />
    <form aria-label="部门模型助手提问" onSubmit={submit}>
      <div className="center-field"><ModelSelector route={routes[moduleCode]} value={selection} onChange={setSelection} label="使用模型" disabled={submitting} /></div>
      <div className="center-field"><label htmlFor="department-model-prompt">问题</label><textarea id="department-model-prompt" value={prompt} maxLength={4000} disabled={submitting} onChange={(event) => setPrompt(event.target.value)} placeholder="请输入需要模型协助分析或起草的内容" /></div>
      <p className="center-note" role="note">助手输出仅供参考，必须人工复核，不得用于工程报价、招聘录用等自动决定。</p>
      <div className="center-actions"><button className="button primary" disabled={submitting || !selection || !prompt.trim()}>{submitting ? "正在生成…" : "提交问题"}</button></div>
    </form>
    {error && <p className="center-note" role="alert">{error}</p>}
    {content && <div className="center-preview" aria-live="polite"><h3>助手回复</h3><p>{content}</p></div>}
  </section>;
}
