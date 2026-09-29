import { FormEvent, useCallback, useEffect, useState } from "react";
import { isApiError } from "../api";
import { CenterLink } from "../centers/shared";
import {
  listKnowledgeChunks,
  listKnowledgeDatasets,
  listKnowledgeDocuments,
  type ChunkPage,
  type DocumentPage,
  type KnowledgeDataset,
  type KnowledgeDocument,
} from "./knowledge-materials-api";
import { formatBytes, formatDate, ProductIcon } from "./workbench-shared";

type LoadState<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; value: T }
  | { kind: "error"; message: string; permission: boolean };

const idle = { kind: "idle" } as const;

function requestError(error: unknown, fallback: string) {
  const permission = isApiError(error) && [401, 403, 404].includes(error.status);
  return {
    permission,
    message: permission ? "当前账号未获得这些知识库资料的访问权限，或授权已撤销。" : error instanceof Error ? error.message : fallback,
  };
}

function compactDatasetName(name: string) {
  return name.replace(/^\d+_产品事业部_/, "") || name;
}

function progressText(progress: KnowledgeDocument["progress"]) {
  if (typeof progress === "number" && Number.isFinite(progress)) {
    const value = progress <= 1 ? progress * 100 : progress;
    return `${Math.max(0, Math.min(100, Math.round(value)))}%`;
  }
  return typeof progress === "string" && progress.trim() ? progress.trim() : "";
}

function extractionState(document: KnowledgeDocument) {
  if (document.chunk_count > 0) return { label: `已解析 ${document.chunk_count} 段`, tone: "ready" };
  const run = String(document.run ?? "").toLowerCase();
  const progress = progressText(document.progress);
  if (["4", "failed", "failure", "error"].some(value => run.includes(value))) return { label: "解析失败", tone: "error" };
  if (["1", "running", "parsing", "processing", "queued"].some(value => run === value || run.includes(value))) return { label: `解析中${progress ? ` ${progress}` : ""}`, tone: "working" };
  if (["2", "cancelled", "canceled"].some(value => run === value || run.includes(value))) return { label: "解析已取消", tone: "muted" };
  if (["3", "done", "completed", "success"].some(value => run === value || run.includes(value))) return { label: "未提取到文字", tone: "muted" };
  return { label: progress && progress !== "0%" ? `等待解析 ${progress}` : "等待解析", tone: "muted" };
}

function Loading({ children }: { children: string }) {
  return <div className="km-loading" role="status" aria-busy="true"><span className="pd-spinner" />{children}</div>;
}

function ErrorState({ title, state, retry }: { title: string; state: Extract<LoadState<unknown>, { kind: "error" }>; retry: () => void }) {
  return <div className="km-state km-error" role="alert"><ProductIcon name="shield"/><div><h3>{title}</h3><p>{state.message}</p><button type="button" className="button secondary" onClick={retry}>{state.permission ? "重新检查权限" : "重新加载"}</button></div></div>;
}

