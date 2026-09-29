import { apiRequest } from "../api";

export interface KnowledgeDataset {
  id: string;
  name: string;
  document_count: number;
}

export interface KnowledgeDocument {
  id: string;
  name: string;
  type: string;
  size: number;
  chunk_count: number;
  run: string | number | null;
  progress: string | number | null;
  updated_at: string;
}

export interface KnowledgeChunk {
  id: string;
  content: string;
}

export interface DocumentPage {
  documents: KnowledgeDocument[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface ChunkPage {
  document: Pick<KnowledgeDocument, "id" | "name">;
  chunks: KnowledgeChunk[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

const root = "/api/product/knowledge/datasets/";

export function listKnowledgeDatasets(signal: AbortSignal) {
  return apiRequest<{ datasets: KnowledgeDataset[] }>(root, { signal });
}

export function listKnowledgeDocuments(datasetId: string, page: number, query: string, signal: AbortSignal) {
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (query) params.set("q", query);
  return apiRequest<DocumentPage>(`${root}${encodeURIComponent(datasetId)}/documents/?${params}`, { signal });
}

export function listKnowledgeChunks(datasetId: string, documentId: string, page: number, signal: AbortSignal) {
  const params = new URLSearchParams({ page: String(page), page_size: "10" });
  return apiRequest<ChunkPage>(`${root}${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}/chunks/?${params}`, { signal });
}