export default function KnowledgeMaterials() {
  const [datasets, setDatasets] = useState<LoadState<KnowledgeDataset[]>>({ kind: "loading" });
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [documents, setDocuments] = useState<LoadState<DocumentPage>>(idle);
  const [selectedDocument, setSelectedDocument] = useState<KnowledgeDocument | null>(null);
  const [chunks, setChunks] = useState<LoadState<ChunkPage>>(idle);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [documentPage, setDocumentPage] = useState(1);
  const [chunkPage, setChunkPage] = useState(1);
  const [datasetReload, setDatasetReload] = useState(0);
  const [documentReload, setDocumentReload] = useState(0);
  const [chunkReload, setChunkReload] = useState(0);

  const denyAccess = useCallback((message: string) => {
    setDatasets({ kind: "error", message, permission: true });
    setSelectedDatasetId("");
    setDocuments(idle);
    setSelectedDocument(null);
    setChunks(idle);
    setSearch("");
    setQuery("");
    setDocumentPage(1);
    setChunkPage(1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDatasets({ kind: "loading" });
    setSelectedDatasetId("");
    setDocuments(idle);
    setSelectedDocument(null);
    setChunks(idle);
    setSearch("");
    setQuery("");
    setDocumentPage(1);
    setChunkPage(1);
    void listKnowledgeDatasets(controller.signal).then(result => {
      if (!Array.isArray(result.datasets)) throw new Error("知识库分类返回格式无效。");
      if (controller.signal.aborted) return;
      setDatasets({ kind: "ready", value: result.datasets });
      setSelectedDatasetId(result.datasets[0]?.id || "");
    }).catch(error => {
      if (controller.signal.aborted) return;
      const detail = requestError(error, "暂时无法读取知识库分类，请稍后重试。");
      setDatasets({ kind: "error", ...detail });
    });
    return () => controller.abort();
  }, [datasetReload]);

  useEffect(() => {
    if (!selectedDatasetId) {
      setDocuments(idle);
      return;
    }
    const controller = new AbortController();
    setDocuments({ kind: "loading" });
    setSelectedDocument(null);
    setChunks(idle);
    void listKnowledgeDocuments(selectedDatasetId, documentPage, query, controller.signal).then(result => {
      if (!Array.isArray(result.documents)) throw new Error("知识库资料返回格式无效。");
      if (!controller.signal.aborted) setDocuments({ kind: "ready", value: result });
    }).catch(error => {
      if (controller.signal.aborted) return;
      const detail = requestError(error, "暂时无法读取知识库资料，请稍后重试。");
      if (detail.permission) denyAccess(detail.message);
      else setDocuments({ kind: "error", ...detail });
    });
    return () => controller.abort();
  }, [selectedDatasetId, documentPage, query, documentReload, denyAccess]);

  useEffect(() => {
    if (!selectedDatasetId || !selectedDocument) {
      setChunks(idle);
      return;
    }
    const controller = new AbortController();
    setChunks({ kind: "loading" });
    void listKnowledgeChunks(selectedDatasetId, selectedDocument.id, chunkPage, controller.signal).then(result => {
      if (!Array.isArray(result.chunks) || result.document.id !== selectedDocument.id) throw new Error("资料预览返回格式无效。");
      if (!controller.signal.aborted) setChunks({ kind: "ready", value: result });
    }).catch(error => {
      if (controller.signal.aborted) return;
      const detail = requestError(error, "暂时无法读取资料正文，请稍后重试。");
      if (detail.permission) denyAccess(detail.message);
      else setChunks({ kind: "error", ...detail });
    });
    return () => controller.abort();
  }, [selectedDatasetId, selectedDocument, chunkPage, chunkReload, denyAccess]);

  const selectedDataset = datasets.kind === "ready" ? datasets.value.find(item => item.id === selectedDatasetId) : undefined;

  function chooseDataset(id: string) {
    if (id === selectedDatasetId) return;
    setSelectedDatasetId(id);
    setSearch("");
    setQuery("");
    setDocumentPage(1);
    setSelectedDocument(null);
    setChunks(idle);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setQuery(search.trim());
    setDocumentPage(1);
    setSelectedDocument(null);
    setDocumentReload(value => value + 1);
  }

  function chooseDocument(document: KnowledgeDocument) {
    setSelectedDocument(document);
    setChunkPage(1);
    setChunks({ kind: "loading" });
  }

  return <div className="pd-workspace km-browser">
    <header className="km-header">
      <div><p className="pd-eyebrow">RAG 知识资料</p><h2>知识库资料</h2><p>按知识库浏览真实文档，并查看已解析的文字片段。这里只读展示，不修改知识库内容。</p></div>
      <CenterLink href="/centers/product/projects" className="button secondary"><ProductIcon name="folder"/>项目资料与任务</CenterLink>
    </header>

    {datasets.kind === "loading" && <Loading>正在读取知识库分类…</Loading>}
    {datasets.kind === "error" && <ErrorState title={datasets.permission ? "无法访问知识库资料" : "知识库暂不可用"} state={datasets} retry={() => setDatasetReload(value => value + 1)}/>}
    {datasets.kind === "ready" && datasets.value.length === 0 && <div className="km-state km-empty"><ProductIcon name="folder"/><div><h3>暂无可浏览的知识库</h3><p>当前账号尚未授权知识库，或已授权知识库中暂无可用分类。</p><button type="button" className="button secondary" onClick={() => setDatasetReload(value => value + 1)}>重新加载</button></div></div>}

    {datasets.kind === "ready" && datasets.value.length > 0 && <>
      <nav className="km-datasets" aria-label="知识库分类">
        {datasets.value.map(dataset => <button type="button" key={dataset.id} title={dataset.name} aria-label={`${dataset.name}，${dataset.document_count} 份资料`} aria-pressed={dataset.id === selectedDatasetId} className={dataset.id === selectedDatasetId ? "active" : ""} onClick={() => chooseDataset(dataset.id)}>
          <span>{compactDatasetName(dataset.name)}</span><strong>{dataset.document_count}</strong>
        </button>)}
      </nav>

      <div className="km-toolbar">
        <div><strong>{selectedDataset?.name || "知识库"}</strong><span>{documents.kind === "ready" ? `当前匹配 ${documents.value.total} 份资料` : `共 ${selectedDataset?.document_count ?? 0} 份资料`}</span></div>
        <form className="km-search" onSubmit={submitSearch}><ProductIcon name="search"/><input aria-label="搜索知识库资料" value={search} onChange={event => setSearch(event.target.value)} maxLength={200} placeholder="搜索当前知识库中的文件名…"/><button type="submit">搜索</button></form>
      </div>

      <div className="km-content">
        <section className="km-documents" aria-label={`${selectedDataset?.name || "知识库"}资料列表`}>
          {documents.kind === "loading" && <Loading>正在读取资料列表…</Loading>}
          {documents.kind === "error" && <ErrorState title="资料列表暂不可用" state={documents} retry={() => setDocumentReload(value => value + 1)}/>}
          {documents.kind === "ready" && documents.value.documents.length === 0 && <div className="km-state km-empty compact"><ProductIcon name="file"/><div><h3>{query ? "没有匹配的资料" : "这个知识库暂时没有资料"}</h3><p>{query ? "请调整文件名关键词后再试。" : "资料同步后会显示真实文件名和解析状态。"}</p></div></div>}
          {documents.kind === "ready" && documents.value.documents.length > 0 && <div className="km-document-list">
            {documents.value.documents.map(document => {
              const extraction = extractionState(document);
              return <button type="button" key={document.id} aria-pressed={selectedDocument?.id === document.id} className={selectedDocument?.id === document.id ? "selected" : ""} onClick={() => chooseDocument(document)}>
                <span className="km-document-icon"><ProductIcon name="file"/></span>
                <span className="km-document-copy"><strong>{document.name}</strong><small>{document.type || "文件"} · {formatBytes(document.size)} · {formatDate(document.updated_at)}</small></span>
                <span className={`km-run ${extraction.tone}`}>{extraction.label}</span>
                <span className="km-open" aria-hidden="true">›</span>
              </button>;
            })}
          </div>}
          {documents.kind === "ready" && documents.value.total > 0 && <div className="km-pagination"><span>第 {documents.value.page} / {Math.max(1, Math.ceil(documents.value.total / documents.value.page_size))} 页</span><div><button type="button" disabled={documentPage <= 1} onClick={() => setDocumentPage(value => value - 1)}>上一页</button><button type="button" disabled={!documents.value.has_more} onClick={() => setDocumentPage(value => value + 1)}>下一页</button></div></div>}
        </section>

        <aside className={`km-preview${selectedDocument ? " open" : ""}`} aria-label="资料内容预览">
          {!selectedDocument && <div className="km-preview-placeholder"><span><ProductIcon name="file"/></span><h3>选择资料查看正文</h3><p>预览展示知识库已提取的纯文本片段，不加载原文件、图片或网页内容。</p></div>}
          {selectedDocument && <>
            <header><div><span>文字片段预览</span><h3>{selectedDocument.name}</h3><p>{selectedDocument.type || "文件"} · {formatBytes(selectedDocument.size)} · 共 {selectedDocument.chunk_count} 段</p></div><button type="button" aria-label="关闭资料预览" onClick={() => setSelectedDocument(null)}>×</button></header>
            <div className="km-preview-body">
              {chunks.kind === "loading" && <Loading>正在读取资料正文…</Loading>}
              {chunks.kind === "error" && <ErrorState title="资料正文暂不可用" state={chunks} retry={() => setChunkReload(value => value + 1)}/>}
              {chunks.kind === "ready" && chunks.value.chunks.length === 0 && <div className="km-state km-empty compact"><ProductIcon name="file"/><div><h3>{selectedDocument.chunk_count === 0 ? "未提取到文字" : "当前页没有文字片段"}</h3><p>{selectedDocument.chunk_count === 0 ? "该资料尚未完成文字提取，或原文件没有可识别文本。" : "请切换其他页继续查看。"}</p></div></div>}
              {chunks.kind === "ready" && chunks.value.chunks.length > 0 && <div className="km-chunks">{chunks.value.chunks.map((chunk, index) => <article key={chunk.id}><span>片段 {(chunks.value.page - 1) * chunks.value.page_size + index + 1}</span><pre>{chunk.content}</pre></article>)}</div>}
            </div>
            {chunks.kind === "ready" && chunks.value.total > 0 && <div className="km-pagination preview"><span>共 {chunks.value.total} 段 · 第 {chunks.value.page} 页</span><div><button type="button" disabled={chunkPage <= 1} onClick={() => setChunkPage(value => value - 1)}>上一页</button><button type="button" disabled={!chunks.value.has_more} onClick={() => setChunkPage(value => value + 1)}>下一页</button></div></div>}
          </>}
        </aside>
      </div>
    </>}
  </div>;
}
